<template>
  <div class="operation-logs-page">
    <!-- 筛选栏 -->
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filterForm" @submit.prevent="handleSearch">
        <el-form-item label="关键词">
          <el-input v-model="filterForm.keyword" placeholder="操作描述或用户名" clearable style="width: 180px" @clear="handleSearch" />
        </el-form-item>
        <el-form-item label="操作类型">
          <el-select v-model="filterForm.action_type" clearable placeholder="全部" style="width: 140px">
            <el-option v-for="(label, value) in actionTypeMap" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filterForm.success" clearable placeholder="全部" style="width: 100px">
            <el-option label="成功" :value="true" />
            <el-option label="失败" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="resetFilter">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 日志表格 -->
    <el-card shadow="never" style="margin-top: 16px">
      <el-table v-loading="loading" :data="logs" stripe size="small">
        <el-table-column prop="username" label="用户" width="120" show-overflow-tooltip />
        <el-table-column prop="action_type" label="类型" width="130">
          <template #default="{ row }">
            {{ actionTypeMap[row.action_type] || row.action_type }}
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作" min-width="200" show-overflow-tooltip />
        <el-table-column prop="success" label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="duration_ms" label="耗时(ms)" width="100" align="right">
          <template #default="{ row }">
            {{ row.duration_ms ?? '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="ip_address" label="IP" width="130" show-overflow-tooltip />
        <el-table-column prop="timestamp" label="时间" min-width="160">
          <template #default="{ row }">{{ formatDate(row.timestamp) }}</template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @size-change="handleSizeChange"
          @current-change="loadLogs"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { adminApi } from '@/api/admin'

const actionTypeMap: Record<string, string> = {
  stock_analysis: '股票分析',
  config_management: '配置管理',
  cache_operation: '缓存操作',
  data_import: '数据导入',
  data_export: '数据导出',
  system_settings: '系统设置',
  user_login: '用户登录',
  user_logout: '用户登出',
  user_management: '用户管理',
  database_operation: '数据库操作',
  screening: '股票筛选',
  report_generation: '报告生成',
}

const loading = ref(false)
const logs = ref<any[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const filterForm = reactive({
  keyword: '',
  action_type: '' as string,
  success: undefined as boolean | undefined,
})

const formatDate = (dt: string) => {
  if (!dt) return '-'
  return dt.replace('T', ' ').slice(0, 19)
}

const loadLogs = async () => {
  loading.value = true
  try {
    const res = await adminApi.getLogs({
      page: currentPage.value,
      page_size: pageSize.value,
      keyword: filterForm.keyword || undefined,
      action_type: filterForm.action_type || undefined,
      success: filterForm.success,
    })
    if (res.success) {
      logs.value = res.data.logs
      total.value = res.data.total
    }
  } catch (e) {
    console.error('加载日志失败:', e)
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadLogs()
}

const handleSizeChange = () => {
  currentPage.value = 1
  loadLogs()
}

const resetFilter = () => {
  filterForm.keyword = ''
  filterForm.action_type = ''
  filterForm.success = undefined
  currentPage.value = 1
  loadLogs()
}

onMounted(() => {
  loadLogs()
})
</script>

<style lang="scss" scoped>
.filter-card {
  :deep(.el-card__body) {
    padding-bottom: 2px;
  }
}
.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
