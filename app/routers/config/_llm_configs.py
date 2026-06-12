"""
配置管理 - LLM 配置 + 数据源配置 CRUD + 测试 + 默认设置
"""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.routers.auth_db import get_current_user
from app.models.user import User
from app.models.config import (
    LLMConfigRequest, DataSourceConfigRequest,
    DatabaseConfigRequest, ConfigTestRequest, ConfigTestResponse,
    LLMConfig, DataSourceConfig, DatabaseConfig, DataSourceGrouping
)
from app.services.config_service import config_service
from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType

from app.routers.config._common import (
    logger, SetDefaultRequest,
    _sanitize_llm_configs, _sanitize_datasource_configs,
    _sanitize_database_configs
)


router = APIRouter(prefix="/config", tags=["配置管理"])


# ========== 大模型配置管理 ==========

@router.post("/llm", response_model=dict)
async def add_llm_config(
    request: LLMConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """添加或更新大模型配置"""
    try:
        logger.info(f"🔧 添加/更新大模型配置开始")
        logger.info(f"📊 请求数据: {request.model_dump()}")
        logger.info(f"🏷️ 厂家: {request.provider}, 模型: {request.model_name}")

        # 创建LLM配置
        llm_config_data = request.model_dump()
        logger.info(f"📋 原始配置数据: {llm_config_data}")

        # 如果没有提供API密钥，从厂家配置中获取
        if not llm_config_data.get('api_key'):
            logger.info(f"🔑 API密钥为空，从厂家配置获取: {request.provider}")

            # 获取厂家配置
            providers = await config_service.get_llm_providers()
            logger.info(f"📊 找到 {len(providers)} 个厂家配置")

            for p in providers:
                logger.info(f"   - 厂家: {p.name}, 有API密钥: {bool(p.api_key)}")

            provider_config = next((p for p in providers if p.name == request.provider), None)

            if provider_config:
                logger.info(f"✅ 找到厂家配置: {provider_config.name}")
                if provider_config.api_key:
                    llm_config_data['api_key'] = provider_config.api_key
                    logger.info(f"✅ 成功获取厂家API密钥 (长度: {len(provider_config.api_key)})")
                else:
                    logger.warning(f"⚠️ 厂家 {request.provider} 没有配置API密钥")
                    llm_config_data['api_key'] = ""
            else:
                logger.warning(f"⚠️ 未找到厂家 {request.provider} 的配置")
                llm_config_data['api_key'] = ""
        else:
            logger.info(f"🔑 使用提供的API密钥 (长度: {len(llm_config_data.get('api_key', ''))})")

        logger.info(f"📋 最终配置数据: {llm_config_data}")
        # 允许通过 REST 写入密钥，但如果是无效的密钥则清空
        # 无效的密钥：空字符串、占位符（your_xxx）、长度不够
        if 'api_key' in llm_config_data:
            api_key = llm_config_data.get('api_key', '')
            # 如果是无效的 Key，则清空（让系统使用环境变量）
            if not api_key or api_key.startswith('your_') or api_key.startswith('your-') or len(api_key) <= 10:
                llm_config_data['api_key'] = ""


        # 尝试创建LLMConfig对象
        try:
            llm_config = LLMConfig(**llm_config_data)
            logger.info(f"✅ LLMConfig对象创建成功")
        except Exception as e:
            logger.error(f"❌ LLMConfig对象创建失败: {e}")
            logger.error(f"📋 失败的数据: {llm_config_data}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"配置数据验证失败: {str(e)}"
            )

        # 保存配置
        success = await config_service.update_llm_config(llm_config)

        if success:
            logger.info(f"✅ 大模型配置更新成功: {llm_config.provider}/{llm_config.model_name}")

            # 同步定价配置到 sinoquant
            try:
                from app.core.config_bridge import sync_pricing_config_now
                sync_pricing_config_now()
                logger.info(f"✅ 定价配置已同步到 sinoquant")
            except Exception as e:
                logger.warning(f"⚠️  同步定价配置失败: {e}")

            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="update_llm_config",
                    details={"provider": llm_config.provider, "model_name": llm_config.model_name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "大模型配置更新成功", "model_name": llm_config.model_name}
        else:
            logger.error(f"❌ 大模型配置保存失败")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="大模型配置更新失败"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 添加大模型配置异常: {e}")
        import traceback
        logger.error(f"📋 异常堆栈: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加大模型配置失败: {str(e)}"
        )


@router.get("/llm", response_model=List[LLMConfig])
async def get_llm_configs(
    include_all: bool = Query(False, description="是否返回全部模型（含禁用）"),
    current_user: User = Depends(get_current_user)
):
    """获取所有大模型配置"""
    try:
        logger.info("🔄 开始获取大模型配置...")
        config = await config_service.get_system_config()

        if not config:
            logger.warning("⚠️ 系统配置为空，返回空列表")
            return []

        logger.info(f"📊 系统配置存在，大模型配置数量: {len(config.llm_configs)}")

        # 支持返回全部模型配置（用于分析页展示完整候选列表）
        if include_all:
            logger.info("✅ include_all=true，返回全部大模型配置")
            return _sanitize_llm_configs(config.llm_configs)

        # 如果没有大模型配置，创建一些示例配置
        if not config.llm_configs:
            logger.info("🔧 没有大模型配置，创建示例配置...")
            # 这里可以根据已有的厂家创建示例配置
            # 暂时返回空列表，让前端显示"暂无配置"

        # 获取所有供应商信息，用于过滤被禁用供应商的模型
        providers = await config_service.get_llm_providers()
        active_provider_names = {p.name for p in providers if p.is_active}

        enabled_configs = [llm_config for llm_config in config.llm_configs if llm_config.enabled]

        # 兼容历史数据：当供应商表为空时，不做供应商过滤，避免把已有模型全部过滤掉
        if active_provider_names:
            filtered_configs = [
                llm_config for llm_config in enabled_configs
                if llm_config.provider in active_provider_names
            ]
            if not filtered_configs and enabled_configs:
                logger.warning("⚠️ LLM 供应商过滤后为空，回退为仅按模型 enabled 过滤")
                filtered_configs = enabled_configs
        else:
            logger.warning("⚠️ 未检测到可用的 LLM 供应商配置，回退为仅按模型 enabled 过滤")
            filtered_configs = enabled_configs

        logger.info(f"✅ 过滤后的大模型配置数量: {len(filtered_configs)} (原始: {len(config.llm_configs)})")

        return _sanitize_llm_configs(filtered_configs)
    except Exception as e:
        logger.error(f"❌ 获取大模型配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取大模型配置失败: {str(e)}"
        )


@router.get("/llm/user-vendors")
async def get_user_llm_vendors(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前用户的 LLM 厂商配置（含 API Key 状态）

    返回用户可见的所有 LLM 厂商配置，标记哪些有用户专属 Key、哪些使用全局默认
    """
    try:
        from app.models.vendor_config import VendorType
        from app.services.vendor_config_service import vendor_config_service

        user_id = str(getattr(current_user, "id", None))
        vendors = await vendor_config_service.get_user_vendors(
            user_id=user_id,
            vendor_type=VendorType.LLM,
        )
        return [
            {
                "id": v.id,
                "name": v.name,
                "display_name": v.display_name,
                "base_url": v.base_url,
                "is_active": v.is_active,
                "is_user_config": v.is_user_config,
                "has_credentials": v.has_credentials,
                "status": v.status.value if v.status else None,
            }
            for v in vendors
        ]
    except Exception as e:
        logger.error(f"❌ 获取用户 LLM 厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户 LLM 厂商配置失败: {str(e)}"
        )


@router.delete("/llm/{provider}/{model_name}")
async def delete_llm_config(
    provider: str,
    model_name: str,
    current_user: User = Depends(get_current_user)
):
    """删除大模型配置"""
    try:
        logger.info(f"🗑️ 删除大模型配置请求 - provider: {provider}, model_name: {model_name}")
        success = await config_service.delete_llm_config(provider, model_name)

        if success:
            logger.info(f"✅ 大模型配置删除成功 - {provider}/{model_name}")

            # 同步定价配置到 sinoquant
            try:
                from app.core.config_bridge import sync_pricing_config_now
                sync_pricing_config_now()
                logger.info(f"✅ 定价配置已同步到 sinoquant")
            except Exception as e:
                logger.warning(f"⚠️  同步定价配置失败: {e}")

            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="delete_llm_config",
                    details={"provider": provider, "model_name": model_name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "大模型配置删除成功"}
        else:
            logger.warning(f"⚠️ 未找到大模型配置 - {provider}/{model_name}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="大模型配置不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除大模型配置异常 - {provider}/{model_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除大模型配置失败: {str(e)}"
        )


@router.post("/llm/set-default")
async def set_default_llm(
    request: SetDefaultRequest,
    current_user: User = Depends(get_current_user)
):
    """设置默认大模型"""
    try:
        success = await config_service.set_default_llm(request.name)
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="set_default_llm",
                    details={"name": request.name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "默认大模型设置成功", "default_llm": request.name}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="指定的大模型不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置默认大模型失败: {str(e)}"
        )


# ========== 数据源配置管理 ==========

@router.post("/datasource", response_model=dict)
async def add_data_source_config(
    request: DataSourceConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """添加数据源配置"""
    try:
        # 开源版本：所有用户都可以修改配置

        # 获取当前配置
        config = await config_service.get_system_config()
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="系统配置不存在"
            )

        # 添加新的数据源配置
        # 支持保存 API Key（与大模型厂家管理逻辑一致）
        from app.utils.api_key_utils import should_skip_api_key_update, is_valid_api_key

        _req = request.model_dump()

        # 处理 API Key
        if 'api_key' in _req:
            api_key = _req.get('api_key', '')
            # 如果是占位符或截断的密钥，清空该字段
            if should_skip_api_key_update(api_key):
                _req['api_key'] = ""
            # 如果是空字符串，保留（表示使用环境变量）
            elif api_key == '':
                _req['api_key'] = ''
            # 如果是新输入的密钥，必须验证有效性
            elif not is_valid_api_key(api_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="API Key 无效：长度必须大于 10 个字符，且不能是占位符"
                )
            # 有效的完整密钥，保留

        # 处理 API Secret
        if 'api_secret' in _req:
            api_secret = _req.get('api_secret', '')
            if should_skip_api_key_update(api_secret):
                _req['api_secret'] = ""
            # 如果是空字符串，保留
            elif api_secret == '':
                _req['api_secret'] = ''
            # 如果是新输入的密钥，必须验证有效性
            elif not is_valid_api_key(api_secret):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="API Secret 无效：长度必须大于 10 个字符，且不能是占位符"
                )

        ds_config = DataSourceConfig(**_req)
        config.data_source_configs.append(ds_config)

        success = await config_service.save_system_config(config)
        if success:
            # 自动创建数据源分组关系
            market_categories = _req.get('market_categories', [])
            if market_categories:
                for category_id in market_categories:
                    try:
                        grouping = DataSourceGrouping(
                            data_source_name=ds_config.name,
                            market_category_id=category_id,
                            priority=ds_config.priority,
                            enabled=ds_config.enabled
                        )
                        await config_service.add_datasource_to_category(grouping)
                    except Exception as e:
                        # 如果分组已存在或其他错误，记录但不影响主流程
                        logger.warning(f"自动创建数据源分组失败: {str(e)}")

            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="add_data_source_config",
                    details={"name": ds_config.name, "market_categories": market_categories},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "数据源配置添加成功", "name": ds_config.name}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="数据源配置添加失败"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加数据源配置失败: {str(e)}"
        )


@router.get("/datasource", response_model=List[DataSourceConfig])
async def get_data_source_configs(
    current_user: User = Depends(get_current_user)
):
    """获取所有数据源配置"""
    try:
        config = await config_service.get_system_config()
        if not config:
            return []
        return _sanitize_datasource_configs(config.data_source_configs)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取数据源配置失败: {str(e)}"
        )


@router.put("/datasource/{name}", response_model=dict)
async def update_data_source_config(
    name: str,
    request: DataSourceConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """更新数据源配置"""
    try:
        # 获取当前配置
        config = await config_service.get_system_config()
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="系统配置不存在"
            )

        # 查找并更新数据源配置
        from app.utils.api_key_utils import should_skip_api_key_update, is_valid_api_key

        def _truncate_api_key(api_key: str, prefix_len: int = 6, suffix_len: int = 6) -> str:
            """截断 API Key 用于显示"""
            if not api_key or len(api_key) <= prefix_len + suffix_len:
                return api_key
            return f"{api_key[:prefix_len]}...{api_key[-suffix_len:]}"

        for i, ds_config in enumerate(config.data_source_configs):
            if ds_config.name == name:
                # 更新配置
                # 处理 API Key 的更新逻辑（与大模型厂家管理逻辑一致）
                _req = request.model_dump()

                # 处理 API Key
                if 'api_key' in _req:
                    api_key = _req.get('api_key')
                    logger.info(f"🔍 [API Key 验证] 收到的 API Key: {repr(api_key)} (类型: {type(api_key).__name__}, 长度: {len(api_key) if api_key else 0})")

                    # 如果是 None 或空字符串，保留原值（不更新）
                    if api_key is None or api_key == '':
                        logger.info(f"⏭️  [API Key 验证] None 或空字符串，保留原值")
                        _req['api_key'] = ds_config.api_key or ""
                    # 如果包含 "..."（截断标记），需要验证是否是未修改的原值
                    elif api_key and "..." in api_key:
                        logger.info(f"🔍 [API Key 验证] 检测到截断标记，验证是否与数据库原值匹配")

                        # 对数据库中的完整 API Key 进行相同的截断处理
                        if ds_config.api_key:
                            truncated_db_key = _truncate_api_key(ds_config.api_key)
                            logger.info(f"🔍 [API Key 验证] 数据库原值截断后: {truncated_db_key}")
                            logger.info(f"🔍 [API Key 验证] 收到的值: {api_key}")

                            # 比较截断后的值
                            if api_key == truncated_db_key:
                                # 相同，说明用户没有修改，保留数据库中的完整值
                                logger.info(f"✅ [API Key 验证] 截断值匹配，保留数据库原值")
                                _req['api_key'] = ds_config.api_key
                            else:
                                # 不同，说明用户修改了但修改得不完整
                                logger.error(f"❌ [API Key 验证] 截断值不匹配，用户可能修改了不完整的密钥")
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"API Key 格式错误：检测到截断标记但与数据库中的值不匹配，请输入完整的 API Key"
                                )
                        else:
                            # 数据库中没有原值，但前端发送了截断值，这是不合理的
                            logger.error(f"❌ [API Key 验证] 数据库中没有原值，但收到了截断值")
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"API Key 格式错误：请输入完整的 API Key"
                            )
                    # 如果是占位符，则不更新（保留原值）
                    elif should_skip_api_key_update(api_key):
                        logger.info(f"⏭️  [API Key 验证] 跳过更新（占位符），保留原值")
                        _req['api_key'] = ds_config.api_key or ""
                    # 如果是新输入的密钥，必须验证有效性
                    elif not is_valid_api_key(api_key):
                        logger.error(f"❌ [API Key 验证] 验证失败: '{api_key}' (长度: {len(api_key)})")
                        logger.error(f"   - 长度检查: {len(api_key)} > 10? {len(api_key) > 10}")
                        logger.error(f"   - 占位符前缀检查: startswith('your_')? {api_key.startswith('your_')}, startswith('your-')? {api_key.startswith('your-')}")
                        logger.error(f"   - 占位符后缀检查: endswith('_here')? {api_key.endswith('_here')}, endswith('-here')? {api_key.endswith('-here')}")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"API Key 无效：长度必须大于 10 个字符，且不能是占位符（当前长度: {len(api_key)}）"
                        )
                    else:
                        logger.info(f"✅ [API Key 验证] 验证通过，将更新密钥 (长度: {len(api_key)})")
                    # 有效的完整密钥，保留（表示更新）

                # 处理 API Secret
                if 'api_secret' in _req:
                    api_secret = _req.get('api_secret')
                    logger.info(f"🔍 [API Secret 验证] 收到的 API Secret: {repr(api_secret)} (类型: {type(api_secret).__name__}, 长度: {len(api_secret) if api_secret else 0})")

                    # 如果是 None 或空字符串，保留原值（不更新）
                    if api_secret is None or api_secret == '':
                        logger.info(f"⏭️  [API Secret 验证] None 或空字符串，保留原值")
                        _req['api_secret'] = ds_config.api_secret or ""
                    # 如果包含 "..."（截断标记），需要验证是否是未修改的原值
                    elif api_secret and "..." in api_secret:
                        logger.info(f"🔍 [API Secret 验证] 检测到截断标记，验证是否与数据库原值匹配")

                        # 对数据库中的完整 API Secret 进行相同的截断处理
                        if ds_config.api_secret:
                            truncated_db_secret = _truncate_api_key(ds_config.api_secret)
                            logger.info(f"🔍 [API Secret 验证] 数据库原值截断后: {truncated_db_secret}")
                            logger.info(f"🔍 [API Secret 验证] 收到的值: {api_secret}")

                            # 比较截断后的值
                            if api_secret == truncated_db_secret:
                                # 相同，说明用户没有修改，保留数据库中的完整值
                                logger.info(f"✅ [API Secret 验证] 截断值匹配，保留数据库原值")
                                _req['api_secret'] = ds_config.api_secret
                            else:
                                # 不同，说明用户修改了但修改得不完整
                                logger.error(f"❌ [API Secret 验证] 截断值不匹配，用户可能修改了不完整的密钥")
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"API Secret 格式错误：检测到截断标记但与数据库中的值不匹配，请输入完整的 API Secret"
                                )
                        else:
                            # 数据库中没有原值，但前端发送了截断值，这是不合理的
                            logger.error(f"❌ [API Secret 验证] 数据库中没有原值，但收到了截断值")
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"API Secret 格式错误：请输入完整的 API Secret"
                            )
                    # 如果是占位符，则不更新（保留原值）
                    elif should_skip_api_key_update(api_secret):
                        logger.info(f"⏭️  [API Secret 验证] 跳过更新（占位符），保留原值")
                        _req['api_secret'] = ds_config.api_secret or ""
                    # 如果是新输入的密钥，必须验证有效性
                    elif not is_valid_api_key(api_secret):
                        logger.error(f"❌ [API Secret 验证] 验证失败: '{api_secret}' (长度: {len(api_secret)})")
                        logger.error(f"   - 长度检查: {len(api_secret)} > 10? {len(api_secret) > 10}")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"API Secret 无效：长度必须大于 10 个字符，且不能是占位符（当前长度: {len(api_secret)}）"
                        )
                    else:
                        logger.info(f"✅ [API Secret 验证] 验证通过，将更新密钥 (长度: {len(api_secret)})")

                updated_config = DataSourceConfig(**_req)
                config.data_source_configs[i] = updated_config

                success = await config_service.save_system_config(config)
                if success:
                    # 同步市场分类关系
                    new_categories = set(_req.get('market_categories', []))

                    # 获取当前的分组关系
                    current_groupings = await config_service.get_datasource_groupings()
                    current_categories = set(
                        g.market_category_id
                        for g in current_groupings
                        if g.data_source_name == name
                    )

                    # 需要添加的分类
                    to_add = new_categories - current_categories
                    for category_id in to_add:
                        try:
                            grouping = DataSourceGrouping(
                                data_source_name=name,
                                market_category_id=category_id,
                                priority=updated_config.priority,
                                enabled=updated_config.enabled
                            )
                            await config_service.add_datasource_to_category(grouping)
                        except Exception as e:
                            logger.warning(f"添加数据源分组失败: {str(e)}")

                    # 需要删除的分类
                    to_remove = current_categories - new_categories
                    for category_id in to_remove:
                        try:
                            await config_service.remove_datasource_from_category(name, category_id)
                        except Exception as e:
                            logger.warning(f"删除数据源分组失败: {str(e)}")

                    # 审计日志（忽略异常）
                    try:
                        await log_operation(
                            user_id=str(getattr(current_user, "id", "")),
                            username=getattr(current_user, "username", "unknown"),
                            action_type=ActionType.CONFIG_MANAGEMENT,
                            action="update_data_source_config",
                            details={"name": name, "market_categories": list(new_categories)},
                            success=True,
                        )
                    except Exception:
                        pass
                    return {"message": "数据源配置更新成功"}
                else:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="数据源配置更新失败"
                    )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源配置不存在"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新数据源配置失败: {str(e)}"
        )


@router.delete("/datasource/{name}", response_model=dict)
async def delete_data_source_config(
    name: str,
    current_user: User = Depends(get_current_user)
):
    """删除数据源配置"""
    try:
        # 获取当前配置
        config = await config_service.get_system_config()
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="系统配置不存在"
            )

        # 查找并删除数据源配置
        for i, ds_config in enumerate(config.data_source_configs):
            if ds_config.name == name:
                config.data_source_configs.pop(i)

                success = await config_service.save_system_config(config)
                if success:
                    # 审计日志（忽略异常）
                    try:
                        await log_operation(
                            user_id=str(getattr(current_user, "id", "")),
                            username=getattr(current_user, "username", "unknown"),
                            action_type=ActionType.CONFIG_MANAGEMENT,
                            action="delete_data_source_config",
                            details={"name": name},
                            success=True,
                        )
                    except Exception:
                        pass
                    return {"message": "数据源配置删除成功"}
                else:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="数据源配置删除失败"
                    )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源配置不存在"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除数据源配置失败: {str(e)}"
        )


@router.post("/datasource/set-default")
async def set_default_data_source(
    request: SetDefaultRequest,
    current_user: User = Depends(get_current_user)
):
    """设置默认数据源"""
    try:
        success = await config_service.set_default_data_source(request.name)
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="set_default_datasource",
                    details={"name": request.name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "默认数据源设置成功", "default_data_source": request.name}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="指定的数据源不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置默认数据源失败: {str(e)}"
        )


# ========== 配置测试端点 ==========

@router.post("/test", response_model=ConfigTestResponse)
async def test_config(
    request: ConfigTestRequest,
    current_user: User = Depends(get_current_user)
):
    """测试配置连接"""
    try:
        if request.config_type == "llm":
            llm_config = LLMConfig(**request.config_data)
            result = await config_service.test_llm_config(llm_config)
        elif request.config_type == "datasource":
            ds_config = DataSourceConfig(**request.config_data)
            result = await config_service.test_data_source_config(ds_config)
        elif request.config_type == "database":
            db_config = DatabaseConfig(**request.config_data)
            result = await config_service.test_database_config(db_config)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不支持的配置类型"
            )

        return ConfigTestResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试配置失败: {str(e)}"
        )
