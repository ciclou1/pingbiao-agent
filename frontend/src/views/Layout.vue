<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const user = computed(() => JSON.parse(localStorage.getItem('pb_user') || 'null'))

const ROLE_LABELS = { admin: '系统管理员', leader: '评标组长', expert: '评标专家', observer: '监督观察' }

const MENUS = [
  { path: '/projects', label: '评标项目', roles: ['admin', 'leader', 'expert', 'observer'] },
  { path: '/users', label: '用户管理', roles: ['admin'] },
  { path: '/audit', label: '审计日志', roles: ['admin', 'observer'] },
]

const menus = computed(() => MENUS.filter((m) => user.value && m.roles.includes(user.value.role)))
const isActive = (path) => route.path === path || route.path.startsWith(path + '/')

function logout() {
  localStorage.removeItem('pb_token')
  localStorage.removeItem('pb_user')
  router.push('/login')
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand" @click="router.push('/projects')">辅助评标智能体</div>
      <nav class="nav">
        <a
          v-for="m in menus"
          :key="m.path"
          class="nav-item"
          :class="{ active: isActive(m.path) }"
          @click="router.push(m.path)"
        >
          {{ m.label }}
        </a>
      </nav>
      <el-dropdown>
        <span class="user-chip">
          {{ user?.display_name || user?.username }}（{{ ROLE_LABELS[user?.role] || user?.role }}）
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item @click="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>

    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  background: #f5f7fa;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  height: 56px;
  background: #1f3a5f;
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(11, 11, 11, 0.12);
}
.brand {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 1px;
  cursor: pointer;
  white-space: nowrap;
}
.nav {
  display: flex;
  align-items: center;
  gap: 24px;
  flex: 1;
}
.nav-item {
  color: rgba(255, 255, 255, 0.72);
  font-size: 14px;
  cursor: pointer;
  padding: 6px 0;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
}
.nav-item:hover {
  color: #fff;
}
.nav-item.active {
  color: #fff;
  font-weight: 600;
  border-bottom-color: #fff;
}
.user-chip {
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.content {
  padding: 18px 24px 28px;
}
</style>
