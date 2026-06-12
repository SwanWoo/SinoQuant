/**
 * 股票代码格式验证工具
 * 仅支持 A股 代码格式验证
 */

export interface StockValidationResult {
  valid: boolean
  market?: 'A股'
  message?: string
  normalizedCode?: string
}

/**
 * A股代码格式验证
 * 格式：6位数字
 * - 60xxxx: 上海主板
 * - 68xxxx: 科创板
 * - 00xxxx: 深圳主板
 * - 30xxxx: 创业板
 * - 43xxxx/83xxxx/87xxxx: 北交所
 */
export function validateAStock(code: string): StockValidationResult {
  // 移除空格和特殊字符
  const cleanCode = code.trim().replace(/[^0-9]/g, '')

  // 必须是6位数字
  if (!/^\d{6}$/.test(cleanCode)) {
    return {
      valid: false,
      message: 'A股代码必须是6位数字'
    }
  }

  // 验证前缀
  const prefix = cleanCode.substring(0, 2)
  const validPrefixes = ['60', '68', '00', '30', '43', '83', '87']

  if (!validPrefixes.includes(prefix)) {
    return {
      valid: false,
      message: 'A股代码前缀不正确（支持：60/68/00/30/43/83/87开头）'
    }
  }

  return {
    valid: true,
    market: 'A股',
    normalizedCode: cleanCode
  }
}

/**
 * 验证股票代码（仅支持A股）
 * @param code 股票代码
 */
export function validateStockCode(code: string): StockValidationResult {
  if (!code || !code.trim()) {
    return {
      valid: false,
      message: '请输入股票代码'
    }
  }

  return validateAStock(code.trim())
}

/**
 * 获取股票代码格式说明
 */
export function getStockCodeFormatHelp(): string {
  return '6位数字，如：000001（平安银行）、600519（贵州茅台）'
}

/**
 * 获取股票代码示例
 */
export function getStockCodeExamples(): string[] {
  return ['000001', '600519', '000858', '300750']
}

/**
 * 格式化股票代码显示
 * @param code 原始代码
 */
export function formatStockCode(code: string): string {
  const validation = validateStockCode(code)
  return validation.normalizedCode || code
}
