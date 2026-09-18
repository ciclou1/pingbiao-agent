<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import ProjectNav from '../components/ProjectNav.vue'

const route = useRoute()
const projectId = Number(route.params.id)

const data = ref({ bidders: [], experts_total: 0 })
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    data.value = await api.get(`/projects/${projectId}/score/summary`)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <ProjectNav :project-id="projectId" title="评分汇总（组长视图）" />
    <el-alert
      type="info"
      show-icon
      :closable="false"
      title="汇总视图仅组长/管理员可见；专家之间在汇总前互相不可见。"
      style="margin-bottom: 12px"
    />

    <el-card v-for="b in data.bidders" :key="b.bidder.id" shadow="never" style="margin-bottom: 16px">
      <template #header>
        <div class="card-head">
          <span>{{ b.bidder.name }}</span>
          <span class="total">汇总平均总分：{{ b.total_average }}</span>
        </div>
      </template>
      <el-table :data="b.items" size="small">
        <el-table-column label="评分项" min-width="240">
          <template #default="{ row }">
            <div class="cell-sub">{{ row.item.clause_text }}</div>
            <div class="cell-hint">{{ row.item.category }} · 分值 {{ row.item.score ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="各专家打分" min-width="260">
          <template #default="{ row }">
            <template v-if="row.scores.length">
              <el-tag v-for="s in row.scores" :key="s.expert_id" size="small" style="margin-right: 6px" effect="plain">
                {{ s.expert_name }}：{{ s.score ?? '—' }}
              </el-tag>
            </template>
            <span v-else class="cell-hint">尚无专家打分</span>
          </template>
        </el-table-column>
        <el-table-column label="均分" width="90">
          <template #default="{ row }">{{ row.average ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="已打分" width="110">
          <template #default="{ row }">{{ row.scored_experts }}/{{ data.experts_total }}</template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-empty v-if="!data.bidders.length && !loading" description="暂无评分数据" />
  </div>
</template>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.total { color: #1f3a5f; font-weight: 600; }
.cell-sub { font-size: 13px; line-height: 1.4; }
.cell-hint { font-size: 12px; color: #999; }
</style>
