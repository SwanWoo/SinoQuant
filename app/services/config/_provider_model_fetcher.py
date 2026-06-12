"""
厂家模型列表获取 Mixin
"""

import re
import asyncio
import logging
from typing import Optional, Dict, Any
from bson import ObjectId
import requests

logger = logging.getLogger(__name__)


class ProviderModelFetcherMixin:
    """从厂家 API 获取可用模型列表"""

    async def fetch_provider_models(self, provider_id: str) -> dict:
        """从厂家 API 获取模型列表"""
        try:
            print(f"🔍 获取厂家模型列表 - provider_id: {provider_id}")

            db = await self._get_db()
            providers_collection = db.llm_providers

            # 兼容处理：尝试 ObjectId 和字符串两种类型
            provider_data = None
            try:
                provider_data = await providers_collection.find_one({"_id": ObjectId(provider_id)})
            except Exception:
                pass

            if not provider_data:
                provider_data = await providers_collection.find_one({"_id": provider_id})

            if not provider_data:
                return {
                    "success": False,
                    "message": f"厂家不存在 (ID: {provider_id})"
                }

            provider_name = provider_data.get("name")
            api_key = provider_data.get("api_key")
            base_url = provider_data.get("default_base_url")
            display_name = provider_data.get("display_name", provider_name)

            # 判断数据库中的 API Key 是否有效
            if not self._is_valid_api_key(api_key):
                # 数据库中的 Key 无效，尝试从环境变量读取
                env_api_key = self._get_env_api_key(provider_name)
                if env_api_key:
                    api_key = env_api_key
                    print(f"✅ 数据库配置无效，从环境变量读取到 {display_name} 的 API Key")
                else:
                    # 某些聚合平台（如 OpenRouter）的 /models 端点不需要 API Key
                    print(f"⚠️ {display_name} 未配置有效的API密钥，尝试无认证访问")
            else:
                print(f"✅ 使用数据库配置的 {display_name} API密钥")

            if not base_url:
                return {
                    "success": False,
                    "message": f"{display_name} 未配置 API 基础地址 (default_base_url)"
                }

            # 调用 OpenAI 兼容的 /v1/models 端点
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._fetch_models_from_api, api_key, base_url, display_name
            )

            return result

        except Exception as e:
            print(f"获取模型列表失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"获取模型列表失败: {str(e)}"
            }

    def _fetch_models_from_api(self, api_key: str, base_url: str, display_name: str) -> dict:
        """从 API 获取模型列表"""
        try:
            # 智能版本号处理：只有在没有版本号的情况下才添加 /v1
            # 避免对已有版本号的URL（如智谱AI的 /v4）重复添加 /v1
            base_url = base_url.rstrip("/")
            if not re.search(r'/v\d+$', base_url):
                # URL末尾没有版本号，添加 /v1（OpenAI标准）
                base_url = base_url + "/v1"
                logger.info(f"   [获取模型列表] 添加 /v1 版本号: {base_url}")
            else:
                # URL已包含版本号（如 /v4），不添加
                logger.info(f"   [获取模型列表] 检测到已有版本号，保持原样: {base_url}")

            url = f"{base_url}/models"

            # 构建请求头
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
                print(f"🔍 请求 URL: {url} (with API Key)")
            else:
                print(f"🔍 请求 URL: {url} (without API Key)")

            response = requests.get(url, headers=headers, timeout=15)

            print(f"📊 响应状态码: {response.status_code}")
            print(f"📊 响应内容: {response.text[:500]}...")

            if response.status_code == 200:
                result = response.json()
                print(f"📊 响应 JSON 结构: {list(result.keys())}")

                if "data" in result and isinstance(result["data"], list):
                    all_models = result["data"]
                    print(f"📊 API 返回 {len(all_models)} 个模型")

                    # 打印前几个模型的完整结构（用于调试价格字段）
                    if all_models:
                        print(f"🔍 第一个模型的完整结构:")
                        import json
                        print(json.dumps(all_models[0], indent=2, ensure_ascii=False))

                    # 打印所有 Anthropic 模型（用于调试）
                    anthropic_models = [m for m in all_models if "anthropic" in m.get("id", "").lower()]
                    if anthropic_models:
                        print(f"🔍 Anthropic 模型列表 ({len(anthropic_models)} 个):")
                        for m in anthropic_models[:20]:  # 只打印前 20 个
                            print(f"   - {m.get('id')}")

                    # 过滤：只保留主流大厂的常用模型
                    filtered_models = self._filter_popular_models(all_models)
                    print(f"✅ 过滤后保留 {len(filtered_models)} 个常用模型")

                    # 转换模型格式，包含价格信息
                    formatted_models = self._format_models_with_pricing(filtered_models)

                    return {
                        "success": True,
                        "models": formatted_models,
                        "message": f"成功获取 {len(formatted_models)} 个常用模型（已过滤）"
                    }
                else:
                    print(f"❌ 响应格式异常，期望 'data' 字段为列表")
                    return {
                        "success": False,
                        "message": f"{display_name} API 响应格式异常（缺少 data 字段或格式不正确）"
                    }
            elif response.status_code == 401:
                return {
                    "success": False,
                    "message": f"{display_name} API密钥无效或已过期"
                }
            elif response.status_code == 403:
                return {
                    "success": False,
                    "message": f"{display_name} API权限不足"
                }
            else:
                try:
                    error_detail = response.json()
                    error_msg = error_detail.get("error", {}).get("message", f"HTTP {response.status_code}")
                    print(f"❌ API 错误: {error_msg}")
                    return {
                        "success": False,
                        "message": f"{display_name} API请求失败: {error_msg}"
                    }
                except:
                    print(f"❌ HTTP 错误: {response.status_code}")
                    return {
                        "success": False,
                        "message": f"{display_name} API请求失败: HTTP {response.status_code}, 响应: {response.text[:200]}"
                    }

        except Exception as e:
            print(f"❌ 异常: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"{display_name} API请求异常: {str(e)}"
            }

    def _format_models_with_pricing(self, models: list) -> list:
        """
        格式化模型列表，包含价格信息

        支持多种价格格式：
        1. OpenRouter: pricing.prompt/completion (USD per token)
        2. 302.ai: price.prompt/completion 或 price.input/output
        3. 其他: 可能没有价格信息
        """
        formatted = []
        for model in models:
            model_id = model.get("id", "")
            model_name = model.get("name", model_id)

            # 尝试从多个字段获取价格信息
            input_price_per_1k = None
            output_price_per_1k = None

            # 方式1：OpenRouter 格式 (pricing.prompt/completion)
            pricing = model.get("pricing", {})
            if pricing:
                prompt_price = pricing.get("prompt", "0")  # USD per token
                completion_price = pricing.get("completion", "0")  # USD per token

                try:
                    if prompt_price and float(prompt_price) > 0:
                        input_price_per_1k = float(prompt_price) * 1000
                    if completion_price and float(completion_price) > 0:
                        output_price_per_1k = float(completion_price) * 1000
                except (ValueError, TypeError):
                    pass

            # 方式2：302.ai 格式 (price.prompt/completion 或 price.input/output)
            if not input_price_per_1k and not output_price_per_1k:
                price = model.get("price", {})
                if price and isinstance(price, dict):
                    # 尝试 prompt/completion 字段
                    prompt_price = price.get("prompt") or price.get("input")
                    completion_price = price.get("completion") or price.get("output")

                    try:
                        if prompt_price and float(prompt_price) > 0:
                            # 假设是 per token，转换为 per 1K tokens
                            input_price_per_1k = float(prompt_price) * 1000
                        if completion_price and float(completion_price) > 0:
                            output_price_per_1k = float(completion_price) * 1000
                    except (ValueError, TypeError):
                        pass

            # 获取上下文长度
            context_length = model.get("context_length")
            if not context_length:
                # 尝试从 top_provider 获取
                top_provider = model.get("top_provider", {})
                context_length = top_provider.get("context_length")

            # 如果还是没有，尝试从 max_completion_tokens 推断
            if not context_length:
                max_tokens = model.get("max_completion_tokens")
                if max_tokens and max_tokens > 0:
                    # 通常上下文长度是最大输出的 4-8 倍
                    context_length = max_tokens * 4

            formatted_model = {
                "id": model_id,
                "name": model_name,
                "context_length": context_length,
                "input_price_per_1k": input_price_per_1k,
                "output_price_per_1k": output_price_per_1k,
            }

            formatted.append(formatted_model)

            # 打印价格信息（用于调试）
            if input_price_per_1k or output_price_per_1k:
                print(f"💰 {model_id}: 输入=${input_price_per_1k:.6f}/1K, 输出=${output_price_per_1k:.6f}/1K")

        return formatted

    def _filter_popular_models(self, models: list) -> list:
        """过滤模型列表，只保留主流大厂的常用模型"""
        # 只保留三大厂：OpenAI、Anthropic、Google
        popular_providers = [
            "openai",      # OpenAI
            "anthropic",   # Anthropic
            "google",      # Google
        ]

        # 常见模型名称前缀（用于识别不带厂商前缀的模型）
        model_prefixes = {
            "gpt-": "openai",           # gpt-3.5-turbo, gpt-4, gpt-4o
            "o1-": "openai",            # o1-preview, o1-mini
            "claude-": "anthropic",     # claude-3-opus, claude-3-sonnet
            "gemini-": "google",        # gemini-pro, gemini-1.5-pro
            "gemini": "google",         # gemini (不带连字符)
        }

        # 排除的关键词
        exclude_keywords = [
            "preview",
            "experimental",
            "alpha",
            "beta",
            "free",
            "extended",
            "nitro",
            ":free",
            ":extended",
            "online",  # 排除带在线搜索的版本
            "instruct",  # 排除 instruct 版本
        ]

        # 日期格式正则表达式（匹配 2024-05-13 这种格式）
        date_pattern = re.compile(r'\d{4}-\d{2}-\d{2}')

        filtered = []
        for model in models:
            model_id = model.get("id", "").lower()
            model_name = model.get("name", "").lower()

            # 检查是否属于三大厂
            # 方式1：模型ID中包含厂商名称（如 openai/gpt-4）
            is_popular_provider = any(provider in model_id for provider in popular_providers)

            # 方式2：模型ID以常见前缀开头（如 gpt-4, claude-3-sonnet）
            if not is_popular_provider:
                for prefix, provider in model_prefixes.items():
                    if model_id.startswith(prefix):
                        is_popular_provider = True
                        print(f"🔍 识别模型前缀: {model_id} -> {provider}")
                        break

            if not is_popular_provider:
                continue

            # 检查是否包含日期（排除带日期的旧版本）
            if date_pattern.search(model_id):
                print(f"⏭️ 跳过带日期的旧版本: {model_id}")
                continue

            # 检查是否包含排除关键词
            has_exclude_keyword = any(keyword in model_id or keyword in model_name for keyword in exclude_keywords)

            if has_exclude_keyword:
                print(f"⏭️ 跳过排除关键词: {model_id}")
                continue

            # 保留该模型
            print(f"✅ 保留模型: {model_id}")
            filtered.append(model)

        return filtered

    def _test_openai_compatible_api(
        self,
        api_key: Optional[str],
        display_name: str,
        base_url: str = None,
        provider_name: str = None,
        test_model: Optional[str] = None
    ) -> dict:
        """测试 OpenAI 兼容 API（用于聚合渠道和自定义厂家）

        只测试 /v1/models 端点，验证厂商是否在线，不需要测试具体模型
        """
        try:
            # 如果没有提供 base_url，使用默认值
            if not base_url:
                return {
                    "success": False,
                    "message": f"{display_name} 未配置 API 基础地址 (default_base_url)"
                }

            # 智能版本号处理：只有在没有版本号的情况下才添加 /v1
            # 避免对已有版本号的URL（如智谱AI的 /v4）重复添加 /v1
            logger.info(f"   [测试API] 原始 base_url: {base_url}")
            base_url = base_url.rstrip("/")
            logger.info(f"   [测试API] 去除斜杠后: {base_url}")

            if not re.search(r'/v\d+$', base_url):
                # URL末尾没有版本号，添加 /v1（OpenAI标准）
                base_url = base_url + "/v1"
                logger.info(f"   [测试API] 添加 /v1 版本号: {base_url}")
            else:
                # URL已包含版本号（如 /v4），不添加
                logger.info(f"   [测试API] 检测到已有版本号，保持原样: {base_url}")

            url = f"{base_url}/models"
            logger.info(f"   [测试API] 测试URL: {url}")

            headers = {
                "Content-Type": "application/json",
            }
            # vLLM 等自部署服务可能关闭鉴权，允许无 Authorization 请求
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            # 发送测试请求到 /models 端点
            response = requests.get(url, headers=headers, timeout=15)

            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                return {
                    "success": True,
                    "message": f"{display_name} 连接成功，可用模型数量: {len(models)}",
                    "details": {"models_count": len(models)}
                }
            elif response.status_code == 401:
                return {
                    "success": False,
                    "message": f"{display_name} API密钥无效或已过期"
                }
            elif response.status_code == 403:
                return {
                    "success": False,
                    "message": f"{display_name} API权限不足或配额已用完"
                }
            else:
                try:
                    error_detail = response.json()
                    error_msg = error_detail.get("error", {}).get("message", f"HTTP {response.status_code}")
                    logger.error(f"❌ [{display_name}] API测试失败")
                    logger.error(f"   请求URL: {url}")
                    logger.error(f"   状态码: {response.status_code}")
                    logger.error(f"   错误详情: {error_detail}")
                    return {
                        "success": False,
                        "message": f"{display_name} 连接失败: {error_msg}"
                    }
                except:
                    logger.error(f"❌ [{display_name}] API测试失败")
                    logger.error(f"   请求URL: {url}")
                    logger.error(f"   状态码: {response.status_code}")
                    logger.error(f"   响应内容: {response.text[:500]}")
                    return {
                        "success": False,
                        "message": f"{display_name} 连接失败: HTTP {response.status_code}"
                    }

        except requests.exceptions.Timeout:
            return {"success": False, "message": f"{display_name} 连接超时"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": f"{display_name} 连接错误，请检查 Base URL"}
        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} 连接测试异常: {str(e)}"
            }
