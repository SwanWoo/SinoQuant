"""
LLM 配置测试 Mixin
"""

import re
import time
import logging
from typing import Dict, Any

import requests
from app.models.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMConfigTesterMixin:
    """LLM 配置 API 测试"""

    async def test_llm_config(self, llm_config: LLMConfig) -> Dict[str, Any]:
        """测试大模型配置 - 真实调用API进行验证"""
        start_time = time.time()
        try:
            # 获取 provider 字符串值（兼容枚举和字符串）
            provider_str = llm_config.provider.value if hasattr(llm_config.provider, 'value') else str(llm_config.provider)

            logger.info(f"🧪 测试大模型配置: {provider_str} - {llm_config.model_name}")
            logger.info(f"📍 API基础URL (模型配置): {llm_config.api_base}")

            # 获取厂家配置（用于获取 API Key 和 default_base_url）
            db = await self._get_db()
            providers_collection = db.llm_providers
            provider_data = await providers_collection.find_one({"name": provider_str})
            provider_extra_config = provider_data.get("extra_config", {}) if provider_data else {}
            api_key_optional = bool(provider_extra_config.get("api_key_optional", False))

            # 1. 确定 API 基础 URL
            api_base = llm_config.api_base
            if not api_base:
                # 如果模型配置没有 api_base，从厂家配置获取 default_base_url
                if provider_data and provider_data.get("default_base_url"):
                    api_base = provider_data["default_base_url"]
                    logger.info(f"✅ 从厂家配置获取 API 基础 URL: {api_base}")
                else:
                    return {
                        "success": False,
                        "message": f"模型配置和厂家配置都未设置 API 基础 URL",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            # 2. 验证 API Key
            api_key = None
            if llm_config.api_key:
                api_key = llm_config.api_key
            else:
                # 从厂家配置获取 API Key
                if provider_data and provider_data.get("api_key"):
                    api_key = provider_data["api_key"]
                    logger.info(f"✅ 从厂家配置获取到API密钥")
                else:
                    # 尝试从环境变量获取
                    api_key = self._get_env_api_key(provider_str)
                    if api_key:
                        logger.info(f"✅ 从环境变量获取到API密钥")

            if not api_key or not self._is_valid_api_key(api_key):
                if api_key_optional:
                    # 自部署 vLLM 等场景可能无鉴权，使用占位值兼容 OpenAI 客户端
                    api_key = "EMPTY"
                    logger.info(f"ℹ️ {provider_str} 启用无密钥模式，使用占位 API Key 测试")
                else:
                    return {
                        "success": False,
                        "message": f"{provider_str} 未配置有效的API密钥",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            # 3. 根据厂家类型选择测试方法
            if provider_str == "google":
                # Google AI 使用专门的测试方法
                logger.info(f"🔍 使用 Google AI 专用测试方法")
                result = self._test_google_api(api_key, f"{provider_str} {llm_config.model_name}", api_base, llm_config.model_name)
                result["response_time"] = time.time() - start_time
                return result
            elif provider_str == "deepseek":
                # DeepSeek 使用专门的测试方法
                logger.info(f"🔍 使用 DeepSeek 专用测试方法")
                result = self._test_deepseek_api(api_key, f"{provider_str} {llm_config.model_name}", llm_config.model_name)
                result["response_time"] = time.time() - start_time
                return result
            elif provider_str == "dashscope":
                # DashScope 使用专门的测试方法
                logger.info(f"🔍 使用 DashScope 专用测试方法")
                result = self._test_dashscope_api(api_key, f"{provider_str} {llm_config.model_name}", llm_config.model_name)
                result["response_time"] = time.time() - start_time
                return result
            else:
                # 其他厂家使用 OpenAI 兼容的测试方法
                logger.info(f"🔍 使用 OpenAI 兼容测试方法")

                # 构建测试请求
                api_base_normalized = api_base.rstrip("/")

                # 智能版本号处理：只有在没有版本号的情况下才添加 /v1
                # 避免对已有版本号的URL（如智谱AI的 /v4）重复添加 /v1
                if not re.search(r'/v\d+$', api_base_normalized):
                    # URL末尾没有版本号，添加 /v1（OpenAI标准）
                    api_base_normalized = api_base_normalized + "/v1"
                    logger.info(f"   添加 /v1 版本号: {api_base_normalized}")
                else:
                    # URL已包含版本号（如 /v4），不添加
                    logger.info(f"   检测到已有版本号，保持原样: {api_base_normalized}")

                url = f"{api_base_normalized}/chat/completions"

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }

                data = {
                    "model": llm_config.model_name,
                    "messages": [
                        {"role": "user", "content": "Hello, please respond with 'OK' if you can read this."}
                    ],
                    "max_tokens": 200,  # 增加到200，给推理模型（如o1/gpt-5）足够空间
                    "temperature": 0.1
                }

                logger.info(f"🌐 发送测试请求到: {url}")
                logger.info(f"📦 使用模型: {llm_config.model_name}")
                logger.info(f"📦 请求数据: {data}")

                # 发送测试请求
                response = requests.post(url, json=data, headers=headers, timeout=15)
                response_time = time.time() - start_time

                logger.info(f"📡 收到响应: HTTP {response.status_code}")

                # 处理响应（仅用于 OpenAI 兼容的厂家）
                if response.status_code == 200:
                    try:
                        result = response.json()
                        logger.info(f"📦 响应JSON: {result}")

                        if "choices" in result and len(result["choices"]) > 0:
                            content = result["choices"][0]["message"]["content"]
                            logger.info(f"📝 响应内容: {content}")

                            if content and len(content.strip()) > 0:
                                logger.info(f"✅ 测试成功: {content[:50]}")
                                return {
                                    "success": True,
                                    "message": f"成功连接到 {provider_str} {llm_config.model_name}",
                                    "response_time": response_time,
                                    "details": {
                                        "provider": provider_str,
                                        "model": llm_config.model_name,
                                        "api_base": api_base,
                                        "response_preview": content[:100]
                                    }
                                }
                            else:
                                logger.warning(f"⚠️ API响应内容为空")
                                return {
                                    "success": False,
                                    "message": "API响应内容为空",
                                    "response_time": response_time,
                                    "details": None
                                }
                        else:
                            logger.warning(f"⚠️ API响应格式异常，缺少 choices 字段")
                            logger.warning(f"   响应内容: {result}")
                            return {
                                "success": False,
                                "message": "API响应格式异常",
                                "response_time": response_time,
                                "details": None
                            }
                    except Exception as e:
                        logger.error(f"❌ 解析响应失败: {e}")
                        logger.error(f"   响应文本: {response.text[:500]}")
                        return {
                            "success": False,
                            "message": f"解析响应失败: {str(e)}",
                            "response_time": response_time,
                            "details": None
                        }
                elif response.status_code == 401:
                    return {
                        "success": False,
                        "message": "API密钥无效或已过期",
                        "response_time": response_time,
                        "details": None
                    }
                elif response.status_code == 403:
                    return {
                        "success": False,
                        "message": "API权限不足或配额已用完",
                        "response_time": response_time,
                        "details": None
                    }
                elif response.status_code == 404:
                    return {
                        "success": False,
                        "message": f"API端点不存在，请检查API基础URL是否正确: {url}",
                        "response_time": response_time,
                        "details": None
                    }
                else:
                    try:
                        error_detail = response.json()
                        error_msg = error_detail.get("error", {}).get("message", f"HTTP {response.status_code}")
                        return {
                            "success": False,
                            "message": f"API测试失败: {error_msg}",
                            "response_time": response_time,
                            "details": None
                        }
                    except:
                        return {
                            "success": False,
                            "message": f"API测试失败: HTTP {response.status_code}",
                            "response_time": response_time,
                            "details": None
                        }

        except requests.exceptions.Timeout:
            response_time = time.time() - start_time
            return {
                "success": False,
                "message": "连接超时，请检查API基础URL是否正确或网络是否可达",
                "response_time": response_time,
                "details": None
            }
        except requests.exceptions.ConnectionError as e:
            response_time = time.time() - start_time
            return {
                "success": False,
                "message": f"连接失败，请检查API基础URL是否正确: {str(e)}",
                "response_time": response_time,
                "details": None
            }
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"❌ 测试大模型配置失败: {e}")
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "response_time": response_time,
                "details": None
            }

    def _truncate_api_key(self, api_key: str, prefix_len: int = 6, suffix_len: int = 6) -> str:
        """
        截断 API Key 用于显示

        Args:
            api_key: 完整的 API Key
            prefix_len: 保留前缀长度
            suffix_len: 保留后缀长度

        Returns:
            截断后的 API Key，例如：0f229a...c550ec
        """
        if not api_key or len(api_key) <= prefix_len + suffix_len:
            return api_key

        return f"{api_key[:prefix_len]}...{api_key[-suffix_len:]}"
