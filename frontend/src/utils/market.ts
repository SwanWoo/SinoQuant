// 市场参数规范化：统一为 A股
export const normalizeMarketForAnalysis = (_market: any): string => {
  return 'A股'
}

export const exchangeCodeToMarket = (code: string): string => {
  const c = (code || '').toLowerCase()
  if (c === 'sh' || c === 'sz' || c === 'bj') return 'A股'
  return 'A股'
}

export const getMarketByStockCode = (code: string): string => {
  if (!code) return 'A股'
  const c = String(code).trim()
  if (c.startsWith('6') || c.startsWith('5')) return 'A股'   // 上海
  if (c.startsWith('0') || c.startsWith('3')) return 'A股'   // 深圳
  if (c.startsWith('8') || c.startsWith('4')) return 'A股'   // 北交所
  return 'A股'
}

export default {
  normalizeMarketForAnalysis,
  exchangeCodeToMarket,
  getMarketByStockCode,
}
