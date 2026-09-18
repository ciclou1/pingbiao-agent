<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'

/**
 * 项目级页面统一导航头：返回（评标项目列表）+ 项目名 + 平级页面互跳。
 * 各页面的操作按钮走 #actions 插槽。
 */
const props = defineProps({
  projectId: { type: Number, required: true },
  title: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()
const project = ref(null)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isManager = ['admin', 'leader'].includes(me.role)

const LINKS = [
  { path: 'items', label: '条目库' },
  { path: 'review', label: '审查工作台' },
  { path: 'qa', label: '专家问答' },
  { path: 'compare', label: '横向对比' },
  { path: 'clearing', label: '清标与预警' },
  { path: 'score', label: '评分表' },
  { path: 'score/summary', label: '评分汇总', manager: true },
  { path: 'report', label: '评标报告', manager: true },
]

const base = computed(() => `/projects/${props.projectId}`)
const links = computed(() => LINKS.filter((l) => !l.manager || isManager))
const isActive = (p) => route.path === `${base.value}/${p}`
const onProjectHome = computed(() => route.path === base.value)

onMounted(async () => {
  try {
    project.value = await api.get(`/projects/${props.projectId}`)
  } catch {
    /* 权限或网络问题不阻塞页面 */
  }
})
</script>

<template>
  <div class="pnav">
    <div class="row">
      <el-page-header @back="router.push('/projects')">
        <template #content>
          <span class="pname" :class="{ link: !onProjectHome }" @click="router.push(base)">
            {{ project?.name || '评标项目' }}
          </span>
          <span v-if="title" class="ptitle">/ {{ title }}</span>
        </template>
      </el-page-header>
      <div class="actions">
        <el-button v-if="!onProjectHome" size="small" @click="router.push(base)">项目详情</el-button>
        <slot name="actions" />
      </div>
    </div>
    <div class="links">
      <el-link
        v-for="l in links"
        :key="l.path"
        :class="{ active: isActive(l.path) }"
        :underline="false"
        @click="router.push(`${base}/${l.path}`)"
      >
        {{ l.label }}
      </el-link>
    </div>
  </div>
</template>

<style scoped>
.pnav {
  border-bottom: 1px solid #e1e0d9;
  padding-bottom: 8px;
  margin-bottom: 14px;
}
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.pname { font-size: 16px; font-weight: 600; color: #0b0b0b; }
.pname.link { cursor: pointer; }
.pname.link:hover { color: #2a78d6; }
.ptitle { color: #898781; font-size: 13px; margin-left: 6px; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.links { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 10px; }
.links :deep(.el-link) { color: #52514e; font-size: 13px; }
.links :deep(.el-link.active) { color: #2a78d6; font-weight: 600; }
</style>
