<template>
  <div class="basic-layout">
    <!-- 侧边栏 -->
    <aside
      class="sidebar"
      :class="{ collapsed: appStore.sidebarCollapsed }"
      :style="{ width: appStore.actualSidebarWidth + 'px' }"
    >
      <div class="sidebar-header">
        <div class="logo">
          <span v-show="appStore.sidebarCollapsed" class="logo-icon">SQ</span>
          <span v-show="!appStore.sidebarCollapsed" class="logo-text">
            SinoQuant
          </span>
        </div>
      </div>

      <nav class="sidebar-nav">
        <SidebarMenu />
      </nav>

      <div class="sidebar-footer">
        <UserProfile />
      </div>
    </aside>

    <!-- 点击蒙层：移动端展开时，点击空白处收起侧边栏 -->
    <div
      v-if="isMobile && !appStore.sidebarCollapsed"
      class="sidebar-overlay"
      @click="appStore.setSidebarCollapsed(true)"
    ></div>

    <!-- 主内容区 -->
    <div class="main-container" :style="{ marginLeft: appStore.actualSidebarWidth + 'px' }" @click="handleMainClick">
      <!-- 顶部导航栏 -->
      <header class="header">
        <div class="header-left">
          <el-button
            type="text"
            @click.stop="appStore.toggleSidebar()"
            class="sidebar-toggle"
          >
            <el-icon><Expand v-if="appStore.sidebarCollapsed" /><Fold v-else /></el-icon>
          </el-button>

          <Breadcrumb />
        </div>

        <div class="header-right">
          <HeaderActions />
        </div>
      </header>

      <!-- 页面内容 -->
      <main class="main-content">
        <div class="content-wrapper">
          <router-view v-slot="{ Component, route }">
            <transition
              :name="route.meta.transition || 'fade'"
              mode="out-in"
              appear
            >
              <keep-alive :include="keepAliveComponents">
                <component :is="Component" :key="route.fullPath" />
              </keep-alive>
            </transition>
          </router-view>
        </div>
      </main>

      <!-- 页脚 -->
      <footer class="footer">
        <AppFooter />
      </footer>
    </div>

    <!-- 回到顶部 -->
    <el-backtop :right="40" :bottom="40" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'
import SidebarMenu from '@/components/Layout/SidebarMenu.vue'
import UserProfile from '@/components/Layout/UserProfile.vue'
import Breadcrumb from '@/components/Layout/Breadcrumb.vue'
import HeaderActions from '@/components/Layout/HeaderActions.vue'
import AppFooter from '@/components/Layout/AppFooter.vue'
import { Expand, Fold } from '@element-plus/icons-vue'

const appStore = useAppStore()
const route = useRoute()
const { width } = useWindowSize()

// 需要缓存的组件
const keepAliveComponents = computed(() => [
  'StockScreening',
  'AnalysisHistory',
  'QueueManagement'
])

// 移动端判断
const isMobile = computed(() => width.value < 768)

// 点击主内容时，若移动端且侧边栏已展开，则收起
const handleMainClick = () => {
  if (isMobile.value && !appStore.sidebarCollapsed) {
    appStore.setSidebarCollapsed(true)
  }
}

// 监听窗口大小变化：在小屏幕上自动折叠侧边栏
watch(width, (newWidth) => {
  if (newWidth < 768 && !appStore.sidebarCollapsed) {
    appStore.setSidebarCollapsed(true)
  }
})

// 路由变化时，移动端收起侧边栏
watch(() => route.fullPath, () => {
  if (isMobile.value) {
    appStore.setSidebarCollapsed(true)
  }
})
</script>

<style lang="scss" scoped>
.basic-layout {
  min-height: 100vh;
  background: var(--el-fill-color-light);
}

:root.dark .basic-layout,
html.dark .basic-layout {
  background: #1f1f1f;
}

.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  z-index: 950;
}

.sidebar {
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

  &.collapsed {
    width: 64px !important;
  }

  .sidebar-header {
    height: 60px;
    display: flex;
    align-items: center;
    padding: 0 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);

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
    padding: 12px 0;
  }

  .sidebar-footer {
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    padding: 6px;
  }
}

.main-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  transition: margin-left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.header {
  height: 60px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 999;

  .header-left {
    display: flex;
    align-items: center;
    gap: 14px;

    .sidebar-toggle {
      padding: 6px;
      border-radius: 8px;
      color: var(--el-text-color-primary);
      transition: background 0.2s;

      &:hover { background: rgba(0, 0, 0, 0.04); }
      .el-icon { font-size: 18px; }
    }
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 14px;
  }
}

.main-content {
  flex: 1;
  padding: 24px;
  min-height: calc(100vh - 60px - 56px);

  .content-wrapper {
    max-width: 1400px;
    margin: 0 auto;
  }
}

.footer {
  height: 56px;
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(8px);
  border-top: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

// dark mode
html.dark {
  .sidebar {
    background: #252525;
    border-color: #333333;
  }
  .sidebar .sidebar-header,
  .sidebar .sidebar-footer {
    border-color: rgba(255, 255, 255, 0.06);
  }
  .header {
    background: rgba(31, 31, 31, 0.85);
    border-color: rgba(255, 255, 255, 0.05);
    .sidebar-toggle { color: #c8c8c8; &:hover { background: rgba(255,255,255,0.06); } }
  }
  .footer {
    background: rgba(31, 31, 31, 0.6);
    border-color: rgba(255, 255, 255, 0.04);
  }
}

// sidebar is always dark — force light text on all interior content
.sidebar :deep(.user-profile .username) {
  color: #e0e0e0 !important;
}
.sidebar :deep(.user-profile .user-role) {
  color: #a0a0a0 !important;
}
.sidebar :deep(.user-profile .profile-info:hover) {
  background-color: rgba(255, 255, 255, 0.06) !important;
}

// responsive
@media (max-width: 768px) {
  .sidebar {
    transform: translateX(-100%);
    &:not(.collapsed) { transform: translateX(0); }
  }
  .main-container { margin-left: 0 !important; }
  .main-content { padding: 16px; }
  .header { padding: 0 16px; }
}

// transitions
.fade-enter-active,
.fade-leave-active { transition: opacity 0.25s ease; }
.fade-enter-from,
.fade-leave-to { opacity: 0; }

.slide-left-enter-active,
.slide-left-leave-active { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
.slide-left-enter-from { transform: translateX(24px); opacity: 0; }
.slide-left-leave-to { transform: translateX(-24px); opacity: 0; }
</style>
