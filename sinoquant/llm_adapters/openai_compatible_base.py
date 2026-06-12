"""
OpenAI兼容适配器基类
为所有支持OpenAI接口的LLM提供商提供统一的基础实现
"""

import copy
import os
import time
from typing import Any, Dict, List, Optional, Union
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import CallbackManagerForLLMRun

# 导入统一日志系统
from sinoquant.utils.logging_init import setup_llm_logging

# 导入日志模块
from sinoquant.utils.logging_manager import get_logger, get_logger_manager
logger = get_logger('agents')
logger = setup_llm_logging()

# 导入token跟踪器
try:
    from sinoquant.config.config_manager import token_tracker
    TOKEN_TRACKING_ENABLED = True
    logger.info("✅ Token跟踪功能已启用")
except ImportError:
    TOKEN_TRACKING_ENABLED = False
    logger.warning("⚠️ Token跟踪功能未启用")


# =========================================================================
# Monkey-patch: 保留 DeepSeek thinking 模式的 reasoning_content
# =========================================================================
# DeepSeek flash/pro 模型默认开启 thinking 模式，返回 reasoning_content。
# API 要求后续请求原样传回 reasoning_content，否则返回 400 错误。
# LangChain 的 _convert_dict_to_message 和 _convert_message_to_dict
# 会丢弃 reasoning_content，导致多轮对话失败。
# 此 patch 让 LangChain 在 AIMessage.additional_kwargs 中保存
# reasoning_content，并在序列化时将其传回 API。

def _patch_langchain_reasoning_content():
    """Patch LangChain to preserve reasoning_content for DeepSeek thinking mode."""
    try:
        from langchain_openai.chat_models.base import (
            _convert_dict_to_message as _orig_convert_dict,
            _convert_message_to_dict as _orig_convert_msg,
        )
        import langchain_openai.chat_models.base as _base_module

        # Patch 1: _convert_dict_to_message — 保存 reasoning_content 到 additional_kwargs
        def _patched_convert_dict_to_message(_dict):
            msg = _orig_convert_dict(_dict)
            if isinstance(msg, AIMessage) and _dict.get("reasoning_content"):
                msg.additional_kwargs["reasoning_content"] = _dict["reasoning_content"]
            return msg

        _base_module._convert_dict_to_message = _patched_convert_dict_to_message

        # Patch 2: _convert_message_to_dict — 从 additional_kwargs 读取 reasoning_content
        def _patched_convert_message_to_dict(message):
            msg_dict = _orig_convert_msg(message)
            if isinstance(message, AIMessage) and "reasoning_content" in message.additional_kwargs:
                msg_dict["reasoning_content"] = message.additional_kwargs["reasoning_content"]
            return msg_dict

        _base_module._convert_message_to_dict = _patched_convert_message_to_dict

        logger.info("✅ LangChain reasoning_content 保留 patch 已安装")
    except Exception as e:
        logger.warning(f"⚠️ LangChain reasoning_content patch 安装失败: {e}")


# 安装 patch
_patch_langchain_reasoning_content()


class OpenAICompatibleBase(ChatOpenAI):
    """
    OpenAI兼容适配器基类
    为所有支持OpenAI接口的LLM提供商提供统一实现
    """

    def __init__(
        self,
        provider_name: str,
        model: str,
        api_key_env_var: str,
        base_url: str,
        api_key: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        """
        初始化OpenAI兼容适配器

        Args:
            provider_name: 提供商名称 (如: "deepseek", "dashscope")
            model: 模型名称
            api_key_env_var: API密钥环境变量名
            base_url: API基础URL
            api_key: API密钥，如果不提供则从环境变量获取
            temperature: 温度参数
            max_tokens: 最大token数
            **kwargs: 其他参数
        """

        # 🔍 [DEBUG] 读取环境变量前的日志
        logger.info(f"🔍 [{provider_name}初始化] 开始初始化 OpenAI 兼容适配器")
        logger.info(f"🔍 [{provider_name}初始化] 模型: {model}")
        logger.info(f"🔍 [{provider_name}初始化] API Key 环境变量名: {api_key_env_var}")
        logger.info(f"🔍 [{provider_name}初始化] 是否传入 api_key 参数: {api_key is not None}")

        # 在父类初始化前先缓存元信息到私有属性（避免Pydantic字段限制）
        object.__setattr__(self, "_provider_name", provider_name)
        object.__setattr__(self, "_model_name_alias", model)

        # 🔒 API Key 只接受通过参数传入（来自数据库/Web 界面配置），不从环境变量读取
        if api_key is None:
            logger.error(f"❌ [{provider_name}初始化] API Key 未传入")
            raise ValueError(
                f"{provider_name} API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )
        else:
            logger.info(f"✅ [{provider_name}初始化] 使用传入的 API Key（来自数据库配置），长度: {len(api_key)}")

        # 设置OpenAI兼容参数
        # 注意：model参数会被Pydantic映射到model_name字段
        openai_kwargs = {
            "model": model,  # 这会被映射到model_name字段
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs
        }

        # 根据LangChain版本使用不同的参数名
        try:
            # 新版本LangChain
            openai_kwargs.update({
                "api_key": api_key,
                "base_url": base_url
            })
        except:
            # 旧版本LangChain
            openai_kwargs.update({
                "openai_api_key": api_key,
                "openai_api_base": base_url
            })

        # 初始化父类
        super().__init__(**openai_kwargs)

        # 再次确保元信息存在（有些实现会在super()中重置__dict__）
        object.__setattr__(self, "_provider_name", provider_name)
        object.__setattr__(self, "_model_name_alias", model)

        logger.info(f"✅ {provider_name} OpenAI兼容适配器初始化成功")
        logger.info(f"   模型: {model}")
        logger.info(f"   API Base: {base_url}")

    @property
    def provider_name(self) -> Optional[str]:
        return getattr(self, "_provider_name", None)

    # 移除model_name property定义，使用Pydantic字段
    # model_name字段由ChatOpenAI基类的Pydantic字段提供

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        生成聊天响应，并记录token使用量
        """

        # 记录开始时间
        start_time = time.time()

        # 清洗消息：移除孤立的 tool_calls（LangGraph 并行分支可能导致 AIMessage
        # 的 tool_calls 缺少对应的 ToolMessage，DeepSeek 等 API 会返回 400 错误）
        messages = self._sanitize_tool_calls(messages)

        # 调用父类生成方法
        result = super()._generate(messages, stop, run_manager, **kwargs)

        # 记录token使用
        self._track_token_usage(result, kwargs, start_time)

        return result

    @staticmethod
    def _sanitize_tool_calls(messages: List[BaseMessage]) -> List[BaseMessage]:
        """清洗消息列表，修复 LangGraph 并行模式下的消息混乱。

        核心问题：LangGraph 并行运行多个分析师，每个分析师的 AIMessage(tool_calls)
        和对应的 ToolMessage 在 state["messages"] 中交错排列。DeepSeek API 要求
        每个 ToolMessage 必须紧跟在包含对应 tool_calls 的 AIMessage 之后。

        修复策略：
        1. 移除无效消息（RemoveMessage、Continue 占位）
        2. 收集有效的 (AIMessage, [ToolMessage]) 配对
        3. 重新排列消息：配对的消息必须相邻，孤立的删除
        4. 保持非配对消息的相对顺序
        """
        if not messages:
            return messages

        from langchain_core.messages import RemoveMessage as LGRemoveMessage, HumanMessage, SystemMessage

        # 过滤掉 RemoveMessage 和 "Continue" 占位消息
        valid_msgs = [m for m in messages
                      if not isinstance(m, LGRemoveMessage)
                      and not (isinstance(m, HumanMessage) and m.content == "Continue")]

        # 🔍 DEBUG: 打印清洗前的消息摘要
        msg_summary = []
        for i, m in enumerate(valid_msgs):
            if isinstance(m, AIMessage):
                tc_ids = [tc.get('id') for tc in m.tool_calls] if getattr(m, 'tool_calls', None) else []
                msg_summary.append(f"[{i}] AI(id={m.id[:8] if m.id else 'None'}, tc={tc_ids})")
            elif isinstance(m, ToolMessage):
                msg_summary.append(f"[{i}] Tool(id={m.id[:8] if m.id else 'None'}, tc_id={m.tool_call_id})")
            else:
                msg_summary.append(f"[{i}] {type(m).__name__}")
        logger.info(f"🔧 [消息清洗] 输入 {len(valid_msgs)} 条: {' | '.join(msg_summary)}")

        # 收集所有 ToolMessage 的 tool_call_id（每个 tool_call_id 只保留第一个）
        tool_msg_ids = {}  # tool_call_id -> ToolMessage (first one only)
        for m in valid_msgs:
            if isinstance(m, ToolMessage):
                tc_id = getattr(m, 'tool_call_id', None)
                if tc_id and tc_id not in tool_msg_ids:
                    tool_msg_ids[tc_id] = m

        # 收集有效的 (AIMessage, [ToolMessage]) 配对
        valid_tool_call_ids = set()
        ai_with_tools = []  # (index, AIMessage, [ToolMessage], [valid_tool_calls]) — 配对信息
        for i, m in enumerate(valid_msgs):
            if isinstance(m, AIMessage) and getattr(m, 'tool_calls', None):
                paired_tools = []
                valid_calls = []
                for tc in m.tool_calls:
                    tc_id = tc.get('id')
                    if tc_id and tc_id in tool_msg_ids:
                        valid_calls.append(tc)
                        paired_tools.append(tool_msg_ids[tc_id])
                        valid_tool_call_ids.add(tc_id)

                if valid_calls:
                    ai_with_tools.append((i, m, paired_tools, valid_calls))
                else:
                    logger.info(
                        f"🔧 [消息清洗] 移除孤立 AIMessage (id={m.id}, "
                        f"orphaned_calls={len(m.tool_calls)})"
                    )

        # 构建最终消息列表
        # 策略：保持原始顺序，但确保每个 AIMessage(tool_calls) 紧跟其 ToolMessage
        # 已配对的 ToolMessage 不再单独出现（跟随 AIMessage）
        paired_tool_ids = set()  # 已被配对使用的 ToolMessage 的 id
        for _, _, tools, _ in ai_with_tools:
            for t in tools:
                paired_tool_ids.add(id(t))

        final = []
        for m in valid_msgs:
            if isinstance(m, AIMessage) and getattr(m, 'tool_calls', None):
                # 查找此 AIMessage 的配对信息
                paired = None
                for _, ai_msg, tools, valid_calls in ai_with_tools:
                    if ai_msg is m:
                        paired = (tools, valid_calls)
                        break

                if paired:
                    tools, valid_calls = paired
                    if len(valid_calls) != len(m.tool_calls):
                        m = copy.deepcopy(m)
                        m.tool_calls = valid_calls
                    final.append(m)
                    # 紧跟所有对应的 ToolMessage
                    for t in tools:
                        final.append(t)
                # else: 孤立的 AIMessage，跳过（上面已记录日志）
            elif isinstance(m, ToolMessage):
                # 只添加未被配对使用的 ToolMessage（孤立的）
                if id(m) not in paired_tool_ids:
                    tc_id = getattr(m, 'tool_call_id', None)
                    if tc_id and tc_id in valid_tool_call_ids:
                        # 这个 ToolMessage 有对应的 tool_call 但没被配对
                        # 说明它可能重复了，丢弃
                        logger.info(
                            f"🔧 [消息清洗] 移除未配对 ToolMessage "
                            f"(tc_id={tc_id})"
                        )
                    else:
                        # 完全孤立的 ToolMessage
                        logger.info(
                            f"🔧 [消息清洗] 移除孤立 ToolMessage "
                            f"(tc_id={tc_id})"
                        )
            else:
                final.append(m)

        # 🔍 DEBUG: 打印清洗后的消息摘要
        out_summary = []
        for i, m in enumerate(final):
            if isinstance(m, AIMessage):
                tc_ids = [tc.get('id') for tc in m.tool_calls] if getattr(m, 'tool_calls', None) else []
                out_summary.append(f"[{i}] AI(id={m.id[:8] if m.id else 'None'}, tc={tc_ids})")
            elif isinstance(m, ToolMessage):
                out_summary.append(f"[{i}] Tool(id={m.id[:8] if m.id else 'None'}, tc_id={m.tool_call_id})")
            else:
                out_summary.append(f"[{i}] {type(m).__name__}")
        logger.info(f"🔧 [消息清洗] 输出 {len(final)} 条: {' | '.join(out_summary)}")

        return final

    def _track_token_usage(self, result: ChatResult, kwargs: Dict, start_time: float):
        """记录token使用量并输出日志"""
        if not TOKEN_TRACKING_ENABLED:
            return
        try:
            # 从 message.usage_metadata 读取（ChatResult 本身没有 usage_metadata）
            total_tokens = None
            prompt_tokens = None
            completion_tokens = None

            if result.generations:
                msg = result.generations[0].message
                usage = getattr(msg, 'usage_metadata', None)
                if usage:
                    total_tokens = usage.get("total_tokens")
                    prompt_tokens = usage.get("input_tokens")
                    completion_tokens = usage.get("output_tokens")

            # 回退到 llm_output
            if total_tokens is None and result.llm_output:
                usage = result.llm_output.get("token_usage", {})
                total_tokens = usage.get("total_tokens")
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")

            elapsed = time.time() - start_time
            logger.info(
                f"📊 Token使用 - Provider: {getattr(self, 'provider_name', 'unknown')}, Model: {getattr(self, 'model_name', 'unknown')}, "
                f"总tokens: {total_tokens}, 提示: {prompt_tokens}, 补全: {completion_tokens}, 用时: {elapsed:.2f}s"
            )
        except Exception as e:
            logger.warning(f"⚠️ Token跟踪记录失败: {e}")


class ChatDeepSeekOpenAI(OpenAICompatibleBase):
    """DeepSeek OpenAI兼容适配器"""

    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        super().__init__(
            provider_name="deepseek",
            model=model,
            api_key_env_var="DEEPSEEK_API_KEY",
            base_url=base_url or "https://api.deepseek.com",
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )


class ChatDashScopeOpenAIUnified(OpenAICompatibleBase):
    """阿里百炼 DashScope OpenAI兼容适配器"""

    def __init__(
        self,
        model: str = "qwen-turbo",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        super().__init__(
            provider_name="dashscope",
            model=model,
            api_key_env_var="DASHSCOPE_API_KEY",
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )


class ChatQianfanOpenAI(OpenAICompatibleBase):
    """文心一言千帆平台 OpenAI兼容适配器"""

    def __init__(
        self,
        model: str = "ernie-3.5-8k",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        # 千帆新一代API使用单一API Key认证
        # 格式: bce-v3/ALTAK-xxx/xxx

        # 🔒 API Key 只接受通过参数传入（来自数据库/Web 界面配置），不从环境变量读取
        if not api_key:
            raise ValueError(
                "千帆 API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家），"
                "格式为: bce-v3/ALTAK-xxx/xxx"
            )
        qianfan_api_key = api_key

        if not qianfan_api_key.startswith('bce-v3/'):
            raise ValueError(
                "QIANFAN_API_KEY格式错误，应为: bce-v3/ALTAK-xxx/xxx"
            )

        super().__init__(
            provider_name="qianfan",
            model=model,
            api_key_env_var="QIANFAN_API_KEY",
            base_url=base_url or "https://qianfan.baidubce.com/v2",
            api_key=qianfan_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

    def _estimate_tokens(self, text: str) -> int:
        """估算文本的token数量（千帆模型专用）"""
        # 千帆模型的token估算：中文约1.5字符/token，英文约4字符/token
        # 保守估算：2字符/token
        return max(1, len(text) // 2)

    def _truncate_messages(self, messages: List[BaseMessage], max_tokens: int = 4500) -> List[BaseMessage]:
        """截断消息以适应千帆模型的token限制"""
        # 为千帆模型预留一些token空间，使用4500而不是5120
        truncated_messages = []
        total_tokens = 0

        # 从最后一条消息开始，向前保留消息
        for message in reversed(messages):
            content = str(message.content) if hasattr(message, 'content') else str(message)
            message_tokens = self._estimate_tokens(content)

            if total_tokens + message_tokens <= max_tokens:
                truncated_messages.insert(0, message)
                total_tokens += message_tokens
            else:
                # 如果是第一条消息且超长，进行内容截断
                if not truncated_messages:
                    remaining_tokens = max_tokens - 100  # 预留100个token
                    max_chars = remaining_tokens * 2  # 2字符/token
                    truncated_content = content[:max_chars] + "...(内容已截断)"

                    # 创建截断后的消息
                    if hasattr(message, 'content'):
                        message.content = truncated_content
                    truncated_messages.insert(0, message)
                break

        if len(truncated_messages) < len(messages):
            logger.warning(f"⚠️ 千帆模型输入过长，已截断 {len(messages) - len(truncated_messages)} 条消息")

        return truncated_messages

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应，包含千帆模型的token截断逻辑"""

        # 对千帆模型进行输入token截断
        truncated_messages = self._truncate_messages(messages)

        # 调用父类的_generate方法
        return super()._generate(truncated_messages, stop, run_manager, **kwargs)


class ChatCustomOpenAI(OpenAICompatibleBase):
    """自定义OpenAI端点适配器（代理/聚合平台）"""

    def __init__(
        self,
        model: str = "gpt-3.5-turbo",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        # 如果没有传入 base_url，尝试从环境变量读取
        if base_url is None:
            env_base_url = os.getenv("CUSTOM_OPENAI_BASE_URL")
            # 只使用有效的环境变量值（不是占位符）
            if env_base_url and not env_base_url.startswith('your_') and not env_base_url.startswith('your-'):
                base_url = env_base_url
            else:
                base_url = "https://api.openai.com/v1"

        super().__init__(
            provider_name="custom_openai",
            model=model,
            api_key_env_var="CUSTOM_OPENAI_API_KEY",
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )


# 支持的OpenAI兼容模型配置
OPENAI_COMPATIBLE_PROVIDERS = {
    "deepseek": {
        "adapter_class": ChatDeepSeekOpenAI,
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": {
            "deepseek-chat": {"context_length": 32768, "supports_function_calling": True},
            "deepseek-coder": {"context_length": 16384, "supports_function_calling": True}
        }
    },
    "dashscope": {
        "adapter_class": ChatDashScopeOpenAIUnified,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "models": {
            "qwen-turbo": {"context_length": 8192, "supports_function_calling": True},
            "qwen-plus": {"context_length": 32768, "supports_function_calling": True},
            "qwen-plus-latest": {"context_length": 32768, "supports_function_calling": True},
            "qwen-max": {"context_length": 32768, "supports_function_calling": True},
            "qwen-max-latest": {"context_length": 32768, "supports_function_calling": True}
        }
    },
    "qianfan": {
        "adapter_class": ChatQianfanOpenAI,
        "base_url": "https://qianfan.baidubce.com/v2",
        "api_key_env": "QIANFAN_API_KEY",
        "models": {
            "ernie-3.5-8k": {"context_length": 5120, "supports_function_calling": True},
            "ernie-4.0-turbo-8k": {"context_length": 5120, "supports_function_calling": True},
            "ERNIE-Speed-8K": {"context_length": 5120, "supports_function_calling": True},
            "ERNIE-Lite-8K": {"context_length": 5120, "supports_function_calling": True}
        }
    },
    "custom_openai": {
        "adapter_class": ChatCustomOpenAI,
        "base_url": None,  # 将由用户配置
        "api_key_env": "CUSTOM_OPENAI_API_KEY",
        "models": {
            "gpt-3.5-turbo": {"context_length": 16384, "supports_function_calling": True},
            "gpt-4": {"context_length": 8192, "supports_function_calling": True},
            "gpt-4-turbo": {"context_length": 128000, "supports_function_calling": True},
            "gpt-4o": {"context_length": 128000, "supports_function_calling": True},
            "gpt-4o-mini": {"context_length": 128000, "supports_function_calling": True},
            "claude-3-haiku": {"context_length": 200000, "supports_function_calling": True},
            "claude-3-sonnet": {"context_length": 200000, "supports_function_calling": True},
            "claude-3-opus": {"context_length": 200000, "supports_function_calling": True},
            "claude-3.5-sonnet": {"context_length": 200000, "supports_function_calling": True},
            "gemini-pro": {"context_length": 32768, "supports_function_calling": True},
            "gemini-1.5-pro": {"context_length": 1000000, "supports_function_calling": True},
            "llama-3.1-8b": {"context_length": 128000, "supports_function_calling": True},
            "llama-3.1-70b": {"context_length": 128000, "supports_function_calling": True},
            "llama-3.1-405b": {"context_length": 128000, "supports_function_calling": True},
            "custom-model": {"context_length": 32768, "supports_function_calling": True}
        }
    }
}


def create_openai_compatible_llm(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    base_url: Optional[str] = None,
    **kwargs
) -> OpenAICompatibleBase:
    """创建OpenAI兼容LLM实例的统一工厂函数"""
    provider_info = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if not provider_info:
        raise ValueError(f"不支持的OpenAI兼容提供商: {provider}")

    adapter_class = provider_info["adapter_class"]

    # 如果调用未提供 base_url，则采用 provider 的默认值（可能为 None）
    if base_url is None:
        base_url = provider_info.get("base_url")

    # 仅当 provider 未内置 base_url（如 custom_openai）时，才将 base_url 传递给适配器，
    # 避免与适配器内部的 super().__init__(..., base_url=...) 冲突导致 "multiple values" 错误。
    init_kwargs = dict(
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    if provider_info.get("base_url") is None and base_url:
        init_kwargs["base_url"] = base_url

    return adapter_class(**init_kwargs)


def test_openai_compatible_adapters():
    """快速测试所有适配器是否能被正确实例化（不发起真实请求）"""
    for provider, info in OPENAI_COMPATIBLE_PROVIDERS.items():
        cls = info["adapter_class"]
        try:
            if provider == "custom_openai":
                cls(model="gpt-3.5-turbo", api_key="test", base_url="https://api.openai.com/v1")
            elif provider == "qianfan":
                # 千帆新一代API仅需QIANFAN_API_KEY，格式: bce-v3/ALTAK-xxx/xxx
                cls(model="ernie-3.5-8k", api_key="bce-v3/test-key/test-secret")
            else:
                cls(model=list(info["models"].keys())[0], api_key="test")
            logger.info(f"✅ 适配器实例化成功: {provider}")
        except Exception as e:
            logger.warning(f"⚠️ 适配器实例化失败（预期或可忽略）: {provider} - {e}")


if __name__ == "__main__":
    test_openai_compatible_adapters()
