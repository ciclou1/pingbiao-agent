<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

/**
 * 原文查看抽屉：用 10 分钟有效的短令牌把 PDF 塞进 iframe，
 * 借浏览器内置阅读器的 #page=N 直接定位到证据页。
 */
const props = defineProps({
  projectId: { type: Number, required: true },
})

const open = ref(false)
const url = ref('')
const title = ref('')

async function show({ docId, page = 1, label = '' }) {
  if (!docId) return ElMessage.warning('该证据缺少文件信息')
  try {
    const { token } = await api.post(`/projects/${props.projectId}/documents/${docId}/view-token`)
    url.value = `/api/v1/documents/${docId}/file?vt=${token}#page=${page}`
    title.value = `${label || '文件'} · 第 ${page} 页`
    open.value = true
  } catch {
    /* interceptor 已提示 */
  }
}

defineExpose({ show })
</script>

<template>
  <el-drawer v-model="open" :title="title" size="72%">
    <iframe v-if="url" :src="url" style="width: 100%; height: 100%; border: 0" />
  </el-drawer>
</template>
