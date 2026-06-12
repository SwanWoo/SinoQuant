<template>
  <div class="system-monitor-page">
    <el-row :gutter="20">
      <!-- 数据库状态 -->
      <el-col :xs="24" :sm="12" :lg="8">
        <el-card shadow="hover">
          <template #header>
            <span>MongoDB</span>
          </template>
          <div class="db-status">
            <el-icon :size="24" :color="health.mongodb?.status === 'healthy' ? '#67c23a' : '#f56c6c'">
              <CircleCheckFilled v-if="health.mongodb?.status === 'healthy'" />
              <CircleCloseFilled v-else />
            </el-icon>
            <span class="status-text">
              {{ health.mongodb?.status === 'healthy' ? '已连接' : '未连接' }}
            </span>
          </div>
        </el-card>
      </el-col>

      <!-- Redis 状态 -->
      <el-col :xs="24" :sm="12" :lg="8">
        <el-card shadow="hover">
          <template #header>
            <span>Redis</span>
          </template>
          <div class="db-status">
            <el-icon :size="24" :color="health.redis?.status === 'healthy' ? '#67c23a' : '#f56c6c'">
              <CircleCheckFilled v-if="health.redis?.status === 'healthy'" />
              <CircleCloseFilled v-else />
            </el-icon>
            <span class="status-text">
              {{ health.redis?.status === 'healthy' ? '已连接' : '未连接' }}
            </span>
          </div>
        </el-card>
      </el-col>

      <!-- 系统概览 -->
      <el-col :xs="24" :sm="12" :lg="8">
        <el-card shadow="hover">
          <template #header>
            <span>快速统计</span>
          </template>
          <div class="quick-stats" v-loading="dashboardLoading">
            <div class="stat-item">
              <span class="stat-label">总用户</span>
              <span class="stat-value">{{ dashboard.user_stats?.total ?? '-' }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">今日分析</span>
              <span class="stat-value">{{ dashboard.analysis_stats?.today ?? '-' }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">进行中</span>
              <span class="stat-value">{{ dashboard.analysis_stats?.processing ?? 0 }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top: 20px">
      <template #header>
        <div class="card-header">
          <span>近7天趋势</span>
          <el-button type="primary" link @click="loadData">刷新</el-button>
        </div>
      </template>
      <div v-if="dashboard.daily_trend?.length" class="trend-chart">
        <div class="trend-bar" v-for="item in dashboard.daily_trend" :key="item.date">
          <div class="trend-bar-wrapper">
            <div class="trend-bar-fill" :style="{ height: getBarHeight(item.analyses) + '%' }">
              <span class="trend-bar-value">{{ item.analyses }}</span>
            </div>
          </div>
          <span class="trend-bar-date">{{ item.date ? item.date.slice(5) : '-' }}</span>
        </div>
      </div>
      <el-empty v-else description="暂无趋势数据" :image-size="80" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { adminApi } from '@/api/admin'
import { CircleCheckFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import { ApiClient } from '@/api/request'

const health = ref<any>({ mongodb: { status: 'checking' }, redis: { status: 'checking' } })
const dashboard = ref<any>({})
const dashboardLoading = ref(false)
const maxAnalyses = ref(1)

const getBarHeight = (analyses: number) => {
  return maxAnalyses.value > 0 ? (analyses / maxAnalyses.value) * 100 : 0
}

const loadHealth = async () => {
  try {
    const res = await ApiClient.get('/api/health')
    health.value = {
      mongodb: { status: res.success ? 'healthy' : 'unhealthy' },
      redis: { status: res.success ? 'healthy' : 'unhealthy' },
    }
  } catch (e) {
    health.value = { mongodb: { status: 'unhealthy' }, redis: { status: 'unhealthy' } }
  }
}

const loadDashboard = async () => {
  dashboardLoading.value = true
  try {
    const res = await adminApi.getDashboard()
    if (res.success) {
      dashboard.value = res.data
      if (res.data.daily_trend?.length) {
        maxAnalyses.value = Math.max(...res.data.daily_trend.map((d: any) => d.analyses), 1)
      }
    }
  } catch (e) {
    console.error('加载面板数据失败:', e)
  } finally {
    dashboardLoading.value = false
  }
}

const loadData = () => {
  loadHealth()
  loadDashboard()
}

let healthTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  loadData()
  healthTimer = setInterval(loadHealth, 30000)
})

onUnmounted(() => {
  if (healthTimer) {
    clearInterval(healthTimer)
  }
})
</script>

<style lang="scss" scoped>
.db-status {
  display: flex;
  align-items: center;
  gap: 12px;
  .status-text {
    font-size: 16px;
    font-weight: 500;
  }
}

.quick-stats {
  display: flex;
  justify-content: space-around;
  .stat-item {
    text-align: center;
    .stat-label {
      display: block;
      font-size: 13px;
      color: var(--el-text-color-secondary);
      margin-bottom: 4px;
    }
    .stat-value {
      display: block;
      font-size: 24px;
      font-weight: 700;
      color: var(--el-text-color-primary);
    }
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
</style>
