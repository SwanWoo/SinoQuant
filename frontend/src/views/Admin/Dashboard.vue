<template>
  <div class="admin-dashboard">
    <!-- 概览卡片 -->
    <el-row :gutter="20" class="stat-cards">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon" style="background: #ecf5ff">
              <el-icon :size="28" color="#42b983"><User /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ dashboardData.user_stats?.total ?? '-' }}</div>
              <div class="stat-label">总用户数</div>
            </div>
          </div>
          <div class="stat-footer">
            今日新增 <strong>{{ dashboardData.user_stats?.today_new ?? 0 }}</strong>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon" style="background: #f0f9eb">
              <el-icon :size="28" color="#67c23a"><UserFilled /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ dashboardData.user_stats?.active ?? '-' }}</div>
              <div class="stat-label">活跃用户</div>
            </div>
          </div>
          <div class="stat-footer">
            管理员 <strong>{{ dashboardData.user_stats?.admins ?? 0 }}</strong>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon" style="background: #fdf6ec">
              <el-icon :size="28" color="#e6a23c"><TrendCharts /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ dashboardData.analysis_stats?.total ?? '-' }}</div>
              <div class="stat-label">总分析数</div>
            </div>
          </div>
          <div class="stat-footer">
            今日 <strong>{{ dashboardData.analysis_stats?.today ?? 0 }}</strong>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon" style="background: #fef0f0">
              <el-icon :size="28" color="#f56c6c"><Loading /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ dashboardData.analysis_stats?.processing ?? 0 }}</div>
              <div class="stat-label">进行中</div>
            </div>
          </div>
          <div class="stat-footer">
            待处理 <strong>{{ dashboardData.analysis_stats?.pending ?? 0 }}</strong>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <!-- 7天趋势 -->
      <el-col :xs="24" :lg="14">
        <el-card shadow="hover">
          <template #header>
            <span>近7天分析趋势</span>
          </template>
          <div v-if="dashboardData.daily_trend?.length" class="trend-chart">
            <div class="trend-bar" v-for="item in dashboardData.daily_trend" :key="item.date">
              <div class="trend-bar-wrapper">
                <div
                  class="trend-bar-fill"
                  :style="{ height: getBarHeight(item.analyses) + '%' }"
                >
                  <span class="trend-bar-value">{{ item.analyses }}</span>
                </div>
              </div>
              <span class="trend-bar-date">{{ item.date ? item.date.slice(5) : '-' }}</span>
            </div>
          </div>
          <el-empty v-else description="暂无数据" :image-size="80" />
        </el-card>
      </el-col>

      <!-- 最近注册用户 -->
      <el-col :xs="24" :lg="10">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>最近注册用户</span>
              <el-button type="primary" link @click="$router.push('/admin/users')">查看全部</el-button>
            </div>
          </template>
          <el-table :data="dashboardData.recent_users || []" size="small" stripe>
            <el-table-column prop="username" label="用户名" min-width="100" show-overflow-tooltip />
            <el-table-column prop="email" label="邮箱" min-width="150" show-overflow-tooltip />
            <el-table-column prop="created_at" label="注册时间" min-width="160">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { adminApi } from '@/api/admin'
import { User, UserFilled, TrendCharts, Loading } from '@element-plus/icons-vue'

const dashboardData = ref<any>({})

const maxAnalyses = ref(1)

const getBarHeight = (analyses: number) => {
  return maxAnalyses.value > 0 ? (analyses / maxAnalyses.value) * 100 : 0
}

const formatDate = (dt: string) => {
  if (!dt) return '-'
  return dt.replace('T', ' ').slice(0, 19)
}

const loadDashboard = async () => {
  try {
    const res = await adminApi.getDashboard()
    if (res.success) {
      dashboardData.value = res.data
      if (res.data.daily_trend?.length) {
        maxAnalyses.value = Math.max(...res.data.daily_trend.map((d: any) => d.analyses), 1)
      }
    }
  } catch (e) {
    console.error('加载管理面板失败:', e)
  }
}

onMounted(() => {
  loadDashboard()
})
</script>

<style lang="scss" scoped>
.admin-dashboard {
  .stat-card {
    .stat-content {
      display: flex;
      align-items: center;
      gap: 16px;

      .stat-icon {
        width: 56px;
        height: 56px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
      }

      .stat-info {
        .stat-value {
          font-size: 28px;
          font-weight: 700;
          color: var(--el-text-color-primary);
          line-height: 1.2;
        }
        .stat-label {
          font-size: 13px;
          color: var(--el-text-color-secondary);
          margin-top: 4px;
        }
      }
    }
    .stat-footer {
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--el-border-color-lighter);
      font-size: 13px;
      color: var(--el-text-color-secondary);
    }
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .trend-chart {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    height: 200px;
    padding: 0 8px;

    .trend-bar {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      height: 100%;

      .trend-bar-wrapper {
        flex: 1;
        width: 100%;
        display: flex;
        align-items: flex-end;
        justify-content: center;
      }

      .trend-bar-fill {
        width: 70%;
        max-width: 40px;
        background: linear-gradient(180deg, #42b983 0%, #68c99e 100%);
        border-radius: 4px 4px 0 0;
        position: relative;
        min-height: 2px;
        transition: height 0.5s ease;

        .trend-bar-value {
          position: absolute;
          top: -22px;
          left: 50%;
          transform: translateX(-50%);
          font-size: 12px;
          color: var(--el-text-color-regular);
          white-space: nowrap;
        }
      }

      .trend-bar-date {
        margin-top: 8px;
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }
    }
  }
}
</style>
