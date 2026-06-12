<template>
  <div class="register-page">
    <div class="register-container">
      <div class="register-header">
        <img src="/logo.svg" alt="SinaQuant" class="logo" />
        <h1 class="title">SinaQuant</h1>
        <p class="subtitle">创建您的账户</p>
      </div>

      <el-card class="register-card" shadow="always">
        <el-form
          :model="registerForm"
          :rules="registerRules"
          ref="registerFormRef"
          label-position="top"
          size="large"
        >
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="registerForm.username"
              placeholder="请输入用户名"
              prefix-icon="User"
            />
          </el-form-item>

          <el-form-item label="邮箱" prop="email">
            <el-input
              v-model="registerForm.email"
              placeholder="请输入邮箱"
              prefix-icon="Message"
            />
          </el-form-item>

          <el-form-item label="密码" prop="password">
            <el-input
              v-model="registerForm.password"
              type="password"
              placeholder="请输入密码（至少6位）"
              prefix-icon="Lock"
              show-password
            />
          </el-form-item>

          <el-form-item label="确认密码" prop="confirmPassword">
            <el-input
              v-model="registerForm.confirmPassword"
              type="password"
              placeholder="请再次输入密码"
              prefix-icon="Lock"
              show-password
              @keyup.enter="handleRegister"
            />
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              size="large"
              style="width: 100%"
              :loading="registerLoading"
              @click="handleRegister"
            >
              注册
            </el-button>
          </el-form-item>

          <el-form-item>
            <div class="form-options">
              <span class="login-link">
                已有账户？<el-link type="primary" @click="goToLogin">立即登录</el-link>
              </span>
            </div>
          </el-form-item>
        </el-form>
      </el-card>

      <div class="register-footer">
        <p>&copy; 2025 SinaQuant. All rights reserved.</p>
        <p class="disclaimer">
          SinaQuant 是一个 AI 多 Agents 的股票分析学习平台。平台中的分析结论、观点和"投资建议"均由 AI 自动生成，仅用于学习、研究与交流，不构成任何形式的投资建议或承诺。
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const registerFormRef = ref()
const registerLoading = ref(false)

const registerForm = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: ''
})

// 自定义验证规则
const validateConfirmPassword = (rule: any, value: string, callback: Function) => {
  if (value === '') {
    callback(new Error('请再次输入密码'))
  } else if (value !== registerForm.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const registerRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度应在3-20位之间', trigger: 'blur' }
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱格式', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于6位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, validator: validateConfirmPassword, trigger: 'blur' }
  ]
}

const handleRegister = async () => {
  // 防止重复提交
  if (registerLoading.value) {
    return
  }

  try {
    await registerFormRef.value.validate()

    registerLoading.value = true
    console.log('📝 开始注册流程...')

    // 调用注册API（转换为后端需要的下划线命名）
    const result = await authStore.register({
      username: registerForm.username,
      email: registerForm.email,
      password: registerForm.password,
      confirm_password: registerForm.confirmPassword
    })

    if (result.success) {
      console.log('✅ 注册成功')
      ElMessage.success('注册成功！正在跳转...')
      
      // 延迟跳转到仪表板
      setTimeout(() => {
        router.push('/dashboard')
      }, 1000)
    } else {
      ElMessage.error(result.message || '注册失败')
    }

  } catch (error: any) {
    console.error('注册失败:', error)
    // 只有在不是表单验证错误时才显示错误消息
    if (error.message && !error.message.includes('validate')) {
      ElMessage.error(error.message || '注册失败，请重试')
    }
  } finally {
    registerLoading.value = false
  }
}

const goToLogin = () => {
  router.push('/login')
}
</script>

<style lang="scss" scoped>
.register-page {
  min-height: 100vh;
  background: linear-gradient(135deg, #42b983 0%, #369a6d 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.register-container {
  width: 100%;
  max-width: 400px;
}

.register-header {
  text-align: center;
  margin-bottom: 32px;
  color: white;

  .logo {
    width: 64px;
    height: 64px;
    margin-bottom: 16px;
  }

  .title {
    font-size: 32px;
    font-weight: 600;
    margin: 0 0 8px 0;
  }

  .subtitle {
    font-size: 16px;
    opacity: 0.9;
    margin: 0;
  }
}

.register-card {
  .form-options {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 100%;

    .login-link {
      color: var(--el-text-color-regular);
      font-size: 14px;
    }
  }
}

.register-footer {
  text-align: center;
  margin-top: 32px;
  color: white;
  opacity: 0.9;

  p {
    margin: 0;
    font-size: 14px;
  }

  .disclaimer {
    margin-top: 8px;
    font-size: 12px;
    line-height: 1.6;
    max-width: 800px;
    margin-left: auto;
    margin-right: auto;
    color: white;
    opacity: 0.85;
  }
}
</style>
