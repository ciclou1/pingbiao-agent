<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isManager = me.role === 'admin' || me.role === 'leader'

const bidders = ref([])
const alerts = ref([])
const itemsByBidder = ref([])
const loading = ref(false)
const running = ref('')
const uploadBidder = ref(null)
const uploading = ref(false)
const activeTab = ref('clearing')

const LEVEL = { high: { label: '高', type: 'danger' }, medium: { label: '中', type: 'warning' }, low: { label: '低', type: 'info' } }
const CODE_LABELS = {
  arithmetic: '算术错误',
  over_limit: '超控制价',
  unbalanced: '不平衡报价',
  missing_item: '疑似漏项',
  similarity: '文本雷同',
  meta: '文件元数据',
  price_pattern: '报价规律',
}

const clearingAlerts = computed(() => alerts.value.filter((a) => a.kind === 'clearing'))
const collusionAlerts = computed(() => alerts.value.filter((a) => a.kind === 'collusion'))
const totalRows = computed(() => itemsByBidder.value.reduce((n, b) => n + b.count, 0))

async function load() {
  loading.value = true
  try {
    const [b, al, items] = await Promise.all([
      api.get(`/projects/${projectId}/bidders`),
      api.get(`/projects/${projectId}/alerts`),
      api.get(`/projects/${projectId}/clearing/items`),
    ])
    bidders.value = b.bidders
    alerts.value = al.alerts
    itemsByBidder.value = items.by_bidder || []
    if (bidders.value.length && !uploadBidder.value) uploadBidder.value = bidders.value[0].id
  } finally {
    loading.value = false
  }
}

async function doUpload(file, fileList) {
  if (!uploadBidder.value) {
    ElMessage.warning('请先选择投标人')
    return false
  }
  const fd = new FormData()
  fd.append('file', file)
  fd.append('doc_role', 'price')
  fd.append('bidder_id', uploadBidder.value)
  uploading.value = true
  try {
    const res = await api.post(`/projects/${projectId}/documents`, fd)
    ElMessage.success('已上传，解析中…')
    await pollJob(res.job_id)
    await load()
  } catch {
    ElMessage.error('上传失败')
  } finally {
    uploading.value = false
  }
  return false
}

async function pollJob(jobId) {
  for (let i = 0; i < 60; i++) {
    const job = await api.get(`/jobs/${jobId}`)
    if (job.status === 'succeeded') {
      const rows = job.result?.price_rows ?? 0
      ElMessage.success(rows ? `解析出 ${rows} 行报价明细` : '文件已解析，但未识别到报价表（请检查表头）')
      return
    }
    if (job.status === 'failed') {
      ElMessage.error((job.error || '解析失败').split('\n').slice(-1)[0])
      return
    }
    await new Promise((r) => setTimeout(r, 1500))
  }
}

async function run(kind) {
  running.value = kind
  try {
    if (kind === 'clearing') {
      const res = await api.post(`/projects/${projectId}/clearing/run`)
      ElMessage.success(`清标完成：${res.alerts} 条异常提示（覆盖 ${res.bidders_with_sheet} 家报价表）`)
    } else {
      const res = await api.post(`/projects/${projectId}/collusion/run`)
      ElMessage.success(`预警分析完成：${res.alerts} 条提示`)
    }
    await load()
  } catch {
    /* interceptor */
  } finally {
    running.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <ProjectNav :project-id="projectId" title="清标与围标串标预警">
      <template #actions>
        <el-button type="primary" size="small" :loading="running === 'clearing'" :disabled="!isManager" @click="run('clearing')">运行清标</el-button>
        <el-button size="small" :loading="running === 'collusion'" :disabled="!isManager" @click="run('collusion')">运行围标串标预警</el-button>
      </template>
    </ProjectNav>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      title="预警结论是机器特征提示（相似度/元数据/报价规律），仅供评标委员会参考，不构成否决依据。"
      style="margin-bottom: 12px"
    />

    <el-card v-if="isManager" shadow="never" class="block">
      <template #header><span>报价清单导入（Excel）</span></template>
      <div class="upload-row">
        <el-select v-model="uploadBidder" style="width: 200px" placeholder="选择投标人">
          <el-option v-for="b in bidders" :key="b.id" :label="b.name" :value="b.id" />
        </el-select>
        <el-upload :show-file-list="false" :before-upload="doUpload" accept=".xlsx,.xls,.xlsm">
          <el-button :loading="uploading">上传报价表</el-button>
        </el-upload>
        <span class="hint">表头需含：项目名称 / 单位 / 数量 / 单价 / 合价（列顺序不限）</span>
        <span class="hint">已导入明细：{{ totalRows }} 行</span>
      </div>
    </el-card>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="`清标结果（${clearingAlerts.length}）`" name="clearing">
        <el-empty v-if="!clearingAlerts.length" description="暂无清标异常（先导入报价表并运行清标）" />
        <el-card v-for="a in clearingAlerts" :key="a.id" shadow="never" class="alert-card">
          <div class="alert-head">
            <el-tag :type="LEVEL[a.level].type" size="small">{{ LEVEL[a.level].label }}风险</el-tag>
            <el-tag size="small" effect="plain">{{ CODE_LABELS[a.code] || a.code }}</el-tag>
            <span class="alert-title">{{ a.title }}</span>
          </div>
          <div v-if="a.detail.rows?.length" class="detail">
            <el-table :data="a.detail.rows" size="small" max-height="260">
              <el-table-column v-if="a.code === 'arithmetic'" prop="item_name" label="条目" min-width="160" show-overflow-tooltip />
              <el-table-column v-if="a.code === 'arithmetic'" prop="qty" label="数量" width="90" />
              <el-table-column v-if="a.code === 'arithmetic'" prop="unit_price" label="单价" width="100" />
              <el-table-column v-if="a.code === 'arithmetic'" prop="amount" label="报价合价" width="110" />
              <el-table-column v-if="a.code === 'arithmetic'" prop="expected" label="应为" width="110" />
              <el-table-column v-if="a.code === 'unbalanced'" prop="item_name" label="条目" min-width="160" show-overflow-tooltip />
              <el-table-column v-if="a.code === 'unbalanced'" prop="bidder" label="投标人" width="120" />
              <el-table-column v-if="a.code === 'unbalanced'" prop="unit_price" label="单价" width="90" />
              <el-table-column v-if="a.code === 'unbalanced'" prop="median" label="各家中位" width="100" />
              <el-table-column v-if="a.code === 'unbalanced'" prop="qty" label="数量" width="90" />
              <el-table-column v-if="a.code === 'missing_item'" prop="item_name" label="条目" min-width="180" show-overflow-tooltip />
              <el-table-column v-if="a.code === 'missing_item'" prop="bidder" label="未报的投标人" width="140" />
              <el-table-column v-if="a.code === 'missing_item'" label="其他家已报" min-width="160">
                <template #default="{ row }">{{ (row.present_in || []).join('、') }}</template>
              </el-table-column>
            </el-table>
          </div>
          <div v-else-if="a.detail.count" class="detail-text">共 {{ a.detail.count }} 处，表中列前 20 处</div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`围标串标预警（${collusionAlerts.length}）`" name="collusion">
        <el-empty v-if="!collusionAlerts.length" description="暂无预警（需先上传投标文件并运行预警分析）" />
        <el-card v-for="a in collusionAlerts" :key="a.id" shadow="never" class="alert-card">
          <div class="alert-head">
            <el-tag :type="LEVEL[a.level].type" size="small">{{ LEVEL[a.level].label }}风险</el-tag>
            <el-tag size="small" effect="plain">{{ CODE_LABELS[a.code] || a.code }}</el-tag>
            <span class="alert-title">{{ a.title }}</span>
          </div>
          <div v-if="a.code === 'meta' && a.detail.rows?.length" class="detail">
            <el-table :data="a.detail.rows" size="small" max-height="240">
              <el-table-column label="投标人" min-width="200">
                <template #default="{ row }">{{ (row.bidders || []).join(' ↔ ') }}</template>
              </el-table-column>
              <el-table-column prop="gap_minutes" label="相差（分钟）" width="120" />
              <el-table-column prop="time_a" label="时间 A" width="170" />
              <el-table-column prop="time_b" label="时间 B" width="170" />
            </el-table>
          </div>
          <div v-else-if="a.code === 'price_pattern' && a.detail.rows?.length" class="detail">
            <el-table :data="a.detail.rows" size="small" max-height="240">
              <el-table-column prop="item_name" label="条目" min-width="180" show-overflow-tooltip />
              <el-table-column prop="unit_price" label="相同单价" width="120" />
              <el-table-column label="投标人" min-width="160">
                <template #default="{ row }">{{ (row.bidders || []).join('、') }}</template>
              </el-table-column>
            </el-table>
          </div>
          <div v-else-if="a.detail.value" class="detail-text">
            {{ a.detail.field ? `${a.detail.field}：` : '' }}{{ a.detail.value }}
            <span v-if="a.detail.bidders"> · 涉及：{{ a.detail.bidders.join('、') }}</span>
          </div>
          <div v-else-if="a.detail.totals" class="detail-text">
            各家报价总额：{{ a.detail.totals.map(([n, v]) => `${n} ${v.toLocaleString()}`).join(' · ') }}
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-card v-if="itemsByBidder.length" shadow="never" class="block">
      <template #header><span>报价明细（已导入 {{ itemsByBidder.length }} 家）</span></template>
      <el-collapse>
        <el-collapse-item v-for="b in itemsByBidder" :key="b.bidder_id" :name="b.bidder_id">
          <template #title>
            <span class="collapse-title">{{ b.bidder_name }}（{{ b.count }} 行）</span>
          </template>
          <el-table :data="b.items" size="small" max-height="320">
            <el-table-column prop="row_no" label="行" width="60" />
            <el-table-column prop="item_name" label="条目" min-width="200" show-overflow-tooltip />
            <el-table-column prop="unit" label="单位" width="70" />
            <el-table-column prop="qty" label="数量" width="90" />
            <el-table-column prop="unit_price" label="单价" width="100" />
            <el-table-column prop="amount" label="合价" width="110" />
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.buttons { display: flex; align-items: center; gap: 10px; }
.block { margin-bottom: 12px; }
.upload-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hint { color: #898781; font-size: 12px; }
.alert-card { margin-bottom: 10px; }
.alert-head { display: flex; align-items: center; gap: 8px; }
.alert-title { font-size: 14px; color: #0b0b0b; }
.detail { margin-top: 10px; }
.detail-text { margin-top: 8px; color: #52514e; font-size: 13px; }
.collapse-title { font-size: 13px; }
</style>
