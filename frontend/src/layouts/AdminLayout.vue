<template>
  <div class="admin-layout">
    <!-- 侧边栏 -->
    <aside
      class="admin-sidebar"
      :class="{ collapsed: sidebarCollapsed }"
      :style="{ width: sidebarCollapsed ? '64px' : '240px' }"
    >
      <div class="sidebar-header">
        <div class="logo">
          <span class="logo-icon">SQ</span>
          <span v-show="!sidebarCollapsed" class="logo-text">SinoQuant</span>
        </div>
      </div>

      <nav class="sidebar-nav">
        <el-menu
          :default-active="activeMenu"
          :collapse="sidebarCollapsed"
          router
          background-color="transparent"
          text-color="var(--el-text-color-regular)"
          active-text-color="var(--el-color-primary)"
        >
          <el-menu-item index="/admin">
            <el-icon><DataBoard /></el-icon>
            <template #title>管理面板</template>
          </el-menu-item>

          <el-menu-item index="/admin/users">
            <el-icon><User /></el-icon>
            <template #title>用户管理</template>
          </el-menu-item>

          <el-menu-item index="/admin/system">
            <el-icon><Monitor /></el-icon>
            <template #title>系统监控</template>
          </el-menu-item>

          <el-menu-item index="/admin/logs">
            <el-icon><Document /></el-icon>
            <template #title>操作日志</template>
          </el-menu-item>
        </el-menu>
      </nav>

      <div class="sidebar-footer">
        <div class="admin-user-info">
          <el-icon><UserFilled /></el-icon>
          <span v-show="!sidebarCollapsed" class="admin-label">{{ authStore.userDisplayName }}</span>
        </div>
      </div>
    </aside>

    <!-- 移动端蒙层 -->
    <div
      v-if="isMobile && !sidebarCollapsed"
      class="sidebar-overlay"
      @click="sidebarCollapsed = true"
    ></div>

    <!-- 主内容区 -->
    <div
      class="main-container"
      :style="{ marginLeft: isMobile ? '0' : (sidebarCollapsed ? '64px' : '240px') }"
    >
      <!-- 顶部栏 -->
      <header class="admin-header">
        <div class="header-left">
          <el-button type="text" @click="sidebarCollapsed = !sidebarCollapsed" class="sidebar-toggle">
            <el-icon><Expand v-if="sidebarCollapsed" /><Fold v-else /></el-icon>
          </el-button>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/admin' }">管理后台</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentPageTitle">{{ currentPageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <el-tag type="danger" effect="dark" size="small">管理员</el-tag>
          <el-button type="text" @click="handleLogout">
            <el-icon><SwitchButton /></el-icon>
            退出登录
          </el-button>
        </div>
      </header>

      <!-- 页面内容 -->
      <main class="main-content">
        <router-view v-slot="{ Component, route }">
          <transition name="fade" mode="out-in" appear>
            <component :is="Component" :key="route.fullPath" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DataBoard, User, Monitor, Document, UserFilled, Expand, Fold, SwitchButton } from '@element-plus/icons-vue'

const route = useRoute()
const authStore = useAuthStore()
const { width } = useWindowSize()

const sidebarCollapsed = ref(width.value < 768)
const isMobile = computed(() => width.value < 768)

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/admin/users')) return '/admin/users'
  if (path.startsWith('/admin/system')) return '/admin/system'
  if (path.startsWith('/admin/logs')) return '/admin/logs'
  return '/admin'
})

const pageTitles: Record<string, string> = {
  '/admin': '',
  '/admin/users': '用户管理',
  '/admin/system': '系统监控',
  '/admin/logs': '操作日志',
}

const currentPageTitle = computed(() => {
  if (route.path.match(/^\/admin\/users\/\w+$/)) return '用户详情'
  return pageTitles[route.path] || ''
})

watch(width, (newWidth) => {
  if (newWidth < 768) {
    sidebarCollapsed.value = true
  }
})

watch(() => route.fullPath, () => {
  if (isMobile.value) {
    sidebarCollapsed.value = true
  }
})

const handleLogout = () => {
  authStore.logout()
}
</script>

<style lang="scss" scoped>
.admin-layout {
  min-height: 100vh;
  background: #f8f9fa;

  :root.dark &,
  html.dark & {
    background: #1f1f1f;
  }
}

.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  z-index: 950;
}

.admin-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  background: #2a2a2a;
  border-right: 1px solid #333333;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 1000;
  display: flex;
  flex-direction: column;

  :root.dark &,
  html.dark & {
    background: #252525;
  }

  .sidebar-header {
    height: 60px;
    display: flex;
    align-items: center;
    padding: 0 16px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);

    .logo {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;

      .logo-icon {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: #42b983;
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 14px;
        letter-spacing: -0.02em;
      }

      .logo-text {
        font-size: 18px;
        font-weight: 700;
        color: #ffffff;
        white-space: nowrap;
        letter-spacing: -0.02em;
      }
    }
  }

  .sidebar-nav {
    flex: 1;
    overflow-y: auto;
    padding: 10px 0;

    :deep(.el-menu) {
      background: transparent;
      border-right: none;

      .el-menu-item {
        color: rgba(255, 255, 255, 0.65) !important;
        margin: 2px 8px;
        border-radius: 8px;

        &:hover {
          background: rgba(255, 255, 255, 0.08);
          color: #ffffff !important;
        }

        &.is-active {
          background: rgba(66, 185, 131, 0.2);
          color: #42b983 !important;
          font-weight: 580;
        }
      }
    }
  }

  .sidebar-footer {
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    padding: 12px;

    .admin-user-info {
      display: flex;
      align-items: center;
      gap: 8px;
      color: rgba(255, 255, 255, 0.55);
      font-size: 13px;

      .admin-label {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
    }
  }
}

.main-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  transition: margin-left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.admin-header {
  height: 60px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 999;

  :root.dark &,
  html.dark & {
    background: rgba(31, 31, 31, 0.8);
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 14px;

    .sidebar-toggle {
      padding: 6px;
      border-radius: 8px;
      transition: background 0.2s ease;

      &:hover {
        background: rgba(0, 0, 0, 0.04);
      }

      :root.dark &,
      html.dark & {
        &:hover { background: rgba(255, 255, 255, 0.06); }
      }

      .el-icon { font-size: 18px; }
    }
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 12px;
  }
}

.main-content {
  flex: 1;
  padding: 24px;
  max-width: 1400px;
  width: 100%;
  box-sizing: border-box;
}

@media (max-width: 768px) {
  .admin-sidebar {
    transform: translateX(-100%);
    &:not(.collapsed) {
      transform: translateX(0);
    }
  }
  .main-content {
    padding: 16px;
  }
  .admin-header {
    padding: 0 16px;
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
