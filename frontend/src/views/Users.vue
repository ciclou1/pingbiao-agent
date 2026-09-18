<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const users = ref([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const form = ref({ username: '', password: '', display_name: '', role: 'expert' })

const ROLE_LABELS = { admin: '系统管理员', leader: '评标组长', expert: '评标专家', observer: '监督观察' }

async function load() {
  loading.value = true
  try {
    const data = await api.get('/users')
    users.value = data.users
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.value.username.trim() || form.value.password.length < 6) {
    ElMessage.warning('用户名必填，密码至少 6 位')
    return
  }
  saving.value = true
  try {
    await api.post('/users', form.value)
    dialog.value = false
    form.value = { username: '', password: '', display_name: '', role: 'expert' }
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleActive(row) {
  const next = !row.is_active
  await ElMessageBox.confirm(`${next ? '启用' : '停用'}用户「${row.display_name || row.username}」？`, '确认')
  await api.patch(`/users/${row.id}`, { is_active: next })
  await load()
}

async function resetPassword(row) {
  const { value } = await ElMessageBox.prompt('输入新密码（至少 6 位）', `重置「${row.display_name || row.username}」的密码`, {
    inputPattern: /^.{6,}$/,
    inputErrorMessage: '至少 6 位',
  })
  await api.patch(`/users/${row.id}`, { password: value })
  ElMessage.success('已重置')
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <h3>用户管理</h3>
      <el-button type="primary" @click="dialog = true">新建用户</el-button>
    </div>
    <el-table :data="users" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="display_name" label="姓名" width="140" />
      <el-table-column label="角色" width="120">
        <template #default="{ row }">{{ ROLE_LABELS[row.role] }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link size="small" @click="resetPassword(row)">重置密码</el-button>
          <el-button link size="small" :type="row.is_active ? 'danger' : 'success'" @click="toggleActive(row)">
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="新建用户" width="460px">
      <el-form label-width="80px">
        <el-form-item label="用户名" required><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码" required><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option v-for="(label, role) in ROLE_LABELS" :key="role" :label="label" :value="role" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
</style>
