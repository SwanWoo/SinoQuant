/**
 * 厂商配置管理 API
 * 统一管理各类服务厂商的配置（LLM、数据源、存储、消息队列等）
 */

import { ApiClient } from './request'

// ==================== 类型定义 ====================

/**
 * 厂商类型
 */
export type VendorType = 
  | 'llm'           // 大模型厂商 (OpenAI, DeepSeek, 阿里云百炼等)
  | 'data_source'   // 数据源厂商 (Tushare, Finnhub 等)
  | 'storage'       // 存储服务 (AWS S3, 阿里云 OSS 等)
  | 'message_queue' // 消息队列
  | 'cdn'           // CDN 服务
  | 'analytics'     // 分析服务
  | 'payment'       // 支付服务
  | 'custom'        // 自定义类型

/**
 * 认证类型
 */
export type AuthType =
  | 'api_key'        // 单一 API Key
  | 'api_key_secret' // API Key + Secret
  | 'bearer_token'   // Bearer Token
  | 'basic_auth'     // 用户名/密码
  | 'oauth2'         // OAuth 2.0
  | 'none'           // 无需认证

/**
 * 厂商配置主模型
 */
export interface VendorConfig {
  id: string
  name: string                    // 唯一标识
  display_name: string            // 显示名称
  description?: string            // 描述
  vendor_type: VendorType         // 厂商类型
  auth_type: AuthType             // 认证类型
  
  // 认证信息（响应中可能被脱敏）
  api_key?: string
  api_secret?: string
  bearer_token?: string
  username?: string
  password?: string
  oauth2_config?: {
    client_id?: string
    client_secret?: string
    token_url?: string
    scope?: string
  }
  
  // 连接配置
  base_url?: string               // API 基础地址
  endpoint?: string               // 自定义端点
  timeout?: number                // 超时时间（秒）
  retry_times?: number            // 重试次数
  rate_limit?: number             // 速率限制（请求/分钟）
  
  // 高级配置
  config_params?: Record<string, any>  // 自定义参数
  headers?: Record<string, string>     // 自定义请求头
  
  // 状态
  is_active: boolean              // 是否启用
  is_default: boolean             // 是否为默认
  is_user_config?: boolean         // 是否为用户专属配置
  
  // 元数据
  created_at: string
  updated_at: string
  created_by?: string
  updated_by?: string
}

/**
 * 创建/更新厂商配置请求
 */
export interface VendorConfigRequest {
  name: string
  display_name: string
  description?: string
  vendor_type: VendorType
  auth_type: AuthType
  
  // 认证信息
  api_key?: string
  api_secret?: string
  bearer_token?: string
  username?: string
  password?: string
  oauth2_config?: {
    client_id?: string
    client_secret?: string
    token_url?: string
    scope?: string
  }
  
  // 连接配置
  base_url?: string
  endpoint?: string
  timeout?: number
  retry_times?: number
  rate_limit?: number
  
  // 高级配置
  config_params?: Record<string, any>
  headers?: Record<string, string>
  
  is_active?: boolean
  is_default?: boolean
}

/**
 * 厂商配置响应（脱敏）
 */
export interface VendorConfigResponse {
  id: string
  name: string
  display_name: string
  description?: string
  vendor_type: VendorType
  auth_type: AuthType
  
  // 脱敏后的认证信息
  api_key_masked?: string         // 如: "sk-****1234"
  api_secret_masked?: string      // 如: "****secret"
  bearer_token_masked?: string    // 如: "Bearer ****token"
  username_masked?: string        // 如: "user****"
  has_oauth2_config?: boolean     // 是否有 OAuth2 配置
  
  // 连接配置
  base_url?: string
  endpoint?: string
  timeout: number
  retry_times: number
  rate_limit: number
  
  // 高级配置
  config_params?: Record<string, any>
  headers?: Record<string, string>
  
  is_active: boolean
  is_default: boolean
  
  created_at: string
  updated_at: string
  created_by?: string
  updated_by?: string
}

/**
 * 简化列表项（用于下拉选择）
 */
export interface VendorConfigListItem {
  id: string
  name: string
  display_name: string
  vendor_type: VendorType
  auth_type: AuthType
  is_active: boolean
  is_default: boolean
}

/**
 * 连接测试请求
 */
export interface VendorTestRequest {
  // 测试新配置时使用
  config?: VendorConfigRequest
  
  // 可选：覆盖测试参数
  test_timeout?: number
  test_payload?: Record<string, any>
}

/**
 * 连接测试结果
 */
export interface VendorTestResponse {
  success: boolean
  message: string
  details?: {
    response_time_ms?: number
    status_code?: number
    response_body?: any
    error_details?: string
    timestamp: string
  }
}

/**
 * 批量导入请求
 */
export interface VendorBulkImportRequest {
  configs: VendorConfigRequest[]
  skip_existing?: boolean         // 跳过已存在的配置
  update_existing?: boolean       // 更新已存在的配置
}

/**
 * 批量导入结果
 */
export interface VendorBulkImportResponse {
  success: boolean
  message: string
  summary: {
    total: number
    created: number
    updated: number
    skipped: number
    failed: number
  }
  details: Array<{
    index: number
    name: string
    status: 'created' | 'updated' | 'skipped' | 'failed'
    message?: string
  }>
}

/**
 * 厂商类型信息
 */
export interface VendorTypeInfo {
  value: VendorType
  label: string
  description: string
  icon?: string
  color?: string
}

/**
 * 认证类型信息
 */
export interface AuthTypeInfo {
  value: AuthType
  label: string
  description: string
  requires: string[]              // 需要的字段
  optional?: string[]             // 可选字段
}

/**
 * 预设厂商
 */
export interface PresetVendor {
  name: string
  display_name: string
  description: string
  vendor_type: VendorType
  auth_type: AuthType
  base_url?: string
  website?: string
  api_doc_url?: string
  register_url?: string
  register_guide?: string
  default_config?: Partial<VendorConfigRequest>
}

// ==================== API 方法 ====================

export const vendorConfigApi = {
  // ========== 厂商类型和认证类型 ==========
  
  /**
   * 获取厂商类型列表
   */
  getVendorTypes(): Promise<any> {
    return ApiClient.get('/api/vendor-configs/types')
  },
  
  /**
   * 获取认证类型列表
   */
  getAuthTypes(): Promise<any> {
    return ApiClient.get('/api/vendor-configs/auth-types')
  },
  
  // ========== CRUD 操作 ==========
  
  /**
   * 获取厂商配置列表
   */
  getVendorConfigs(params?: {
    vendor_type?: VendorType
    auth_type?: AuthType
    is_active?: boolean
    keyword?: string
  }): Promise<any> {
    return ApiClient.get('/api/vendor-configs', params)
  },
  
  /**
   * 获取简化列表（下拉选择用）
   */
  getVendorList(params?: {
    vendor_type?: VendorType
    is_active?: boolean
  }): Promise<any> {
    return ApiClient.get('/api/vendor-configs/list', params)
  },
  
  /**
   * 获取厂商详情
   */
  getVendorConfig(id: string): Promise<any> {
    return ApiClient.get(`/api/vendor-configs/${id}`)
  },
  
  /**
   * 创建厂商配置
   */
  createVendorConfig(config: VendorConfigRequest): Promise<{
    success: boolean
    message: string
    data: VendorConfigResponse
  }> {
    return ApiClient.post('/api/vendor-configs', config)
  },
  
  /**
   * 更新厂商配置
   */
  updateVendorConfig(
    id: string,
    config: Partial<VendorConfigRequest>
  ): Promise<{ success: boolean; message: string }> {
    return ApiClient.put(`/api/vendor-configs/${id}`, config)
  },
  
  /**
   * 删除厂商配置
   */
  deleteVendorConfig(id: string): Promise<{ success: boolean; message: string }> {
    return ApiClient.delete(`/api/vendor-configs/${id}`)
  },
  
  /**
   * 启用/禁用厂商
   */
  toggleVendorConfig(
    id: string,
    isActive: boolean
  ): Promise<{ success: boolean; message: string }> {
    return ApiClient.patch(`/api/vendor-configs/${id}/toggle`, { is_active: isActive })
  },
  
  /**
   * 设为默认厂商
   */
  setDefaultVendor(
    id: string
  ): Promise<{ success: boolean; message: string; data?: { previous_default?: string } }> {
    return ApiClient.post(`/api/vendor-configs/${id}/set-default`)
  },
  
  // ========== 测试接口 ==========
  
  /**
   * 测试新配置（保存前）
   */
  testNewConfig(request: VendorTestRequest): Promise<VendorTestResponse> {
    return ApiClient.post('/api/vendor-configs/test', request)
  },
  
  /**
   * 测试已保存配置
   */
  testSavedConfig(id: string): Promise<VendorTestResponse> {
    return ApiClient.post(`/api/vendor-configs/${id}/test`)
  },
  
  // ========== 批量导入/导出 ==========
  
  /**
   * 批量导入
   */
  bulkImport(request: VendorBulkImportRequest): Promise<any> {
    return ApiClient.post('/api/vendor-configs/import', request)
  },
  
  /**
   * 导出配置
   */
  exportConfigs(params?: {
    vendor_type?: VendorType
    include_sensitive?: boolean
  }): Promise<void> {
    return ApiClient.download(
      '/api/vendor-configs/export/download',
      `vendor-configs-${Date.now()}.json`,
      { params, responseType: 'blob' }
    )
  },
  
  // ========== 预设厂商 ==========
  
  /**
   * 获取预设厂商列表
   */
  getPresetVendors(params?: {
    vendor_type?: VendorType
  }): Promise<any> {
    return ApiClient.get('/api/vendor-configs/presets/list', params)
  },

  // ========== 用户级配置（我的配置） ==========

  /**
   * 获取当前用户可见的厂商配置（用户自有 + 全局）
   */
  getMyVendors(params?: {
    vendor_type?: VendorType
    is_active?: boolean
  }): Promise<any> {
    return ApiClient.get('/api/vendor-configs/my', params)
  },

  /**
   * 创建用户专属的厂商配置
   */
  createMyVendor(config: VendorConfigRequest): Promise<any> {
    return ApiClient.post('/api/vendor-configs/my', config)
  },

  /**
   * 更新用户专属的厂商配置
   */
  updateMyVendor(id: string, config: Partial<VendorConfigRequest>): Promise<any> {
    return ApiClient.put(`/api/vendor-configs/my/${id}`, config)
  },

  /**
   * 删除用户专属的厂商配置
   */
  deleteMyVendor(id: string): Promise<any> {
    return ApiClient.delete(`/api/vendor-configs/my/${id}`)
  },

  /**
   * 测试用户专属的厂商配置
   */
  testMyVendor(id: string): Promise<any> {
    return ApiClient.post(`/api/vendor-configs/my/${id}/test`)
  }
}

// ==================== 常量定义 ====================

/**
 * 厂商类型常量
 */
export const VENDOR_TYPES: Record<VendorType, { label: string; description: string; color: string }> = {
  llm: {
    label: '大模型厂商',
    description: '提供大语言模型服务（如 OpenAI, DeepSeek, 阿里云百炼等）',
    color: '#42b983'
  },
  data_source: {
    label: '数据源厂商',
    description: '提供金融数据服务（如 Tushare, Finnhub 等）',
    color: '#67C23A'
  },
  storage: {
    label: '存储服务',
    description: '对象存储服务（如 AWS S3, 阿里云 OSS 等）',
    color: '#E6A23C'
  },
  message_queue: {
    label: '消息队列',
    description: '消息队列服务（如 RabbitMQ, Kafka 等）',
    color: '#F56C6C'
  },
  cdn: {
    label: 'CDN 服务',
    description: '内容分发网络服务',
    color: '#909399'
  },
  analytics: {
    label: '分析服务',
    description: '数据分析与监控服务',
    color: '#8E44AD'
  },
  payment: {
    label: '支付服务',
    description: '支付处理服务',
    color: '#16A085'
  },
  custom: {
    label: '自定义类型',
    description: '自定义服务类型',
    color: '#606266'
  }
}

/**
 * 认证类型常量
 */
export const AUTH_TYPES: Record<AuthType, { label: string; description: string; requires: string[]; optional?: string[] }> = {
  api_key: {
    label: 'API Key',
    description: '单一 API Key 认证',
    requires: ['api_key']
  },
  api_key_secret: {
    label: 'API Key + Secret',
    description: 'API Key 配合 Secret 使用',
    requires: ['api_key', 'api_secret']
  },
  bearer_token: {
    label: 'Bearer Token',
    description: 'Bearer Token 认证',
    requires: ['bearer_token']
  },
  basic_auth: {
    label: 'Basic Auth',
    description: '用户名/密码认证',
    requires: ['username', 'password']
  },
  oauth2: {
    label: 'OAuth 2.0',
    description: 'OAuth 2.0 标准认证',
    requires: ['oauth2_config'],
    optional: ['oauth2_config.client_id', 'oauth2_config.client_secret']
  },
  none: {
    label: '无需认证',
    description: '无需任何认证信息',
    requires: []
  }
}

/**
 * 预设厂商配置
 */
export const PRESET_VENDORS: PresetVendor[] = [
  // === LLM 厂商 ===
  {
    name: 'openai',
    display_name: 'OpenAI',
    description: 'OpenAI 提供 GPT 系列大语言模型',
    vendor_type: 'llm',
    auth_type: 'api_key',
    base_url: 'https://api.openai.com/v1',
    website: 'https://openai.com',
    api_doc_url: 'https://platform.openai.com/docs',
    register_url: 'https://platform.openai.com/signup',
    register_guide: '注册 OpenAI 账号并创建 API Key'
  },
  {
    name: 'deepseek',
    display_name: 'DeepSeek',
    description: 'DeepSeek 提供高性能 AI 推理服务',
    vendor_type: 'llm',
    auth_type: 'api_key',
    base_url: 'https://api.deepseek.com',
    website: 'https://www.deepseek.com',
    api_doc_url: 'https://platform.deepseek.com/api-docs',
    register_url: 'https://platform.deepseek.com/sign_up',
    register_guide: '注册 DeepSeek 账号并获取 API Key'
  },
  {
    name: 'dashscope',
    display_name: '阿里云百炼',
    description: '阿里云百炼大模型服务平台，提供通义千问等模型',
    vendor_type: 'llm',
    auth_type: 'api_key',
    base_url: 'https://dashscope.aliyuncs.com/api/v1',
    website: 'https://bailian.console.aliyun.com',
    api_doc_url: 'https://help.aliyun.com/zh/dashscope/',
    register_url: 'https://account.aliyun.com/register',
    register_guide: '注册阿里云账号并开通百炼服务'
  },
  {
    name: 'zhipu',
    display_name: '智谱 AI',
    description: '智谱 AI 提供 GLM 系列中文大模型',
    vendor_type: 'llm',
    auth_type: 'api_key',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    website: 'https://zhipuai.cn',
    api_doc_url: 'https://open.bigmodel.cn/doc',
    register_url: 'https://open.bigmodel.cn/login',
    register_guide: '注册智谱 AI 账号并获取 API Key'
  },
  {
    name: 'anthropic',
    display_name: 'Anthropic',
    description: 'Anthropic 专注于 AI 安全研究，提供 Claude 系列模型',
    vendor_type: 'llm',
    auth_type: 'api_key',
    base_url: 'https://api.anthropic.com',
    website: 'https://anthropic.com',
    api_doc_url: 'https://docs.anthropic.com',
    register_url: 'https://console.anthropic.com/signup',
    register_guide: '注册 Anthropic 账号并获取 API Key'
  },
  // === 数据源厂商 ===
  {
    name: 'tushare',
    display_name: 'Tushare',
    description: 'Tushare 提供中国金融数据接口',
    vendor_type: 'data_source',
    auth_type: 'api_key',
    website: 'https://tushare.pro',
    api_doc_url: 'https://tushare.pro/document/2',
    register_url: 'https://tushare.pro/register',
    register_guide: '注册 Tushare 账号并获取 Token'
  },
  {
    name: 'finnhub',
    display_name: 'Finnhub',
    description: 'Finnhub 提供全球金融实时数据',
    vendor_type: 'data_source',
    auth_type: 'api_key',
    base_url: 'https://finnhub.io/api/v1',
    website: 'https://finnhub.io',
    api_doc_url: 'https://finnhub.io/docs/api',
    register_url: 'https://finnhub.io/register',
    register_guide: '注册 Finnhub 账号并获取 API Key'
  },
  {
    name: 'akshare',
    display_name: 'AKShare',
    description: 'AKShare 是开源的金融数据接口库',
    vendor_type: 'data_source',
    auth_type: 'none',
    website: 'https://www.akshare.xyz',
    api_doc_url: 'https://www.akshare.xyz'
  },
  // === 存储服务 ===
  {
    name: 'aliyun_oss',
    display_name: '阿里云 OSS',
    description: '阿里云对象存储服务',
    vendor_type: 'storage',
    auth_type: 'api_key_secret',
    website: 'https://www.aliyun.com/product/oss',
    api_doc_url: 'https://help.aliyun.com/product/31815.html',
    register_url: 'https://www.aliyun.com',
    register_guide: '开通阿里云 OSS 服务并创建 Access Key'
  },
  {
    name: 'aws_s3',
    display_name: 'AWS S3',
    description: 'Amazon Web Services 对象存储服务',
    vendor_type: 'storage',
    auth_type: 'api_key_secret',
    website: 'https://aws.amazon.com/s3',
    api_doc_url: 'https://docs.aws.amazon.com/s3',
    register_url: 'https://aws.amazon.com',
    register_guide: '注册 AWS 账号并创建 IAM 访问密钥'
  }
]

// ==================== 辅助函数 ====================

/**
 * 获取厂商类型显示名称
 */
export function getVendorTypeLabel(type: VendorType): string {
  return VENDOR_TYPES[type]?.label || type
}

/**
 * 获取认证类型显示名称
 */
export function getAuthTypeLabel(type: AuthType): string {
  return AUTH_TYPES[type]?.label || type
}

/**
 * 获取厂商类型颜色
 */
export function getVendorTypeColor(type: VendorType): string {
  return VENDOR_TYPES[type]?.color || '#909399'
}

/**
 * 验证厂商配置
 */
export function validateVendorConfig(config: Partial<VendorConfigRequest>): string[] {
  const errors: string[] = []
  
  if (!config.name?.trim()) {
    errors.push('厂商标识不能为空')
  } else if (!/^[a-z0-9_-]+$/.test(config.name)) {
    errors.push('厂商标识只能包含小写字母、数字、下划线和连字符')
  }
  
  if (!config.display_name?.trim()) {
    errors.push('显示名称不能为空')
  }
  
  if (!config.vendor_type) {
    errors.push('请选择厂商类型')
  }
  
  if (!config.auth_type) {
    errors.push('请选择认证类型')
  }
  
  // 根据认证类型验证必填字段
  if (config.auth_type && config.auth_type !== 'none') {
    const authInfo = AUTH_TYPES[config.auth_type]
    if (authInfo) {
      for (const field of authInfo.requires) {
        const value = (config as any)[field]
        if (!value || (typeof value === 'string' && !value.trim())) {
          errors.push(`${field} 是 ${authInfo.label} 的必填字段`)
        }
      }
    }
  }
  
  if (config.timeout !== undefined && config.timeout <= 0) {
    errors.push('超时时间必须大于 0')
  }
  
  if (config.retry_times !== undefined && config.retry_times < 0) {
    errors.push('重试次数不能为负数')
  }
  
  return errors
}

/**
 * 创建默认配置
 */
export function createDefaultVendorConfig(
  vendorType: VendorType = 'custom',
  authType: AuthType = 'api_key'
): Partial<VendorConfigRequest> {
  return {
    name: '',
    display_name: '',
    description: '',
    vendor_type: vendorType,
    auth_type: authType,
    timeout: 30,
    retry_times: 3,
    rate_limit: 100,
    is_active: true,
    is_default: false,
    config_params: {},
    headers: {}
  }
}
