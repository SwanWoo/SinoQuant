"""
分析配置相关函数 — 模块级工具，无类依赖
"""

import logging
from typing import Any, Dict, List, Optional

from sinoquant.default_config import DEFAULT_CONFIG
from app.services.config_service import ConfigService
from app.core.config import settings
from app.utils.encryption import decrypt_sensitive_fields

logger = logging.getLogger("app.services.simple_analysis_service")

config_service = ConfigService()


# ---------------------------------------------------------------------------
# API Key 校验
# ---------------------------------------------------------------------------

def _is_valid_api_key_value(api_key: Optional[str]) -> bool:
    """校验 API Key 是否为可用值（过滤常见占位符）"""
    if not api_key:
        return False

    normalized = api_key.strip()
    if not normalized:
        return False

    invalid_literals = {
        "your-api-key",
        "your-deepseek-api-key",
        "your-openai-api-key",
        "your-dashscope-api-key",
        "your-anthropic-api-key",
        "test-api-key",
        "changeme",
        "none",
        "null",
    }
    lower_value = normalized.lower()
    if lower_value in invalid_literals:
        return False
    if lower_value.startswith("your-"):
        return False

    return True


# ---------------------------------------------------------------------------
# 厂家配置文档查询
# ---------------------------------------------------------------------------

def _get_provider_doc_sync(db, provider: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """获取厂家配置，优先用户配置，再全局 vendor_configs，最后 llm_providers。"""
    # 1) 用户专属 vendor_configs 配置
    if user_id:
        vendor_doc = db.vendor_configs.find_one(
            {"name": provider, "user_id": user_id, "vendor_type": "llm", "is_active": True}
        )
        if vendor_doc:
            logger.info(f"✅ [同步查询] 使用用户专属配置: {provider} (user={user_id})")
            return _adapt_vendor_doc(vendor_doc, "vendor_configs_user")

    # 2) 全局 vendor_configs 配置
    vendor_doc = db.vendor_configs.find_one(
        {"name": provider, "user_id": None, "vendor_type": "llm", "is_active": True}
    ) or db.vendor_configs.find_one(
        {"name": provider, "user_id": {"$exists": False}, "vendor_type": "llm"}
    )
    if vendor_doc:
        return _adapt_vendor_doc(vendor_doc, "vendor_configs")

    # 3) 兼容旧配置：llm_providers
    provider_doc = db.llm_providers.find_one({"name": provider})
    if provider_doc:
        provider_doc = dict(provider_doc)
        provider_doc["_config_source"] = "llm_providers"
        return provider_doc

    return None


def _adapt_vendor_doc(vendor_doc, source: str) -> Dict[str, Any]:
    """适配 vendor_configs 文档字段名，统一为 llm_providers 的读取习惯"""
    # 解密敏感字段（兼容未加密的存量数据）
    enc_key = settings.ENCRYPTION_KEY or None
    if enc_key:
        vendor_doc = dict(vendor_doc)  # 避免修改原始文档
        decrypt_sensitive_fields(vendor_doc, enc_key)

    return {
        "name": vendor_doc.get("name"),
        "display_name": vendor_doc.get("display_name"),
        "api_key": vendor_doc.get("api_key"),
        "default_base_url": vendor_doc.get("base_url"),
        "is_active": vendor_doc.get("is_active", True),
        "_config_source": source,
    }


# ---------------------------------------------------------------------------
# 按模型名称查找供应商（异步）
# ---------------------------------------------------------------------------

async def get_provider_by_model_name(model_name: str) -> str:
    """
    根据模型名称从数据库配置中查找对应的供应商（异步版本）

    Args:
        model_name: 模型名称，如 'qwen-turbo', 'gpt-4' 等

    Returns:
        str: 供应商名称，如 'dashscope', 'openai' 等
    """
    try:
        # 从配置服务获取系统配置
        system_config = await config_service.get_system_config()
        if not system_config or not system_config.llm_configs:
            logger.warning(f"⚠️ 系统配置为空，使用默认供应商映射")
            return _get_default_provider_by_model(model_name)

        # 在LLM配置中查找匹配的模型
        for llm_config in system_config.llm_configs:
            if llm_config.model_name == model_name:
                provider = llm_config.provider.value if hasattr(llm_config.provider, 'value') else str(llm_config.provider)
                logger.info(f"✅ 从数据库找到模型 {model_name} 的供应商: {provider}")
                return provider

        # 如果数据库中没有找到，使用默认映射
        logger.warning(f"⚠️ 数据库中未找到模型 {model_name}，使用默认映射")
        return _get_default_provider_by_model(model_name)

    except Exception as e:
        logger.error(f"❌ 查找模型供应商失败: {e}")
        return _get_default_provider_by_model(model_name)


# ---------------------------------------------------------------------------
# 按模型名称查找供应商（同步）
# ---------------------------------------------------------------------------

def get_provider_by_model_name_sync(model_name: str) -> str:
    """
    根据模型名称从数据库配置中查找对应的供应商（同步版本）

    Args:
        model_name: 模型名称，如 'qwen-turbo', 'gpt-4' 等

    Returns:
        str: 供应商名称，如 'dashscope', 'openai' 等
    """
    provider_info = get_provider_and_url_by_model_sync(model_name)
    return provider_info["provider"]


def get_provider_and_url_by_model_sync(model_name: str, user_id: Optional[str] = None) -> dict:
    """
    根据模型名称从数据库配置中查找对应的供应商和 API URL（同步版本）

    Args:
        model_name: 模型名称，如 'qwen-turbo', 'gpt-4' 等
        user_id: 用户ID（可选，传入时优先使用用户专属配置）

    Returns:
        dict: {"provider": "google", "backend_url": "https://...", "api_key": "xxx"}
    """
    try:
        # 使用同步 MongoDB 客户端直接查询
        from pymongo import MongoClient
        from app.core.config import settings

        client = MongoClient(settings.MONGO_URI)
        db = client[settings.MONGO_DB]

        # 查询最新的活跃配置
        configs_collection = db.system_configs
        doc = configs_collection.find_one({"is_active": True}, sort=[("version", -1)])

        if doc and "llm_configs" in doc:
            llm_configs = doc["llm_configs"]

            for config_dict in llm_configs:
                if config_dict.get("model_name") == model_name:
                    provider = config_dict.get("provider")
                    api_base = config_dict.get("api_base")
                    model_api_key = config_dict.get("api_key")  # 🔥 获取模型配置的 API Key

                    # 从 llm_providers 集合中查找厂家配置（优先用户配置）
                    provider_doc = _get_provider_doc_sync(db, provider, user_id=user_id)

                    # 🔒 确定 API Key（仅数据库：模型配置 > 厂家配置）
                    api_key = None
                    if _is_valid_api_key_value(model_api_key):
                        api_key = model_api_key
                        logger.info(f"✅ [同步查询] 使用模型配置的 API Key")
                    elif provider_doc and provider_doc.get("api_key"):
                        provider_api_key = provider_doc["api_key"]
                        if _is_valid_api_key_value(provider_api_key):
                            api_key = provider_api_key
                            logger.info(f"✅ [同步查询] 使用厂家配置的 API Key")

                    if not api_key:
                        logger.warning(f"⚠️ [同步查询] 未在数据库中找到 {provider} 的 API Key，请在 Web 界面配置")

                    # 确定 backend_url
                    backend_url = None
                    if api_base:
                        backend_url = api_base
                        logger.info(f"✅ [同步查询] 模型 {model_name} 使用自定义 API: {api_base}")
                    elif provider_doc and provider_doc.get("default_base_url"):
                        backend_url = provider_doc["default_base_url"]
                        provider_source = provider_doc.get("_config_source", "llm_providers")
                        logger.info(
                            f"✅ [同步查询] 模型 {model_name} 使用厂家默认 API: {backend_url} (source={provider_source})"
                        )
                    else:
                        backend_url = _get_default_backend_url(provider)
                        logger.warning(f"⚠️ [同步查询] 厂家 {provider} 没有配置 default_base_url，使用硬编码默认值")

                    client.close()
                    return {
                        "provider": provider,
                        "backend_url": backend_url,
                        "api_key": api_key
                    }

        client.close()

        # 如果数据库中没有找到模型配置，使用默认映射
        logger.warning(f"⚠️ [同步查询] 数据库中未找到模型 {model_name}，使用默认映射")
        provider = _get_default_provider_by_model(model_name)

        # 尝试从厂家配置中获取 default_base_url 和 API Key
        try:
            client = MongoClient(settings.MONGO_URI)
            db = client[settings.MONGO_DB]
            provider_doc = _get_provider_doc_sync(db, provider, user_id=user_id)

            backend_url = _get_default_backend_url(provider)
            api_key = None

            if provider_doc:
                if provider_doc.get("default_base_url"):
                    backend_url = provider_doc["default_base_url"]
                    provider_source = provider_doc.get("_config_source", "llm_providers")
                    logger.info(
                        f"✅ [同步查询] 使用厂家 {provider} 的 default_base_url: {backend_url} (source={provider_source})"
                    )

                if provider_doc.get("api_key"):
                    provider_api_key = provider_doc["api_key"]
                    if _is_valid_api_key_value(provider_api_key):
                        api_key = provider_api_key
                        logger.info(f"✅ [同步查询] 使用厂家 {provider} 的 API Key")

            if not api_key:
                logger.warning(f"⚠️ [同步查询] 未在数据库中找到 {provider} 的 API Key，请在 Web 界面配置")

            client.close()
            return {
                "provider": provider,
                "backend_url": backend_url,
                "api_key": api_key
            }
        except Exception as e:
            logger.warning(f"⚠️ [同步查询] 无法查询厂家配置: {e}")

        # 最后回退：无 API Key
        return {
            "provider": provider,
            "backend_url": _get_default_backend_url(provider),
            "api_key": None
        }

    except Exception as e:
        logger.error(f"❌ [同步查询] 查找模型供应商失败: {e}")
        provider = _get_default_provider_by_model(model_name)

        # 尝试从厂家配置中获取 default_base_url 和 API Key
        try:
            from pymongo import MongoClient
            from app.core.config import settings

            client = MongoClient(settings.MONGO_URI)
            db = client[settings.MONGO_DB]
            provider_doc = _get_provider_doc_sync(db, provider, user_id=user_id)

            backend_url = _get_default_backend_url(provider)
            api_key = None

            if provider_doc:
                if provider_doc.get("default_base_url"):
                    backend_url = provider_doc["default_base_url"]
                    provider_source = provider_doc.get("_config_source", "llm_providers")
                    logger.info(
                        f"✅ [同步查询] 使用厂家 {provider} 的 default_base_url: {backend_url} (source={provider_source})"
                    )

                if provider_doc.get("api_key"):
                    provider_api_key = provider_doc["api_key"]
                    if _is_valid_api_key_value(provider_api_key):
                        api_key = provider_api_key
                        logger.info(f"✅ [同步查询] 使用厂家 {provider} 的 API Key")

            if not api_key:
                logger.warning(f"⚠️ [同步查询] 未在数据库中找到 {provider} 的 API Key，请在 Web 界面配置")

            client.close()
            return {
                "provider": provider,
                "backend_url": backend_url,
                "api_key": api_key
            }
        except Exception as e2:
            logger.warning(f"⚠️ [同步查询] 无法查询厂家配置: {e2}")

        # 最后回退：无 API Key
        return {
            "provider": provider,
            "backend_url": _get_default_backend_url(provider),
            "api_key": None
        }


# ---------------------------------------------------------------------------
# 默认 URL / 供应商映射
# ---------------------------------------------------------------------------

def _get_default_backend_url(provider: str) -> str:
    """
    根据供应商名称返回默认的 backend_url

    Args:
        provider: 供应商名称，如 'google', 'dashscope' 等

    Returns:
        str: 默认的 backend_url
    """
    default_urls = {
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "dashscope": "https://dashscope.aliyuncs.com/api/v1",
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com",
        "anthropic": "https://api.anthropic.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "qianfan": "https://qianfan.baidubce.com/v2",
        "302ai": "https://api.302.ai/v1",
    }

    url = default_urls.get(provider, "https://dashscope.aliyuncs.com/compatible-mode/v1")
    logger.info(f"🔧 [默认URL] {provider} -> {url}")
    return url


def _get_default_provider_by_model(model_name: str) -> str:
    """
    根据模型名称返回默认的供应商映射
    这是一个后备方案，当数据库查询失败时使用
    """
    # 模型名称到供应商的默认映射
    model_provider_map = {
        # 阿里百炼 (DashScope)
        'qwen-turbo': 'dashscope',
        'qwen-plus': 'dashscope',
        'qwen-max': 'dashscope',
        'qwen-plus-latest': 'dashscope',
        'qwen-max-longcontext': 'dashscope',

        # OpenAI
        'gpt-3.5-turbo': 'openai',
        'gpt-4': 'openai',
        'gpt-4-turbo': 'openai',
        'gpt-4o': 'openai',
        'gpt-4o-mini': 'openai',

        # Google
        'gemini-pro': 'google',
        'gemini-2.0-flash': 'google',
        'gemini-2.0-flash-thinking-exp': 'google',

        # DeepSeek
        'deepseek-chat': 'deepseek',
        'deepseek-coder': 'deepseek',
        'deepseek-v4-pro': 'deepseek',
        'deepseek-flash': 'deepseek',
        'deepseek-reasoner': 'deepseek',

        # 智谱AI
        'glm-4': 'zhipu',
        'glm-3-turbo': 'zhipu',
        'chatglm3-6b': 'zhipu'
    }

    provider = model_provider_map.get(model_name, 'deepseek')
    logger.info(f"🔧 使用默认映射: {model_name} -> {provider}")
    return provider


# ---------------------------------------------------------------------------
# 创建分析配置
# ---------------------------------------------------------------------------

def create_analysis_config(
    research_depth,  # 支持数字(1-5)或字符串("快速", "标准", "深度")
    selected_analysts: list,
    quick_model: str,
    deep_model: str,
    llm_provider: str,
    market_type: str = "A股",
    quick_model_config: dict = None,  # 新增：快速模型的完整配置
    deep_model_config: dict = None,   # 新增：深度模型的完整配置
    user_id: Optional[str] = None     # 用户ID（用于用户级配置解析）
) -> dict:
    """
    创建分析配置 - 支持数字等级和中文等级

    Args:
        research_depth: 研究深度，支持数字(1-5)或中文("快速", "基础", "标准", "深度", "全面")
        selected_analysts: 选中的分析师列表
        quick_model: 快速分析模型
        deep_model: 深度分析模型
        llm_provider: LLM供应商
        market_type: 市场类型
        quick_model_config: 快速模型的完整配置（包含 max_tokens、temperature、timeout 等）
        deep_model_config: 深度模型的完整配置（包含 max_tokens、temperature、timeout 等）

    Returns:
        dict: 完整的分析配置
    """
    # 🔍 [调试] 记录接收到的原始参数
    logger.info(f"🔍 [配置创建] 接收到的research_depth参数: {research_depth} (类型: {type(research_depth).__name__})")

    # 数字等级到中文等级的映射
    numeric_to_chinese = {
        1: "快速",
        2: "基础",
        3: "标准",
        4: "深度",
        5: "全面"
    }

    # 标准化研究深度：支持数字输入
    if isinstance(research_depth, (int, float)):
        research_depth = int(research_depth)
        if research_depth in numeric_to_chinese:
            chinese_depth = numeric_to_chinese[research_depth]
            logger.info(f"🔢 [等级转换] 数字等级 {research_depth} → 中文等级 '{chinese_depth}'")
            research_depth = chinese_depth
        else:
            logger.warning(f"⚠️ 无效的数字等级: {research_depth}，使用默认标准分析")
            research_depth = "标准"
    elif isinstance(research_depth, str):
        # 如果是字符串形式的数字，转换为整数
        if research_depth.isdigit():
            numeric_level = int(research_depth)
            if numeric_level in numeric_to_chinese:
                chinese_depth = numeric_to_chinese[numeric_level]
                logger.info(f"🔢 [等级转换] 字符串数字 '{research_depth}' → 中文等级 '{chinese_depth}'")
                research_depth = chinese_depth
            else:
                logger.warning(f"⚠️ 无效的字符串数字等级: {research_depth}，使用默认标准分析")
                research_depth = "标准"
        # 如果已经是中文等级，直接使用
        elif research_depth in ["快速", "基础", "标准", "深度", "全面"]:
            logger.info(f"📝 [等级确认] 使用中文等级: '{research_depth}'")
        else:
            logger.warning(f"⚠️ 未知的研究深度: {research_depth}，使用默认标准分析")
            research_depth = "标准"
    else:
        logger.warning(f"⚠️ 无效的研究深度类型: {type(research_depth)}，使用默认标准分析")
        research_depth = "标准"

    # 从DEFAULT_CONFIG开始，完全复制web目录的逻辑
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = llm_provider
    config["deep_think_llm"] = deep_model
    config["quick_think_llm"] = quick_model

    # 根据研究深度调整配置 - 支持5个级别（与Web界面保持一致）
    if research_depth == "快速":
        # 1级 - 快速分析
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 1
        config["memory_enabled"] = False  # 禁用记忆以加速
        config["online_tools"] = True  # 统一使用在线工具，避免离线工具的各种问题
        logger.info(f"🔧 [1级-快速分析] {market_type}使用统一工具，确保数据源正确和稳定性")
        logger.info(f"🔧 [1级-快速分析] 使用用户配置的模型: quick={quick_model}, deep={deep_model}")

    elif research_depth == "基础":
        # 2级 - 基础分析
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 1
        config["memory_enabled"] = True
        config["online_tools"] = True
        logger.info(f"🔧 [2级-基础分析] {market_type}使用在线工具，获取最新数据")
        logger.info(f"🔧 [2级-基础分析] 使用用户配置的模型: quick={quick_model}, deep={deep_model}")

    elif research_depth == "标准":
        # 3级 - 标准分析（推荐）
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 2
        config["memory_enabled"] = True
        config["online_tools"] = True
        logger.info(f"🔧 [3级-标准分析] {market_type}平衡速度和质量（推荐）")
        logger.info(f"🔧 [3级-标准分析] 使用用户配置的模型: quick={quick_model}, deep={deep_model}")

    elif research_depth == "深度":
        # 4级 - 深度分析
        config["max_debate_rounds"] = 2
        config["max_risk_discuss_rounds"] = 2
        config["memory_enabled"] = True
        config["online_tools"] = True
        logger.info(f"🔧 [4级-深度分析] {market_type}多轮辩论，深度研究")
        logger.info(f"🔧 [4级-深度分析] 使用用户配置的模型: quick={quick_model}, deep={deep_model}")

    elif research_depth == "全面":
        # 5级 - 全面分析
        config["max_debate_rounds"] = 3
        config["max_risk_discuss_rounds"] = 3
        config["memory_enabled"] = True
        config["online_tools"] = True
        logger.info(f"🔧 [5级-全面分析] {market_type}最全面的分析，最高质量")
        logger.info(f"🔧 [5级-全面分析] 使用用户配置的模型: quick={quick_model}, deep={deep_model}")

    else:
        # 默认使用标准分析
        logger.warning(f"⚠️ 未知的研究深度: {research_depth}，使用标准分析")
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 2
        config["memory_enabled"] = True
        config["online_tools"] = True

    # 🔧 获取 backend_url 和 API Key（优先级：模型配置 > 厂家配置 > 环境变量）
    try:
        # 1️⃣ 优先从数据库获取（包含模型配置的 api_base、API Key 和厂家的 default_base_url、API Key）
        quick_provider_info = get_provider_and_url_by_model_sync(quick_model, user_id=user_id)
        deep_provider_info = get_provider_and_url_by_model_sync(deep_model, user_id=user_id)

        config["backend_url"] = quick_provider_info["backend_url"]
        config["quick_api_key"] = quick_provider_info.get("api_key")  # 🔥 保存快速模型的 API Key
        config["deep_api_key"] = deep_provider_info.get("api_key")    # 🔥 保存深度模型的 API Key

        logger.info(f"✅ 使用数据库配置的 backend_url: {quick_provider_info['backend_url']}")
        logger.info(f"   来源: 模型 {quick_model} 的配置或厂家 {quick_provider_info['provider']} 的默认地址")
        logger.info(f"🔑 快速模型 API Key: {'已配置' if config['quick_api_key'] else '未配置（将使用环境变量）'}")
        logger.info(f"🔑 深度模型 API Key: {'已配置' if config['deep_api_key'] else '未配置（将使用环境变量）'}")
    except Exception as e:
        logger.warning(f"⚠️  无法从数据库获取 backend_url 和 API Key: {e}")
        # 2️⃣ 回退到硬编码的默认 URL，API Key 将从环境变量读取
        if llm_provider == "dashscope":
            config["backend_url"] = "https://dashscope.aliyuncs.com/api/v1"
        elif llm_provider == "deepseek":
            config["backend_url"] = "https://api.deepseek.com"
        elif llm_provider == "openai":
            config["backend_url"] = "https://api.openai.com/v1"
        elif llm_provider == "google":
            config["backend_url"] = "https://generativelanguage.googleapis.com/v1beta"
        elif llm_provider == "qianfan":
            config["backend_url"] = "https://aip.baidubce.com"
        else:
            # 🔧 未知厂家，尝试从数据库获取厂家的 default_base_url
            logger.warning(f"⚠️  未知厂家 {llm_provider}，尝试从数据库获取配置")
            try:
                from pymongo import MongoClient
                from app.core.config import settings

                client = MongoClient(settings.MONGO_URI)
                db = client[settings.MONGO_DB]
                provider_doc = _get_provider_doc_sync(db, llm_provider, user_id=user_id)

                if provider_doc and provider_doc.get("default_base_url"):
                    config["backend_url"] = provider_doc["default_base_url"]
                    provider_source = provider_doc.get("_config_source", "llm_providers")
                    logger.info(
                        f"✅ 从数据库获取自定义厂家 {llm_provider} 的 backend_url: {config['backend_url']} (source={provider_source})"
                    )
                else:
                    # 如果数据库中也没有，使用 OpenAI 兼容格式作为最后的回退
                    config["backend_url"] = "https://api.openai.com/v1"
                    logger.warning(f"⚠️  数据库中未找到厂家 {llm_provider} 的配置，使用默认 OpenAI 端点")

                client.close()
            except Exception as e2:
                logger.error(f"❌ 查询数据库失败: {e2}，使用默认 OpenAI 端点")
                config["backend_url"] = "https://api.openai.com/v1"

        logger.info(f"⚠️  使用回退的 backend_url: {config['backend_url']}")

    # 添加分析师配置
    config["selected_analysts"] = selected_analysts
    config["debug"] = False

    # 🔧 添加research_depth到配置中，使工具函数能够访问分析级别信息
    config["research_depth"] = research_depth

    # 🔧 添加模型配置参数（max_tokens、temperature、timeout、retry_times）
    if quick_model_config:
        config["quick_model_config"] = quick_model_config
        logger.info(f"🔧 [快速模型配置] max_tokens={quick_model_config.get('max_tokens')}, "
                   f"temperature={quick_model_config.get('temperature')}, "
                   f"timeout={quick_model_config.get('timeout')}, "
                   f"retry_times={quick_model_config.get('retry_times')}")

    if deep_model_config:
        config["deep_model_config"] = deep_model_config
        logger.info(f"🔧 [深度模型配置] max_tokens={deep_model_config.get('max_tokens')}, "
                   f"temperature={deep_model_config.get('temperature')}, "
                   f"timeout={deep_model_config.get('timeout')}, "
                   f"retry_times={deep_model_config.get('retry_times')}")

    logger.info(f"📋 ========== 创建分析配置完成 ==========")
    logger.info(f"   🎯 研究深度: {research_depth}")
    logger.info(f"   🔥 辩论轮次: {config['max_debate_rounds']}")
    logger.info(f"   ⚖️ 风险讨论轮次: {config['max_risk_discuss_rounds']}")
    logger.info(f"   💾 记忆功能: {config['memory_enabled']}")
    logger.info(f"   🌐 在线工具: {config['online_tools']}")
    logger.info(f"   🤖 LLM供应商: {llm_provider}")
    logger.info(f"   ⚡ 快速模型: {config['quick_think_llm']}")
    logger.info(f"   🧠 深度模型: {config['deep_think_llm']}")
    logger.info(f"📋 ========================================")

    return config


# ---------------------------------------------------------------------------
# 研究深度归一化 & 报告可见性策略
# ---------------------------------------------------------------------------

def _normalize_research_depth_label(research_depth: Any) -> str:
    """将研究深度统一归一化为中文标签。"""
    numeric_to_chinese = {
        1: "快速",
        2: "基础",
        3: "标准",
        4: "深度",
        5: "全面",
    }

    if isinstance(research_depth, (int, float)):
        return numeric_to_chinese.get(int(research_depth), "标准")

    if isinstance(research_depth, str):
        s = research_depth.strip()
        if s.isdigit():
            return numeric_to_chinese.get(int(s), "标准")
        if s in ("快速", "基础", "标准", "深度", "全面"):
            return s

    return "标准"


def _apply_report_visibility_policy(
    reports: Dict[str, Any],
    research_depth: Any,
    selected_analysts: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    报告可见性策略：
    - 各等级均按"选中的分析师"收敛报告
    - 深度越高，逐步增加团队决策类摘要模块
    - 5级（全面）不过滤，返回全量报告
    """
    if not isinstance(reports, dict):
        return reports

    depth_label = _normalize_research_depth_label(research_depth)
    depth_level_map = {
        "快速": 1,
        "基础": 2,
        "标准": 3,
        "深度": 4,
        "全面": 5,
    }
    depth_level = depth_level_map.get(depth_label, 3)

    # 全面分析不做任何过滤，保持全量
    if depth_level >= 5:
        return reports

    analyst_alias_map = {
        "market": "market",
        "fundamentals": "fundamentals",
        "news": "news",
        "social": "social",
        "social_media": "social",
        "市场分析师": "market",
        "基本面分析师": "fundamentals",
        "新闻分析师": "news",
        "社媒分析师": "social",
        "社交媒体分析师": "social",
    }
    analyst_report_map = {
        "market": "market_report",
        "fundamentals": "fundamentals_report",
        "news": "news_report",
        "social": "sentiment_report",
    }

    selected_analysts = selected_analysts or []
    normalized_selected = []
    for analyst in selected_analysts:
        if not analyst:
            continue
        normalized_selected.append(analyst_alias_map.get(str(analyst), str(analyst)))

    # 基础可见项：所选分析师报告 + 最终交易决策
    allowed_keys = {"final_trade_decision"}
    for analyst in normalized_selected:
        report_key = analyst_report_map.get(analyst)
        if report_key:
            allowed_keys.add(report_key)

    # 按深度逐级放开团队摘要模块（默认不放开冗长辩论历史）
    if depth_level >= 2:
        allowed_keys.add("research_team_decision")
    if depth_level >= 3:
        allowed_keys.add("trader_investment_plan")
    if depth_level >= 4:
        allowed_keys.add("risk_management_decision")
    if depth_level >= 5:
        allowed_keys.add("investment_plan")

    filtered_reports = {
        key: value for key, value in reports.items() if key in allowed_keys
    }

    # 兜底：若未命中预期键，至少保留关键模块
    if not filtered_reports:
        fallback_candidates = [
            "final_trade_decision",
            "market_report",
            "fundamentals_report",
            "news_report",
            "sentiment_report",
            "research_team_decision",
            "trader_investment_plan",
            "risk_management_decision",
            "investment_plan",
        ]
        for fallback_key in fallback_candidates:
            if fallback_key in reports:
                filtered_reports[fallback_key] = reports[fallback_key]

    return filtered_reports or reports
