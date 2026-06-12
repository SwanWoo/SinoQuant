import type { App } from 'vue'
import MultiMarketStockSearch from './Global/MultiMarketStockSearch.vue'

// 全局组件注册
export function setupGlobalComponents(app: App) {
  // 注册股票搜索组件
  app.component('MultiMarketStockSearch', MultiMarketStockSearch)
}

export default setupGlobalComponents
