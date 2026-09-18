<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import DocViewer from '../components/DocViewer.vue'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.id)
const me = computed(() => JSON.parse(localStorage.getItem('pb_user') || '{}'))
const isManager = computed(() => ['admin', 'leader'].includes(me.value.role))

const project = ref(null)
const members = ref([])
const bidders = ref([])
const documents = ref([])
const allUsers = ref([])
const parsePoll = ref(null)

const ROLE_LABELS = { leader: '组长', expert: '专家', observer: '观察' }
const DOC_ROLE_LABELS = { tender: '招标文件', bid: '投标文件', price: '报价清单', other: '其他' }
const PARSE_LABELS = { pending: '待解析', parsing: '解析中', parsed: '已解析', failed: '失败', manual_required: '需人工' }

// 上传
const uploadAction = `/api/v1/projects/${projectId}/documents`
const uploadHeaders = computed(() => ({ Authorization: `Bearer ${localStorage.getItem('pb_token') || ''}` }))
const uploadBidderId = ref(null)
const uploadRole = ref('tender')
const uploading = ref(false)

// 新建投标人
const bidderDialog = ref(false)
const bidderForm = ref({ name: '', package_no: '' })

// 添加成员
const memberDialog = ref(false)
const memberForm = ref({ user_id: null, member_role: 'expert', group_no: '' })

async function load() {
  const [p, m, b, d] = await Promise.all([
    api.get(`/projects/${projectId}`),
    api.get(`/projects/${projectId}/members`),
    api.get(`/projects/${projectId}/bidders`),
    api.get(`/projects/${projectId}/documents`),
  ])
  project.value = p
  members.value = m.members
  bidders.value = b.bidders
  documents.value = d.documents
  schedulePoll()
}

function schedulePoll() {
  // 有解析中的文档时 2s 轮询刷新
  if (parsePoll.value) clearTimeout(parsePoll.value)
  if (documents.value.some((d) => d.parse_status === 'pending' || d.parse_status === 'parsing')) {
    parsePoll.value = setTimeout(load, 2000)
  }
}

async function beforeUpload(file) {
  if (uploadRole.value === 'bid' && !uploadBidderId.value) {
    ElMessage.warning('上传投标文件前请先选择投标人')
    return false
  }
  const ok = /\.(pdf|docx|xlsx|zip)$/i.test(file.name)
  if (!ok) ElMessage.error('仅支持 pdf/docx/xlsx/zip')
  return ok
}

async function onUploadDone(resp) {
  let data = resp
  if (typeof resp === 'string') {
    try { data = JSON.parse(resp) } catch { data = {} }
  }
  ElMessage.success('已上传，解析任务已排队')
  await load()
}

function onUploadError(err) {
  let detail = '上传失败'
  try { detail = JSON.parse(err.message).detail || detail } catch { /* keep */ }
  ElMessage.error(detail)
}

async function createBidder() {
  if (!bidderForm.value.name.trim()) return ElMessage.warning('请填写投标人名称')
  await api.post(`/projects/${projectId}/bidders`, bidderForm.value)
  bidderDialog.value = false
  bidderForm.value = { name: '', package_no: '' }
  await load()
}

async function loadUsers() {
  const data = await api.get('/users')
  allUsers.value = data.users
}

async function openMemberDialog() {
  await loadUsers()
  memberDialog.value = true
}

async function addMember() {
  if (!memberForm.value.user_id) return ElMessage.warning('请选择用户')
  try {
    await api.post(`/projects/${projectId}/members`, memberForm.value)
    memberDialog.value = false
    await load()
  } catch { /* message shown by interceptor */ }
}

async function removeMember(row) {
  await ElMessageBox.confirm(`移除成员「${row.display_name || row.username}」？`, '确认')
  await api.delete(`/projects/${projectId}/members/${row.id}`)
  await load()
}

async function removeDoc(row) {
  await ElMessageBox.confirm(`删除文件「${row.original_name}」？`, '确认')
  await api.delete(`/projects/${projectId}/documents/${row.id}`)
  await load()
}

// 原文查看：只有 PDF 能内联渲染，其余格式给下载
const viewer = ref(null)
function canPreview(name) {
  return (name || '').toLowerCase().endsWith('.pdf')
}

function previewDoc(row) {
  if (!canPreview(row.original_name)) {
    ElMessage.info('该格式无法在浏览器内预览，请下载后查看')
    return
  }
  viewer.value?.show({ docId: row.id, page: 1, label: row.original_name })
}

async function downloadDoc(row) {
  try {
    const blob = await api.get(`/projects/${projectId}/documents/${row.id}/download`, { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.original_name
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}

onMounted(load)
</script>

<template>
  <div v-if="project">
    <ProjectNav :project-id="projectId" title="项目详情">
      <template #actions>
        <el-tag v-if="isManager" type="info" size="small">
          {{ project.status === 'draft' ? '编制中' : project.status === 'reviewing' ? '评审中' : '已结束' }}
        </el-tag>
      </template>
    </ProjectNav>
    <div class="head">
      <span class="sub">控制价 {{ project.control_price ?? '—' }} 万 · 评委 {{ project.member_count }} 人 · 投标人 {{ project.bidder_count }} 家 · 文件 {{ project.doc_count }} 个</span>
    </div>

    <el-row :gutter="16">
      <!-- 左：成员 + 投标人 -->
      <el-col :span="8">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>评审成员（{{ members.length }}）</span>
              <el-button v-if="isManager" size="small" @click="openMemberDialog">添加成员</el-button>
            </div>
          </template>
          <el-table :data="members" size="small">
            <el-table-column prop="display_name" label="姓名" min-width="90" />
            <el-table-column label="角色" width="70">
              <template #default="{ row }">{{ ROLE_LABELS[row.member_role] }}</template>
            </el-table-column>
            <el-table-column prop="group_no" label="分组" width="60" />
            <el-table-column v-if="isManager" label="" width="60">
              <template #default="{ row }">
                <el-button link type="danger" size="small" @click="removeMember(row)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" style="margin-top: 16px">
          <template #header>
            <div class="card-head">
              <span>投标人（{{ bidders.length }}）</span>
              <el-button v-if="isManager" size="small" @click="bidderDialog = true">新增投标人</el-button>
            </div>
          </template>
          <el-table :data="bidders" size="small">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="package_no" label="包件" width="80" />
          </el-table>
        </el-card>
      </el-col>

      <!-- 右：文件管理 -->
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>项目文件</span>
              <div v-if="isManager || me.role === 'expert'" class="upload-controls">
                <el-select v-model="uploadRole" size="small" style="width: 120px">
                  <el-option label="招标文件" value="tender" />
                  <el-option label="投标文件" value="bid" />
                  <el-option label="报价清单" value="price" />
                  <el-option label="其他" value="other" />
                </el-select>
                <el-select v-if="uploadRole === 'bid'" v-model="uploadBidderId" size="small" placeholder="选择投标人" style="width: 160px">
                  <el-option v-for="b in bidders" :key="b.id" :label="b.name" :value="b.id" />
                </el-select>
                <el-upload
                  :action="uploadAction"
                  :headers="uploadHeaders"
                  :data="{ doc_role: uploadRole, bidder_id: uploadRole === 'bid' ? uploadBidderId : null }"
                  :before-upload="beforeUpload"
                  :on-success="onUploadDone"
                  :on-error="onUploadError"
                  :show-file-list="false"
                  accept=".pdf,.docx,.xlsx,.zip"
                  multiple
                >
                  <el-button type="primary" size="small" :loading="uploading">上传文件</el-button>
                </el-upload>
              </div>
            </div>
          </template>
          <el-table :data="documents" size="small" v-loading="!documents.length">
            <el-table-column label="文件名" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="previewDoc(row)">{{ row.original_name }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="90">
              <template #default="{ row }">{{ DOC_ROLE_LABELS[row.doc_role] }}</template>
            </el-table-column>
            <el-table-column label="解析状态" width="90">
              <template #default="{ row }">
                <el-tooltip
                  :disabled="!['manual_required', 'failed'].includes(row.parse_status)"
                  :content="row.parse_status === 'manual_required'
                    ? '无可提取文本：疑似扫描件（系统未启用 OCR）或容器已加密，需人工查阅/录入'
                    : '解析失败，可删除后重新上传'"
                  placement="top"
                >
                  <el-tag size="small" :type="row.parse_status === 'parsed' ? 'success' : row.parse_status === 'parsing' ? 'warning' : row.parse_status === 'failed' ? 'danger' : 'info'">
                    {{ PARSE_LABELS[row.parse_status] }}
                  </el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column prop="page_count" label="页数" width="70" />
            <el-table-column label="大小" width="90">
              <template #default="{ row }">{{ (row.size_bytes / 1024 / 1024).toFixed(1) }} MB</template>
            </el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canPreview(row.original_name)" link type="primary" size="small" @click="previewDoc(row)">查看</el-button>
                <el-button link type="primary" size="small" @click="downloadDoc(row)">下载</el-button>
                <el-button v-if="isManager" link type="danger" size="small" @click="removeDoc(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <DocViewer ref="viewer" :project-id="projectId" />

    <el-dialog v-model="bidderDialog" title="新增投标人" width="420px">
      <el-form label-width="80px">
        <el-form-item label="名称" required><el-input v-model="bidderForm.name" /></el-form-item>
        <el-form-item label="包件号"><el-input v-model="bidderForm.package_no" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bidderDialog = false">取消</el-button>
        <el-button type="primary" @click="createBidder">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="memberDialog" title="添加评审成员" width="420px">
      <el-form label-width="80px">
        <el-form-item label="用户" required>
          <el-select v-model="memberForm.user_id" filterable style="width: 100%">
            <el-option v-for="u in allUsers" :key="u.id" :label="`${u.display_name || u.username}（${u.role_label}）`" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目角色">
          <el-select v-model="memberForm.member_role">
            <el-option label="评标组长" value="leader" />
            <el-option label="评标专家" value="expert" />
            <el-option label="监督观察" value="observer" />
          </el-select>
        </el-form-item>
        <el-form-item label="分组号"><el-input v-model="memberForm.group_no" placeholder="如 A/B 组，可空" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="memberDialog = false">取消</el-button>
        <el-button type="primary" @click="addMember">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.nav-links { display: flex; gap: 14px; margin: 6px 0; }
.sub { color: #888; font-size: 13px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.upload-controls { display: flex; gap: 8px; align-items: center; }
</style>
