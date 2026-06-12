<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { alphaApi, type StrategyInfo, type BacktestInfo } from '@/api/alpha'

const quickBacktestLoading = ref(false)

const route = useRoute()
const router = useRouter()
const strategyId = route.params.strategyId as string

const loading = ref(false)
const strategy = ref<StrategyInfo | null>(null)
const backtests = ref<BacktestInfo[]>([])
const activeTab = ref((route.query.tab as string | undefined) || 'code')
const editingCode = ref('')
const editMode = ref(false)
const validating = ref(false)
const validationErrors = ref<string[]>([])

const backtestDialogVisible = ref(false)
const backtestForm = ref({ start_date: '', end_date: '', capital: 1000000 })

async function loadStrategy() {
  loading.value = true
  try {
    const res = await alphaApi.getStrategy(strategyId)
    strategy.value = res.data
    editingCode.value = res.data?.code || ''
  } catch {
    ElMessage.error('加载策略失败')
  } finally {
    loading.value = false
  }
}

async function loadBacktests() {
  try {
    const res = await alphaApi.listBacktests(strategyId)
    backtests.value = res.data?.items || []
  } catch {
    // ignore
  }
}

async function handleValidate() {
  validating.value = true
  try {
    const res = await alphaApi.validateStrategy(strategyId)
    validationErrors.value = res.data?.errors || []
    if (res.data?.valid) {
      ElMessage.success('代码验证通过')
    } else {
      ElMessage.error('代码验证失败')
    }
    loadStrategy()
  } catch {
    ElMessage.error('验证失败')
  } finally {
    validating.value = false
  }
}

async function handleSaveCode() {
  try {
    const res = await alphaApi.updateStrategy(strategyId, { code: editingCode.value })
    if (res.success) {
      strategy.value = res.data
      editMode.value = false
      ElMessage.success('策略代码已更新')
    }
  } catch {
    ElMessage.error('更新失败')
  }
}

async function handleRunBacktest() {
  if (!backtestForm.value.start_date || !backtestForm.value.end_date) {
    ElMessage.warning('请选择回测日期范围')
    return
  }
  try {
    const res = await alphaApi.runBacktest({
      strategy_id: strategyId,
      symbols: [strategy.value!.symbol],
      start_date: backtestForm.value.start_date,
      end_date: backtestForm.value.end_date,
      capital: backtestForm.value.capital,
    })
    ElMessage.success('回测任务已提交')
    backtestDialogVisible.value = false
    setTimeout(() => router.push(`/alpha/backtest/${res.data.backtest_id}`), 500)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || '提交回测失败')
  }
}

async function handleStartSimulation() {
  try {
    const res = await alphaApi.startSimulation({
      strategy_id: strategyId,
      symbols: [strategy.value!.symbol],
      capital: backtestForm.value.capital,
    })
    ElMessage.success('模拟交易已启动')
    setTimeout(() => router.push(`/alpha/simulation/${res.data.simulation_id}`), 500)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || '启动模拟失败')
  }
}

async function handleQuickBacktest() {
  quickBacktestLoading.value = true
  try {
    const res = await alphaApi.quickBacktest({
      strategy_id: strategyId,
      symbols: [strategy.value!.symbol],
      trading_days: 5,
      capital: backtestForm.value.capital,
    })
    ElMessage.success('快速回测已提交')
    setTimeout(() => router.push(`/alpha/backtest/${res.data.backtest_id}`), 500)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || '快速回测失败')
  } finally {
    quickBacktestLoading.value = false
  }
}

watch(() => route.query.tab, (val: string | (string | null)[] | null) => {
  if (val) activeTab.value = Array.isArray(val) ? val[0] || 'code' : val
})

onMounted(() => {
  loadStrategy()
  loadBacktests()
})
</script>

<template>
  <div class="strategy-detail">
    <div class="page-header">
      <h1 class="page-title">
        <el-button link @click="router.push('/alpha')"><el-icon><ArrowLeft /></el-icon></el-button>
        {{ strategy?.name || '策略详情' }}
      </h1>
      <p class="page-description">{{ strategy?.description }}</p>
    </div>

    <el-card v-loading="loading" shadow="never">
      <template v-if="strategy">
        <el-descriptions :column="3" border style="margin-bottom: 20px">
          <el-descriptions-item label="策略ID">{{ strategy.strategy_id }}</el-descriptions-item>
          <el-descriptions-item label="股票代码">{{ strategy.symbol }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="strategy.status === 'validated' ? 'success' : 'danger'" size="small">
              {{ strategy.status === 'validated' ? '已验证' : '异常' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="LLM模型">{{ strategy.llm_model_info || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ new Date(strategy.created_at).toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ new Date(strategy.updated_at).toLocaleString() }}</el-descriptions-item>
        </el-descriptions>

        <el-tabs v-model="activeTab">
          <el-tab-pane label="策略代码" name="code">
            <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 12px">
              <el-button v-if="!editMode" type="primary" size="small" @click="editMode = true">编辑</el-button>
              <template v-else>
                <el-button size="small" @click="editMode = false; editingCode = strategy.code">取消</el-button>
                <el-button type="success" size="small" @click="handleSaveCode">保存</el-button>
              </template>
              <el-button type="warning" size="small" :loading="validating" @click="handleValidate">验证代码</el-button>
            </div>
            <el-input
              v-model="editingCode"
              type="textarea"
              :rows="20"
              :readonly="!editMode"
              style="font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 13px"
              spellcheck="false"
            />
            <div v-if="validationErrors.length" style="margin-top: 12px">
              <el-alert type="error" title="验证错误" :closable="false">
                <ul style="margin: 4px 0; padding-left: 20px">
                  <li v-for="(err, i) in validationErrors" :key="i">{{ err }}</li>
                </ul>
              </el-alert>
            </div>
          </el-tab-pane>

          <el-tab-pane label="回测记录" name="backtest">
            <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 12px">
              <el-button type="primary" size="small" :loading="quickBacktestLoading" @click="handleQuickBacktest">前五日回测</el-button>
              <el-button type="success" size="small" @click="backtestDialogVisible = true">运行回测</el-button>
            </div>
            <el-table :data="backtests" stripe>
              <el-table-column prop="backtest_id" label="回测ID" width="140" />
              <el-table-column label="日期范围" width="200">
                <template #default="{ row }">
                  {{ row.parameters?.start_date }} ~ {{ row.parameters?.end_date }}
                </template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'completed' ? 'success' : row.status === 'running' ? 'warning' : 'danger'" size="small">
                    {{ row.status === 'completed' ? '完成' : row.status === 'running' ? '运行中' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="收益率" width="120">
                <template #default="{ row }">
                  <span :style="{ color: (row.statistics?.total_return || 0) >= 0 ? '#67c23a' : '#f56c6c' }">
                    {{ row.statistics?.total_return?.toFixed(2) || '-' }}%
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="Sharpe" width="100">
                <template #default="{ row }">{{ row.statistics?.sharpe_ratio?.toFixed(2) || '-' }}</template>
              </el-table-column>
              <el-table-column label="最大回撤" width="120">
                <template #default="{ row }">{{ row.statistics?.max_ddpercent?.toFixed(2) || '-' }}%</template>
              </el-table-column>
              <el-table-column label="操作" width="80">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="router.push(`/alpha/backtest/${row.backtest_id}`)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="模拟交易" name="simulate">
            <div style="display: flex; justify-content: flex-end; margin-bottom: 12px">
              <el-button v-if="strategy.status === 'validated'" type="success" size="small" @click="handleStartSimulation">
                启动模拟交易
              </el-button>
            </div>
            <el-empty v-if="strategy.status !== 'validated'" description="策略代码未通过验证，无法启动模拟交易" />
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>

    <el-dialog v-model="backtestDialogVisible" title="运行回测" width="480px">
      <el-form :model="backtestForm" label-width="100px">
        <el-form-item label="开始日期" required>
          <el-date-picker v-model="backtestForm.start_date" type="date" value-format="YYYY-MM-DD" placeholder="选择开始日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束日期" required>
          <el-date-picker v-model="backtestForm.end_date" type="date" value-format="YYYY-MM-DD" placeholder="选择结束日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number v-model="backtestForm.capital" :min="10000" :step="100000" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="backtestDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleRunBacktest">开始回测</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.strategy-detail {
  .page-header {
    margin-bottom: 20px;
    .page-title {
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .page-description {
      color: var(--el-text-color-secondary);
      font-size: 14px;
    }
  }
}
</style>
