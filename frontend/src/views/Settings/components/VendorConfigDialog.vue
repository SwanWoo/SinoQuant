<template>
  <el-dialog
    :model-value="visible"
    :title="isEdit ? '编辑厂商配置' : '添加厂商配置'"
    width="700px"
    :close-on-click-modal="false"
    @update:model-value="handleVisibleChange"
    @close="handleClose"
  >
    <el-form
      ref="formRef"
      :model="formData"
      :rules="rules"
      label-width="120px"
      v-loading="loading"
    >
      <!-- 快速选择预设 -->
      <el-form-item v-if="!isEdit" label="快速选择">
        <el-select
          v-model="selectedPreset"
          placeholder="选择预设厂商或手动填写"
          clearable
          style="width: 100%"
          @change="handlePresetChange"
        >
          <el-option-group
            v-for="(group, type) in presetGroups"
            :key="type"
            :label="getVendorTypeLabel(type as VendorType)"
          >
            <el-option
              v-for="preset in group"
              :key="preset.name"
              :label="preset.display_name"
              :value="preset.name"
            />
          </el-option-group>
        </el-select>
      </el-form-item>

      <!-- 注册引导提示 -->
      <el-alert
        v-if="currentPreset?.register_url"
        :title="`📝 ${currentPreset.display_name} 注册引导`"
        type="info"
        :closable="false"
        style="margin-bottom: 20px"
      >
        <template #default>
          <div class="register-guide">
            <p>{{ currentPreset.register_guide || '如果您还没有账号，请先注册：' }}</p>
            <el-button
              v-if="currentPreset.register_url"
              type="primary"
              size="small"
              link
              @click="openRegisterUrl"
            >
              <el-icon><Link /></el-icon>
              前往注册 {{ currentPreset.display_name }}
            </el-button>
            <el-button
              v-if="currentPreset.api_doc_url"
              type="info"
              size="small"
              link
              @click="openApiDocUrl"
            >
              <el-icon><Document /></el-icon>
              查看 API 文档
            </el-button>
          </div>
        </template>
      </el-alert>

      <!-- 基本信息 -->
      <el-divider content-position="left">基本信息</el-divider>

      <el-form-item label="厂商标识" prop="name">
        <el-input
          v-model="formData.name"
          placeholder="如: openai, deepseek"
          :disabled="isEdit"
        />
        <div class="form-tip">唯一的英文标识，创建后不可修改</div>
      </el-form-item>

      <el-form-item label="显示名称" prop="display_name">
        <el-input
          v-model="formData.display_name"
          placeholder="如: OpenAI, DeepSeek"
        />
      </el-form-item>

      <el-form-item label="厂商类型" prop="vendor_type">
        <el-select
          v-model="formData.vendor_type"
          placeholder="选择厂商类型"
          style="width: 100%"
          :disabled="!!selectedPreset"
        >
          <el-option
            v-for="(info, type) in VENDOR_TYPES"
            :key="type"
            :label="info.label"
            :value="type"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="formData.description"
          type="textarea"
          :rows="2"
          placeholder="厂商简介"
        />
      </el-form-item>

      <!-- 认证配置 -->
      <el-divider content-position="left">认证配置</el-divider>

      <el-form-item label="认证类型" prop="auth_type">
        <el-select
          v-model="formData.auth_type"
          placeholder="选择认证类型"
          style="width: 100%"
          @change="handleAuthTypeChange"
        >
          <el-option
            v-for="(info, type) in AUTH_TYPES"
            :key="type"
            :label="info.label"
            :value="type"
          >
            <div style="display: flex; flex-direction: column">
              <span>{{ info.label }}</span>
              <span style="font-size: 12px; color: var(--el-text-color-secondary)">{{ info.description }}</span>
            </div>
          </el-option>
        </el-select>
      </el-form-item>

      <!-- 根据认证类型显示不同字段 -->
      <template v-if="formData.auth_type === 'api_key'">
        <el-form-item label="API Key" prop="api_key">
          <el-input
            v-model="formData.api_key"
            type="password"
            placeholder="输入 API Key"
            show-password
            clearable
          />
          <div v-if="isEdit && props.config?.api_key_masked" class="form-tip">
            当前: {{ props.config.api_key_masked }}
          </div>
        </el-form-item>
      </template>

      <template v-if="formData.auth_type === 'api_key_secret'">
        <el-form-item label="API Key" prop="api_key">
          <el-input
            v-model="formData.api_key"
            type="password"
            placeholder="输入 API Key"
            show-password
            clearable
          />
          <div v-if="isEdit && props.config?.api_key_masked" class="form-tip">
            当前: {{ props.config.api_key_masked }}
          </div>
        </el-form-item>
        <el-form-item label="API Secret" prop="api_secret">
          <el-input
            v-model="formData.api_secret"
            type="password"
            placeholder="输入 API Secret"
            show-password
            clearable
          />
          <div v-if="isEdit && props.config?.api_secret_masked" class="form-tip">
            当前: {{ props.config.api_secret_masked }}
          </div>
        </el-form-item>
      </template>

      <template v-if="formData.auth_type === 'bearer_token'">
        <el-form-item label="Bearer Token" prop="bearer_token">
          <el-input
            v-model="formData.bearer_token"
            type="password"
            placeholder="输入 Bearer Token"
            show-password
            clearable
          />
          <div v-if="isEdit && props.config?.bearer_token_masked" class="form-tip">
            当前: {{ props.config.bearer_token_masked }}
          </div>
        </el-form-item>
      </template>

      <template v-if="formData.auth_type === 'basic_auth'">
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="formData.username"
            placeholder="输入用户名"
            clearable
          />
          <div v-if="isEdit && props.config?.username_masked" class="form-tip">
            当前: {{ props.config.username_masked }}
          </div>
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="formData.password"
            type="password"
            placeholder="输入密码"
            show-password
            clearable
          />
        </el-form-item>
      </template>

      <template v-if="formData.auth_type === 'oauth2'">
        <el-form-item label="Client ID" prop="oauth2_config.client_id">
          <el-input
            v-model="oauth2Form.client_id"
            placeholder="输入 Client ID"
            clearable
          />
        </el-form-item>
        <el-form-item label="Client Secret" prop="oauth2_config.client_secret">
          <el-input
            v-model="oauth2Form.client_secret"
            type="password"
            placeholder="输入 Client Secret"
            show-password
            clearable
          />
        </el-form-item>
        <el-form-item label="Token URL" prop="oauth2_config.token_url">
          <el-input
            v-model="oauth2Form.token_url"
            placeholder="https://..."
            clearable
          />
        </el-form-item>
        <el-form-item label="Scope">
          <el-input
            v-model="oauth2Form.scope"
            placeholder="可选，多个 scope 用空格分隔"
            clearable
          />
        </el-form-item>
      </template>

      <!-- 连接配置 -->
      <el-divider content-position="left">连接配置</el-divider>

      <el-form-item label="基础 URL">
        <el-input
          v-model="formData.base_url"
          placeholder="https://api.example.com/v1"
          clearable
        />
      </el-form-item>

      <el-form-item label="自定义端点">
        <el-input
          v-model="formData.endpoint"
          placeholder="可选，覆盖默认端点"
          clearable
        />
      </el-form-item>

      <el-row :gutter="16">
        <el-col :span="8">
          <el-form-item label="超时时间">
            <el-input-number
              v-model="formData.timeout"
              :min="1"
              :max="300"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="重试次数">
            <el-input-number
              v-model="formData.retry_times"
              :min="0"
              :max="10"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="速率限制">
            <el-input-number
              v-model="formData.rate_limit"
              :min="1"
              :max="10000"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <!-- 高级配置 -->
      <el-divider content-position="left">高级配置</el-divider>

      <el-form-item label="自定义参数">
        <el-input
          v-model="configParamsText"
          type="textarea"
          :rows="4"
          placeholder='{"key": "value"}'
        />
        <div class="form-tip">JSON 格式，用于传递额外的配置参数</div>
      </el-form-item>

      <el-form-item label="自定义请求头">
        <el-input
          v-model="headersText"
          type="textarea"
          :rows="3"
          placeholder='{"X-Custom-Header": "value"}'
        />
        <div class="form-tip">JSON 格式</div>
      </el-form-item>

      <!-- 状态 -->
      <el-divider content-position="left">状态</el-divider>

      <el-form-item>
        <el-switch
          v-model="formData.is_active"
          active-text="启用"
          inactive-text="禁用"
        />
      </el-form-item>

      <el-form-item>
        <el-switch
          v-model="formData.is_default"
          active-text="设为默认"
          inactive-text="非默认"
          :disabled="formData.is_default"
        />
        <div class="form-tip">设为默认后，同类型的其他厂商将自动取消默认状态</div>
      </el-form-item>
    </el-form>

    <!-- 底部按钮 -->
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button
          type="info"
          @click="handleTest"
          :loading="testing"
          :disabled="!canTest"
        >
          <el-icon><Connection /></el-icon>
          测试连接
        </el-button>
        <el-button
          type="primary"
          @click="handleSubmit"
          :loading="submitting"
        >
          {{ isEdit ? '保存' : '创建' }}
        </el-button>
      </div>
    </template>

    <!-- 测试结果弹窗 -->
    <el-dialog
      v-model="testResultVisible"
      title="连接测试结果"
      width="400px"
      append-to-body
    >
      <div v-if="testResult" class="test-result">
        <div class="result-icon" :class="{ success: testResult.success }">
          <el-icon :size="48">
            <CircleCheck v-if="testResult.success" />
            <CircleClose v-else />
          </el-icon>
        </div>
        <div class="result-message">{{ testResult.message }}</div>
        <div v-if="testResult.details" class="result-details">
          <p v-if="testResult.details.response_time_ms">
            响应时间: {{ testResult.details.response_time_ms }}ms
          </p>
          <p v-if="testResult.details.status_code">
            状态码: {{ testResult.details.status_code }}
          </p>
        </div>
      </div>
    </el-dialog>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, reactive, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  Link,
  Document,
  Connection,
  CircleCheck,
  CircleClose
} from '@element-plus/icons-vue'

import {
  vendorConfigApi,
  VENDOR_TYPES,
  AUTH_TYPES,
  PRESET_VENDORS,
  getVendorTypeLabel,
  type VendorConfigResponse,
  type VendorConfigRequest,
  type VendorTestResponse,
  type VendorType,
  type AuthType,
  type PresetVendor
} from '@/api/vendorConfig'

// ==================== Props & Emits ====================

interface Props {
  visible: boolean
  config?: VendorConfigResponse | null
  isEdit?: boolean
  userMode?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  config: null,
  isEdit: false,
  userMode: false
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'success': []
}>()

// ==================== 响应式数据 ====================

const formRef = ref<FormInstance>()
const loading = ref(false)
const submitting = ref(false)
const testing = ref(false)
const selectedPreset = ref('')
const testResultVisible = ref(false)
const testResult = ref<VendorTestResponse | null>(null)

// OAuth2 表单
const oauth2Form = reactive({
  client_id: '',
  client_secret: '',
  token_url: '',
  scope: ''
})

// 主表单数据
const formData = reactive<VendorConfigRequest>({
  name: '',
  display_name: '',
  description: '',
  vendor_type: 'custom',
  auth_type: 'api_key',
  api_key: '',
  api_secret: '',
  bearer_token: '',
  username: '',
  password: '',
  base_url: '',
  endpoint: '',
  timeout: 30,
  retry_times: 3,
  rate_limit: 100,
  config_params: {},
  headers: {},
  is_active: true,
  is_default: false
})

// JSON 文本（用于高级配置）
const configParamsText = ref('{}')
const headersText = ref('{}')

// ==================== 计算属性 ====================

// 按类型分组的预设厂商
const presetGroups = computed(() => {
  const groups: Record<string, PresetVendor[]> = {}
  
  PRESET_VENDORS.forEach(preset => {
    if (!groups[preset.vendor_type]) {
      groups[preset.vendor_type] = []
    }
    groups[preset.vendor_type].push(preset)
  })
  
  return groups
})

// 当前选中的预设厂商
const currentPreset = computed(() => {
  if (!selectedPreset.value) return null
  return PRESET_VENDORS.find(p => p.name === selectedPreset.value) || null
})

// 是否可以测试
const canTest = computed(() => {
  if (!formData.name || !formData.display_name) return false
  
  // 根据认证类型检查必填字段
  switch (formData.auth_type) {
    case 'api_key':
      return !!formData.api_key || (props.isEdit && !!props.config?.api_key_masked)
    case 'api_key_secret':
      return (!!formData.api_key || (props.isEdit && !!props.config?.api_key_masked)) &&
             (!!formData.api_secret || (props.isEdit && !!props.config?.api_secret_masked))
    case 'bearer_token':
      return !!formData.bearer_token || (props.isEdit && !!props.config?.bearer_token_masked)
    case 'basic_auth':
      return !!formData.username || (props.isEdit && !!props.config?.username_masked)
    case 'none':
      return true
    default:
      return false
  }
})

// ==================== 表单验证规则 ====================

// 动态验证规则 - 根据认证类型返回对应的验证规则
const getDynamicRules = (): FormRules => {
  const baseRules: FormRules = {
    name: [
      { required: true, message: '请输入厂商标识', trigger: 'blur' },
      { pattern: /^[a-z0-9_-]+$/, message: '只能包含小写字母、数字、下划线和连字符', trigger: 'blur' },
      { min: 2, max: 50, message: '长度在 2 到 50 个字符', trigger: 'blur' }
    ],
    display_name: [
      { required: true, message: '请输入显示名称', trigger: 'blur' },
      { min: 1, max: 100, message: '长度在 1 到 100 个字符', trigger: 'blur' }
    ],
    vendor_type: [
      { required: true, message: '请选择厂商类型', trigger: 'change' }
    ],
    auth_type: [
      { required: true, message: '请选择认证类型', trigger: 'change' }
    ]
  }

  // 根据认证类型添加对应的验证规则
  switch (formData.auth_type) {
    case 'api_key':
      baseRules.api_key = [
        { required: true, message: '请输入 API Key', trigger: 'blur' }
      ]
      break
    case 'api_key_secret':
      baseRules.api_key = [
        { required: true, message: '请输入 API Key', trigger: 'blur' }
      ]
      baseRules.api_secret = [
        { required: true, message: '请输入 API Secret', trigger: 'blur' }
      ]
      break
    case 'bearer_token':
      baseRules.bearer_token = [
        { required: true, message: '请输入 Bearer Token', trigger: 'blur' }
      ]
      break
    case 'basic_auth':
      baseRules.username = [
        { required: true, message: '请输入用户名', trigger: 'blur' }
      ]
      baseRules.password = [
        { required: true, message: '请输入密码', trigger: 'blur' }
      ]
      break
    case 'oauth2':
      baseRules['oauth2_config.client_id'] = [
        { required: true, message: '请输入 Client ID', trigger: 'blur' }
      ]
      baseRules['oauth2_config.token_url'] = [
        { required: true, message: '请输入 Token URL', trigger: 'blur' },
        { type: 'url', message: '请输入有效的 URL', trigger: 'blur' }
      ]
      break
    case 'none':
    default:
      // 无需认证，不添加额外验证规则
      break
  }

  return baseRules
}

// 计算属性，用于绑定到 el-form 的 :rules
const rules = computed(() => getDynamicRules())

// ==================== 方法 ====================

// 重置表单
const resetForm = () => {
  formData.name = ''
  formData.display_name = ''
  formData.description = ''
  formData.vendor_type = 'custom'
  formData.auth_type = 'api_key'
  formData.api_key = ''
  formData.api_secret = ''
  formData.bearer_token = ''
  formData.username = ''
  formData.password = ''
  formData.base_url = ''
  formData.endpoint = ''
  formData.timeout = 30
  formData.retry_times = 3
  formData.rate_limit = 100
  formData.config_params = {}
  formData.headers = {}
  formData.is_active = true
  formData.is_default = false
  
  oauth2Form.client_id = ''
  oauth2Form.client_secret = ''
  oauth2Form.token_url = ''
  oauth2Form.scope = ''
  
  configParamsText.value = '{}'
  headersText.value = '{}'
  selectedPreset.value = ''
}

// 处理预设选择
const handlePresetChange = (presetName: string) => {
  if (!presetName) {
    resetForm()
    return
  }
  
  const preset = PRESET_VENDORS.find(p => p.name === presetName)
  if (preset) {
    formData.name = preset.name
    formData.display_name = preset.display_name
    formData.description = preset.description
    formData.vendor_type = preset.vendor_type
    formData.auth_type = preset.auth_type
    formData.base_url = preset.base_url || ''
    
    // 应用默认配置
    if (preset.default_config) {
      Object.assign(formData, preset.default_config)
    }
  }
}

// 处理认证类型变化
const handleAuthTypeChange = (_authType: AuthType) => {
  // 清空之前的认证信息
  formData.api_key = ''
  formData.api_secret = ''
  formData.bearer_token = ''
  formData.username = ''
  formData.password = ''
  oauth2Form.client_id = ''
  oauth2Form.client_secret = ''
  oauth2Form.token_url = ''
  oauth2Form.scope = ''
  
  // 清除表单验证状态，避免之前的验证错误显示
  nextTick(() => {
    formRef.value?.clearValidate()
  })
}

// 打开注册链接
const openRegisterUrl = () => {
  if (currentPreset.value?.register_url) {
    window.open(currentPreset.value.register_url, '_blank')
  }
}

// 打开 API 文档
const openApiDocUrl = () => {
  if (currentPreset.value?.api_doc_url) {
    window.open(currentPreset.value.api_doc_url, '_blank')
  }
}

// 处理可见性变化
const handleVisibleChange = (value: boolean) => {
  emit('update:visible', value)
}

// 处理关闭
const handleClose = () => {
  emit('update:visible', false)
  formRef.value?.resetFields()
  resetForm()
}

// 解析 JSON
const parseJson = (text: string, defaultValue: any = {}): any => {
  try {
    return JSON.parse(text || '{}')
  } catch (e) {
    return defaultValue
  }
}

// 构建提交数据（用于创建新配置）
const buildSubmitData = (): VendorConfigRequest => {
  const data: VendorConfigRequest = {
    name: formData.name,
    display_name: formData.display_name,
    description: formData.description,
    vendor_type: formData.vendor_type,
    auth_type: formData.auth_type,
    api_key: formData.api_key,
    api_secret: formData.api_secret,
    bearer_token: formData.bearer_token,
    username: formData.username,
    password: formData.password,
    base_url: formData.base_url,
    endpoint: formData.endpoint,
    timeout: formData.timeout,
    retry_times: formData.retry_times,
    rate_limit: formData.rate_limit,
    config_params: parseJson(configParamsText.value),
    headers: parseJson(headersText.value),
    is_active: formData.is_active,
    is_default: formData.is_default
  }
  
  // 处理 OAuth2 配置
  if (formData.auth_type === 'oauth2') {
    (data as any).oauth2_config = { ...oauth2Form }
  }
  
  return data
}

// 测试连接
const handleTest = async () => {
  // 验证表单
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    ElMessage.warning('请填写完整的配置信息')
    return
  }
  
  testing.value = true
  try {
    const data = buildSubmitData()
    
    let result: VendorTestResponse
    
    if (props.isEdit && props.config) {
      // 测试已保存的配置
      if (props.userMode) {
        result = await vendorConfigApi.testMyVendor(props.config.id)
      } else {
        result = await vendorConfigApi.testSavedConfig(props.config.id)
      }
    } else {
      // 测试新配置
      result = await vendorConfigApi.testNewConfig({ config: data })
    }
    
    testResult.value = result
    testResultVisible.value = true
    
    if (result.success) {
      ElMessage.success('连接测试成功')
    } else {
      ElMessage.error(`连接测试失败: ${result.message}`)
    }
  } catch (error: any) {
    ElMessage.error(error.message || '测试失败')
  } finally {
    testing.value = false
  }
}

// 提交表单
const handleSubmit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  
  submitting.value = true
  try {
    // 构建提交数据（不过滤空字符串，保留所有字段）
    const data: VendorConfigRequest = {
      name: formData.name,
      display_name: formData.display_name,
      description: formData.description,
      vendor_type: formData.vendor_type,
      auth_type: formData.auth_type,
      api_key: formData.api_key,
      api_secret: formData.api_secret,
      bearer_token: formData.bearer_token,
      username: formData.username,
      password: formData.password,
      base_url: formData.base_url,
      endpoint: formData.endpoint,
      timeout: formData.timeout,
      retry_times: formData.retry_times,
      rate_limit: formData.rate_limit,
      config_params: parseJson(configParamsText.value),
      headers: parseJson(headersText.value),
      is_active: formData.is_active,
      is_default: formData.is_default
    }
    
    // 根据认证类型清理不必要的认证字段
    const cleanedData: VendorConfigRequest = { ...data }
    if (cleanedData.auth_type !== 'oauth2') {
      delete (cleanedData as any).oauth2_config
    }
    
    if (props.isEdit && props.config) {
      // 编辑模式：只提交有变化的字段
      const updateData: Partial<VendorConfigRequest> = {}
      
      // 对比原配置，只提交变化的字段
      const original = props.config
      
      if (data.display_name !== original.display_name) updateData.display_name = data.display_name
      if (data.description !== original.description) updateData.description = data.description
      if (data.auth_type !== original.auth_type) updateData.auth_type = data.auth_type
      if (data.base_url !== original.base_url) updateData.base_url = data.base_url
      if (data.endpoint !== original.endpoint) updateData.endpoint = data.endpoint
      if (data.timeout !== original.timeout) updateData.timeout = data.timeout
      if (data.retry_times !== original.retry_times) updateData.retry_times = data.retry_times
      if (data.rate_limit !== original.rate_limit) updateData.rate_limit = data.rate_limit
      if (data.is_active !== original.is_active) updateData.is_active = data.is_active
      if (data.is_default !== original.is_default) updateData.is_default = data.is_default
      
      // 认证信息：只要有值就提交（包括空字符串，表示清除）
      // 注意：后端只更新非空值，所以这里需要确保值确实变化了才提交
      if (data.api_key !== undefined && data.api_key !== (original as any).api_key) {
        updateData.api_key = data.api_key
      }
      if (data.api_secret !== undefined && data.api_secret !== (original as any).api_secret) {
        updateData.api_secret = data.api_secret
      }
      if (data.bearer_token !== undefined && data.bearer_token !== (original as any).bearer_token) {
        updateData.bearer_token = data.bearer_token
      }
      if (data.username !== undefined && data.username !== (original as any).username) {
        updateData.username = data.username
      }
      if (data.password !== undefined && data.password !== (original as any).password) {
        updateData.password = data.password
      }
      if (data.oauth2_config) updateData.oauth2_config = data.oauth2_config
      
      // JSON 字段需要序列化比较
      const newParams = JSON.stringify(data.config_params)
      const oldParams = JSON.stringify(original.config_params || {})
      if (newParams !== oldParams) updateData.config_params = data.config_params
      
      const newHeaders = JSON.stringify(data.headers)
      const oldHeaders = JSON.stringify(original.headers || {})
      if (newHeaders !== oldHeaders) updateData.headers = data.headers
      
      console.log('🚀 提交更新数据:', updateData)
      if (props.userMode) {
        await vendorConfigApi.updateMyVendor(props.config.id, updateData)
      } else {
        await vendorConfigApi.updateVendorConfig(props.config.id, updateData)
      }
      ElMessage.success('更新成功')
    } else {
      // 创建模式：发送完整数据
      console.log('🚀 提交创建数据:', cleanedData)
      if (props.userMode) {
        await vendorConfigApi.createMyVendor(cleanedData)
      } else {
        await vendorConfigApi.createVendorConfig(cleanedData)
      }
      ElMessage.success('创建成功')
    }
    
    emit('success')
    handleClose()
  } catch (error: any) {
    console.error('❌ 提交失败:', error)
    ElMessage.error(error.message || (props.isEdit ? '更新失败' : '创建失败'))
  } finally {
    submitting.value = false
  }
}

// ==================== 监听 ====================

// 监听配置变化（编辑模式）
watch(() => props.config, (newConfig) => {
  if (newConfig && props.isEdit) {
    formData.name = newConfig.name
    formData.display_name = newConfig.display_name
    formData.description = newConfig.description || ''
    formData.vendor_type = newConfig.vendor_type
    formData.auth_type = newConfig.auth_type
    formData.base_url = newConfig.base_url || ''
    formData.endpoint = newConfig.endpoint || ''
    formData.timeout = newConfig.timeout
    formData.retry_times = newConfig.retry_times
    formData.rate_limit = newConfig.rate_limit
    formData.is_active = newConfig.is_active
    formData.is_default = newConfig.is_default
    
    // 高级配置
    formData.config_params = newConfig.config_params || {}
    formData.headers = newConfig.headers || {}
    configParamsText.value = JSON.stringify(newConfig.config_params || {}, null, 2)
    headersText.value = JSON.stringify(newConfig.headers || {}, null, 2)
  } else {
    resetForm()
  }
}, { immediate: true })
</script>

<style lang="scss" scoped>
.register-guide {
  p {
    margin: 0 0 12px 0;
    font-size: 14px;
    color: var(--el-text-color-regular);
  }
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  margin-top: 4px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.test-result {
  text-align: center;
  padding: 20px;

  .result-icon {
    color: #F56C6C;
    margin-bottom: 16px;

    &.success {
      color: #67C23A;
    }
  }

  .result-message {
    font-size: 16px;
    color: var(--el-text-color-primary);
    margin-bottom: 12px;
  }

  .result-details {
    font-size: 14px;
    color: var(--el-text-color-regular);

    p {
      margin: 4px 0;
    }
  }
}
</style>
