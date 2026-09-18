<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import DocViewer from '../components/DocViewer.vue'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isLeader = me.role === 'admin' || me.role === 'leader'

const items = ref([])
const summary = ref({})
const loading = ref(false)
const activeTab = ref('all')
// 用字符串枚举而不是 null/true/false：Element Plus 的 radio 把 null 当作「未提供」，
// 会回退去取 label 属性，导致点击后拿到 undefined 而不是预期的值。
const filterConfirmed = ref('all') // all | pending | confirmed
const extractJobId = ref(null)
const extractProgress = ref(0)
const extractMessage = ref('')
let pollTimer = null

const TYPE_LABELS = { qualification: '资格条件', compliance: '符合性(否决)', scoring: '评分项' }
const TYPE_ORDER = ['qualification', 'compliance', 'scoring']

const shownItems = computed(() => {
  let list = items.value
  if (activeTab.value !== 'all') list = list.filter((i) => i.item_type === activeTab.value)
  if (filterConfirmed.value === 'pending') list = list.filter((i) => !i.expert_confirmed)
  else if (filterConfirmed.value === 'confirmed') list = list.filter((i) => i.expert_confirmed)
  return list
})

const allConfirmed = computed(() => items.value.length > 0 && items.value.every((i) => i.expert_confirmed))

async function load() {
  loading.value = true
  try {
    const data = await api.get(`/projects/${projectId}/requirement-items`)
    items.value = data.items
    summary.value = data.summary
  } finally {
    loading.value = false
  }
}

async function startExtract() {
  if (items.value.length) {
    await ElMessageBox.confirm('已有条目，继续抽取将追加（LLM 自动去重）。继续？', '确认')
  }
  const { job_id } = await api.post(`/projects/${projectId}/requirement-items/extract`)
  extractJobId.value = job_id
  extractProgress.value = 5
  extractMessage.value = '任务已排队'
  pollTimer = setInterval(pollExtract, 2000)
}

async function pollExtract() {
  const job = await api.get(`/jobs/${extractJobId.value}`)
  extractProgress.value = Math.max(extractProgress.value, job.progress)
  extractMessage.value = job.message || extractMessage.value
  if (job.status === 'succeeded') {
    clearInterval(pollTimer)
    extractJobId.value = null
    const r = job.result || {}
    ElMessage.success(`抽取完成：${r.items_total ?? 0} 条（${Object.entries(r.by_type || {}).map(([k, v]) => `${k}${v}`).join('、')}）`)
    await load()
  } else if (job.status === 'failed') {
    clearInterval(pollTimer)
    extractJobId.value = null
    ElMessage.error((job.error || '抽取失败').split('\n').slice(-1)[0])
  }
}

async function toggleConfirm(row) {
  await api.patch(`/projects/${projectId}/requirement-items/${row.id}`, { expert_confirmed: !row.expert_confirmed })
  await load()
}

async function confirmAllShown() {
  for (const it of shownItems.value.filter((i) => !i.expert_confirmed)) {
    await api.patch(`/projects/${projectId}/requirement-items/${it.id}`, { expert_confirmed: true })
  }
  ElMessage.success('本页已全部确认')
  await load()
}

// 编辑
const editDialog = ref(false)
const editForm = ref({})
const isNew = ref(false)

function openEdit(row) {
  isNew.value = false
  editForm.value = { ...row }
  editDialog.value = true
}

function openCreate() {
  isNew.value = true
  editForm.value = { item_type: 'qualification', category: '', clause_text: '', requirement: '', score: null, source_page: null }
  editDialog.value = true
}

async function saveEdit() {
  const f = editForm.value
  if (!f.clause_text?.trim() && !f.requirement?.trim()) {
    ElMessage.warning('条款原文与要求要点至少填一项')
    return
  }
  if (isNew.value) {
    await api.post(`/projects/${projectId}/requirement-items`, f)
  } else {
    const { id, ...patch } = f
    await api.patch(`/projects/${projectId}/requirement-items/${id}`, patch)
  }
  editDialog.value = false
  await load()
}

async function removeItem(row) {
  await ElMessageBox.confirm('删除该条目？', '确认')
  await api.delete(`/projects/${projectId}/requirement-items/${row.id}`)
  await load()
}

// 跳原文核对条款（专家确认条目时的关键动作）
const viewer = ref(null)
function openSource(row) {
  viewer.value?.show({ docId: row.source_doc_id, page: row.source_page, label: '招标文件' })
}

onMounted(load)
</script>

<template>
  <div>
    <ProjectNav :project-id="projectId" title="评审条目库">
      <template #actions>
        <el-tag v-if="allConfirmed" type="success" size="small">全部已确认</el-tag>
        <el-tag v-else type="warning" size="small">含待确认条目</el-tag>
        <el-radio-group v-model="filterConfirmed" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="pending">待确认</el-radio-button>
          <el-radio-button value="confirmed">已确认</el-radio-button>
        </el-radio-group>
        <template v-if="isLeader">
          <el-button size="small" @click="openCreate">手工录入</el-button>
          <el-button type="primary" size="small" :loading="!!extractJobId" @click="startExtract">
            {{ items.length ? '重新抽取（追加）' : 'AI 抽取条目' }}
          </el-button>
        </template>
      </template>
    </ProjectNav>

    <el-progress
      v-if="extractJobId"
      :percentage="extractProgress"
      style="margin-bottom: 12px"
      :stroke-width="14"
    >
      <span class="extract-msg">{{ extractMessage }}</span>
    </el-progress>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="全部" name="all">
        <template #label>
          全部（{{ summary.qualification?.total ?? 0 }}/{{ (summary.qualification?.total ?? 0) + (summary.compliance?.total ?? 0) + (summary.scoring?.total ?? 0) }})
        </template>
      </el-tab-pane>
      <el-tab-pane v-for="t in TYPE_ORDER" :key="t" :name="t">
        <template #label>
          {{ TYPE_LABELS[t] }}（{{ summary[t]?.confirmed ?? 0 }}/{{ summary[t]?.total ?? 0 }}）
        </template>
      </el-tab-pane>
    </el-tabs>

    <el-table :data="shownItems" v-loading="loading" stripe size="small">
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.item_type === 'compliance' ? 'danger' : row.item_type === 'scoring' ? 'warning' : 'primary'">
            {{ TYPE_LABELS[row.item_type] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="category" label="分类" width="90" />
      <el-table-column prop="clause_text" label="条款原文" min-width="280" show-overflow-tooltip />
      <el-table-column prop="requirement" label="要求要点" min-width="180" show-overflow-tooltip />
      <el-table-column label="分值" width="70">
        <template #default="{ row }">{{ row.score ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="来源页" width="90">
        <template #default="{ row }">
          <el-link
            v-if="row.source_page && row.source_doc_id"
            type="primary"
            :underline="false"
            @click="openSource(row)"
          >
            第{{ row.source_page }}页
          </el-link>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.ai_extracted ? 'info' : 'success'">{{ row.ai_extracted ? 'AI' : '手工' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="确认" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.expert_confirmed ? 'success' : 'warning'">{{ row.expert_confirmed ? '已确认' : '待确认' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="isLeader" label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="toggleConfirm(row)">{{ row.expert_confirmed ? '撤销' : '确认' }}</el-button>
          <el-button link size="small" type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link size="small" type="danger" @click="removeItem(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="isLeader && shownItems.some((i) => !i.expert_confirmed)" style="margin-top: 12px">
      <el-button size="small" @click="confirmAllShown">确认本页全部</el-button>
    </div>

    <DocViewer ref="viewer" :project-id="projectId" />

    <el-dialog v-model="editDialog" :title="isNew ? '手工录入条目' : '编辑条目'" width="640px">
      <el-form label-width="90px">
        <el-form-item label="类型" required>
          <el-select v-model="editForm.item_type" :disabled="!isLeader">
            <el-option label="资格条件" value="qualification" />
            <el-option label="符合性(否决)" value="compliance" />
            <el-option label="评分项" value="scoring" />
          </el-select>
        </el-form-item>
        <el-form-item label="分类"><el-input v-model="editForm.category" placeholder="资质/业绩/保证金/…" /></el-form-item>
        <el-form-item label="条款原文">
          <el-input v-model="editForm.clause_text" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="要求要点">
          <el-input v-model="editForm.requirement" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item v-if="editForm.item_type === 'scoring'" label="分值">
          <el-input-number v-model="editForm.score" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="来源页码">
          <el-input-number v-model="editForm.source_page" :min="1" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.left, .right { display: flex; align-items: center; gap: 10px; }
.extract-msg { font-size: 12px; color: #888; }
</style>
