<template>
  <div class="stock-search">
    <el-input
      v-model="searchQuery"
      placeholder="输入股票代码或名称（如：000001 或 平安银行）"
      clearable
      @input="handleSearch"
      @clear="handleClear"
      class="search-input"
    >
      <template #prefix>
        <el-icon><Search /></el-icon>
      </template>
    </el-input>

    <div v-if="loading" class="search-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>搜索中...</span>
    </div>

    <div v-else-if="searchResults.length > 0" class="search-results">
      <div
        v-for="stock in searchResults"
        :key="stock.code"
        class="result-item"
        @click="handleSelectStock(stock)"
      >
        <div class="stock-info">
          <div class="stock-code-name">
            <span class="stock-code">{{ stock.code }}</span>
            <span class="stock-name">{{ stock.name }}</span>
          </div>
          <div class="stock-meta">
            <el-tag v-if="stock.industry" size="small">{{ stock.industry }}</el-tag>
            <span v-if="stock.pe" class="stock-pe">PE: {{ stock.pe.toFixed(2) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="searchQuery && !loading" class="no-results">
      <el-empty description="未找到相关股票" :image-size="80" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Search, Loading } from '@element-plus/icons-vue'
import { analysisApi } from '@/api/analysis'
import { ElMessage } from 'element-plus'

interface StockItem {
  code: string
  name: string
  industry?: string
  pe?: number
}

interface Emits {
  (e: 'select', stock: StockItem): void
}

const emit = defineEmits<Emits>()

const searchQuery = ref('')
const searchResults = ref<StockItem[]>([])
const loading = ref(false)

let searchTimer: ReturnType<typeof setTimeout> | null = null

const handleSearch = () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }

  if (!searchQuery.value.trim()) {
    searchResults.value = []
    return
  }

  // 防抖：500ms后执行搜索
  searchTimer = setTimeout(async () => {
    await performSearch()
  }, 500)
}

const performSearch = async () => {
  if (!searchQuery.value.trim()) {
    return
  }

  loading.value = true
  try {
    const data = await analysisApi.searchStocks(searchQuery.value.trim())
    searchResults.value = (Array.isArray(data) ? data : []).map((item: any) => ({
      code: item.symbol || item.code,
      name: item.name,
      industry: item.industry,
      pe: item.pe
    }))
  } catch (error: any) {
    console.error('搜索失败:', error)
    ElMessage.error(error.message || '搜索失败')
    searchResults.value = []
  } finally {
    loading.value = false
  }
}

const handleClear = () => {
  searchResults.value = []
}

const handleSelectStock = (stock: StockItem) => {
  emit('select', stock)
  searchQuery.value = ''
  searchResults.value = []
}
</script>

<style scoped lang="scss">
.stock-search {
  width: 100%;
}

.search-input {
  width: 100%;
}

.search-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px;
  color: var(--el-text-color-secondary);
}

.search-results {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
}

.result-item {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--el-border-color-lighter);
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: var(--el-fill-color-light);
  }
}

.stock-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stock-code-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stock-code {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.stock-name {
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.stock-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stock-pe {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.no-results {
  padding: 20px;
  text-align: center;
}
</style>
