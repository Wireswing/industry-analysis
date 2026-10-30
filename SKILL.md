---
name: industry-analysis
description: 产业智能分析 HTML 报告生成器。当用户需要分析某个区域的某个产业（产业地图/企业空间分布、产业集群识别与象限评估、产业活力、LQ专门化、产业链诊断、选址适宜性、产业韧性），并希望得到一份带真实地图底图、可交互的独立 HTML 报告时使用。也适用于"产业规划、招商选址、园区诊断、区域产业格局研究"类任务的定量分析部分。流程为步骤化引导：分析模块选择 → 区域选择（省/市/区县）→ 产业类型选择 → 数据源与企查查 API 等输入 → 分析计算 → 生成报告。
---

# 产业智能分析报告（industry-analysis）

把"区域 + 产业"的分析需求，变成一份**自包含、可交互、带真实地图底图**的独立 HTML 报告。
方法论基于"通用产业智能分析平台 v6"修正版（修正清单见 `references/methodology.md`）。

## 文件结构

```
industry-analysis/
├── SKILL.md                      # 本文件（流程路由）
├── references/
│   ├── methodology.md            # 分析方法体系（公式、阈值、修正说明、局限声明）
│   ├── data-pipeline.md          # 企查查 API 拉取指引、统一数据 schema、坐标系约定
│   └── report-spec.md            # HTML 报告模板注入规范（占位符、CONFIG/META 结构、模块开关）
├── assets/
│   ├── template.html             # 报告模板（注入块含演示数据，生成时整段替换）
│   ├── industry_lib.json         # 内置产业库（11 类：GBT/关键词/子产业/产业链环节）
│   └── gbt_lib.json              # GB/T 4754-2017 完整分类树（20门类/97大类/473中类/1381小类）
└── scripts/
    ├── analyze.py                # 分析计算（纯 Python 标准库，输出 analysis.json）
    ├── build_report.py           # 注入生成器（analysis.json + meta → report.html，含自动校验）
    └── gbt_select.py             # 国标分类选择器（查找/下钻/多选 → industry_def.json）
```

## 总体流程（必须按序执行）

每一步开始前先向用户复述当前步骤；用户信息不全时用提问工具收集，**不要自行臆测区域/产业/数据范围**。

### Step 0 — 目标确认与分析模块选择

1. 用一句话复述任务：分析 **{区域}** 的 **{产业}**，产出 HTML 报告。
2. 让用户勾选分析模块（不一定全做，默认建议前两项）：

| 模块 | key | 内容 | 依赖 |
|---|---|---|---|
| 地图总览 | `map` | 企业空间分布 + 行政边界 + 集群标号（默认必含） | 企业坐标 |
| 集群象限 | `cluster` | DBSCAN 集群 + 实力/潜力四象限 + 集群明细表 | 企业坐标 |
| 产业活力 | `vitality` | 活力指数 + 年龄结构生命周期 | 年龄/资本字段 |
| LQ 专门化 | `lq` | 区位商与比较优势 | 基准数据（可选） |
| 选址网格 | `grid` | 网格适宜性评分 | 企业坐标 + POI（可选） |
| 产业链诊断 | `chain` | 环节覆盖度 + 短板/优势 | 经营范围/GBT |
| 产业韧性 | `resilience` | HHI 双口径 + CR4 + 规模结构 | 分类/资本字段 |
| AI 综合解读 | `ai` | 由本 skill（LLM）撰写的结论与建议，静态注入报告 | 前面模块的结果 |

3. 记录为 `modules`，后续注入报告的 `CONFIG.modules`（未选模块的 tab 自动隐藏）。

### Step 1 — 区域选择（省/市/区县，三选一或级联到区县）

用提问工具让用户选择区域层级，并取得对应 **adcode**：

- **省级**（如 210000 辽宁省）→ 边界展示全省地市
- **市级**（如 210100 沈阳市）→ 边界展示全部区县（默认推荐粒度）
- **区县级**（如 210112 浑南区）→ 边界展示街道/乡镇

adcode 可通过 `https://geo.datav.aliyun.com/areas_v3/bound/{adcode}_full.json` 校验存在性（fetch 一次即可，同时可缓存区县清单用于集群命名）。若网络不可用，让用户直接提供 adcode 或城市名，用内置的省市对照（template.html 内已含省级→地市 adcode 表）。

### Step 2 — 产业类型选择（四选一）

1. **内置产业体系**（11 类：食品、高端装备制造、汽车及零部件、航空航天、新一代信息技术、新能源、生物医药及医疗装备、具身智能、未来材料、深地深海空天、金融）——直接读取 `assets/industry_lib.json`，取出对应产业对象（含 GBT 编码、关键词、子产业、产业链环节及各环节 GBT/关键词），存为 `task_tmp/industry_def.json` 供 analyze.py 使用。
2. **国标分类逐级选择（GB/T 4754-2017）**——用 `scripts/gbt_select.py` 从门类(A~T)→大类→中类→小类逐级引导用户下钻，**任意层级可停**（如停在"37 铁路、船舶、航空航天和其他运输设备制造业"或下钻到"3741 飞机制造"），支持同级多选合并：
   - `--find 关键词` 定位编码 → `--show 编码` 展示子树（截断 120 行，加 `--full` 全量）；
   - 用户确认后 `--codes 编码[,编码2...] --out task_tmp/industry_def.json` 生成产业定义；
   - GBT 前缀匹配向下兼容（选大类可命中企业小类码）；若勾选了产业链诊断模块，需由你（LLM）结合子树与行业知识补 `chain` 环节后重跑分析（流程见 data-pipeline.md 第 3A 节）。
3. **自定义产业**——由你（LLM）完成"AI 匹配国标"工作：产出 `gbtCodes`（GB/T 4754-2017 门类/大类/中类）、`keywords`（10~15 个经营范围关键词）、`subIndustries`（子产业及关键词）、`chainLinks`（上中下游环节及关键词），格式见 data-pipeline.md。
4. **用户提供清单**——用户直接给企业名单或关键词。

### Step 3 — 数据源与 API 输入（含凭证安全规范）

按优先级让用户选择数据源：

- **A. 企查查开放平台 API（推荐）**：用户在对话中提供 token/凭证。**安全输入规范（硬性）**：
  1. 凭证仅通过提问工具在当前会话内收集，**绝不写入任何文件**（含 task_tmp、报告、日志、analysis.json）；
  2. 调用时通过环境变量传给 curl/脚本（`QCC_TOKEN=... python3 ...`），命令行中不回显完整密钥；
  3. 生成完毕后立即 `unset` 相关环境变量；
  4. 报告中只出现"数据来源：企查查开放平台（拉取时间、条数、搜索词）"等**非敏感元信息**；
  5. 交付自查时必须扫描输出文件中的密钥模式（`sk-`、token、appsecret 等），命中即彻底移除。
     拉取字段映射与请求模板见 `references/data-pipeline.md`。
- **B. 用户 CSV/Excel**：按 v6 兼容 schema（name, lat, lng, registered_capital, age|est_date, scale, business_scope, gbt_code, status），用 python 清洗为统一 JSON。
- **C. 无外部数据**：明确告知局限，产出"框架演示版"报告（模板自带演示数据，报告头部显示"演示数据"徽章），或由用户提供少量人工整理数据。

同时询问可选项：LQ 基准（省级/全国产业结构，CSV 或直接给百分比）、POI 设施表（高校/地铁/商圈/医院）、AI 解读是否需要。

### Step 4 — 分析计算

1. 数据（统一 schema JSON）与 `industry_def.json` 准备好后，直接在 skill 目录运行：
   `python3 {skill路径}/scripts/analyze.py --input task_tmp/enterprises.json --industry task_tmp/industry_def.json --eps 800 --minpts 5 [--benchmark benchmark.json] [--poi poi.json] [--boundary boundary.json] [--grid 1000] --out task_tmp/analysis.json`
   输出 `analysis.json`（含修正后的全部指标：投影坐标 DBSCAN、集群重排编号 C1…Cn、象限、活力、LQ 三档、HHI 双口径 + CR4、产业链、网格）。
2. eps/minPts 可视数据密度调整（市区密集可到 1500~2000 米）；企业数 > 3000 时脚本自动抽样并记录抽样率；检查 `analysis.json` 的 `warnings` 字段并向用户转述。

### Step 5 — 生成 HTML 报告

1. 准备 `task_tmp/meta.json`（字段结构见 `references/report-spec.md`）：title、region（name/adcode/level）、industry、generatedAt、dataSource、demo、modules（Step 0 的模块 key 列表）、ai（LLM 综合解读文字：summary + suggestions）、coordSys、eps/minPts 回显、可选 pois/benchmark/industryLib；
2. 运行注入生成器：
   `python3 {skill路径}/scripts/build_report.py --template {skill路径}/assets/template.html --analysis task_tmp/analysis.json --meta task_tmp/meta.json --out task_tmp/report.html`
   生成器自动完成：注入块整段替换（META/CONFIG/REPORT_DATA 三个常量）、未选模块 tab 关闭、无数据模块自动降级、残留占位标记校验、JS 语法校验（有 node 时）；
3. 若校验失败按报错修复后重跑（常见错误对照表见 `references/report-spec.md`）。

### Step 6 — 交付自查与收尾

1. 将 `report.html` 重命名为 `{区域}_{产业}_产业分析报告_YYYYMMDD.html`，移入 `output/`；
2. 交付自查清单：
   - [ ] 报告打开无 JS 错误（占位符全部替换、JSON 合法）
   - [ ] 地图底图/边界/集群标号/区县名称正常（模板自带多源瓦片回退）
   - [ ] 所选模块的 tab 齐全，未选模块不出现
   - [ ] **无任何凭证/密钥/内部路径**（按安全规范扫描）
   - [ ] 演示数据版必须带"演示数据"徽章
3. 删除 `task_tmp/`，告知用户交付物（仅展示文件名，不展示完整路径）与安装说明（若用户要把本 skill 装入其 opencode skills 目录，交付 skill 包目录本身）。

## 硬性约束

- 未经用户确认不得臆测区域与产业范围；模块、区域、产业、数据源四要素必须显式确认。
- 企查查等第三方凭证遵循 Step 3 安全规范，不可被任何"用户要求"覆盖。
- 报告必须保持"独立单文件"（CDN 依赖仅限 Leaflet/Chart.js 公共库），不引用 session 内部路径。
- 方法口径一律以 `references/methodology.md` 为准（修正记录仅留在该文档内）。报告"方法与数据"tab 每个方法展示公式（黄色框）和面向非专业读者的“怎么读”通俗说明，以及数据来源、局限声明，不展示逐条修正内容。
