<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import DocViewer from '../components/DocViewer.vue'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)

const bidders = ref([])
const scope = ref('all')
const bidderId = ref(null)
const messages = ref([])
const question = ref('')
const asking = ref(false)
const listRef = ref(null)
const viewer = ref(null)
const vecStatus = ref(null)

// 向量检索状态：不可用时检索会静默退回关键词，必须让用户看得见
async function loadVectorStatus() {
  try {
    vecStatus.value = await api.get(`/projects/${projectId}/vector-status`)
  } catch {
    vecStatus.value = null
  }
}

const vecLabel = computed(() => {
  const s = vecStatus.value
  if (!s || !s.configured) return { text: '关键词检索', type: 'info', tip: '未配置向量服务（纯关键词 + 同义词扩展）' }
  if (!s.service_healthy) return { text: '向量服务离线', type: 'warning', tip: '向量服务不可用，当前已自动退回关键词检索' }
  if (!s.coverage?.embedded) return { text: '未建向量索引', type: 'warning', tip: '服务在线但本项目尚无向量索引，可到项目详情触发补建' }
  return {
    text: `语义检索 ${Math.round((s.coverage.ratio || 0) * 100)}%`,
    type: 'success',
    tip: `关键词 + 向量混合检索（RRF 融合），已索引 ${s.coverage.embedded}/${s.coverage.total} 个分块`,
  }
})

const PRESETS = [
  '各投标人的投标报价分别是多少？是否超过最高限价？',
  '各投标人提供了几个类似业绩？是否满足要求？',
  '哪些投标人的投标有效期不满足要求？',
  '招标文件对投标保证金的要求是什么？',
]

async function loadBidders() {
  const data = await api.get(`/projects/${projectId}/bidders`)
  bidders.value = data.bidders
  if (bidders.value.length && !bidderId.value) bidderId.value = bidders.value[0].id
}

async function loadHistory() {
  const data = await api.get(`/projects/${projectId}/qa/history`)
  messages.value = data.messages
  await scrollToEnd()
}

async function ask(text) {
  const q = (text ?? question.value).trim()
  if (!q) return
  if (scope.value === 'bidder' && !bidderId.value) return ElMessage.warning('请先选择投标人')
  asking.value = true
  try {
    const body = { question: q, scope: scope.value }
    if (scope.value === 'bidder') body.bidder_id = bidderId.value
    const msg = await api.post(`/projects/${projectId}/qa/ask`, body)
    messages.value.push(msg)
    question.value = ''
    await scrollToEnd()
  } finally {
    asking.value = false
  }
}

async function scrollToEnd() {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

function openCitation(c) {
  if ((c.doc_name || '').toLowerCase().endsWith('.zip')) {
    ElMessage.info('该证据在标书包（zip）内，需用平台工具查看')
    return
  }
  const label = `${c.bidder_name || '招标文件'} · ${c.doc_name}`
  viewer.value?.show({ docId: c.doc_id, page: c.page, label })
}

function scopeHint() {
  if (scope.value === 'tender') return '仅检索招标文件'
  if (scope.value === 'bidder') {
    const b = bidders.value.find((x) => x.id === bidderId.value)
    return `仅检索${b?.name || ''}的投标文件（不含招标文件要求）`
  }
  return '检索招标文件 + 全部投标文件'
}

onMounted(async () => {
  await loadBidders()
  await loadHistory()
  loadVectorStatus()
})
</script>

<template>
  <div class="qa-page">
    <ProjectNav :project-id="projectId" title="专家问答">
      <template #actions>
        <el-radio-group v-model="scope" size="small">
          <el-radio-button value="all">全部材料</el-radio-button>
          <el-radio-button value="tender">仅招标文件</el-radio-button>
          <el-radio-button value="bidder">指定投标人</el-radio-button>
        </el-radio-group>
        <el-select v-if="scope === 'bidder'" v-model="bidderId" size="small" style="width: 160px">
          <el-option v-for="b in bidders" :key="b.id" :label="b.name" :value="b.id" />
        </el-select>
        <span class="hint">{{ scopeHint() }}</span>
        <el-tooltip :content="vecLabel.tip" placement="bottom">
          <el-tag :type="vecLabel.type" size="small" effect="plain">{{ vecLabel.text }}</el-tag>
        </el-tooltip>
      </template>
    </ProjectNav>

    <div ref="listRef" class="list">
      <el-empty v-if="!messages.length" description="提问即检索标书原文作答，回答必须带页码出处；材料里没有的会明确说未找到">
        <div class="presets">
          <el-tag
            v-for="p in PRESETS"
            :key="p"
            class="preset"
            type="info"
            effect="plain"
            @click="ask(p)"
          >
            {{ p }}
          </el-tag>
        </div>
      </el-empty>

      <div v-for="m in messages" :key="m.id" class="qa-item">
        <div class="q-row">
          <span class="who">{{ m.username }}</span>
          <span class="q-text">{{ m.question }}</span>
        </div>
        <el-card shadow="never" class="a-card">
          <div class="a-head">
            <el-tag size="small" :type="m.insufficient ? 'warning' : 'success'" effect="plain">
              {{ m.insufficient ? '材料中未找到' : '依据材料作答' }}
            </el-tag>
            <span class="meta">{{ m.scope === 'tender' ? '招标文件' : m.scope === 'bidder' ? '指定投标人' : '全部材料' }}</span>
          </div>
          <div class="a-body">{{ m.answer }}</div>
          <div v-if="m.citations.length" class="cites">
            <span class="cites-title">出处（点击查看原文）：</span>
            <el-tag
              v-for="(c, i) in m.citations"
              :key="i"
              class="cite"
              size="small"
              :type="c.cited ? 'primary' : 'info'"
              :effect="c.cited ? 'light' : 'plain'"
              @click="openCitation(c)"
            >
              {{ c.bidder_name || '招标文件' }} · {{ c.doc_name || '' }} 第{{ c.page }}页{{ c.cited ? ' ✓' : '' }}
            </el-tag>
          </div>
        </el-card>
      </div>
    </div>

    <div class="ask-bar">
      <el-input
        v-model="question"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="用自然语言提问，例如：投标人乙的类似业绩是否满足要求？（Enter 发送，Shift+Enter 换行）"
        @keydown.enter.exact.prevent="ask()"
      />
      <el-button type="primary" :loading="asking" @click="ask()">提问</el-button>
    </div>

    <DocViewer ref="viewer" :project-id="projectId" />
  </div>
</template>

<style scoped>
/* 顶栏 56 + 内容区上下留白 ≈ 112，让问答页正好填满剩余视口 */
.qa-page { display: flex; flex-direction: column; height: calc(100vh - 112px); }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; }
.scope { display: flex; align-items: center; gap: 10px; }
.hint { color: #898781; font-size: 12px; }
.list { flex: 1; overflow-y: auto; padding-right: 4px; }
.qa-item { margin-bottom: 16px; }
.q-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.who { color: #52514e; font-size: 12px; }
.q-text { font-weight: 600; font-size: 14px; color: #0b0b0b; }
.a-card :deep(.el-card__body) { padding: 12px 14px; }
.a-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.meta { color: #898781; font-size: 12px; }
.a-body { white-space: pre-wrap; line-height: 1.7; font-size: 14px; color: #0b0b0b; }
.cites { margin-top: 10px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.cites-title { color: #898781; font-size: 12px; }
.cite { cursor: pointer; }
.ask-bar { display: flex; gap: 10px; align-items: flex-end; padding-top: 10px; border-top: 1px solid #e1e0d9; }
.presets { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 12px; }
.preset { cursor: pointer; }
</style>
