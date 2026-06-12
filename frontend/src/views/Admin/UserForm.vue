<template>
  <el-dialog
    :model-value="visible"
    :title="editUser ? '编辑用户' : '创建用户'"
    width="500px"
    @update:model-value="$emit('update:visible', $event)"
    @closed="resetForm"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
      <el-form-item label="用户名" prop="username">
        <el-input v-model="form.username" :disabled="!!editUser" placeholder="3-50个字符" />
      </el-form-item>

      <el-form-item label="邮箱" prop="email">
        <el-input v-model="form.email" placeholder="user@example.com" />
      </el-form-item>

      <el-form-item v-if="!editUser" label="密码" prop="password">
        <el-input v-model="form.password" type="password" show-password placeholder="至少6位" />
      </el-form-item>

      <el-form-item label="角色">
        <el-switch v-model="form.is_admin" active-text="管理员" inactive-text="普通用户" />
      </el-form-item>

      <el-form-item label="每日配额">
        <el-input-number v-model="form.daily_quota" :min="0" :max="100000" :step="100" />
      </el-form-item>

      <el-form-item label="并发限制">
        <el-input-number v-model="form.concurrent_limit" :min="1" :max="50" />
      </el-form-item>

      <template v-if="editUser">
        <el-form-item label="账户状态">
          <el-switch v-model="form.is_active" active-text="活跃" inactive-text="禁用" />
        </el-form-item>

        <el-form-item label="邮箱验证">
          <el-switch v-model="form.is_verified" active-text="已验证" inactive-text="未验证" />
        </el-form-item>
      </template>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" @click="handleSubmit" :loading="submitting">
        {{ editUser ? '保存' : '创建' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, watch, computed } from 'vue'
import { adminApi, type AdminUser, type CreateUserParams, type UpdateUserParams } from '@/api/admin'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

const props = defineProps<{
  visible: boolean
  editUser: AdminUser | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'success': []
}>()

const formRef = ref<FormInstance>()
const submitting = ref(false)

const form = reactive({
  username: '',
  email: '',
  password: '',
  is_admin: false,
  daily_quota: 1000,
  concurrent_limit: 3,
  is_active: true,
  is_verified: false,
})

const rules = computed<FormRules>(() => ({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '3-50个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: props.editUser ? [] : [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '至少6位', trigger: 'blur' },
  ],
}))

watch(() => props.visible, (val) => {
  if (val) {
    if (props.editUser) {
      form.username = props.editUser.username
      form.email = props.editUser.email
      form.password = ''
      form.is_admin = props.editUser.is_admin
      form.daily_quota = props.editUser.daily_quota
      form.concurrent_limit = props.editUser.concurrent_limit
      form.is_active = props.editUser.is_active
      form.is_verified = props.editUser.is_verified
    } else {
      // 创建模式：重置表单，避免残留上次编辑的数据
      form.username = ''
      form.email = ''
      form.password = ''
      form.is_admin = false
      form.daily_quota = 1000
      form.concurrent_limit = 3
      form.is_active = true
      form.is_verified = false
    }
  }
})

const resetForm = () => {
  form.username = ''
  form.email = ''
  form.password = ''
  form.is_admin = false
  form.daily_quota = 1000
  form.concurrent_limit = 3
  form.is_active = true
  form.is_verified = false
  formRef.value?.resetFields()
}

const handleSubmit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    if (props.editUser) {
      const data: UpdateUserParams = {
        email: form.email,
        is_admin: form.is_admin,
        daily_quota: form.daily_quota,
        concurrent_limit: form.concurrent_limit,
        is_active: form.is_active,
        is_verified: form.is_verified,
      }
      const res = await adminApi.updateUser(props.editUser.id, data)
      if (res.success) {
        ElMessage.success('用户更新成功')
        emit('success')
      }
    } else {
      const data: CreateUserParams = {
        username: form.username,
        email: form.email,
        password: form.password,
        is_admin: form.is_admin,
        daily_quota: form.daily_quota,
        concurrent_limit: form.concurrent_limit,
      }
      const res = await adminApi.createUser(data)
      if (res.success) {
        ElMessage.success(res.message)
        emit('success')
      }
    }
  } catch (e) {
    console.error('保存用户失败:', e)
  } finally {
    submitting.value = false
  }
}
</script>
