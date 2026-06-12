<template>
  <div class="user-list-page">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="search-card">
      <el-form :inline="true" :model="searchForm" @submit.prevent="handleSearch">
        <el-form-item label="关键词">
          <el-input
            v-model="searchForm.keyword"
            placeholder="用户名或邮箱"
            clearable
            style="width: 200px"
            @clear="handleSearch"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.active_only" clearable placeholder="全部" style="width: 120px">
            <el-option label="活跃" :value="true" />
            <el-option label="已禁用" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
          <el-button type="success" @click="showCreateDialog">创建用户</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 用户表格 -->
    <el-card shadow="never" style="margin-top: 16px">
      <el-table
        v-loading="loading"
        :data="users"
        stripe
        style="width: 100%"
      >
        <el-table-column prop="username" label="用户名" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <el-button type="primary" link @click="$router.push(`/admin/users/${row.id}`)">
              {{ row.username }}
            </el-button>
          </template>
        </el-table-column>

        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />

        <el-table-column label="角色" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" type="danger" size="small">管理员</el-tag>
            <el-tag v-else size="small">用户</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small" effect="plain">
              {{ row.is_active ? '活跃' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="配额" width="80" align="center">
          <template #default="{ row }">
            {{ row.total_analyses }}/{{ row.daily_quota }}
          </template>
        </el-table-column>

        <el-table-column label="成功率" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.total_analyses > 0">
              {{ ((row.successful_analyses / row.total_analyses) * 100).toFixed(1) }}%
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>

        <el-table-column prop="last_login" label="最后登录" min-width="160">
          <template #default="{ row }">{{ formatDate(row.last_login) }}</template>
        </el-table-column>

        <el-table-column prop="created_at" label="注册时间" min-width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>

        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="showEditDialog(row)">编辑</el-button>
            <el-button
              size="small" link type="warning"
              @click="showResetPasswordDialog(row)"
            >重置密码</el-button>
            <el-button
              v-if="row.is_active"
              size="small" link type="danger"
              @click="handleDeactivate(row)"
            >停用</el-button>
            <el-button
              v-else
              size="small" link type="success"
              @click="handleActivate(row)"
            >激活</el-button>
            <el-popconfirm title="确认删除该用户？（将禁用账户）" @confirm="handleDeleteUser(row)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="loadUsers"
        />
      </div>
    </el-card>

    <!-- 创建/编辑用户弹窗 -->
    <UserForm
      v-model:visible="formVisible"
      :edit-user="editingUser"
      @success="handleFormSuccess"
    />

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="resetPasswordVisible" title="重置密码" width="400px">
      <el-form :model="resetPasswordForm" label-width="100px">
        <el-form-item label="用户名">
          <el-input :model-value="resetPasswordTarget?.username" disabled />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="resetPasswordForm.new_password" type="password" show-password placeholder="至少6位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetPasswordVisible = false">取消</el-button>
        <el-button type="primary" @click="handleResetPassword" :loading="resetPasswordLoading">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { adminApi, type AdminUser } from '@/api/admin'
import { ElMessage, ElMessageBox } from 'element-plus'
import UserForm from './UserForm.vue'

const loading = ref(false)
const users = ref<AdminUser[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const searchForm = reactive({
  keyword: '',
  active_only: undefined as boolean | undefined,
})

const formVisible = ref(false)
const editingUser = ref<AdminUser | null>(null)

const resetPasswordVisible = ref(false)
const resetPasswordTarget = ref<AdminUser | null>(null)
const resetPasswordForm = reactive({ new_password: '' })
const resetPasswordLoading = ref(false)

const formatDate = (dt: string) => {
  if (!dt) return '-'
  return dt.replace('T', ' ').slice(0, 19)
}

const loadUsers = async () => {
  loading.value = true
  try {
    const res = await adminApi.getUsers({
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
      keyword: searchForm.keyword || undefined,
      active_only: searchForm.active_only,
    })
    if (res.success) {
      users.value = res.data.users
      total.value = res.data.total
    }
  } catch (e) {
    console.error('加载用户列表失败:', e)
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadUsers()
}

const handleSizeChange = () => {
  currentPage.value = 1
  loadUsers()
}

const resetSearch = () => {
  searchForm.keyword = ''
  searchForm.active_only = undefined
  currentPage.value = 1
  loadUsers()
}

const showCreateDialog = () => {
  editingUser.value = null
  formVisible.value = true
}

const showEditDialog = (user: AdminUser) => {
  editingUser.value = user
  formVisible.value = true
}

const handleFormSuccess = () => {
  formVisible.value = false
  loadUsers()
}

const showResetPasswordDialog = (user: AdminUser) => {
  resetPasswordTarget.value = user
  resetPasswordForm.new_password = ''
  resetPasswordVisible.value = true
}

const handleResetPassword = async () => {
  if (!resetPasswordTarget.value || !resetPasswordForm.new_password) {
    ElMessage.warning('请输入新密码')
    return
  }
  if (resetPasswordForm.new_password.length < 6) {
    ElMessage.warning('密码至少6位')
    return
  }
  resetPasswordLoading.value = true
  try {
    const res = await adminApi.resetPassword(resetPasswordTarget.value.id, resetPasswordForm.new_password)
    if (res.success) {
      ElMessage.success(res.message)
      resetPasswordVisible.value = false
    }
  } catch (e) {
    console.error('重置密码失败:', e)
  } finally {
    resetPasswordLoading.value = false
  }
}

const handleDeactivate = async (user: AdminUser) => {
  try {
    await ElMessageBox.confirm(`确认停用用户 "${user.username}"？`, '停用用户', { type: 'warning' })
    const res = await adminApi.deactivateUser(user.id)
    if (res.success) {
      ElMessage.success(res.message)
      loadUsers()
    }
  } catch {}
}

const handleActivate = async (user: AdminUser) => {
  try {
    await ElMessageBox.confirm(`确认激活用户 "${user.username}"？`, '激活用户', { type: 'info' })
    const res = await adminApi.activateUser(user.id)
    if (res.success) {
      ElMessage.success(res.message)
      loadUsers()
    }
  } catch {}
}

const handleDeleteUser = async (user: AdminUser) => {
  try {
    const res = await adminApi.deleteUser(user.id)
    if (res.success) {
      ElMessage.success(res.message)
      loadUsers()
    }
  } catch (e) {
    console.error('删除用户失败:', e)
  }
}

onMounted(() => {
  loadUsers()
})
</script>

<style lang="scss" scoped>
.search-card {
  :deep(.el-card__body) {
    padding-bottom: 2px;
  }
}
.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
