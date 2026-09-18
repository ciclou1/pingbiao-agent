# 辅助评标智能体

面向**评标委员会**的「开标后辅助评标工作台」：解析招标文件形成结构化评审条目库，逐条对照各投标文件给出**带原文页码证据**的三态结论，辅助专家审查、打分、问答、横向对比、清标与围标预警，最终汇编评标报告底稿。

> 本项目**不做**电子招投标交易平台（报名、CA、远程开标、电子签章、供应商门户），也不做自动定标。

## 合规红线（设计约束）

1. **AI 只辅助，不决策**：不自动否决、不自动定标。判定只有三态：`通过 / 不通过 / 待复核`，**默认待复核**。
2. **无证据不得下结论**：每条结论必须带招标/投标文件的原文页码引用；检索不到材料一律转「待复核」。
3. **专家独立评审**：打分在汇总前**专家之间互相不可见**；审查结论属评标委员会共同结论，不做隔离。
4. **全程留痕**：登录、上传、确认、打分、改分、导出均写 append-only 审计日志；改分记录前后值与理由。
5. 报告首行声明「系统辅助生成底稿，结论以评标委员会为准」，并留签字栏。

## 架构

| 服务 | 说明 |
|---|---|
| `api` | FastAPI：项目/文件/条目库/审查/评分/问答/对比/清标/报告 + JWT 四角色鉴权 |
| `worker` | 后台任务（解析、条目抽取、审查、向量索引），独立进程避免长任务拖慢接口 |
| `web` | Vue3 + Element Plus + ECharts，nginx 托管（前端 API 一律相对路径 `/api/v1`） |
| `db` | PostgreSQL 16 + pgvector（关系数据与向量同库） |
| `embed` | BGE-M3 向量化服务（CPU；模型挂载，不烘焙进镜像） |
| `ocr` | 扫描件 OCR（可选，compose profile `ocr`） |

**AI 编排**：LLM 走 OpenAI 兼容网关（`.env` 配 `LLM_BASE_URL/API_KEY/MODEL`），带重试、模型降级与截断修复；
**检索**：中文 n-gram 关键词（+领域同义词扩展）与 BGE-M3 向量按路径组合——审查路径关键词优先、向量只补位；问答路径 RRF 融合。
**判定**：LLM 三态判定 + 确定性校验器（数值门槛复核、无引用/低置信一律降级为待复核）。

## 快速开始

```bash
cp .env.example .env          # 填 SECRET_KEY、LLM_API_KEY
docker compose -p pingbiao up -d --build
docker compose -p pingbiao --profile ocr up -d ocr     # 可选：扫描件 OCR
```

- 界面：http://localhost:5180 （初始账号见 `.env` 的 `ADMIN_USERNAME/ADMIN_PASSWORD`）
- 向量检索需自备模型：把 BGE-M3 放到 `models/bge-m3/`（可从魔搭下载：`snapshot_download('BAAI/bge-m3')`）。
  **模型不入库**（2.2GB）；未配置时检索自动退回纯关键词，流程不受影响。
- 查看向量状态：`GET /api/v1/projects/{id}/vector-status`，问答页右上角也有标识。

## 目录

```
backend/app/      FastAPI 应用（api/ 路由 · worker.py 任务 · retriever.py 混合检索
                  · reviewer.py 判定 · checker.py 确定性校验 · report.py 报告汇编
                  · clearing.py 清标 · collusion.py 围标预警 · extractor.py 条目抽取）
frontend/src/     Vue3 页面与组件（views/ · components/ProjectNav|DocViewer · viz/ 图表配色）
embed_service/    BGE-M3 向量化服务      ocr_service/  扫描件 OCR 服务
eval/             评测集（cases/ 样例 + results/ 报告）
scripts/          评测与验证脚本（eval.py · retrieval_probe.py · verify_vector.py
                  · load_test.py · real1_bids.py · check-vue-templates.mjs）
参考资料/README.md 调研索引（第三方仓库未入库）
PRD_辅助评标智能体.md  方案文档（含 as-built 实施记录与实测数据）
```

## 评测与验证

```bash
python scripts/eval.py demo              # 端到端评测（建项目→抽取→审查→出报告）
python scripts/new_case.py 我的项目        # 建真实项目脚手架（放标书 + case.json）
python scripts/load_test.py 50 30        # 50 并发压测
```

已有实测（详见方案文档）：条目抽取来源页码 100%、证据定位自洽率 100%、审查判定 0 漏判 0 误判（合成样例）、
50 并发 196 req/s 且 0 错误、困难口语化提问 recall@10 由 50% 提升至 88%。

## 依赖与数据说明

- **不入库**：`.env`（密钥）、`models/`（模型）、上传的标书与评测样例文件（`*.pdf/docx/xlsx/zip`）、第三方调研仓库。
- 标书文件按《招标投标法》属受控材料，请勿将真实项目文件提交到任何远端仓库。
