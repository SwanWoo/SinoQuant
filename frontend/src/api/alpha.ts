import { ApiClient } from './request'

export interface StrategyInfo {
  strategy_id: string
  user_id: string
  name: string
  description: string
  code: string
  parameters: Record<string, any>
  symbol: string
  status: 'draft' | 'validated' | 'error'
  validation_errors: string[]
  analysis_task_id?: string
  llm_model_info?: string
  created_at: string
  updated_at: string
}

export interface BacktestInfo {
  backtest_id: string
  strategy_id: string
  user_id: string
  status: 'running' | 'completed' | 'failed'
  parameters: {
    symbols: string[]
    start_date: string
    end_date: string
    capital: number
    strategy_params: Record<string, any>
  }
  statistics: BacktestStatistics | null
  daily_pnl: DailyPnlItem[]
  trades: TradeRecord[]
  orders: OrderRecord[]
  logs: string[]
  error_message?: string
  started_at: string
  completed_at?: string
  duration_seconds: number
}

export interface BacktestStatistics {
  start_date: string
  end_date: string
  total_days: number
  profit_days: number
  loss_days: number
  capital: number
  end_balance: number
  max_drawdown: number
  max_ddpercent: number
  max_drawdown_duration: number
  total_net_pnl: number
  daily_net_pnl: number
  total_commission: number
  daily_commission: number
  total_turnover: number
  daily_turnover: number
  total_trade_count: number
  daily_trade_count: number
  total_return: number
  annual_return: number
  daily_return: number
  return_std: number
  sharpe_ratio: number
  return_drawdown_ratio: number
}

export interface DailyPnlItem {
  date: string
  trade_count: number
  turnover: number
  commission: number
  trading_pnl: number
  holding_pnl: number
  total_pnl: number
  net_pnl: number
}

export interface TradeRecord {
  symbol: string
  direction: string
  offset: string
  price: number
  volume: number
  datetime: string
}

export interface OrderRecord {
  symbol: string
  direction: string
  offset: string
  price: number
  volume: number
  traded: number
  status: string
  datetime: string
}

export interface SimulationInfo {
  simulation_id: string
  strategy_id: string
  symbols: string[]
  capital: number
  status: 'stopped' | 'running' | 'paused' | 'error'
  current_cash: number
  positions_value: number
  total_pnl: number
  realized_pnl: number
  trade_count: number
  started_at: string
  last_update: string
  error_message?: string
}

export interface GenerateStrategyRequest {
  analysis_task_id?: string
  symbol: string
  market_report?: string
  sentiment_report?: string
  news_report?: string
  fundamentals_report?: string
  trade_decision?: Record<string, any>
  model_name?: string
}

export interface RunBacktestRequest {
  strategy_id: string
  symbols: string[]
  start_date: string
  end_date: string
  capital?: number
  strategy_params?: Record<string, any>
}

export interface QuickBacktestRequest {
  strategy_id: string
  symbols: string[]
  trading_days?: number
  capital?: number
  strategy_params?: Record<string, any>
}

export interface StartSimulationRequest {
  strategy_id: string
  symbols: string[]
  capital?: number
  strategy_params?: Record<string, any>
}

export const alphaApi = {
  generateStrategy(data: GenerateStrategyRequest) {
    return ApiClient.post<StrategyInfo>('/api/alpha/strategies/generate', data, { showLoading: true })
  },
  listStrategies(limit = 50, offset = 0) {
    return ApiClient.get<{ items: StrategyInfo[] }>('/api/alpha/strategies', { limit, offset })
  },
  getStrategy(strategyId: string) {
    return ApiClient.get<StrategyInfo>(`/api/alpha/strategies/${strategyId}`)
  },
  updateStrategy(strategyId: string, data: { code: string }) {
    return ApiClient.put<StrategyInfo>(`/api/alpha/strategies/${strategyId}`, data)
  },
  deleteStrategy(strategyId: string) {
    return ApiClient.delete(`/api/alpha/strategies/${strategyId}`)
  },
  validateStrategy(strategyId: string) {
    return ApiClient.post<{ valid: boolean; errors: string[] }>(`/api/alpha/strategies/${strategyId}/validate`)
  },

  runBacktest(data: RunBacktestRequest) {
    return ApiClient.post<BacktestInfo>('/api/alpha/backtests', data, { showLoading: true })
  },
  quickBacktest(data: QuickBacktestRequest) {
    return ApiClient.post<BacktestInfo>('/api/alpha/backtests/quick', data, { showLoading: true })
  },
  getBacktest(backtestId: string) {
    return ApiClient.get<BacktestInfo>(`/api/alpha/backtests/${backtestId}`)
  },
  listBacktests(strategyId?: string) {
    return ApiClient.get<{ items: BacktestInfo[] }>('/api/alpha/backtests', { strategy_id: strategyId })
  },

  startSimulation(data: StartSimulationRequest) {
    return ApiClient.post<SimulationInfo>('/api/alpha/simulations', data, { showLoading: true })
  },
  stopSimulation(simulationId: string) {
    return ApiClient.post(`/api/alpha/simulations/${simulationId}/stop`)
  },
  pauseSimulation(simulationId: string) {
    return ApiClient.post(`/api/alpha/simulations/${simulationId}/pause`)
  },
  listSimulations() {
    return ApiClient.get<{ items: SimulationInfo[] }>('/api/alpha/simulations')
  },
  getSimulation(simulationId: string) {
    return ApiClient.get<SimulationInfo>(`/api/alpha/simulations/${simulationId}`)
  },
  getSimulationPositions(simulationId: string) {
    return ApiClient.get<{ items: any[] }>(`/api/alpha/simulations/${simulationId}/positions`)
  },
  getSimulationOrders(simulationId: string) {
    return ApiClient.get<{ items: any[] }>(`/api/alpha/simulations/${simulationId}/orders`)
  },
  getSimulationPnl(simulationId: string) {
    return ApiClient.get<{ items: any[] }>(`/api/alpha/simulations/${simulationId}/pnl`)
  },

  syncData(symbols: string[], startDate?: string, endDate?: string) {
    return ApiClient.post('/api/alpha/data/sync', { symbols, start_date: startDate, end_date: endDate }, { showLoading: true })
  },
  getDataStatus(symbols: string[]) {
    return ApiClient.get('/api/alpha/data/status', { symbols: symbols.join(',') })
  },
}
