<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const router = useRouter()
const projects = ref([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const form = ref({ name: '', type: 'goods', method_type: 'comprehensive', control_price: null, description: '' })

const TYPE_LABELS = { goods: '货物', engineering: '工程', service: '服务' }
const METHOD_LABELS = { comprehensive: '综合评分法', lowest_price: '最低价法' }
const STATUS_LABELS = { draft: '编制中', reviewing: '评审中', closed: '已结束' }
const canCreate = ['admin', 'leader'].includes(JSON.parse(localStorage.getItem('pb_user') || '{}').role)

async function load() {
  loading.value = true
  try {
    const data = await api.get('/projects')
    projects.value = data.projects
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  saving.value = true
  try {
    await api.post('/projects', form.value)
    dialog.value = false
    form.value = { name: '', type: 'goods', method_type: 'comprehensive', control_price: null, description: '' }
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <h3>评标项目</h3>
      <el-button v-if="canCreate" type="primary" @click="dialog = true">新建项目</el-button>
    </div>
    <el-table :data="projects" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="项目名称" min-width="220">
        <template #default="{ row }">
          <el-link type="primary" @click="router.push(`/projects/${row.id}`)">{{ row.name }}</el-link>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="80">
        <template #default="{ row }">{{ TYPE_LABELS[row.type] || row.type }}</template>
      </el-table-column>
      <el-table-column label="评标办法" width="110">
        <template #default="{ row }">{{ METHOD_LABELS[row.method_type] || row.method_type }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'reviewing' ? 'warning' : row.status === 'closed' ? 'info' : 'success'">
            {{ STATUS_LABELS[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="member_count" label="专家数" width="80" />
      <el-table-column prop="bidder_count" label="投标人" width="80" />
      <el-table-column prop="doc_count" label="文件数" width="80" />
    </el-table>

    <el-dialog v-model="dialog" title="新建评标项目" width="520px">
      <el-form label-width="90px">
        <el-form-item label="项目名称" required>
          <el-input v-model="form.name" maxlength="200" />
        </el-form-item>
        <el-form-item label="项目类型">
          <el-select v-model="form.type">
            <el-option label="货物" value="goods" />
            <el-option label="工程" value="engineering" />
            <el-option label="服务" value="service" />
          </el-select>
        </el-form-item>
        <el-form-item label="评标办法">
          <el-select v-model="form.method_type">
            <el-option label="综合评分法" value="comprehensive" />
            <el-option label="最低价法" value="lowest_price" />
          </el-select>
        </el-form-item>
        <el-form-item label="控制价(万)">
          <el-input-number v-model="form.control_price" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" />
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
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
</style>
