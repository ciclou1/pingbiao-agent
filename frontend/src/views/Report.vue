<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)

const data = ref(null)
const loading = ref(false)
const exporting = ref('')

const STATE_LABELS = { passed: '通过', failed: '不通过', review: '待复核' }
const TYPE_LABELS = { qualification: '资格条件', compliance: '符合性(否决)', scoring: '评分项' }

const stats = computed(() => data.value?.stats || {})
const cellsOf = (row, bidderId) => row.cells.find((c) => c.bidder_id === bidderId) || {}

async function load() {
  loading.value = true
  try {
    data.value = await api.get(`/projects/${projectId}/report/data`)
  } finally {
    loading.value = false
  }
}

async function download(kind) {
  exporting.value = kind
  try {
    const blob = await api.get(`/projects/${projectId}/report/export.${kind}`, { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const suffix = kind === 'docx' ? '评标报告' : '评标明细'
    a.href = url
    a.download = `${suffix}_${data.value?.project.name || ''}.${kind}`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch {
    ElMessage.error('导出失败')
  } finally {
    exporting.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <ProjectNav :project-id="projectId" title="评标报告（底稿）">
      <template #actions>
        <el-button type="primary" size="small" :loading="exporting === 'docx'" @click="download('docx')">导出 Word 底稿</el-button>
        <el-button size="small" :loading="exporting === 'xlsx'" @click="download('xlsx')">导出 Excel 明细</el-button>
      </template>
    </ProjectNav>

    <el-alert
      type="warning"
      show-icon
      :closable="false"
      title="本报告为系统辅助生成的底稿：AI 结论仅供参考，否决与评分最终以评标委员会审议结果为准。"
      style="margin-bottom: 12px"
    />

    <template v-if="data">
      <el-card shadow="never" class="block">
        <template #header><span>一、项目基本情况</span></template>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="项目名称">{{ data.project.name }}</el-descriptions-item>
          <el-descriptions-item label="项目类型">{{ data.project.type }}</el-descriptions-item>
          <el-descriptions-item label="评标办法">{{ data.project.method_type }}</el-descriptions-item>
          <el-descriptions-item label="控制价（万元）">{{ data.project.control_price ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="投标人">{{ stats.bidders }} 家</el-descriptions-item>
          <el-descriptions-item label="评标委员会">{{ stats.experts }} 人</el-descriptions-item>
          <el-descriptions-item label="上传文件">{{ stats.documents }} 个</el-descriptions-item>
          <el-descriptions-item label="确认条目">{{ stats.items_total }} 条</el-descriptions-item>
          <el-descriptions-item label="专家已定结论">{{ stats.decided_by_expert }} 项</el-descriptions-item>
        </el-descriptions>
        <div class="members">
          评标委员会成员：
          <el-tag v-for="m in data.members.filter((x) => ['expert', 'leader'].includes(x.role))" :key="m.username" size="small" style="margin-right: 6px">
            {{ m.name }}{{ m.group_no ? `（${m.group_no}组）` : '' }}
          </el-tag>
        </div>
      </el-card>

      <el-card shadow="never" class="block">
        <template #header>
          <span>二、资格与符合性审查结果（{{ stats.by_type.qualification || 0 }} 资格 / {{ stats.by_type.compliance || 0 }} 符合性）</span>
        </template>
        <el-table :data="data.review_rows" size="small" height="420">
          <el-table-column label="评审条目" min-width="280">
            <template #default="{ row }">
              <div class="cell-sub">{{ row.clause_text }}</div>
              <div class="cell-hint">{{ TYPE_LABELS[row.item_type] }} · {{ row.category || '未分类' }}<span v-if="row.source_page"> · 第{{ row.source_page }}页</span></div>
            </template>
          </el-table-column>
          <el-table-column v-for="b in data.bidders" :key="b.id" :label="b.name" width="120" align="center">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="cellsOf(row, b.id).state === 'passed' ? 'success' : cellsOf(row, b.id).state === 'failed' ? 'danger' : cellsOf(row, b.id).state === 'review' ? 'warning' : 'info'"
              >
                {{ STATE_LABELS[cellsOf(row, b.id).state] || '未审' }}
              </el-tag>
              <div v-if="cellsOf(row, b.id).expert_state" class="cell-hint">专家终值</div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-row :gutter="12">
        <el-col :span="12">
          <el-card shadow="never" class="block">
            <template #header><span>三、否决（不通过）情况（{{ data.failures.length }} 条 / {{ (data.failure_groups || []).length }} 组事实）</span></template>
            <el-empty v-if="!data.failures.length" description="无否决项" :image-size="50" />
            <div v-for="(g, i) in data.failure_groups || []" :key="i" class="list-item">
              <el-tag type="danger" size="small">{{ g.bidder }}</el-tag>
              <span class="li-text">{{ g.reason }}</span>
              <el-tag v-if="g.count > 1" size="small" type="warning" effect="plain" style="margin-left: 6px">
                涉及 {{ g.count }} 条条款
              </el-tag>
              <div v-if="g.count > 1" class="li-clauses">
                <div v-for="(it, j) in g.items" :key="j" class="cell-hint">· {{ it.clause }}</div>
              </div>
            </div>
            <div v-if="(data.failure_groups || []).some((g) => g.count > 1)" class="cell-hint" style="margin-top: 8px">
              说明：招标文件同一实质要求常在不同章节重复规定，故按「否决事实」归并展示。
            </div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="block">
            <template #header><span>四、待复核事项（{{ data.pending.length }}）</span></template>
            <el-empty v-if="!data.pending.length" description="无待复核项" :image-size="50" />
            <div v-for="(x, i) in data.pending" :key="i" class="list-item">
              <el-tag type="warning" size="small">{{ x.bidder }}</el-tag>
              <span class="li-text">{{ x.clause }}</span>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="block">
        <template #header><span>五、评分汇总（{{ data.scoring_items_count }} 个评分项）</span></template>
        <el-table v-if="data.scoring_items_count" :data="data.bidders" size="small">
          <el-table-column label="投标人" width="160">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="分项均分（按评分项）" min-width="320">
            <template #default="{ row, $index }">
              <span v-for="(it, i) in data.scores[$index].items" :key="i" class="score-chip">
                {{ it.category || `第${i + 1}项` }}：{{ it.average ?? '—' }}/{{ it.max_score ?? '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="汇总平均总分" width="130" align="center">
            <template #default="{ $index }">
              <span class="total">{{ data.scores[$index].total_average }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="本项目无评分项" :image-size="50" />
      </el-card>

      <el-card shadow="never" class="block">
        <template #header><span>六、签字栏</span></template>
        <div class="sign">
          <div v-for="m in data.members.filter((x) => ['expert', 'leader'].includes(x.role))" :key="m.username" class="sign-item">
            {{ m.name }}　____________________
          </div>
        </div>
        <div class="cell-hint">生成时间：{{ new Date(data.generated_at).toLocaleString('zh-CN') }}</div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.actions { display: flex; gap: 8px; }
.block { margin-bottom: 12px; }
.members { margin-top: 10px; color: #52514e; font-size: 13px; }
.cell-sub { font-size: 13px; line-height: 1.4; }
.cell-hint { font-size: 12px; color: #898781; }
.list-item { padding: 8px 0; border-bottom: 1px solid #e1e0d9; }
.li-text { margin-left: 8px; font-size: 13px; }
.li-clauses { margin: 4px 0 0 26px; }
.li-reason { color: #52514e; font-size: 12px; margin-top: 4px; }
.score-chip { display: inline-block; margin-right: 12px; font-size: 12px; color: #52514e; }
.total { font-size: 18px; font-weight: 600; }
.sign { display: flex; flex-wrap: wrap; gap: 18px; }
.sign-item { font-size: 14px; color: #0b0b0b; }
</style>
