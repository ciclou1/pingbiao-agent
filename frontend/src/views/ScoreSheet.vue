<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isLeader = me.role === 'admin' || me.role === 'leader'

const bidders = ref([])
const bidderId = ref(null)
const rows = ref([])
const myProgress = ref({ scored: 0, total: 0 })
const loading = ref(false)
const savingId = ref(null)
const drafts = ref({}) // item_id -> {score, comment, reason}

async function loadBidders() {
  const data = await api.get(`/projects/${projectId}/bidders`)
  bidders.value = data.bidders
  if (!bidderId.value && bidders.value.length) bidderId.value = bidders.value[0].id
}

async function loadSheet() {
  if (!bidderId.value) return
  loading.value = true
  try {
    const data = await api.get(`/projects/${projectId}/score/sheet`, { params: { bidder_id: bidderId.value } })
    rows.value = data.rows
    myProgress.value = data.my_progress
    drafts.value = {}
    for (const r of data.rows) {
      drafts.value[r.item.id] = {
        score: r.my_score?.score ?? null,
        comment: r.my_score?.comment ?? '',
        reason: '',
      }
    }
  } finally {
    loading.value = false
  }
}

async function save(row) {
  const d = drafts.value[row.item.id]
  savingId.value = row.item.id
  try {
    await api.put(`/projects/${projectId}/score`, {
      item_id: row.item.id,
      bidder_id: bidderId.value,
      score: d.score,
      comment: d.comment,
      reason: d.reason,
    })
    ElMessage.success('已保存')
    await loadSheet()
  } catch { /* interceptor 提示 */ } finally {
    savingId.value = null
  }
}

function rangeText(s) {
  if (!s) return '—'
  const lo = s.range_low ?? '—'
  const hi = s.range_high ?? '—'
  const v = s.suggested_score ?? '—'
  return `建议 ${v}（${lo} ~ ${hi}）`
}

watch(bidderId, loadSheet)
onMounted(async () => {
  await loadBidders()
  await loadSheet()
})
</script>

<template>
  <div>
    <ProjectNav :project-id="projectId" title="评分表">
      <template #actions>
        <el-select v-model="bidderId" size="small" style="width: 180px">
          <el-option v-for="b in bidders" :key="b.id" :label="b.name" :value="b.id" />
        </el-select>
        <el-tag type="info" size="small">我已打分 {{ myProgress.scored }}/{{ myProgress.total }}</el-tag>
      </template>
    </ProjectNav>

    <el-alert
      type="warning"
      show-icon
      :closable="false"
      title="专家打分互相不可见：本页只显示你自己的分数，汇总后由组长查看。每次改分都会记录前后值与理由。"
      style="margin-bottom: 12px"
    />

    <el-table :data="rows" v-loading="loading" size="small">
      <el-table-column label="评分项" min-width="240">
        <template #default="{ row }">
          <div class="cell-sub">{{ row.item.clause_text }}</div>
          <div class="cell-hint">{{ row.item.category }} · 分值 {{ row.item.score ?? '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="AI 建议（仅供参考）" width="230">
        <template #default="{ row }">
          <template v-if="row.suggestion">
            <div class="cell-sub">{{ rangeText(row.suggestion) }}</div>
            <el-popover placement="right" width="380" trigger="hover" :content="row.suggestion.summary || '无摘要'">
              <template #reference>
                <el-link type="primary" :underline="false">查看要点摘要</el-link>
              </template>
            </el-popover>
          </template>
          <span v-else class="cell-hint">未生成</span>
        </template>
      </el-table-column>
      <el-table-column label="我的打分" width="140">
        <template #default="{ row }">
          <el-input-number
            v-model="drafts[row.item.id].score"
            :min="0"
            :max="row.item.score ?? 999"
            :precision="2"
            size="small"
            controls-position="right"
            style="width: 120px"
          />
        </template>
      </el-table-column>
      <el-table-column label="打分理由 / 备注" min-width="180">
        <template #default="{ row }">
          <el-input v-model="drafts[row.item.id].comment" size="small" placeholder="评标依据说明" />
        </template>
      </el-table-column>
      <el-table-column label="改分理由" width="150">
        <template #default="{ row }">
          <el-input v-model="drafts[row.item.id].reason" size="small" :placeholder="row.my_score ? '改分需说明' : '首次打分可空'" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :loading="savingId === row.item.id" @click="save(row)">保存</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!rows.length && !loading" description="评分项尚未确认（请在条目库确认后由组长跑一次审查）" />
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.left, .right { display: flex; align-items: center; gap: 10px; }
.cell-sub { font-size: 13px; line-height: 1.4; }
.cell-hint { font-size: 12px; color: #999; }
</style>
