<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import DocViewer from '../components/DocViewer.vue'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isLeader = me.role === 'admin' || me.role === 'leader'

const bidders = ref([])
const bidderId = ref(null)
const rows = ref([])
const summary = ref({})
const loading = ref(false)
const selected = ref(null)
const verdict = ref('')
const verdictReason = ref('')

const runJobId = ref(null)
const runProgress = ref(0)
const runMessage = ref('')
let pollTimer = null

// 原文预览（共享组件）
const viewer = ref(null)

// 工程标书包是 zip 容器，浏览器无法定位渲染 → 交给平台工具
function isContainer(name) {
  return (name || '').toLowerCase().endsWith('.zip')
}

const STATE_LABELS = { passed: '通过', failed: '不通过', review: '待复核' }
const STATE_TYPES = { passed: 'success', failed: 'danger', review: 'warning' }
const TYPE_LABELS = { qualification: '资格条件', compliance: '符合性(否决)' }

const current = computed(() => rows.value.find((r) => r.item.id === selected.value) || null)

function stateOf(row) {
  const r = row.result
  if (!r) return 'pending'
  return r.expert_state || r.ai_state
}

async function loadBidders() {
  const data = await api.get(`/projects/${projectId}/bidders`)
  bidders.value = data.bidders
  if (!bidderId.value && bidders.value.length) bidderId.value = bidders.value[0].id
}

async function loadMatrix() {
  if (!bidderId.value) return
  loading.value = true
  try {
    const data = await api.get(`/projects/${projectId}/review/matrix`, { params: { bidder_id: bidderId.value } })
    rows.value = data.rows
    summary.value = data.summary
    if (!selected.value && rows.value.length) select(rows.value[0])
    else if (selected.value) {
      const again = rows.value.find((r) => r.item.id === selected.value)
      if (again) select(again)
    }
  } finally {
    loading.value = false
  }
}

function select(row) {
  selected.value = row.item.id
  verdict.value = row.result?.expert_state || ''
  verdictReason.value = row.result?.expert_reason || ''
}

async function runReview() {
  const { jobs } = await api.post(`/projects/${projectId}/review/run`, { bidder_id: bidderId.value })
  runJobId.value = jobs[0].job_id
  runProgress.value = 5
  runMessage.value = '任务已排队'
  pollTimer = setInterval(pollRun, 2000)
}

async function pollRun() {
  const job = await api.get(`/jobs/${runJobId.value}`)
  runProgress.value = Math.max(runProgress.value, job.progress)
  runMessage.value = job.message || runMessage.value
  if (job.status === 'succeeded') {
    clearInterval(pollTimer)
    runJobId.value = null
    const r = job.result || {}
    ElMessage.success(`审查完成：通过 ${r.by_state?.passed ?? 0} / 不通过 ${r.by_state?.failed ?? 0} / 待复核 ${r.by_state?.review ?? 0}`)
    await loadMatrix()
  } else if (job.status === 'failed') {
    clearInterval(pollTimer)
    runJobId.value = null
    ElMessage.error((job.error || '审查失败').split('\n').slice(-1)[0])
  }
}

async function saveVerdict() {
  if (!verdict.value) return ElMessage.warning('请选择专家终值')
  if (verdict.value === 'failed' && !verdictReason.value.trim()) return ElMessage.warning('判不通过必须填写理由')
  try {
    await api.patch(`/projects/${projectId}/review/results/${current.value.result.id}`, {
      expert_state: verdict.value,
      expert_reason: verdictReason.value,
    })
    ElMessage.success('已保存专家终值')
    await loadMatrix()
  } catch { /* interceptor 提示 */ }
}

async function openDoc(ev) {
  if (!ev.doc_id) return ElMessage.warning('该证据缺少文件信息')
  viewer.value?.show({ docId: ev.doc_id, page: ev.page || 1, label: '投标文件' })
}

watch(bidderId, loadMatrix)

onMounted(async () => {
  await loadBidders()
  await loadMatrix()
})
</script>

<template>
  <div>
    <ProjectNav :project-id="projectId" title="审查工作台">
      <template #actions>
        <el-select v-model="bidderId" placeholder="选择投标人" size="small" style="width: 180px">
          <el-option v-for="b in bidders" :key="b.id" :label="b.name" :value="b.id" />
        </el-select>
        <template v-if="summary.total">
          <el-tag type="success" size="small">通过 {{ summary.passed || 0 }}</el-tag>
          <el-tag type="danger" size="small">不通过 {{ summary.failed || 0 }}</el-tag>
          <el-tag type="warning" size="small">待复核 {{ summary.review || 0 }}</el-tag>
          <el-tag type="info" size="small">未审 {{ summary.pending || 0 }}</el-tag>
        </template>
        <el-button v-if="isLeader" type="primary" size="small" :loading="!!runJobId" @click="runReview">AI 审查本投标人</el-button>
      </template>
    </ProjectNav>

    <el-progress v-if="runJobId" :percentage="runProgress" :stroke-width="14" style="margin-bottom: 12px">
      <span class="run-msg">{{ runMessage }}</span>
    </el-progress>

    <el-row :gutter="16" v-if="rows.length">
      <el-col :span="9">
        <el-table
          :data="rows"
          v-loading="loading"
          size="small"
          height="640"
          highlight-current-row
          :current-row-key="selected"
          :row-key="(row) => row.item.id"
          @row-click="select"
        >
          <el-table-column label="条目" min-width="150">
            <template #default="{ row }">
              <div class="cell-title">{{ TYPE_LABELS[row.item.item_type] }} · {{ row.item.category || '—' }}</div>
              <div class="cell-sub">{{ row.item.clause_text }}</div>
            </template>
          </el-table-column>
          <el-table-column label="结论" width="86">
            <template #default="{ row }">
              <el-tag size="small" :type="stateOf(row) === 'pending' ? 'info' : STATE_TYPES[stateOf(row)]">
                {{ stateOf(row) === 'pending' ? '未审' : STATE_LABELS[stateOf(row)] }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-col>

      <el-col :span="15">
        <el-card shadow="never" v-if="current">
          <div class="item-head">
            <el-tag size="small">{{ TYPE_LABELS[current.item.item_type] }}</el-tag>
            <el-tag size="small" type="info">{{ current.item.category || '未分类' }}</el-tag>
            <span class="page" v-if="current.item.source_page">招标文件 第{{ current.item.source_page }}页</span>
          </div>
          <p class="clause">{{ current.item.clause_text }}</p>
          <p class="req" v-if="current.item.requirement">要求要点：{{ current.item.requirement }}</p>

          <el-divider content-position="left">AI 判定</el-divider>
          <template v-if="current.result">
            <div class="verdict-line">
              <el-tag :type="STATE_TYPES[current.result.ai_state]">{{ STATE_LABELS[current.result.ai_state] }}</el-tag>
              <span class="conf">置信度 {{ current.result.ai_confidence ?? '—' }}</span>
              <el-tag v-for="f in current.result.ai_check?.fired || []" :key="f" size="small" type="warning" effect="plain">
                校验器 {{ f }}
              </el-tag>
            </div>
            <p class="reason">{{ current.result.ai_reason }}</p>

            <div class="evidence-title">证据（{{ (current.result.ai_evidence || []).length }} 条）</div>
            <div v-for="(ev, i) in current.result.ai_evidence" :key="i" class="evidence">
              <div class="evi-head">
                <el-tag size="small">第 {{ ev.page }} 页</el-tag>
                <span v-if="ev.doc_name" class="evi-doc">{{ ev.doc_name }}</span>
                <el-tag v-if="ev.cited" size="small" type="success" effect="plain">AI 引用</el-tag>
                <el-tag v-else size="small" type="info" effect="plain">检索命中</el-tag>
                <el-link v-if="!isContainer(ev.doc_name)" type="primary" :underline="false" @click="openDoc(ev)">查看原文</el-link>
                <span v-else class="evi-hint">标书包（zip）需在平台工具中查看</span>
              </div>
              <div class="snippet">{{ ev.snippet }}</div>
            </div>
          </template>
          <el-empty v-else description="尚未审查，点击右上角「AI 审查本投标人」" :image-size="60" />

          <el-divider content-position="left">专家终值（评标委员会结论）</el-divider>
          <el-radio-group v-model="verdict">
            <el-radio-button value="passed">通过</el-radio-button>
            <el-radio-button value="failed">不通过</el-radio-button>
            <el-radio-button value="review">待复核</el-radio-button>
          </el-radio-group>
          <el-input
            v-model="verdictReason"
            type="textarea"
            :rows="2"
            style="margin-top: 10px"
            :placeholder="verdict === 'failed' ? '判不通过必须填写理由（引用招标文件条款）' : '备注（可选）'"
          />
          <div style="margin-top: 10px">
            <el-button type="primary" :disabled="!current.result" @click="saveVerdict">保存终值</el-button>
            <span class="hint">审查结论为评标委员会共同结论，不做专家间隔离；打分才隔离。</span>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <el-empty v-else description="请先添加投标人、上传投标文件并确认评审条目" />

    <DocViewer ref="viewer" :project-id="projectId" />
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.left, .right { display: flex; align-items: center; gap: 10px; }
.run-msg { font-size: 12px; color: #888; }
.cell-title { font-size: 12px; color: #666; }
.cell-sub { font-size: 13px; line-height: 1.4; }
.item-head { display: flex; align-items: center; gap: 8px; }
.page { color: #888; font-size: 12px; }
.clause { margin: 10px 0 4px; font-size: 14px; line-height: 1.6; }
.req { color: #555; font-size: 13px; }
.verdict-line { display: flex; align-items: center; gap: 10px; }
.conf { color: #888; font-size: 12px; }
.reason { font-size: 13px; color: #333; margin: 8px 0; }
.evidence-title { font-size: 13px; color: #666; margin: 6px 0; }
.evidence { border-left: 3px solid #e4e7ed; padding: 6px 10px; margin-bottom: 8px; background: #fafafa; }
.evi-head { display: flex; align-items: center; gap: 8px; }
.evi-doc { font-size: 12px; color: #52514e; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evi-hint { font-size: 12px; color: #898781; }
.snippet { font-size: 12px; color: #555; line-height: 1.5; margin-top: 4px; white-space: pre-wrap; }
.hint { margin-left: 10px; color: #999; font-size: 12px; }
</style>
