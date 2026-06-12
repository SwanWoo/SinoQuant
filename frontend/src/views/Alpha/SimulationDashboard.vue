<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { alphaApi, type SimulationInfo } from '@/api/alpha'

const route = useRoute()
const router = useRouter()
const simulationId = route.params.simulationId as string

const loading = ref(true)
const simulation = ref<SimulationInfo | null>(null)
const positions = ref<any[]>([])
const orders = ref<any[]>([])
const pnlHistory = ref<any[]>([])
const pollTimer = ref<any>(null)

async function loadStatus() {
  try {
    const [simRes, posRes, ordRes, pnlRes] = await Promise.allSettled([
      alphaApi.getSimulation(simulationId),
      alphaApi.getSimulationPositions(simulationId),
      alphaApi.getSimulationOrders(simulationId),
      alphaApi.getSimulationPnl(simulationId),
    ])

    if (simRes.status === 'fulfilled') simulation.value = simRes.value.data
    if (posRes.status === 'fulfilled') positions.value = posRes.value.data?.items || []
    if (ordRes.status === 'fulfilled') orders.value = ordRes.value.data?.items || []
    if (pnlRes.status === 'fulfilled') pnlHistory.value = pnlRes.value.data?.items || []
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

async function handleStop() {
  await ElMessageBox.confirm('确定停止模拟交易吗？', '确认')
  try {
    await alphaApi.stopSimulation(simulationId)
    ElMessage.success('模拟交易已停止')
    loadStatus()
  } catch {
    ElMessage.error('操作失败')
  }
}

async function handlePause() {
  try {
    await alphaApi.pauseSimulation(simulationId)
    ElMessage.success('模拟交易已暂停')
    loadStatus()
  } catch {
    ElMessage.error('操作失败')
  }
}

const isRunning = computed(() => simulation.value?.status === 'running')
const isPaused = computed(() => simulation.value?.status === 'paused')

onMounted(() => {
  loadStatus()
  pollTimer.value = setInterval(loadStatus, 10000)
})

onUnmounted(() => {
  if (pollTimer.value) clearInterval(pollTimer.value)
})
</script>

<template>
  <div class="simulation-dashboard">
    <div class="page-header">
      <h1 class="page-title">
        <el-button link @click="router.push('/alpha')"><el-icon><ArrowLeft /></el-icon></el-button>
        策略模拟交易
      </h1>
      <p class="page-description">模拟ID: {{ simulationId }}</p>
    </div>

    <div v-loading="loading">
      <template v-if="simulation">
        <!-- Controls -->
        <el-card shadow="never" style="margin-bottom: 16px">
          <div style="display: flex; justify-content: space-between; align-items: center">
            <div>
              <el-tag :type="isRunning ? 'success' : isPaused ? 'warning' : simulation.status === 'error' ? 'danger' : 'info'" size="large">
                {{ simulation.status === 'running' ? '运行中' : simulation.status === 'paused' ? '已暂停' : simulation.status === 'error' ? '异常' : '已停止' }}
              </el-tag>
              <span style="margin-left: 12px; color: var(--el-text-color-secondary); font-size: 13px">
                标的: {{ simulation.symbols?.join(', ') }} | 初始资金: {{ simulation.capital?.toLocaleString() }}
              </span>
            </div>
            <div style="display: flex; gap: 8px">
              <el-button v-if="isRunning" @click="handlePause">暂停</el-button>
              <el-button v-if="isRunning || isPaused" type="danger" @click="handleStop">停止</el-button>
            </div>
          </div>
        </el-card>

        <!-- Account Summary -->
        <el-row :gutter="12" style="margin-bottom: 16px">
          <el-col :span="6">
            <el-card shadow="never" style="text-align: center">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">总权益</div>
              <div style="font-size: 20px; font-weight: 600">{{ ((simulation.current_cash || 0) + (simulation.positions_value || 0))?.toLocaleString() }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never" style="text-align: center">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">可用资金</div>
              <div style="font-size: 20px; font-weight: 600">{{ simulation.current_cash?.toLocaleString() }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never" style="text-align: center">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">持仓市值</div>
              <div style="font-size: 20px; font-weight: 600">{{ simulation.positions_value?.toLocaleString() }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never" style="text-align: center">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">总盈亏</div>
              <div style="font-size: 20px; font-weight: 600" :style="{ color: (simulation.total_pnl || 0) >= 0 ? '#67c23a' : '#f56c6c' }">
                {{ simulation.total_pnl?.toLocaleString() }}
              </div>
            </el-card>
          </el-col>
        </el-row>

        <!-- Positions -->
        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header><span>当前持仓 ({{ positions.length }})</span></template>
          <el-table :data="positions" stripe size="small">
            <el-table-column prop="vt_symbol" label="标的" width="140" />
            <el-table-column prop="quantity" label="数量" width="100" />
            <el-table-column prop="avg_cost" label="成本价" width="120" />
            <el-table-column prop="market_value" label="市值" width="120">
              <template #default="{ row }">
                {{ ((row.quantity || 0) * (row.avg_cost || 0))?.toLocaleString() }}
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!positions.length" description="暂无持仓" :image-size="60" />
        </el-card>

        <!-- PnL History -->
        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header><span>每日盈亏 ({{ pnlHistory.length }}天)</span></template>
          <el-table :data="pnlHistory.slice(-30)" stripe size="small" max-height="360">
            <el-table-column prop="date" label="日期" width="120" />
            <el-table-column prop="total_value" label="总权益" width="130">
              <template #default="{ row }">{{ Number(row.total_value)?.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="daily_pnl" label="日盈亏" width="130">
              <template #default="{ row }">
                <span :style="{ color: Number(row.daily_pnl) >= 0 ? '#67c23a' : '#f56c6c' }">
                  {{ Number(row.daily_pnl)?.toFixed(2) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="total_pnl" label="累计盈亏" width="130">
              <template #default="{ row }">
                <span :style="{ color: Number(row.total_pnl) >= 0 ? '#67c23a' : '#f56c6c' }">
                  {{ Number(row.total_pnl)?.toFixed(2) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- Recent Orders -->
        <el-card shadow="never">
          <template #header><span>最近交易 ({{ orders.length }})</span></template>
          <el-table :data="orders.slice(-30).reverse()" stripe size="small" max-height="360">
            <el-table-column prop="timestamp" label="时间" width="170" />
            <el-table-column prop="vt_symbol" label="标的" width="130" />
            <el-table-column prop="direction" label="方向" width="80" />
            <el-table-column prop="fill_price" label="成交价" width="100" />
            <el-table-column prop="volume" label="数量" width="100" />
          </el-table>
        </el-card>

        <el-alert
          v-if="simulation.error_message"
          :title="simulation.error_message"
          type="error"
          style="margin-top: 16px"
          :closable="false"
        />
      </template>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.simulation-dashboard {
  .page-header {
    margin-bottom: 20px;
    .page-title {
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .page-description {
      color: var(--el-text-color-secondary);
      font-size: 13px;
    }
  }
}
</style>
