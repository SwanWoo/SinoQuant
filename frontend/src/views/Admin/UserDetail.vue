<template>
  <div class="user-detail-page">
    <!-- 用户基本信息 -->
    <el-card v-loading="loading" shadow="never">
      <template #header>
        <div class="card-header">
          <div class="card-header-left">
            <el-button type="primary" link @click="$router.push('/admin/users')">
              <el-icon><ArrowLeft /></el-icon> 返回
            </el-button>
            <span style="font-size: 16px; font-weight: 600">{{ user?.username }}</span>
            <el-tag v-if="user?.is_admin" type="danger" size="small">管理员</el-tag>
            <el-tag :type="user?.is_active ? 'success' : 'info'" size="small">
              {{ user?.is_active ? '活跃' : '禁用' }}
            </el-tag>
          </div>
          <div class="card-header-right">
            <el-button size="small" @click="showResetPassword = true">重置密码</el-button>
            <el-button
              v-if="user?.is_active"
              size="small" type="danger"
              @click="handleToggleActive(false)"
            >停用</el-button>
            <el-button
              v-else
              size="small" type="success"
              @click="handleToggleActive(true)"
            >激活</el-button>
          </div>
        </div>
      </template>

      <el-descriptions :column="3" border>
        <el-descriptions-item label="用户名">{{ user?.username }}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{ user?.email }}</el-descriptions-item>
        <el-descriptions-item label="ID">{{ user?.id }}</el-descriptions-item>
        <el-descriptions-item label="注册时间">{{ formatDate(user?.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="最后登录">{{ formatDate(user?.last_login) }}</el-descriptions-item>
        <el-descriptions-item label="每日配额">{{ user?.daily_quota }}</el-descriptions-item>
        <el-descriptions-item label="并发限制">{{ user?.concurrent_limit }}</el-descriptions-item>
        <el-descriptions-item label="邮箱验证">
          <el-tag :type="user?.is_verified ? 'success' : 'info'" size="small">
            {{ user?.is_verified ? '已验证' : '未验证' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="总分析数">{{ user?.total_analyses }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- Tabs: 用户数据 -->
    <el-card shadow="never" style="margin-top: 16px">
      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <el-tab-pane label="分析记录" name="analyses">
          <el-table v-loading="tabLoading" :data="analyses" size="small" stripe>
            <el-table-column prop="symbol" label="股票代码" width="100" />
            <el-table-column prop="stock_name" label="股票名称" width="120" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="completed_at" label="完成时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.completed_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <template v-if="row.status === 'pending' || row.status === 'processing' || row.status === 'running'">
                  <el-popconfirm title="确认取消该任务？" @confirm="handleCancelAnalysis(row)">
                    <template #reference>
                      <el-button type="warning" link size="small">取消</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="确认将任务标记为失败？" @confirm="handleMarkAnalysisFailed(row)">
                    <template #reference>
                      <el-button type="danger" link size="small">标记失败</el-button>
                    </template>
                  </el-popconfirm>
                </template>
                <el-popconfirm title="确认删除该分析记录及其关联报告？" @confirm="handleDeleteAnalysis(row)">
                  <template #reference>
                    <el-button type="danger" link size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination-wrapper" v-if="analysesTotal > 20">
            <el-pagination
              v-model:current-page="analysesPage"
              :page-size="20"
              :total="analysesTotal"
              layout="prev, pager, next"
              @current-change="loadAnalyses"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="分析报告" name="reports">
          <el-table v-loading="tabLoading" :data="reports" size="small" stripe>
            <el-table-column prop="stock_symbol" label="股票代码" width="100" />
            <el-table-column prop="stock_name" label="股票名称" width="120" show-overflow-tooltip />
            <el-table-column prop="recommendation" label="建议" width="120" show-overflow-tooltip />
            <el-table-column prop="confidence_score" label="置信度" width="80" align="center" />
            <el-table-column prop="created_at" label="创建时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <el-popconfirm title="确认删除该报告？" @confirm="handleDeleteReport(row)">
                  <template #reference>
                    <el-button type="danger" link size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination-wrapper" v-if="reportsTotal > 20">
            <el-pagination
              v-model:current-page="reportsPage"
              :page-size="20"
              :total="reportsTotal"
              layout="prev, pager, next"
              @current-change="loadReports"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="自选股" name="favorites">
          <el-table v-loading="tabLoading" :data="favorites" size="small" stripe>
            <el-table-column prop="stock_code" label="代码" width="100" />
            <el-table-column prop="stock_name" label="名称" width="120" />
            <el-table-column prop="market" label="市场" width="80" />
            <el-table-column prop="added_at" label="添加时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.added_at) }}</template>
            </el-table-column>
            <el-table-column prop="notes" label="备注" min-width="150" show-overflow-tooltip />
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <el-popconfirm title="确认删除该自选股？" @confirm="handleDeleteFavorite(row)">
                  <template #reference>
                    <el-button type="danger" link size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="操作日志" name="logs">
          <el-table v-loading="tabLoading" :data="logs" size="small" stripe>
            <el-table-column prop="action_type" label="类型" width="120" />
            <el-table-column prop="action" label="操作" min-width="200" show-overflow-tooltip />
            <el-table-column prop="success" label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="timestamp" label="时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.timestamp) }}</template>
            </el-table-column>
          </el-table>
          <div class="pagination-wrapper" v-if="logsTotal > 20">
            <el-pagination
              v-model:current-page="logsPage"
              :page-size="20"
              :total="logsTotal"
              layout="prev, pager, next"
              @current-change="loadLogs"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="统计数据" name="stats">
          <div v-loading="tabLoading">
            <el-row v-if="stats" :gutter="20">
              <el-col :xs="12" :sm="6">
                <el-statistic title="总分析数" :value="stats.total_analyses" />
              </el-col>
              <el-col :xs="12" :sm="6">
                <el-statistic title="成功" :value="stats.successful_analyses" />
              </el-col>
              <el-col :xs="12" :sm="6">
                <el-statistic title="失败" :value="stats.failed_analyses" />
              </el-col>
              <el-col :xs="12" :sm="6">
                <el-statistic title="Token消耗" :value="stats.total_tokens_used" />
              </el-col>
              <el-col :xs="12" :sm="6" style="margin-top: 16px">
                <el-statistic title="近7天分析" :value="stats.recent_7days_analyses" />
              </el-col>
              <el-col :xs="12" :sm="6" style="margin-top: 16px">
                <el-statistic title="每日配额" :value="stats.daily_quota" />
              </el-col>
              <el-col :xs="12" :sm="6" style="margin-top: 16px">
                <el-statistic title="并发限制" :value="stats.concurrent_limit" />
              </el-col>
            </el-row>
            <el-descriptions v-if="stats?.status_breakdown" :column="3" border style="margin-top: 20px" title="分析状态分布">
              <el-descriptions-item
                v-for="(count, status) in stats.status_breakdown"
                :key="status"
                :label="String(status)"
              >
                {{ count }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="showResetPassword" title="重置密码" width="400px">
      <el-form label-width="100px">
        <el-form-item label="用户名">
          <el-input :model-value="user?.username" disabled />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" show-password placeholder="至少6位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showResetPassword = false">取消</el-button>
        <el-button type="primary" @click="handleResetPassword" :loading="resetLoading">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { adminApi, type AdminUser } from '@/api/admin'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'

const route = useRoute()
const userId = computed(() => route.params.id as string)

const loading = ref(false)
const tabLoading = ref(false)
const user = ref<AdminUser | null>(null)
const activeTab = ref('analyses')

const analyses = ref<any[]>([])
const analysesTotal = ref(0)
const analysesPage = ref(1)

const reports = ref<any[]>([])
const reportsTotal = ref(0)
const reportsPage = ref(1)

const favorites = ref<any[]>([])

const logs = ref<any[]>([])
const logsTotal = ref(0)
const logsPage = ref(1)

const stats = ref<any>(null)

const showResetPassword = ref(false)
const newPassword = ref('')
const resetLoading = ref(false)

const formatDate = (dt: string) => {
  if (!dt) return '-'
  return dt.replace('T', ' ').slice(0, 19)
}

const statusType = (status: string) => {
  const map: Record<string, string> = {
    completed: 'success',
    processing: 'warning',
    pending: 'info',
    failed: 'danger',
    cancelled: 'info',
    running: 'warning',
  }
  return map[status] || 'info'
}

const statusLabel = (status: string) => {
  const map: Record<string, string> = {
    completed: '已完成',
    processing: '处理中',
    pending: '等待中',
    failed: '失败',
    cancelled: '已取消',
    running: '运行中',
  }
  return map[status] || status
}

const loadUser = async () => {
  loading.value = true
  try {
    const res = await adminApi.getUserDetail(userId.value)
    if (res.success) {
      user.value = res.data as AdminUser
    }
  } catch (e) {
    console.error('加载用户详情失败:', e)
  } finally {
    loading.value = false
  }
}

const loadAnalyses = async () => {
  tabLoading.value = true
  try {
    const res = await adminApi.getUserAnalyses(userId.value, { page: analysesPage.value, page_size: 20 })
    if (res.success) {
      analyses.value = res.data.analyses
      analysesTotal.value = res.data.total
    }
  } catch (e) {
    console.error('加载分析记录失败:', e)
  } finally {
    tabLoading.value = false
  }
}

const loadReports = async () => {
  tabLoading.value = true
  try {
    const res = await adminApi.getUserReports(userId.value, { page: reportsPage.value, page_size: 20 })
    if (res.success) {
      reports.value = res.data.reports
      reportsTotal.value = res.data.total
    }
  } catch (e) {
    console.error('加载报告失败:', e)
  } finally {
    tabLoading.value = false
  }
}

const loadFavorites = async () => {
  tabLoading.value = true
  try {
    const res = await adminApi.getUserFavorites(userId.value)
    if (res.success) {
      favorites.value = res.data.favorites
    }
  } catch (e) {
    console.error('加载自选股失败:', e)
  } finally {
    tabLoading.value = false
  }
}

const loadLogs = async () => {
  tabLoading.value = true
  try {
    const res = await adminApi.getUserLogs(userId.value, { page: logsPage.value, page_size: 20 })
    if (res.success) {
      logs.value = res.data.logs
      logsTotal.value = res.data.total
    }
  } catch (e) {
    console.error('加载日志失败:', e)
  } finally {
    tabLoading.value = false
  }
}

const loadStats = async () => {
  tabLoading.value = true
  try {
    const res = await adminApi.getUserStats(userId.value)
    if (res.success) {
      stats.value = res.data
    }
  } catch (e) {
    console.error('加载统计失败:', e)
  } finally {
    tabLoading.value = false
  }
}

const handleTabChange = (tab: string) => {
  switch (tab) {
    case 'analyses': loadAnalyses(); break
    case 'reports': loadReports(); break
    case 'favorites': loadFavorites(); break
    case 'logs': loadLogs(); break
    case 'stats': loadStats(); break
  }
}

const handleToggleActive = async (active: boolean) => {
  if (!user.value) return
  try {
    const action = active ? '激活' : '停用'
    await ElMessageBox.confirm(`确认${action}用户 "${user.value.username}"？`, `${action}用户`, { type: 'warning' })
    const res = active
      ? await adminApi.activateUser(userId.value)
      : await adminApi.deactivateUser(userId.value)
    if (res.success) {
      ElMessage.success(res.message)
      loadUser()
    }
  } catch {}
}

const handleResetPassword = async () => {
  if (!newPassword.value || newPassword.value.length < 6) {
    ElMessage.warning('密码至少6位')
    return
  }
  resetLoading.value = true
  try {
    const res = await adminApi.resetPassword(userId.value, newPassword.value)
    if (res.success) {
      ElMessage.success(res.message)
      showResetPassword.value = false
      newPassword.value = ''
    }
  } catch (e) {
    console.error('重置密码失败:', e)
  } finally {
    resetLoading.value = false
  }
}

const handleDeleteAnalysis = async (row: any) => {
  try {
    const taskId = row.task_id || row.id
    const res = await adminApi.deleteUserAnalysis(userId.value, taskId)
    if (res.success) {
      ElMessage.success(res.message)
      loadAnalyses()
    }
  } catch (e) {
    console.error('删除分析记录失败:', e)
  }
}

const handleCancelAnalysis = async (row: any) => {
  try {
    const taskId = row.task_id || row.id
    const res = await adminApi.cancelUserAnalysis(userId.value, taskId)
    if (res.success) {
      ElMessage.success(res.message)
      loadAnalyses()
    }
  } catch (e) {
    console.error('取消分析任务失败:', e)
  }
}

const handleMarkAnalysisFailed = async (row: any) => {
  try {
    const taskId = row.task_id || row.id
    const res = await adminApi.markUserAnalysisFailed(userId.value, taskId)
    if (res.success) {
      ElMessage.success(res.message)
      loadAnalyses()
    }
  } catch (e) {
    console.error('标记分析任务失败:', e)
  }
}

const handleDeleteReport = async (row: any) => {
  try {
    const reportId = row.id || row._id
    const res = await adminApi.deleteUserReport(userId.value, reportId)
    if (res.success) {
      ElMessage.success(res.message)
      loadReports()
    }
  } catch (e) {
    console.error('删除报告失败:', e)
  }
}

const handleDeleteFavorite = async (row: any) => {
  try {
    const res = await adminApi.deleteUserFavorite(userId.value, row.stock_code)
    if (res.success) {
      ElMessage.success(res.message)
      loadFavorites()
    }
  } catch (e) {
    console.error('删除自选股失败:', e)
  }
}

onMounted(() => {
  loadUser()
  loadAnalyses()
})

// 当路由参数变化时（从同一组件跳到另一个用户详情）
watch(userId, () => {
  // 重置状态
  activeTab.value = 'analyses'
  analyses.value = []
  analysesPage.value = 1
  reports.value = []
  reportsPage.value = 1
  favorites.value = []
  logs.value = []
  logsPage.value = 1
  stats.value = null
  showResetPassword.value = false
  newPassword.value = ''
  // 重新加载
  loadUser()
  loadAnalyses()
})
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  .card-header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}
.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
