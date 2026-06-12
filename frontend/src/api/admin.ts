import { ApiClient } from './request'

// 用户列表参数
export interface UserListParams {
  skip?: number
  limit?: number
  keyword?: string
  active_only?: boolean
}

// 用户详情
export interface AdminUser {
  id: string
  username: string
  email: string
  is_active: boolean
  is_verified: boolean
  is_admin: boolean
  created_at: string
  updated_at: string
  last_login?: string
  preferences: Record<string, any>
  daily_quota: number
  concurrent_limit: number
  total_analyses: number
  successful_analyses: number
  failed_analyses: number
}

// 创建用户参数
export interface CreateUserParams {
  username: string
  email: string
  password: string
  is_admin?: boolean
  daily_quota?: number
  concurrent_limit?: number
}

// 编辑用户参数
export interface UpdateUserParams {
  email?: string
  daily_quota?: number
  concurrent_limit?: number
  is_active?: boolean
  is_verified?: boolean
  is_admin?: boolean
}

export const adminApi = {
  // === Dashboard ===
  getDashboard: () =>
    ApiClient.get('/api/admin/dashboard'),

  // === 用户 CRUD ===
  getUsers: (params?: UserListParams) =>
    ApiClient.get('/api/admin/users', params),

  getUserDetail: (userId: string) =>
    ApiClient.get(`/api/admin/users/${userId}`),

  createUser: (data: CreateUserParams) =>
    ApiClient.post('/api/admin/users', data),

  updateUser: (userId: string, data: UpdateUserParams) =>
    ApiClient.put(`/api/admin/users/${userId}`, data),

  deleteUser: (userId: string) =>
    ApiClient.delete(`/api/admin/users/${userId}`),

  activateUser: (userId: string) =>
    ApiClient.post(`/api/admin/users/${userId}/activate`),

  deactivateUser: (userId: string) =>
    ApiClient.post(`/api/admin/users/${userId}/deactivate`),

  resetPassword: (userId: string, newPassword: string) =>
    ApiClient.post(`/api/admin/users/${userId}/reset-password`, { new_password: newPassword }),

  // === 用户数据 ===
  getUserAnalyses: (userId: string, params?: { page?: number; page_size?: number; status?: string }) =>
    ApiClient.get(`/api/admin/users/${userId}/analyses`, params),

  getUserReports: (userId: string, params?: { page?: number; page_size?: number }) =>
    ApiClient.get(`/api/admin/users/${userId}/reports`, params),

  getUserFavorites: (userId: string) =>
    ApiClient.get(`/api/admin/users/${userId}/favorites`),

  getUserLogs: (userId: string, params?: { page?: number; page_size?: number; action_type?: string }) =>
    ApiClient.get(`/api/admin/users/${userId}/logs`, params),

  getUserStats: (userId: string) =>
    ApiClient.get(`/api/admin/users/${userId}/stats`),

  // === 删除操作 ===
  deleteUserAnalysis: (userId: string, taskId: string) =>
    ApiClient.delete(`/api/admin/users/${userId}/analyses/${taskId}`),

  cancelUserAnalysis: (userId: string, taskId: string) =>
    ApiClient.post(`/api/admin/users/${userId}/analyses/${taskId}/cancel`),

  markUserAnalysisFailed: (userId: string, taskId: string) =>
    ApiClient.post(`/api/admin/users/${userId}/analyses/${taskId}/mark-failed`),

  deleteUserReport: (userId: string, reportId: string) =>
    ApiClient.delete(`/api/admin/users/${userId}/reports/${reportId}`),

  deleteUserFavorite: (userId: string, stockCode: string) =>
    ApiClient.delete(`/api/admin/users/${userId}/favorites/${encodeURIComponent(stockCode)}`),

  // === 全局日志 ===
  getLogs: (params?: { page?: number; page_size?: number; action_type?: string; success?: boolean; keyword?: string; user_id?: string }) =>
    ApiClient.get('/api/admin/logs', params),
}
