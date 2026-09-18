<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'

const logs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await api.get('/audit-logs', { params: { page: page.value, page_size: pageSize } })
    logs.value = data.logs
    total.value = data.total
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h3>审计日志（仅追加）</h3>
    <el-table :data="logs" v-loading="loading" stripe size="small">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="created_at" label="时间" width="180">
        <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="120" />
      <el-table-column prop="action" label="动作" width="140" />
      <el-table-column label="对象" width="160">
        <template #default="{ row }">{{ row.target_type }}#{{ row.target_id }}</template>
      </el-table-column>
      <el-table-column prop="ip" label="IP" width="140" />
      <el-table-column label="详情" min-width="220">
        <template #default="{ row }">
          <code style="font-size: 12px">{{ JSON.stringify(row.detail) }}</code>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      style="margin-top: 12px"
      layout="prev, pager, next, total"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="(p) => { page = p; load() }"
    />
  </div>
</template>
