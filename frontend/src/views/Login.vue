<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

const router = useRouter()
const form = ref({ username: '', password: '' })
const loading = ref(false)

async function login() {
  loading.value = true
  try {
    const data = await api.post('/auth/login', form.value)
    localStorage.setItem('pb_token', data.token)
    localStorage.setItem('pb_user', JSON.stringify(data.user))
    router.push('/projects')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">辅助评标智能体</h2>
      <el-form @submit.prevent="login">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" autofocus />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password @keyup.enter="login" />
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" native-type="submit">
          登 录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f3a5f 0%, #2c5f8a 100%);
}
.login-card {
  width: 380px;
  padding: 8px 12px;
}
.title {
  text-align: center;
  color: #1f3a5f;
  margin-bottom: 24px;
}
</style>
