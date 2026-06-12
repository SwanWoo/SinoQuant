<template>
  <el-card shadow="hover" class="dashboard-kline-card">
    <template #header>
      <div class="card-header">
        <span class="title">K线行情</span>
        <div class="controls">
          <el-input
            v-model="searchKeyword"
            placeholder="输入股票代码"
            size="small"
            style="width: 140px"
            clearable
            @keyup.enter="onSearch"
          >
            <template #append>
              <el-button :icon="Search" @click="onSearch" :loading="loading" />
            </template>
          </el-input>
          <el-segmented v-model="period" :options="periodOptions" size="small" @change="fetchKline" />
          <el-select v-if="!isIndex" v-model="adj" size="small" style="width: 80px" @change="fetchKline">
            <el-option label="不复权" value="none" />
            <el-option label="前复权" value="qfq" />
            <el-option label="后复权" value="hfq" />
          </el-select>
        </div>
      </div>
    </template>

    <!-- 股票/指数信息条 -->
    <div v-if="currentCode" class="stock-info-bar">
      <span class="stock-code">{{ currentCode }}</span>
      <span class="stock-name">{{ stockName }}</span>
      <span class="stock-price" :class="changeClass">
        {{ fmtPrice(quote.close) }}
        <span class="change-pct">{{ fmtPercent(quote.changePercent) }}</span>
      </span>
      <el-button text size="small" @click="goToDetail" :disabled="!currentCode || isIndex">
        详情 <el-icon><ArrowRight /></el-icon>
      </el-button>
    </div>

    <!-- K线图 -->
    <div class="kline-container">
      <v-chart
        v-if="hasData"
        class="k-chart"
        :option="kOption"
        autoresize
      />
      <el-empty v-else :description="loading ? '正在加载...' : '暂无K线数据'" :image-size="80" />
    </div>

    <!-- 图例 -->
    <div v-if="hasData" class="legend">
      {{ period }} · {{ klineSource || '-' }} · {{ lastKTime || '-' }} · 收 {{ fmtPrice(lastKClose) }}
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, ArrowRight } from '@element-plus/icons-vue'
import { use as echartsUse } from 'echarts/core'
import { CandlestickChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import { stocksApi } from '@/api/stocks'

echartsUse([CandlestickChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, CanvasRenderer])

const router = useRouter()
const emit = defineEmits<{ (e: 'selectStock', code: string): void }>()

// 常见指数映射（与后端 app/utils/code_utils.py 保持一致）
const INDEX_MAP: Record<string, { fullCode: string; name: string }> = {
  '000001': { fullCode: 'sh000001', name: '上证指数' },
  '399001': { fullCode: 'sz399001', name: '深证成指' },
  '399006': { fullCode: 'sz399006', name: '创业板指' },
  '000300': { fullCode: 'sh000300', name: '沪深300' },
  '000016': { fullCode: 'sh000016', name: '上证50' },
  '000905': { fullCode: 'sh000905', name: '中证500' },
}

function resolveCode(input: string): { code: string; isIndex: boolean; name: string } {
  const s = input.trim().toLowerCase()
  // 已带 sh/sz 前缀
  if (/^(sh|sz)\d{6}$/.test(s)) {
    const code6 = s.slice(2)
    const info = INDEX_MAP[code6]
    return { code: s, isIndex: true, name: info?.name || '' }
  }
  // 6位数字查映射
  const code6 = s.padStart(6, '0')
  if (code6 in INDEX_MAP) {
    const info = INDEX_MAP[code6]
    return { code: info.fullCode, isIndex: true, name: info.name }
  }
  return { code: s.toUpperCase(), isIndex: false, name: '' }
}

const searchKeyword = ref('')
const currentCode = ref('')
const stockName = ref('')
const isIndex = ref(false)
const periodOptions = ['日K', '周K', '月K']
const period = ref('日K')
const adj = ref('none')
const loading = ref(false)
const klineSource = ref('')
const lastKTime = ref('')
const lastKClose = ref<number | null>(null)
const hasData = ref(false)

const quote = reactive({ close: NaN, changePercent: NaN })
const changeClass = computed(() => quote.changePercent > 0 ? 'up' : quote.changePercent < 0 ? 'down' : '')

const kOption = ref<EChartsOption>({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'cross' },
    backgroundColor: 'rgba(32, 33, 36, 0.9)',
    borderColor: '#555',
    textStyle: { color: '#eee', fontSize: 12 }
  },
  legend: {
    data: ['K线', '成交量'],
    top: 0,
    textStyle: { color: '#999', fontSize: 11 }
  },
  grid: [
    { left: 50, right: 20, top: 30, height: '55%' },
    { left: 50, right: 20, top: '72%', height: '18%' }
  ],
  xAxis: [
    { type: 'category', data: [], boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
    { type: 'category', data: [], boundaryGap: true, axisLine: { onZero: false }, gridIndex: 1, axisLabel: { fontSize: 10 } }
  ],
  yAxis: [
    { scale: true, gridIndex: 0, splitArea: { show: false } },
    { scale: true, gridIndex: 1, splitNumber: 2, splitArea: { show: false }, axisLabel: { fontSize: 10 } }
  ],
  dataZoom: [
    { type: 'inside', xAxisIndex: [0, 1], start: 70, end: 100 },
    { show: true, xAxisIndex: [0, 1], type: 'slider', bottom: 4, height: 18, start: 70, end: 100 }
  ],
  series: [
    {
      name: 'K线',
      type: 'candlestick',
      data: [],
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: { color: '#ef4444', color0: '#16a34a', borderColor: '#ef4444', borderColor0: '#16a34a' }
    },
    {
      name: '成交量',
      type: 'bar',
      data: [],
      xAxisIndex: 1,
      yAxisIndex: 1,
      itemStyle: { color: '#ef4444', opacity: 0.7 }
    }
  ]
})

function periodToParam(p: string): string {
  if (p.includes('周')) return 'week'
  if (p.includes('月')) return 'month'
  return 'day'
}

async function onSearch() {
  const raw = searchKeyword.value.trim()
  if (!raw) return
  await loadStock(raw)
}

async function loadStock(rawCode: string) {
  const resolved = resolveCode(rawCode)
  currentCode.value = resolved.code
  isIndex.value = resolved.isIndex
  stockName.value = resolved.name
  emit('selectStock', currentCode.value)
  loading.value = true
  try {
    await Promise.all([fetchQuote(), fetchKline()])
  } finally {
    loading.value = false
  }
}

async function fetchQuote() {
  if (!currentCode.value) return
  try {
    const res = await stocksApi.getQuote(currentCode.value)
    const d: any = (res as any)?.data || {}
    quote.close = Number(d.price ?? d.close ?? NaN)
    quote.changePercent = Number(d.change_percent ?? NaN)
    if (d.name) stockName.value = d.name
  } catch { /* ignore */ }
}

async function fetchKline() {
  if (!currentCode.value) return
  try {
    const param = periodToParam(period.value)
    const res = await stocksApi.getKline(currentCode.value, param as any, 200, adj.value as any)
    const d: any = (res as any)?.data || {}
    klineSource.value = d.source || ''
    const items: any[] = Array.isArray(d.items) ? d.items : []

    const category: string[] = []
    const ohlc: number[][] = []
    const volumes: number[] = []

    for (const it of items) {
      const t = String(it.time || it.trade_time || it.trade_date || '')
      const o = Number(it.open ?? NaN)
      const h = Number(it.high ?? NaN)
      const l = Number(it.low ?? NaN)
      const c = Number(it.close ?? NaN)
      const v = Number(it.volume ?? 0)
      if (!Number.isFinite(o) || !Number.isFinite(h) || !Number.isFinite(l) || !Number.isFinite(c) || !t) continue
      category.push(t)
      ohlc.push([o, c, l, h])
      volumes.push(c >= o ? v : -v)
    }

    hasData.value = category.length > 0
    if (category.length) {
      lastKTime.value = category[category.length - 1]
      lastKClose.value = ohlc[ohlc.length - 1][1]
    }

    kOption.value = {
      ...kOption.value,
      xAxis: [
        { type: 'category', data: category, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
        { type: 'category', data: category, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 1, axisLabel: { fontSize: 10 } }
      ],
      series: [
        {
          name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
          itemStyle: { color: '#ef4444', color0: '#16a34a', borderColor: '#ef4444', borderColor0: '#16a34a' }
        },
        {
          name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1,
          itemStyle: { color: '#ef4444', opacity: 0.7 }
        }
      ] as any
    }
  } catch (e) {
    console.error('获取K线失败', e)
    hasData.value = false
  }
}

function goToDetail() {
  if (currentCode.value && !isIndex.value) router.push(`/stocks/${currentCode.value}`)
}

function fmtPrice(v: any) { const n = Number(v); return Number.isFinite(n) ? n.toFixed(2) : '-' }
function fmtPercent(v: any) { const n = Number(v); return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(2)}%` : '' }

// 实时刷新
let refreshTimer: ReturnType<typeof setInterval> | null = null

function isTradingHours(): boolean {
  const now = new Date()
  const day = now.getUTCDay()
  if (day === 0 || day === 6) return false
  // 转换为北京时间 (UTC+8)
  const utcH = now.getUTCHours()
  const utcM = now.getUTCMinutes()
  const bjH = (utcH + 8) % 24
  const t = bjH * 60 + utcM
  return (t >= 570 && t <= 690) || (t >= 780 && t <= 900) // 9:30-11:30, 13:00-15:00
}

function startRealtimeRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = setInterval(() => {
    if (isTradingHours() && currentCode.value) {
      fetchQuote()
    }
  }, 60000)
}

defineExpose({ loadStock })

onMounted(() => {
  loadStock('sh000001')  // 默认加载上证指数
  startRealtimeRefresh()
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped lang="scss">
.dashboard-kline-card {
  border-radius: 12px;

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;

    .title {
      font-size: 16px;
      font-weight: 600;
    }

    .controls {
      display: flex;
      align-items: center;
      gap: 8px;
    }
  }

  .stock-info-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0 12px;
    border-bottom: 1px solid var(--el-border-color-lighter);
    margin-bottom: 8px;

    .stock-code {
      font-weight: 700;
      font-size: 16px;
    }

    .stock-name {
      color: var(--el-text-color-secondary);
      font-size: 14px;
    }

    .stock-price {
      font-size: 20px;
      font-weight: 800;
      margin-left: auto;
      margin-right: 8px;

      .change-pct {
        font-size: 13px;
        margin-left: 8px;
        font-weight: 600;
      }

      &.up { color: #ef4444; }
      &.down { color: #16a34a; }
    }
  }

  .kline-container {
    .k-chart {
      height: 360px;
    }
  }

  .legend {
    margin-top: 6px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

@media (max-width: 768px) {
  .dashboard-kline-card {
    .card-header {
      flex-direction: column;
      align-items: flex-start;
    }

    .stock-info-bar {
      flex-wrap: wrap;
    }
  }
}
</style>
