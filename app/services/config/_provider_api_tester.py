"""
厂家 API 连接测试 Mixin
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from bson import ObjectId
import requests

logger = logging.getLogger(__name__)


class ProviderApiTesterMixin:
    """厂家 API 连接测试（test_provider_api 及各厂家 _test_*_api 方法）"""

    async def test_provider_api(self, provider_id: str) -> dict:
        """测试厂家API密钥"""
        try:
            print(f"🔍 测试厂家API - provider_id: {provider_id}")

            db = await self._get_db()
            providers_collection = db.llm_providers

            # 兼容处理：尝试 ObjectId 和字符串两种类型
            provider_data = None
            try:
                # 先尝试作为 ObjectId 查询
                provider_data = await providers_collection.find_one({"_id": ObjectId(provider_id)})
            except Exception:
                pass

            # 如果没有找到，再尝试作为字符串查询
            if not provider_data:
                provider_data = await providers_collection.find_one({"_id": provider_id})

            if not provider_data:
                return {
                    "success": False,
                    "message": f"厂家不存在 (ID: {provider_id})"
                }

            provider_name = provider_data.get("name")
            api_key = provider_data.get("api_key")
            display_name = provider_data.get("display_name", provider_name)
            extra_config = provider_data.get("extra_config") or {}
            api_key_optional = bool(extra_config.get("api_key_optional", False))

            # 判断数据库中的 API Key 是否有效
            if not self._is_valid_api_key(api_key):
                # 数据库中的 Key 无效，尝试从环境变量读取
                env_api_key = self._get_env_api_key(provider_name)
                if env_api_key:
                    api_key = env_api_key
                    print(f"✅ 数据库配置无效，从环境变量读取到 {display_name} 的 API Key")
                elif api_key_optional:
                    # vLLM 等本地部署场景常见：不校验 API Key，仅需 OpenAI 兼容地址
                    api_key = "EMPTY"
                    print(f"ℹ️ {display_name} 已启用无密钥模式，使用占位 API Key 进行连通测试")
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} 未配置有效的API密钥（数据库和环境变量中都未找到）"
                    }
            else:
                print(f"✅ 使用数据库配置的 {display_name} API密钥")

            # 根据厂家类型调用相应的测试函数
            test_result = await self._test_provider_connection(
                provider_name=provider_name,
                api_key=api_key,
                display_name=display_name,
                provider_data=provider_data
            )

            return test_result

        except Exception as e:
            print(f"测试厂家API失败: {e}")
            return {
                "success": False,
                "message": f"测试失败: {str(e)}"
            }

    async def _test_provider_connection(
        self,
        provider_name: str,
        api_key: str,
        display_name: str,
        provider_data: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """测试具体厂家的连接"""
        try:
            if provider_data is None:
                db = await self._get_db()
                providers_collection = db.llm_providers
                provider_data = await providers_collection.find_one({"name": provider_name})

            base_url = provider_data.get("default_base_url") if provider_data else None
            extra_config = provider_data.get("extra_config") if provider_data else {}
            test_model = (extra_config or {}).get("test_model")

            # 聚合渠道（使用 OpenAI 兼容 API）
            if provider_name in ["302ai", "oneapi", "newapi", "custom_aggregator"]:
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._test_openai_compatible_api, api_key, display_name, base_url, provider_name, test_model
                )
            elif provider_name == "google":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_google_api, api_key, display_name, base_url)
            elif provider_name == "deepseek":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_deepseek_api, api_key, display_name)
            elif provider_name == "dashscope":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_dashscope_api, api_key, display_name)
            elif provider_name == "openrouter":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_openrouter_api, api_key, display_name)
            elif provider_name == "openai":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_openai_api, api_key, display_name)
            elif provider_name == "anthropic":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_anthropic_api, api_key, display_name)
            elif provider_name == "qianfan":
                return await asyncio.get_event_loop().run_in_executor(None, self._test_qianfan_api, api_key, display_name)
            else:
                # 对于未知的自定义厂家，使用 OpenAI 兼容 API 测试
                logger.info(f"🔍 使用 OpenAI 兼容 API 测试自定义厂家: {provider_name}")

                if not base_url:
                    return {
                        "success": False,
                        "message": f"自定义厂家 {display_name} 未配置 API 基础 URL"
                    }

                return await asyncio.get_event_loop().run_in_executor(
                    None, self._test_openai_compatible_api, api_key, display_name, base_url, provider_name, test_model
                )
        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} 连接测试失败: {str(e)}"
            }

    def _test_google_api(self, api_key: str, display_name: str, base_url: str = None, model_name: str = None) -> dict:
        """测试Google AI API"""
        try:
            # 如果没有指定模型，使用默认模型
            if not model_name:
                model_name = "gemini-2.0-flash-exp"
                logger.info(f"⚠️ 未指定模型，使用默认模型: {model_name}")

            logger.info(f"🔍 [Google AI 测试] 开始测试")
            logger.info(f"   display_name: {display_name}")
            logger.info(f"   model_name: {model_name}")
            logger.info(f"   base_url (原始): {base_url}")
            logger.info(f"   api_key 长度: {len(api_key) if api_key else 0}")

            # 使用配置的 base_url 或默认值
            if not base_url:
                base_url = "https://generativelanguage.googleapis.com/v1beta"
                logger.info(f"   ⚠️ base_url 为空，使用默认值: {base_url}")

            # 移除末尾的斜杠
            base_url = base_url.rstrip('/')
            logger.info(f"   base_url (去除斜杠): {base_url}")

            # 如果 base_url 以 /v1 结尾，替换为 /v1beta（Google AI 的正确端点）
            if base_url.endswith('/v1'):
                base_url = base_url[:-3] + '/v1beta'
                logger.info(f"   ✅ 将 /v1 替换为 /v1beta: {base_url}")

            # 构建完整的 API 端点（使用用户配置的模型）
            url = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

            logger.info(f"🔗 [Google AI 测试] 最终请求 URL: {url.replace(api_key, '***')}")

            headers = {
                "Content-Type": "application/json"
            }

            # 增加 token 限制到 2000，避免思考模式消耗导致无输出
            data = {
                "contents": [{
                    "parts": [{
                        "text": "Hello, please respond with 'OK' if you can read this."
                    }]
                }],
                "generationConfig": {
                    "maxOutputTokens": 2000,
                    "temperature": 0.1
                }
            }

            response = requests.post(url, json=data, headers=headers, timeout=15)

            print(f"📥 [Google AI 测试] 响应状态码: {response.status_code}")

            if response.status_code == 200:
                # 打印完整的响应内容用于调试
                print(f"📥 [Google AI 测试] 响应内容（前1000字符）: {response.text[:1000]}")

                result = response.json()
                print(f"📥 [Google AI 测试] 解析后的 JSON 结构:")
                print(f"   - 顶层键: {list(result.keys())}")
                print(f"   - 是否包含 'candidates': {'candidates' in result}")
                if "candidates" in result:
                    print(f"   - candidates 长度: {len(result['candidates'])}")
                    if len(result['candidates']) > 0:
                        print(f"   - candidates[0] 的键: {list(result['candidates'][0].keys())}")

                if "candidates" in result and len(result["candidates"]) > 0:
                    candidate = result["candidates"][0]
                    print(f"📥 [Google AI 测试] candidate 结构: {candidate}")

                    # 检查 finishReason
                    finish_reason = candidate.get("finishReason", "")
                    print(f"📥 [Google AI 测试] finishReason: {finish_reason}")

                    if "content" in candidate:
                        content = candidate["content"]

                        # 检查是否有 parts
                        if "parts" in content and len(content["parts"]) > 0:
                            text = content["parts"][0].get("text", "")
                            print(f"📥 [Google AI 测试] 提取的文本: {text}")

                            if text and len(text.strip()) > 0:
                                return {
                                    "success": True,
                                    "message": f"{display_name} API连接测试成功"
                                }
                            else:
                                print(f"❌ [Google AI 测试] 文本为空")
                                return {
                                    "success": False,
                                    "message": f"{display_name} API响应内容为空"
                                }
                        else:
                            # content 中没有 parts，可能是因为 MAX_TOKENS 或其他原因
                            print(f"❌ [Google AI 测试] content 中没有 parts")
                            print(f"   content 的键: {list(content.keys())}")

                            if finish_reason == "MAX_TOKENS":
                                return {
                                    "success": False,
                                    "message": f"{display_name} API响应被截断（MAX_TOKENS），请增加 maxOutputTokens 配置"
                                }
                            else:
                                return {
                                    "success": False,
                                    "message": f"{display_name} API响应格式异常（缺少 parts，finishReason: {finish_reason}）"
                                }
                    else:
                        print(f"❌ [Google AI 测试] candidate 中缺少 'content'")
                        print(f"   candidate 的键: {list(candidate.keys())}")
                        return {
                            "success": False,
                            "message": f"{display_name} API响应格式异常（缺少 content）"
                        }
                else:
                    print(f"❌ [Google AI 测试] 缺少 candidates 或 candidates 为空")
                    return {
                        "success": False,
                        "message": f"{display_name} API无有效候选响应"
                    }
            elif response.status_code == 400:
                print(f"❌ [Google AI 测试] 400 错误，响应内容: {response.text[:500]}")
                try:
                    error_detail = response.json()
                    error_msg = error_detail.get("error", {}).get("message", "未知错误")
                    return {
                        "success": False,
                        "message": f"{display_name} API请求错误: {error_msg}"
                    }
                except:
                    return {
                        "success": False,
                        "message": f"{display_name} API请求格式错误"
                    }
            elif response.status_code == 403:
                print(f"❌ [Google AI 测试] 403 错误，响应内容: {response.text[:500]}")
                return {
                    "success": False,
                    "message": f"{display_name} API密钥无效或权限不足"
                }
            elif response.status_code == 503:
                print(f"❌ [Google AI 测试] 503 错误，响应内容: {response.text[:500]}")
                try:
                    error_detail = response.json()
                    error_code = error_detail.get("code", "")
                    error_msg = error_detail.get("message", "服务暂时不可用")

                    if error_code == "NO_KEYS_AVAILABLE":
                        return {
                            "success": False,
                            "message": f"{display_name} 中转服务暂时无可用密钥，请稍后重试或联系中转服务提供商"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} 服务暂时不可用: {error_msg}"
                        }
                except:
                    return {
                        "success": False,
                        "message": f"{display_name} 服务暂时不可用 (HTTP 503)"
                    }
            else:
                print(f"❌ [Google AI 测试] {response.status_code} 错误，响应内容: {response.text[:500]}")
                return {
                    "success": False,
                    "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_deepseek_api(self, api_key: str, display_name: str, model_name: str = None) -> dict:
        """测试DeepSeek API — 只看HTTP状态码，不解析body"""
        try:
            if not model_name:
                model_name = "deepseek-chat"

            logger.info(f"🔍 [DeepSeek 测试] 模型: {model_name}")

            url = "https://api.deepseek.com/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            data = {
                "model": model_name,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }

            response = requests.post(url, json=data, headers=headers, timeout=10)

            if response.status_code == 200:
                return {
                    "success": True,
                    "message": f"{display_name} ({model_name}) 连接成功"
                }
            else:
                return {
                    "success": False,
                    "message": f"{display_name} ({model_name}) HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_dashscope_api(self, api_key: str, display_name: str, model_name: str = None) -> dict:
        """测试阿里云百炼API"""
        try:
            # 如果没有指定模型，无法测试
            if not model_name:
                return {
                    "success": False,
                    "message": f"{display_name} API测试需要指定模型名称"
                }

            logger.info(f"🔍 [DashScope 测试] 使用模型: {model_name}")

            # 使用阿里云百炼的OpenAI兼容接口
            url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            data = {
                "model": model_name,
                "messages": [
                    {"role": "user", "content": "你好，请简单介绍一下你自己。"}
                ],
                "max_tokens": 200,
                "temperature": 0.1
            }

            response = requests.post(url, json=data, headers=headers, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"].get("content", "")
                    reasoning = result["choices"][0]["message"].get("reasoning_content", "")
                    if (content and len(content.strip()) > 0) or (reasoning and len(reasoning.strip()) > 0):
                        return {
                            "success": True,
                            "message": f"{display_name} API连接测试成功"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} API响应为空"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} API响应格式异常"
                    }
            else:
                return {
                    "success": False,
                    "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_openrouter_api(self, api_key: str, display_name: str) -> dict:
        """测试OpenRouter API"""
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://sinoquant.cn",  # OpenRouter要求
                "X-Title": "SinaQuant"
            }

            data = {
                "model": "meta-llama/llama-3.2-3b-instruct:free",  # 使用免费模型
                "messages": [
                    {"role": "user", "content": "你好，请简单介绍一下你自己。"}
                ],
                "max_tokens": 50,
                "temperature": 0.1
            }

            response = requests.post(url, json=data, headers=headers, timeout=15)

            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 0:
                        return {
                            "success": True,
                            "message": f"{display_name} API连接测试成功"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} API响应为空"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} API响应格式异常"
                    }
            else:
                return {
                    "success": False,
                    "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_openai_api(self, api_key: str, display_name: str, model_name: str = None) -> dict:
        """测试OpenAI API"""
        try:
            if not model_name:
                return {
                    "success": False,
                    "message": f"{display_name} API测试需要指定模型名称"
                }

            url = "https://api.openai.com/v1/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            data = {
                "model": model_name,
                "messages": [
                    {"role": "user", "content": "你好，请简单介绍一下你自己。"}
                ],
                "max_tokens": 50,
                "temperature": 0.1
            }

            response = requests.post(url, json=data, headers=headers, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 0:
                        return {
                            "success": True,
                            "message": f"{display_name} API连接测试成功"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} API响应为空"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} API响应格式异常"
                    }
            else:
                return {
                    "success": False,
                    "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_anthropic_api(self, api_key: str, display_name: str) -> dict:
        """测试Anthropic API"""
        try:
            url = "https://api.anthropic.com/v1/messages"

            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            }

            data = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 50,
                "messages": [
                    {"role": "user", "content": "你好，请简单介绍一下你自己。"}
                ]
            }

            response = requests.post(url, json=data, headers=headers, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if "content" in result and len(result["content"]) > 0:
                    content = result["content"][0]["text"]
                    if content and len(content.strip()) > 0:
                        return {
                            "success": True,
                            "message": f"{display_name} API连接测试成功"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} API响应为空"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} API响应格式异常"
                    }
            else:
                return {
                    "success": False,
                    "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }

    def _test_qianfan_api(self, api_key: str, display_name: str) -> dict:
        """测试百度千帆API"""
        try:
            # 千帆新一代API使用OpenAI兼容接口
            url = "https://qianfan.baidubce.com/v2/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            data = {
                "model": "ernie-3.5-8k",
                "messages": [
                    {"role": "user", "content": "你好，请简单介绍一下你自己。"}
                ],
                "max_tokens": 50,
                "temperature": 0.1
            }

            response = requests.post(url, json=data, headers=headers, timeout=15)

            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 0:
                        return {
                            "success": True,
                            "message": f"{display_name} API连接测试成功"
                        }
                    else:
                        return {
                            "success": False,
                            "message": f"{display_name} API响应为空"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"{display_name} API响应格式异常"
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
                    return {
                        "success": False,
                        "message": f"{display_name} API测试失败: {error_msg}"
                    }
                except:
                    return {
                        "success": False,
                        "message": f"{display_name} API测试失败: HTTP {response.status_code}"
                    }

        except Exception as e:
            return {
                "success": False,
                "message": f"{display_name} API测试异常: {str(e)}"
            }
