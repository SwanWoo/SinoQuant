<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { alphaApi, type StrategyInfo } from '@/api/alpha'

const router = useRouter()
const loading = ref(false)
const strategies = ref<StrategyInfo[]>([])
const generateDialogVisible = ref(false)
const generateForm = ref({ symbol: '', market_report: '', sentiment_report: '', news_report: '', fundamentals_report: '' })
const generating = ref(false)

async function loadStrategies() {
  loading.value = true
  try {
    const res = await alphaApi.listStrategies()
    strategies.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error('加载策略列表失败')
  } finally {
    loading.value = false
  }
}

async function handleGenerate() {
  if (!generateForm.value.symbol) {
    ElMessage.warning('请输入股票代码')
    return
  }
  generating.value = true
  try {
    const res = await alphaApi.generateStrategy({
      symbol: generateForm.value.symbol.padStart(6, '0'),
      market_report: generateForm.value.market_report,
    })
    if (res.success) {
      ElMessage.success('策略生成成功')
      generateDialogVisible.value = false
      generateForm.value = { symbol: '', market_report: '', sentiment_report: '', news_report: '', fundamentals_report: '' }
      loadStrategies()
    } else {
      ElMessage.error(res.message || '策略生成失败')
    }
  } catch (e: any) {
    ElMessage.error('策略生成失败')
  } finally {
    generating.value = false
  }
}

async function handleDelete(row: StrategyInfo) {
  await ElMessageBox.confirm(`确定删除策略 "${row.name}" 吗？关联的回测记录也会被删除。`, '确认删除', { type: 'warning' })
  try {
    await alphaApi.deleteStrategy(row.strategy_id)
    ElMessage.success('已删除')
    loadStrategies()
  } catch {
    ElMessage.error('删除失败')
  }
}

function statusTagType(status: string) {
  return status === 'validated' ? 'success' : status === 'error' ? 'danger' : 'info'
}

function statusLabel(status: string) {
  return status === 'validated' ? '已验证' : status === 'error' ? '异常' : '草稿'
}

onMounted(loadStrategies)
</script>

<template>
  <div class="strategy-list">
    <div class="page-header">
      <h1 class="page-title"><el-icon><TrendCharts /></el-icon> 量化策略管理</h1>
      <p class="page-description">基于 LLM 分析报告自动生成 vnpy 量化交易策略，支持回测验证和模拟交易</p>
    </div>

    <el-card shadow="never">
      <div style="display: flex; justify-content: space-between; margin-bottom: 16px">
        <el-button type="primary" @click="generateDialogVisible = true">
          <el-icon><Plus /></el-icon> 生成策略
        </el-button>
        <el-button @click="loadStrategies">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>

      <el-table :data="strategies" v-loading="loading" stripe>
        <el-table-column prop="name" label="策略名称" min-width="180" />
        <el-table-column prop="symbol" label="股票代码" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="参数" min-width="160">
          <template #default="{ row }">
            <span v-for="(val, key) in row.parameters" :key="key" style="margin-right: 8px">
              <el-tag size="small" type="info">{{ key }}={{ val }}</el-tag>
            </span>
            <span v-if="!row.parameters || Object.keys(row.parameters).length === 0" style="color: var(--el-text-color-secondary)">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="router.push(`/alpha/strategy/${row.strategy_id}`)">详情</el-button>
            <el-button v-if="row.status === 'validated'" type="success" link size="small" @click="router.push(`/alpha/strategy/${row.strategy_id}?tab=backtest`)">回测</el-button>
            <el-button v-if="row.status === 'validated'" type="warning" link size="small" @click="router.push(`/alpha/strategy/${row.strategy_id}?action=simulate`)">模拟</el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="generateDialogVisible" title="生成量化策略" width="560px">
      <el-form :model="generateForm" label-width="100px">
        <el-form-item label="股票代码" required>
          <el-input v-model="generateForm.symbol" placeholder="输入6位股票代码，如 000001" maxlength="6" />
        </el-form-item>
        <el-form-item label="市场分析">
          <el-input v-model="generateForm.market_report" type="textarea" :rows="3" placeholder="可选：粘贴市场分析报告内容" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="generateDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="handleGenerate">生成策略</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.strategy-list {
  .page-header {
    margin-bottom: 20px;
    .page-title {
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .page-description {
      color: var(--el-text-color-secondary);
      font-size: 14px;
    }
  }
}
</style>
