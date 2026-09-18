<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import api from '../api'
import ProjectNav from '../components/ProjectNav.vue'
import { INK, STATUS, STATE_LABELS, SURFACE, bidderColorMap } from '../viz/palette'
const route = useRoute()
const projectId = Number(route.params.id)
const me = JSON.parse(localStorage.getItem('pb_user') || '{}')
const isLeader = me.role === 'admin' || me.role === 'leader'

const matrix = ref({ bidders: [], rows: [], summary: [] })
const scores = ref(null)
const scoresDenied = ref(false)
const loading = ref(false)

const stateEl = ref(null)
const scoreEl = ref(null)
let stateChart = null
let scoreChart = null

const STATE_ORDER = ['passed', 'review', 'failed', 'pending']

function totalsOf() {
  const t = {}
  for (const s of matrix.value.summary) t[s.bidder_name] = s.passed + s.failed + s.review + s.pending
  return t
}

function buildStateChart() {
  if (!stateEl.value || !matrix.value.bidders.length) return
  stateChart = stateChart || echarts.init(stateEl.value)
  const totals = totalsOf()
  // 最后一个非零段才做圆角，圆角锚在数据端
  const nonEmpty = STATE_ORDER.filter((s) => matrix.value.summary.some((x) => x[s] > 0))
  const lastKey = nonEmpty[nonEmpty.length - 1]
  const firstKey = nonEmpty[0]

  stateChart.setOption({
    grid: { left: 96, right: 24, top: 36, bottom: 28 },
    legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: INK.secondary, fontSize: 12 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const total = totals[ps[0].name] || 0
        const lines = ps
          .filter((p) => p.value > 0)
          .map((p) => `${p.marker}${p.seriesName}：${p.value} 条`)
        return [`${ps[0].name}　共 ${total} 条`, ...lines].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { lineStyle: { color: INK.axis } },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: INK.grid, width: 1, type: 'solid' } },
      axisLabel: { color: INK.muted, fontSize: 11 },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: matrix.value.bidders.map((b) => b.name),
      axisLine: { lineStyle: { color: INK.axis } },
      axisTick: { show: false },
      axisLabel: { color: INK.secondary, fontSize: 12 },
    },
    series: STATE_ORDER.map((key) => ({
      name: STATE_LABELS[key],
      type: 'bar',
      stack: 'total',
      barMaxWidth: 22,
      itemStyle: {
        color: STATUS[key],
        borderColor: SURFACE,
        borderWidth: 2, // 段间 2px 表面缝，替代描边分隔
        borderRadius: key === lastKey ? [4, 4, 4, 4] : key === firstKey ? [4, 0, 0, 4] : 0,
      },
      label: {
        show: true,
        position: 'inside',
        // 只给放得下的段打标签（占比 >= 15%），避免标签被裁切
        formatter: (p) => {
          const total = totals[p.name] || 0
          return total && p.value / total >= 0.15 ? String(p.value) : ''
        },
        color: '#ffffff',
        fontSize: 11,
      },
      data: matrix.value.summary.map((s) => s[key]),
    })),
  })
}

function buildScoreChart() {
  if (!scoreEl.value || !scores.value) return
  scoreChart = scoreChart || echarts.init(scoreEl.value)
  const items = scores.value.items
  const shortLabel = (it, i) => it.category || `第${i + 1}项`

  scoreChart.setOption({
    grid: { left: 44, right: 24, top: 36, bottom: 46 },
    legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: INK.secondary, fontSize: 12 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const max = items[ps[0].dataIndex]?.max_score
        const lines = ps.map((p) => `${p.marker}${p.seriesName}：${p.value ?? '未打分'}`)
        return [`${ps[0].name}${max != null ? `（满分 ${max}）` : ''}`, ...lines].join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: items.map(shortLabel),
      axisLine: { lineStyle: { color: INK.axis } },
      axisTick: { show: false },
      axisLabel: { color: INK.secondary, fontSize: 11, interval: 0, width: 90, overflow: 'truncate' },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: INK.grid, width: 1, type: 'solid' } },
      axisLabel: { color: INK.muted, fontSize: 11 },
    },
    series: scores.value.bidders.map((b, i) => ({
      name: b.bidder_name,
      type: 'bar',
      barMaxWidth: 26,
      itemStyle: { color: bidderColorMap(scores.value.bidders.map((x) => x.bidder_id)).get(b.bidder_id), borderRadius: [4, 4, 0, 0] },
      // 数值由下方表格承载，图上不逐点打标签
      data: b.items.map((it) => it.average),
    })),
  })
}

function resize() {
  stateChart?.resize()
  scoreChart?.resize()
}

async function load() {
  loading.value = true
  try {
    matrix.value = await api.get(`/projects/${projectId}/compare/matrix`)
    if (isLeader) {
      try {
        scores.value = await api.get(`/projects/${projectId}/compare/scores`)
      } catch (e) {
        scoresDenied.value = true
      }
    } else {
      scoresDenied.value = true
    }
    await nextTick()
    buildStateChart()
    if (scores.value) buildScoreChart()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  stateChart?.dispose()
  scoreChart?.dispose()
})
</script>

<template>
  <div v-loading="loading">
    <ProjectNav :project-id="projectId" title="横向对比" />

    <!-- 每家一个统计块：总分是单值，用数字而不是单柱图 -->
    <div v-if="scores" class="tiles">
      <el-card v-for="b in scores.bidders" :key="b.bidder_id" shadow="never" class="tile">
        <div class="tile-name">{{ b.bidder_name }}</div>
        <div class="tile-hero">{{ b.total_average }}</div>
        <div class="tile-sub">汇总平均总分 · 已打分 {{ b.scored_items }}/{{ scores.items.length }} 项 · {{ scores.experts_total }} 位评委</div>
      </el-card>
    </div>
    <el-card v-else-if="scoresDenied" shadow="never" class="denied">
      <span>评分对比含他人分数，仅组长/管理员可见（专家之间汇总前互相不可见）。审查结论对比见下表与下图。</span>
    </el-card>

    <el-card shadow="never" class="chart-card">
      <template #header><span>审查结论分布（按投标人）</span></template>
      <div ref="stateEl" class="chart" style="height: 240px" />
    </el-card>

    <el-card v-if="scores" shadow="never" class="chart-card">
      <template #header><span>分项均分对比（数值见下方表格）</span></template>
      <div ref="scoreEl" class="chart" style="height: 300px" />
    </el-card>

    <el-card shadow="never" class="chart-card">
      <template #header><span>响应矩阵：条目 × 投标人</span></template>
      <el-table :data="matrix.rows" size="small" height="520">
        <el-table-column label="评审条目" min-width="260">
          <template #default="{ row }">
            <div class="cell-sub">{{ row.item.clause_text }}</div>
            <div class="cell-hint">
              {{ row.item.item_type === 'compliance' ? '符合性(否决)' : '资格条件' }} · {{ row.item.category || '未分类' }}
              <span v-if="row.item.source_page"> · 第{{ row.item.source_page }}页</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column v-for="b in matrix.bidders" :key="b.id" :label="b.name" width="110" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.cells.find((c) => c.bidder_id === b.id)?.state === 'passed' ? 'success'
                : row.cells.find((c) => c.bidder_id === b.id)?.state === 'failed' ? 'danger'
                : row.cells.find((c) => c.bidder_id === b.id)?.state === 'review' ? 'warning' : 'info'"
            >
              {{ row.cells.find((c) => c.bidder_id === b.id)?.state
                ? STATE_LABELS[row.cells.find((c) => c.bidder_id === b.id).state] : '未审' }}
            </el-tag>
            <div v-if="row.cells.find((c) => c.bidder_id === b.id)?.decided" class="decided">专家已定</div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="scores" shadow="never" class="chart-card">
      <template #header><span>分项均分明细（表格视图）</span></template>
      <el-table :data="scores.items" size="small">
        <el-table-column label="评分项" min-width="240">
          <template #default="{ row }">
            <div class="cell-sub">{{ row.clause_text }}</div>
            <div class="cell-hint">{{ row.category }} · 满分 {{ row.max_score ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column v-for="b in scores.bidders" :key="b.bidder_id" :label="b.bidder_name" width="120" align="center">
          <template #default="{ row }">
            {{ b.items.find((x) => x.item_id === row.id)?.average ?? '—' }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.links { display: flex; gap: 14px; }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.tile { flex: 1; min-width: 220px; }
.tile-name { color: #52514e; font-size: 13px; }
.tile-hero { font-size: 32px; font-weight: 600; color: #0b0b0b; line-height: 1.3; }
.tile-sub { color: #898781; font-size: 12px; }
.denied { margin-bottom: 12px; color: #52514e; font-size: 13px; }
.chart-card { margin-bottom: 12px; }
.cell-sub { font-size: 13px; line-height: 1.4; }
.cell-hint { font-size: 12px; color: #898781; }
.decided { font-size: 11px; color: #898781; margin-top: 2px; }
</style>
