<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { alphaApi, type BacktestInfo } from '@/api/alpha'

const route = useRoute()
const router = useRouter()
const backtestId = route.params.backtestId as string

const loading = ref(true)
const backtest = ref<BacktestInfo | null>(null)
const pollTimer = ref<any>(null)

async function loadResult() {
  try {
    const res = await alphaApi.getBacktest(backtestId)
    backtest.value = res.data
    if (res.data?.status === 'running') {
      pollTimer.value = setTimeout(loadResult, 2000)
    }
  } catch {
    ElMessage.error('加载回测结果失败')
  } finally {
    loading.value = false
  }
}

const statCards = computed(() => {
  const s = backtest.value?.statistics
  if (!s) return []
  return [
    { label: '总收益率', value: `${s.total_return.toFixed(2)}%`, positive: s.total_return >= 0 },
    { label: '年化收益', value: `${s.annual_return.toFixed(2)}%`, positive: s.annual_return >= 0 },
    { label: 'Sharpe Ratio', value: s.sharpe_ratio.toFixed(2), positive: s.sharpe_ratio >= 1 },
    { label: '最大回撤', value: `${s.max_ddpercent.toFixed(2)}%`, positive: false },
    { label: '收益回撤比', value: s.return_drawdown_ratio.toFixed(2), positive: s.return_drawdown_ratio >= 1 },
    { label: '总交易次数', value: String(s.total_trade_count), positive: true },
    { label: '总盈亏', value: `${s.total_net_pnl.toFixed(2)}`, positive: s.total_net_pnl >= 0 },
    { label: '总手续费', value: `${s.total_commission.toFixed(2)}`, positive: false },
  ]
})

import { computed } from 'vue'

onMounted(loadResult)
onUnmounted(() => {
  if (pollTimer.value) clearTimeout(pollTimer.value)
})
</script>

<template>
  <div class="backtest-result">
    <div class="page-header">
      <h1 class="page-title">
        <el-button link @click="router.back()"><el-icon><ArrowLeft /></el-icon></el-button>
        回测结果
      </h1>
    </div>

    <div v-loading="loading">
      <template v-if="backtest">
        <!-- Status Alert -->
        <el-alert
          v-if="backtest.status === 'running'"
          title="回测运行中..."
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-alert
          v-else-if="backtest.status === 'failed'"
          :title="backtest.error_message || '回测执行失败'"
          type="error"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />

        <!-- Statistics Cards -->
        <el-row :gutter="12" style="margin-bottom: 20px">
          <el-col :span="6" v-for="card in statCards" :key="card.label">
            <el-card shadow="never" style="text-align: center">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">{{ card.label }}</div>
              <div style="font-size: 20px; font-weight: 600; margin-top: 4px" :style="{ color: card.positive ? '#67c23a' : '#f56c6c' }">
                {{ card.value }}
              </div>
            </el-card>
          </el-col>
        </el-row>

        <!-- Detailed Stats -->
        <el-card v-if="backtest.statistics" shadow="never" style="margin-bottom: 20px">
          <template #header><span>详细统计</span></template>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="回测区间">{{ backtest.statistics.start_date }} ~ {{ backtest.statistics.end_date }}</el-descriptions-item>
            <el-descriptions-item label="交易天数">{{ backtest.statistics.total_days }}</el-descriptions-item>
            <el-descriptions-item label="盈利/亏损天数">{{ backtest.statistics.profit_days }} / {{ backtest.statistics.loss_days }}</el-descriptions-item>
            <el-descriptions-item label="初始资金">{{ backtest.statistics.capital?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="结束资金">{{ backtest.statistics.end_balance?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="最长回撤天数">{{ backtest.statistics.max_drawdown_duration }}</el-descriptions-item>
            <el-descriptions-item label="日均盈亏">{{ backtest.statistics.daily_net_pnl?.toFixed(2) }}</el-descriptions-item>
            <el-descriptions-item label="日均收益">{{ backtest.statistics.daily_return?.toFixed(4) }}%</el-descriptions-item>
            <el-descriptions-item label="收益标准差">{{ backtest.statistics.return_std?.toFixed(4) }}%</el-descriptions-item>
            <el-descriptions-item label="日均成交额">{{ backtest.statistics.daily_turnover?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="日均手续费">{{ backtest.statistics.daily_commission?.toFixed(2) }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ backtest.duration_seconds?.toFixed(2) }}s</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- Daily PnL Table -->
        <el-card v-if="backtest.daily_pnl?.length" shadow="never" style="margin-bottom: 20px">
          <template #header><span>每日盈亏 (最近30天)</span></template>
          <el-table :data="backtest.daily_pnl.slice(-30)" stripe size="small" max-height="400">
            <el-table-column prop="date" label="日期" width="120" />
            <el-table-column prop="trade_count" label="交易次数" width="90" />
            <el-table-column prop="turnover" label="成交额" width="120">
              <template #default="{ row }">{{ Number(row.turnover)?.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="commission" label="手续费" width="100" />
            <el-table-column prop="net_pnl" label="净盈亏" width="120">
              <template #default="{ row }">
                <span :style="{ color: Number(row.net_pnl) >= 0 ? '#67c23a' : '#f56c6c' }">
                  {{ Number(row.net_pnl)?.toFixed(2) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- Trades Table -->
        <el-card v-if="backtest.trades?.length" shadow="never">
          <template #header><span>交易记录 (最近50笔)</span></template>
          <el-table :data="backtest.trades.slice(-50)" stripe size="small" max-height="400">
            <el-table-column prop="datetime" label="时间" width="170" />
            <el-table-column prop="symbol" label="标的" width="130" />
            <el-table-column prop="direction" label="方向" width="80" />
            <el-table-column prop="offset" label="开平" width="80" />
            <el-table-column prop="price" label="价格" width="100" />
            <el-table-column prop="volume" label="数量" width="100" />
          </el-table>
        </el-card>
      </template>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.backtest-result {
  .page-header {
    margin-bottom: 20px;
    .page-title {
      font-size: 24px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }
  }
}
</style>
