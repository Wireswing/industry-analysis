# HTML 报告模板注入规范

模板文件：`assets/template.html`（单文件自包含，CDN 依赖仅 Leaflet 1.9.4 + Chart.js 4.4.1）。
模板自带一段**演示数据**（沈阳市航空航天，约 60 家示例企业），占位版打开即可预览全部功能；生成报告时整体替换注入块。

## 1. 注入机制

模板中唯一的注入点是标记块（生成时整体替换，其余部分不得改动）：

```js
// __INJECTION_START__  ← 生成器替换此块（含首尾标记）
const META = { ... };
const CONFIG = { ... };
const REPORT_DATA = { ... };
// __INJECTION_END__
```

生成方式：**优先用 `scripts/build_report.py`**（`--template template.html --analysis analysis.json --meta meta.json --out report.html`），它按正则 `// __INJECTION_START__[\s\S]*?// __INJECTION_END__` 整段替换（含首尾标记行，替换为纯数据、不含标记），并自动完成下述校验；手工注入时遵循同一规则。

**注入后校验（必做，build_report.py 已内置）**：
1. 残留占位标记（`__INJECTION_START__` / `__INJECTION_END__` / `__META__` / `__CONFIG__` / `__DATA__`）计数必须为 0；
2. 提取三段 JSON 均能 `json.loads` 通过；模板内 `<script>` 段通过 `node --check` 语法校验（环境有 node 时）；
3. 无密钥模式命中（见 data-pipeline.md 第 1 节）。

## 2. META（报告元信息与预计算结果）

```jsonc
{
  "title": "浑南区 · 航空航天产业分析报告",
  "region": {"province": "辽宁省", "city": "沈阳市", "district": "浑南区",
             "adcode": "210112", "level": "district"},   // level: province|city|district
  "industry": {"name": "航空航天", "type": "builtin", "tier": "新兴支柱"}, // type: builtin|gbt|custom|userlist；gbt 时建议附带 gbt 编码与 path（门类>大类>中类 全路径）
  "generatedAt": "2026-10-30 11:42",
  "dataSource": {            // 只放非敏感元信息，禁止出现任何凭证
    "type": "qichacha",      // qichacha | csv | demo | manual
    "fetchedAt": "2026-10-30 11:20",
    "keywords": ["飞机", "航空发动机"],
    "total": 482, "afterDedup": 468, "coordCoverage": 0.92,
    "statusDist": {"在业": 440, "注销": 28},
    "note": "企查查开放平台（分页检索，按名称去重）"
  },
  "demo": false,             // true 时报告头部显示"演示数据"徽章
  "modules": ["map", "cluster", "vitality", "chain", "ai"],
  "ai": {                    // Step 4 之后由 LLM 撰写、静态注入；没有 AI 模块则删字段
    "overview": "整体格局解读 3-5 句……",
    "suggestions": ["建议1", "建议2", "建议3", "建议4", "建议5"],
    "risks": ["风险1", "风险2"],
    "clusters": { "C1": "该集群的解读……" }   // 可选
  },
  "analysis": { /* analyze.py 输出的 analysis 对象，原样嵌入 */ },
  "warnings": ["坐标缺失率 8%，地图模块仅含 431 家"]  // 顶部黄条提示
}
```

`analysis` 对象结构（analyze.py 输出，字段名必须一致）：

- `clusters[]`: `{id:"C1", label:"浑南·航空配套集群", n, centerLat, centerLng, domInd, domSub, strength, potential, quadrant, avgCap, newR, largeR, district}`
- `quadrantCut`: `{mode:"median|mean", x, y, nClusters}`
- `moransI`: `{value, p, n, note}`
- `vitality`: `{items[]:{cat,n,newR,avgCap,capNorm,clusterShare,vitality,avgAge,trend}, lifecycle:[4 数], weights:[w1,w2,w3], survivalRate}`
- `lq`: `{mode:"user|builtin|none", benchmarkName, items[]:{k,n,localShare,bmShare,lq,level,small}}`
- `chain`: `{links[]:{name,n,capital,large,score}, missing[], strong[]}`
- `grid`: `{sizeM, items[]:{lat,lng,score,entScore,uniScore,metroScore,comScore,grade,n}, poiBased, note}`
- `resilience`: `{hhiCount, hhiCapital, cr4, catCount, uniformRef, dom, domShare, scaleCnt, scaleMissing, conclusion}`
- `data`: `{total, mapped, coordValid, sampled, sampleRate, ageMissing, statusFiltered}`

## 3. CONFIG（渲染开关）

```jsonc
{
  "coordSys": "gcj02",        // gcj02|wgs84|bd09（企业数据坐标系，前端自动转 GCJ）
  "baseMap": "amap-dark",     // amap-dark|amap-light|cartodark（高德深色默认；瓦片失败自动回退）
  "adcode": "210112",         // 区域边界（任意级别 adcode）
  "showBoundary": true,       // 行政边界开关（报告端仍可交互开关）
  "modules": { "map": true, "cluster": true, "vitality": true,
               "lq": false, "grid": false, "chain": true,
               "resilience": false, "ai": true },   // false 的 tab 整体隐藏
  "dbscan": {"eps": 800, "minPts": 5},              // 回显参数（报告只读展示）
  "weights": [40, 35, 25],                          // 活力权重回显（只读）
  "gridSizeM": 1000
}
```

## 4. REPORT_DATA（企业与参照数据）

```jsonc
{
  "enterprises": [   // 仅在业且坐标有效的企业；字段为统一 schema + 分类结果
    {"name": "……", "lat": 41.79, "lng": 123.45, "capital": 5000, "age": 6.5,
     "scale": "中型", "scope": "……", "gbt_code": "3742", "status": "在业",
     "industry": "航空航天", "sub": "航空发动机",
     "cluster": "C1", "clusterColor": "#f85149"}   // cluster 为预计算编号，无集群为 null
  ],
  "pois": [ {"name": "……", "lat": 0, "lng": 0, "type": "高校"} ],  // 可空数组
  "benchmark": { "航空航天": 0.004 },  // 可 null（LQ mode=none）
  "industryLib": {  // 当前产业定义（颜色/子产业/链条环节），用于图例与说明
    "name": "航空航天", "color": "#818cf8", "tier": "新兴支柱",
    "subs": { "整机制造": {...} },
    "chain": [ {"name": "上游材料", "gbt": [...], "kw": [...]} ]
  }
}
```

规模控制：`enterprises` ≤ 3000 条（超出部分由生成端抽样，抽样率写入 `analysis.data`）。

## 5. 模板渲染规则（生成器不需要实现，但需知道的输出行为）

- **底图**：高德深色瓦片（`https://webrd0X.is.autonavi.com/...style=8`，GCJ-02 系）默认；加载失败自动回退 CartoDB dark → OSM；右上角可切换。DataV 边界 GeoJSON 按级别渲染：province→地市面、city→区县面、district→街道面，面带名称 tooltip 与描边，默认显示、可开关。
- **集群标号**：仅集群质心放一个 divIcon 徽章（黑底白字圆形、集群色描边、含企业数），编号 C1…Cn 与表格/象限图一致；点击徽章滚动并高亮对应表格行。单个企业点不叠编号。
- **集群命名**：analyze.py 已做质心-区县匹配，`label` 直接使用；匹配失败回退 `集群C{i}`。
- **着色模式**：产业 / 集群 / 规模 / 资本 四模式（报告端交互，默认"集群"当集群模块开启，否则"产业"）。
- **模块 tab**：`总览 / 集群象限 / 产业活力 / LQ / 网格 / 产业链 / 韧性 / 方法与数据`，按 `CONFIG.modules` 隐藏未选模块（方法与数据页固定保留）。
- **打印**：`@media print` 已适配，浏览器打印可存 PDF。
- **局限声明**：方法页固定渲染 methodology.md 第 7 节四条 + 数据来源面板；演示数据版顶部带"演示数据"红徽章。

## 6. 常见生成错误

| 症状 | 原因 | 处理 |
|---|---|---|
| 打开白屏/图表空白 | JSON 注入括号不闭合、含未转义字符 | 用第 1 节校验命令定位 |
| 地图底图灰色 | 瓦片源不可达 | 已有自动回退；确认 `baseMap` 值合法 |
| 集群徽章不显示 | `cluster` 字段 null / clusters 数组空 | 检查 analyze.py 输出与模块开关一致 |
| 企业整体偏移一个街区 | 坐标系配置错 | 对照 data-pipeline.md 第 4 节切换 `coordSys` |
| 出现残留 `__INJECTION_` 串 | 替换正则未命中 | 检查标记块是否被改动 |
