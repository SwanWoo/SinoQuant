<template>
  <div class="vendor-config-management">
    <!-- 页面标题 -->
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">
          <el-icon><Shop /></el-icon>
          厂商配置管理
        </h1>
        <p class="page-description">
          统一管理各类服务厂商配置（LLM、数据源、存储等）
        </p>
      </div>
      <div class="header-right">
        <el-button @click="handleRefresh" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          v-if="activeTab === 'global'"
          type="success"
          @click="handleExport"
        >
          <el-icon><Download /></el-icon>
          导出
        </el-button>
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          {{ activeTab === 'my' ? '添加我的配置' : '添加厂商' }}
        </el-button>
      </div>
    </div>

    <!-- Tab 切换 -->
    <el-tabs v-model="activeTab" class="view-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="我的配置" name="my" />
      <el-tab-pane v-if="authStore.isAdmin" label="全局配置" name="global" />
    </el-tabs>

    <!-- 筛选栏 -->
    <el-card class="filter-card" shadow="never">
      <el-form :model="filterForm" inline>
        <el-form-item label="厂商类型">
          <el-select
            v-model="filterForm.vendor_type"
            placeholder="全部类型"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="(info, type) in VENDOR_TYPES"
              :key="type"
              :label="info.label"
              :value="type"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="认证类型">
          <el-select
            v-model="filterForm.auth_type"
            placeholder="全部类型"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="(info, type) in AUTH_TYPES"
              :key="type"
              :label="info.label"
              :value="type"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="状态">
          <el-select
            v-model="filterForm.is_active"
            placeholder="全部状态"
            clearable
            style="width: 120px"
          >
            <el-option label="启用" :value="true" />
            <el-option label="禁用" :value="false" />
          </el-select>
        </el-form-item>

        <el-form-item label="关键词">
          <el-input
            v-model="filterForm.keyword"
            placeholder="名称/描述"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSearch">
            <el-icon><Search /></el-icon>
            搜索
          </el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 数据表格 -->
    <el-card class="table-card" shadow="never" v-loading="loading">
      <el-table
        :data="filteredConfigs"
        style="width: 100%"
        row-key="id"
        @sort-change="handleSortChange"
      >
        <!-- 厂商信息 -->
        <el-table-column label="厂商" min-width="200" sortable="custom">
          <template #default="{ row }">
            <div class="vendor-info">
              <div class="vendor-name">
                <el-tag
                  :color="getVendorTypeColor(row.vendor_type)"
                  effect="dark"
                  size="small"
                  class="vendor-type-tag"
                >
                  {{ getVendorTypeLabel(row.vendor_type) }}
                </el-tag>
                <span class="display-name">{{ row.display_name }}</span>
                <el-tag
                  v-if="row.is_default"
                  type="warning"
                  size="small"
                  effect="dark"
                >
                  默认
                </el-tag>
              </div>
              <div class="vendor-id">{{ row.name }}</div>
              <div v-if="row.description" class="vendor-desc">{{ row.description }}</div>
            </div>
          </template>
        </el-table-column>

        <!-- 配置来源 (仅我的配置 Tab 显示) -->
        <el-table-column v-if="activeTab === 'my'" label="来源" width="120" align="center">
          <template #default="{ row }">
            <el-tag
              :type="row.is_user_config !== false ? 'success' : 'info'"
              size="small"
              effect="plain"
            >
              {{ row.is_user_config !== false ? '我的 Key' : '全局默认' }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 认证信息 -->
        <el-table-column label="认证" width="150">
          <template #default="{ row }">
            <div class="auth-info">
              <el-tag size="small" type="info">
                {{ getAuthTypeLabel(row.auth_type) }}
              </el-tag>
              <div v-if="row.api_key_masked" class="masked-key">
                {{ row.api_key_masked }}
              </div>
            </div>
          </template>
        </el-table-column>

        <!-- 连接配置 -->
        <el-table-column label="连接配置" width="180">
          <template #default="{ row }">
            <div class="connection-info">
              <div v-if="row.base_url" class="info-item" :title="row.base_url">
                <el-icon><Link /></el-icon>
                <span class="truncate">{{ row.base_url }}</span>
              </div>
              <div class="info-item">
                <el-icon><Timer /></el-icon>
                <span>超时: {{ row.timeout }}s</span>
              </div>
              <div class="info-item">
                <el-icon><RefreshRight /></el-icon>
                <span>重试: {{ row.retry_times }}次</span>
              </div>
            </div>
          </template>
        </el-table-column>

        <!-- 状态 -->
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <!-- 我的配置 Tab 中全局配置不允许切换状态 -->
            <el-switch
              v-if="activeTab === 'global' || row.is_user_config !== false"
              v-model="row.is_active"
              @change="(val: any) => handleToggle(row, val as boolean)"
              :loading="toggleLoading[row.id]"
            />
            <el-tag v-else :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 更新时间 -->
        <el-table-column
          label="更新时间"
          width="160"
          prop="updated_at"
          sortable="custom"
        >
          <template #default="{ row }">
            {{ formatDate(row.updated_at) }}
          </template>
        </el-table-column>

        <!-- 操作 -->
        <el-table-column label="操作" :width="activeTab === 'my' ? 280 : 280" fixed="right">
          <template #default="{ row }">
            <!-- 我的配置 Tab -->
            <template v-if="activeTab === 'my' && row.is_user_config === false">
              <!-- 全局配置：只读，显示覆盖按钮 -->
              <el-button
                size="small"
                type="warning"
                @click="handleOverride(row)"
              >
                <el-icon><Edit /></el-icon>
                覆盖
              </el-button>
              <el-button
                size="small"
                type="primary"
                @click="handleTest(row)"
                :loading="testLoading[row.id]"
              >
                <el-icon><Connection /></el-icon>
                测试
              </el-button>
            </template>
            <template v-else>
              <!-- 用户配置 或 全局配置 Tab -->
              <el-button size="small" @click="handleEdit(row)">
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button
                size="small"
                type="primary"
                @click="handleTest(row)"
                :loading="testLoading[row.id]"
              >
                <el-icon><Connection /></el-icon>
                测试
              </el-button>
              <el-dropdown @command="(cmd: string) => handleCommand(cmd, row)">
                <el-button size="small">
                  更多<el-icon class="el-icon--right"><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="setDefault" :disabled="row.is_default">
                      <el-icon><Star /></el-icon>设为默认
                    </el-dropdown-item>
                    <el-dropdown-item command="copy">
                      <el-icon><CopyDocument /></el-icon>复制配置
                    </el-dropdown-item>
                    <el-dropdown-item command="delete" divided>
                      <el-icon><Delete /></el-icon>删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <!-- 空状态 -->
      <el-empty v-if="filteredConfigs.length === 0 && !loading" description="暂无厂商配置">
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          {{ activeTab === 'my' ? '添加我的配置' : '添加第一个厂商' }}
        </el-button>
      </el-empty>

      <!-- 分页 -->
      <div class="pagination-wrapper" v-if="filteredConfigs.length > 0">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <!-- 批量导入对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="批量导入厂商配置"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-tabs v-model="importActiveTab">
        <el-tab-pane label="上传文件" name="file">
          <el-upload
            ref="uploadRef"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleImportFileChange"
            :limit="1"
            accept=".json"
          >
            <el-icon class="el-icon--upload"><Upload /></el-icon>
            <div class="el-upload__text">
              拖拽文件到此处或 <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 JSON 格式，可导出后编辑再导入
              </div>
            </template>
          </el-upload>
        </el-tab-pane>
        <el-tab-pane label="粘贴 JSON" name="paste">
          <el-input
            v-model="importJsonText"
            type="textarea"
            :rows="10"
            placeholder="粘贴 JSON 格式的配置数据"
          />
        </el-tab-pane>
      </el-tabs>

      <el-form :model="importOptions" label-width="120px" style="margin-top: 20px">
        <el-form-item label="导入选项">
          <el-radio-group v-model="importOptions.mode">
            <el-radio label="skip">跳过已存在</el-radio>
            <el-radio label="update">更新已存在</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleImport" :loading="importLoading">
          开始导入
        </el-button>
      </template>
    </el-dialog>

    <!-- 测试结果对话框 -->
    <el-dialog
      v-model="testResultVisible"
      title="连接测试结果"
      width="500px"
    >
      <div v-if="testResult" class="test-result">
        <div class="result-header" :class="{ success: testResult.success }">
          <el-icon :size="48">
            <CircleCheck v-if="testResult.success" color="#67C23A" />
            <CircleClose v-else color="#F56C6C" />
          </el-icon>
          <h3>{{ testResult.success ? '连接成功' : '连接失败' }}</h3>
        </div>
        <el-divider />
        <div class="result-body">
          <p><strong>消息:</strong> {{ testResult.message }}</p>
          <template v-if="testResult.details">
            <p v-if="testResult.details.response_time_ms">
              <strong>响应时间:</strong> {{ testResult.details.response_time_ms }}ms
            </p>
            <p v-if="testResult.details.status_code">
              <strong>状态码:</strong> {{ testResult.details.status_code }}
            </p>
            <p v-if="testResult.details.error_details">
              <strong>错误详情:</strong> {{ testResult.details.error_details }}</p>
          </template>
        </div>
      </div>
    </el-dialog>

    <!-- 厂商配置对话框 -->
    <VendorConfigDialog
      v-model:visible="dialogVisible"
      :config="currentConfig"
      :is-edit="isEdit"
      :user-mode="activeTab === 'my'"
      @success="handleDialogSuccess"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import {
  Shop,
  Refresh,
  Download,
  Plus,
  Search,
  Link,
  Timer,
  RefreshRight,
  Edit,
  Connection,
  ArrowDown,
  Star,
  CopyDocument,
  Delete,
  Upload,
  CircleCheck,
  CircleClose
} from '@element-plus/icons-vue'

import {
  vendorConfigApi,
  VENDOR_TYPES,
  AUTH_TYPES,
  getVendorTypeLabel,
  getVendorTypeColor,
  getAuthTypeLabel,
  type VendorConfigResponse,
  type VendorTestResponse
} from '@/api/vendorConfig'

import { useAuthStore } from '@/stores/auth'

import VendorConfigDialog from './components/VendorConfigDialog.vue'

// ==================== 响应式数据 ====================

const authStore = useAuthStore()
const activeTab = ref<'my' | 'global'>('my')
const loading = ref(false)
const configs = ref<VendorConfigResponse[]>([])

// 筛选表单
const filterForm = reactive({
  vendor_type: '' as string | undefined,
  auth_type: '' as string | undefined,
  is_active: undefined as boolean | undefined,
  keyword: ''
})

// 分页
const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0
})

// 排序
const sortConfig = reactive({
  prop: 'updated_at',
  order: 'descending'
})

// 操作状态
const toggleLoading = ref<Record<string, boolean>>({})
const testLoading = ref<Record<string, boolean>>({})

// 导入相关
const importDialogVisible = ref(false)
const importActiveTab = ref('file')
const uploadRef = ref<UploadInstance>()
const importFile = ref<File | null>(null)
const importJsonText = ref('')
const importOptions = reactive({
  mode: 'skip' as 'skip' | 'update'
})
const importLoading = ref(false)

// 测试结果
const testResultVisible = ref(false)
const testResult = ref<VendorTestResponse | null>(null)

// 对话框
const dialogVisible = ref(false)
const currentConfig = ref<VendorConfigResponse | null>(null)
const isEdit = ref(false)

// ==================== 计算属性 ====================

// 当前是否为用户配置模式
const isUserMode = computed(() => activeTab.value === 'my')

// 筛选后的配置列表
const filteredConfigs = computed(() => {
  let result = [...configs.value]

  // 类型筛选
  if (filterForm.vendor_type) {
    result = result.filter(c => c.vendor_type === filterForm.vendor_type)
  }

  if (filterForm.auth_type) {
    result = result.filter(c => c.auth_type === filterForm.auth_type)
  }

  if (filterForm.is_active !== undefined) {
    result = result.filter(c => c.is_active === filterForm.is_active)
  }

  if (filterForm.keyword) {
    const keyword = filterForm.keyword.toLowerCase()
    result = result.filter(c =>
      c.name.toLowerCase().includes(keyword) ||
      c.display_name.toLowerCase().includes(keyword) ||
      (c.description && c.description.toLowerCase().includes(keyword))
    )
  }

  // 排序
  if (sortConfig.prop) {
    result.sort((a: any, b: any) => {
      let aVal = a[sortConfig.prop]
      let bVal = b[sortConfig.prop]

      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase()
        bVal = bVal.toLowerCase()
      }

      if (aVal < bVal) return sortConfig.order === 'ascending' ? -1 : 1
      if (aVal > bVal) return sortConfig.order === 'ascending' ? 1 : -1
      return 0
    })
  }

  // 更新总数（前端分页模拟）
  pagination.total = result.length

  // 分页
  const start = (pagination.page - 1) * pagination.pageSize
  const end = start + pagination.pageSize

  return result.slice(start, end)
})

// ==================== 方法 ====================

// 加载数据
const loadConfigs = async () => {
  loading.value = true
  try {
    let data: any
    if (isUserMode.value) {
      data = await vendorConfigApi.getMyVendors()
    } else {
      data = await vendorConfigApi.getVendorConfigs()
    }
    configs.value = data
    pagination.total = data.length
  } catch (error) {
    console.error('加载厂商配置失败:', error)
    ElMessage.error('加载厂商配置失败')
  } finally {
    loading.value = false
  }
}

// Tab 切换
const handleTabChange = (_tab: string | number) => {
  // 重置筛选和分页
  filterForm.vendor_type = ''
  filterForm.auth_type = ''
  filterForm.is_active = undefined
  filterForm.keyword = ''
  pagination.page = 1
  loadConfigs()
}

// 搜索
const handleSearch = () => {
  pagination.page = 1
}

// 重置筛选
const handleReset = () => {
  filterForm.vendor_type = ''
  filterForm.auth_type = ''
  filterForm.is_active = undefined
  filterForm.keyword = ''
  pagination.page = 1
}

// 排序
const handleSortChange = ({ prop, order }: { prop: string; order: string }) => {
  sortConfig.prop = prop
  sortConfig.order = order || 'descending'
}

// 分页
const handleSizeChange = (size: number) => {
  pagination.pageSize = size
  pagination.page = 1
}

const handlePageChange = (page: number) => {
  pagination.page = page
}

// 刷新
const handleRefresh = () => {
  loadConfigs()
  ElMessage.success('刷新成功')
}

// 添加
const handleAdd = () => {
  currentConfig.value = null
  isEdit.value = false
  dialogVisible.value = true
}

// 编辑
const handleEdit = (row: VendorConfigResponse) => {
  currentConfig.value = row
  isEdit.value = true
  dialogVisible.value = true
}

// 覆盖全局配置（从"我的配置" Tab 中对全局配置创建用户覆盖）
const handleOverride = (row: VendorConfigResponse) => {
  // 用全局配置的数据预填充对话框，但作为新建用户配置
  currentConfig.value = row
  isEdit.value = false
  dialogVisible.value = true
}

// 切换状态
const handleToggle = async (row: VendorConfigResponse, isActive: boolean) => {
  toggleLoading.value[row.id] = true
  try {
    if (isUserMode.value) {
      await vendorConfigApi.updateMyVendor(row.id, { is_active: isActive })
    } else {
      await vendorConfigApi.toggleVendorConfig(row.id, isActive)
    }
    row.is_active = isActive
    ElMessage.success(`已${isActive ? '启用' : '禁用'} ${row.display_name}`)
  } catch (error) {
    row.is_active = !isActive
    ElMessage.error('操作失败')
  } finally {
    toggleLoading.value[row.id] = false
  }
}

// 测试连接
const handleTest = async (row: VendorConfigResponse) => {
  testLoading.value[row.id] = true
  try {
    let result: VendorTestResponse
    if (isUserMode.value) {
      result = await vendorConfigApi.testMyVendor(row.id)
    } else {
      result = await vendorConfigApi.testSavedConfig(row.id)
    }
    testResult.value = result
    testResultVisible.value = true

    if (result.success) {
      ElMessage.success(`${row.display_name} 连接测试成功`)
    } else {
      ElMessage.error(`${row.display_name} 连接测试失败`)
    }
  } catch (error) {
    ElMessage.error('测试失败')
  } finally {
    testLoading.value[row.id] = false
  }
}

// 更多操作
const handleCommand = async (command: string, row: VendorConfigResponse) => {
  switch (command) {
    case 'setDefault':
      await handleSetDefault(row)
      break
    case 'copy':
      await handleCopy(row)
      break
    case 'delete':
      await handleDelete(row)
      break
  }
}

// 设为默认
const handleSetDefault = async (row: VendorConfigResponse) => {
  try {
    if (isUserMode.value) {
      await vendorConfigApi.updateMyVendor(row.id, { is_default: true })
    } else {
      await vendorConfigApi.setDefaultVendor(row.id)
    }

    // 更新本地数据
    configs.value.forEach(c => {
      if (c.vendor_type === row.vendor_type) {
        c.is_default = c.id === row.id
      }
    })

    ElMessage.success(`${row.display_name} 已设为默认`)
  } catch (error) {
    ElMessage.error('设置失败')
  }
}

// 复制配置
const handleCopy = async (row: VendorConfigResponse) => {
  try {
    // 构造新配置
    const newConfig = {
      ...row,
      name: `${row.name}_copy`,
      display_name: `${row.display_name} (复制)`,
      is_default: false
    }

    // 删除 id 和时间戳字段
    delete (newConfig as any).id
    delete (newConfig as any).created_at
    delete (newConfig as any).updated_at

    if (isUserMode.value) {
      await vendorConfigApi.createMyVendor(newConfig as any)
    } else {
      await vendorConfigApi.createVendorConfig(newConfig as any)
    }

    ElMessage.success('复制成功')
    loadConfigs()
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

// 删除
const handleDelete = async (row: VendorConfigResponse) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除厂商 "${row.display_name}" 吗？此操作不可恢复。`,
      '删除确认',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger'
      }
    )

    if (isUserMode.value) {
      await vendorConfigApi.deleteMyVendor(row.id)
    } else {
      await vendorConfigApi.deleteVendorConfig(row.id)
    }

    // 从本地数据中移除
    const index = configs.value.findIndex(c => c.id === row.id)
    if (index > -1) {
      configs.value.splice(index, 1)
    }

    ElMessage.success('删除成功')
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '删除失败')
    }
  }
}

// 导出
const handleExport = async () => {
  try {
    await vendorConfigApi.exportConfigs({
      vendor_type: filterForm.vendor_type as any,
      include_sensitive: false
    })
    ElMessage.success('导出成功')
  } catch (error) {
    ElMessage.error('导出失败')
  }
}

// 导入文件选择
const handleImportFileChange = (uploadFile: UploadFile) => {
  if (uploadFile.raw) {
    importFile.value = uploadFile.raw
  }
}

// 导入
const handleImport = async () => {
  let importConfigs: any[] = []

  try {
    if (importActiveTab.value === 'file') {
      if (!importFile.value) {
        ElMessage.warning('请选择文件')
        return
      }
      const text = await importFile.value.text()
      importConfigs = JSON.parse(text)
    } else {
      if (!importJsonText.value.trim()) {
        ElMessage.warning('请输入 JSON 数据')
        return
      }
      importConfigs = JSON.parse(importJsonText.value)
    }

    if (!Array.isArray(importConfigs)) {
      ElMessage.error('数据格式错误，应为数组')
      return
    }
  } catch (error) {
    ElMessage.error('解析 JSON 失败')
    return
  }

  importLoading.value = true
  try {
    const result = await vendorConfigApi.bulkImport({
      configs: importConfigs,
      skip_existing: importOptions.mode === 'skip',
      update_existing: importOptions.mode === 'update'
    })

    if (result.success) {
      ElMessage.success(
        `导入成功: 创建 ${result.summary.created} 个, ` +
        `更新 ${result.summary.updated} 个, ` +
        `跳过 ${result.summary.skipped} 个`
      )
      importDialogVisible.value = false
      loadConfigs()
    } else {
      ElMessage.error(result.message)
    }
  } catch (error) {
    ElMessage.error('导入失败')
  } finally {
    importLoading.value = false
  }
}

// 对话框成功回调
const handleDialogSuccess = () => {
  loadConfigs()
}

// 格式化日期
const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadConfigs()
})
</script>

<style lang="scss" scoped>
.vendor-config-management {
  padding: 20px;

  .view-tabs {
    margin-bottom: 0;
  }

  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;

    .header-left {
      .page-title {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 24px;
        font-weight: 600;
        color: var(--el-text-color-primary);
        margin: 0 0 8px 0;
      }

      .page-description {
        color: var(--el-text-color-regular);
        margin: 0;
      }
    }

    .header-right {
      display: flex;
      gap: 8px;
    }
  }

  .filter-card {
    margin-bottom: 20px;
  }

  .table-card {
    .vendor-info {
      .vendor-name {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;

        .vendor-type-tag {
          font-size: 10px;
          padding: 0 4px;
          height: 18px;
          line-height: 18px;
        }

        .display-name {
          font-weight: 500;
          color: var(--el-text-color-primary);
        }
      }

      .vendor-id {
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }

      .vendor-desc {
        font-size: 12px;
        color: var(--el-text-color-placeholder);
        margin-top: 4px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        max-width: 300px;
      }
    }

    .auth-info {
      .masked-key {
        font-size: 12px;
        color: var(--el-text-color-secondary);
        margin-top: 4px;
        font-family: monospace;
      }
    }

    .connection-info {
      .info-item {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 12px;
        color: var(--el-text-color-regular);
        margin-bottom: 4px;

        .truncate {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          max-width: 150px;
        }
      }
    }

    .pagination-wrapper {
      margin-top: 20px;
      display: flex;
      justify-content: flex-end;
    }
  }

  .test-result {
    .result-header {
      text-align: center;
      padding: 20px;

      &.success {
        color: #67C23A;
      }

      h3 {
        margin-top: 12px;
        font-size: 18px;
      }
    }

    .result-body {
      p {
        margin: 8px 0;
        color: var(--el-text-color-regular);

        strong {
          color: var(--el-text-color-primary);
        }
      }
    }
  }
}
</style>
