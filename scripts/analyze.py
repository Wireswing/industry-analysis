#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产业智能分析 · 分析计算脚本（纯 Python 标准库）
输入：统一 schema 的企业 JSON（+ 可选 POI/基准/边界/产业定义）
输出：analysis.json（含 analysis 结果对象 + 增强后的 enterprises 数组）
口径：见 references/methodology.md（v6 修正版）
"""
import argparse, json, math, random, re, sys
from datetime import date, datetime

CLUSTER_COLORS = ['#f85149', '#f59e0b', '#3fb950', '#06b6d4', '#818cf8', '#ec4899',
                  '#f97316', '#a855f7', '#fbbf24', '#34d399', '#60a5fa', '#fb7185',
                  '#a3e635', '#e879f9', '#38bdf8', '#fb923c']
BUILTIN_BENCHMARK = {  # methodology.md 附录 B（近似，仅产业大类层）
    '食品': .055, '高端装备制造': .045, '汽车及零部件': .030, '航空航天': .004,
    '新一代信息技术': .052, '新能源': .022, '生物医药及医疗装备': .018, '具身智能': .008,
    '未来材料': .015, '深地深海空天': .006, '金融': .028}
ACTIVE = ('在业', '存续', '开业', '正常')


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--industry', default=None, help='产业定义 JSON（单产业对象）')
    p.add_argument('--benchmark', default=None, help='基准 JSON：{"产业/子产业": 占比}')
    p.add_argument('--poi', default=None, help='POI JSON 数组 [{name,lat,lng,type}]')
    p.add_argument('--boundary', default=None, help='行政边界 GeoJSON（含 properties.name，用于集群命名）')
    p.add_argument('--eps', type=float, default=800, help='DBSCAN 半径（米）')
    p.add_argument('--minpts', type=int, default=5)
    p.add_argument('--grid', type=float, default=1000, help='网格尺寸（米）')
    p.add_argument('--weights', default='40,35,25')
    p.add_argument('--today', default=None, help='分析基准日 YYYY-MM-DD（默认今天）')
    p.add_argument('--max-map-points', type=int, default=3000)
    p.add_argument('--out', default='analysis.json')
    return p.parse_args()


def load_json(path):
    if not path:
        return None
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception as e:
        print(f'[warn] 无法读取 {path}: {e}', file=sys.stderr)
        return None


def to_age(rec, today):
    if rec.get('age') not in (None, '', 0):
        try:
            return float(rec['age'])
        except Exception:
            pass
    est = rec.get('est_date') or rec.get('startDate') or ''
    m = re.match(r'(\d{4})[-/年]?(\d{1,2})[-/月]?(\d{1,2})?', str(est))
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
            return max(0.0, (today - d).days / 365.25)
        except Exception:
            pass
    return None


def parse_capital(rec):
    v = rec.get('capital', rec.get('registered_capital'))
    if v in (None, ''):
        return None
    try:
        return float(v)
    except Exception:
        s = str(v)
        num = re.sub(r'[^\d.]', '', s)
        try:
            f = float(num) if num else 0.0
            if '亿美元' in s:
                f *= 70000
            elif '美元' in s:
                f *= 0.07
            elif '万' not in s:
                f /= 10000.0   # 元 → 万元
            return f
        except Exception:
            return None


def scale_of(rec):
    s = str(rec.get('scale') or '')
    for k in ('大型', '中型', '小型', '微型'):
        if k in s:
            return k
    return None


def gbt_prefix_match(gbt, codes):
    if not gbt or not codes:
        return False
    gbt = str(gbt).strip()
    codes = [str(c) for c in codes]
    return any(gbt.startswith(c) or c.startswith(gbt) for c in codes if c)


def classify(rec, ind):
    """rec 缺 industry/sub 时按产业定义分类（GBT 优先、关键词归一化命中率）"""
    if rec.get('industry') and rec.get('sub'):
        return rec['industry'], rec['sub']
    if not ind:
        return rec.get('industry') or '其他', rec.get('sub') or '其他'
    gbt = str(rec.get('gbt_code') or '').strip()
    scope = str(rec.get('scope') or rec.get('business_scope') or '').lower()
    kws = ind.get('kw') or []
    score = 10.0 if gbt_prefix_match(gbt, ind.get('gbt') or []) else 0.0
    if kws:
        score += 10.0 * sum(1 for k in kws if k.lower() in scope) / len(kws)
    if score <= 0:
        return '其他', '其他'
    sub, best_ss = '其他', 0.0
    for name, info in (ind.get('subs') or {}).items():
        ss = 10.0 if gbt_prefix_match(gbt, info.get('gbt') or []) else 0.0
        ik = info.get('kw') or []
        if ik:
            ss += 10.0 * sum(1 for k in ik if k.lower() in scope) / len(ik)
        if ss > best_ss:
            best_ss, sub = ss, name
    if sub == '其他' and ind.get('catchAllSub'):
        sub = ind['catchAllSub']
    return ind.get('name', '目标产业'), sub


# ── 投影与距离（修正：米制） ─────────────────────────────
def project(lat, lng, phi0):
    return lng * 111320.0 * math.cos(math.radians(phi0)), lat * 110574.0


def grid_dbscan(pts, eps, minpts):
    """pts: [(x,y)] 米制坐标；返回 labels（-1 噪声，>=0 簇号）。网格分桶 O(n)。"""
    n = len(pts)
    if n == 0:
        return []
    cell = max(eps, 1.0)
    buckets = {}
    for i, (x, y) in enumerate(pts):
        buckets.setdefault((int(x // cell), int(y // cell)), []).append(i)
    labels = [-1] * n

    def neighbors(i):
        bx, by = int(pts[i][0] // cell), int(pts[i][1] // cell)
        nb = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((bx + dx, by + dy), ()):
                    if j == i:
                        continue
                    ddx, ddy = pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]
                    if ddx * ddx + ddy * ddy <= eps * eps:
                        nb.append(j)
        return nb

    cid = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        nb = neighbors(i)
        if len(nb) < minpts:
            continue   # 噪声
        labels[i] = cid
        queue = [j for j in nb if labels[j] == -1]
        while queue:
            j = queue.pop()
            if labels[j] != -1:
                continue
            labels[j] = cid
            nb2 = neighbors(j)
            if len(nb2) >= minpts:
                queue.extend(k for k in nb2 if labels[k] == -1)
        cid += 1
    return labels


# ── 点在多边形（射线法） ─────────────────────────────────
def pip(pt, geom):
    x, y = pt

    def ring(r):
        inside = False
        for i in range(len(r) - 1):
            x1, y1, x2, y2 = r[i][0], r[i][1], r[i + 1][0], r[i + 1][1]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                inside = not inside
        return inside

    if not geom:
        return False
    if geom.get('type') == 'Polygon':
        return any(ring(r) for r in geom['coordinates'])
    if geom.get('type') == 'MultiPolygon':
        return any(any(ring(r) for r in poly) for poly in geom['coordinates'])
    return False


def district_of(lat, lng, boundary):
    for f in (boundary or {}).get('features', []):
        if pip((lng, lat), f.get('geometry')):
            return (f.get('properties') or {}).get('name')
    return None


# ── Moran's I（k 近邻行标准化 + 置换检验） ────────────────
def morans_i(vals, pts, k=8, perms=199, seed=42):
    n = len(vals)
    if n < 10:
        return None
    mean = sum(vals) / n
    dev = [v - mean for v in vals]
    denom = sum(d * d for d in dev)
    if denom == 0:
        return None
    k = min(k, n - 1)
    W = []
    for i in range(n):
        ds = sorted((((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2), j)
                    for j in range(n) if j != i)
        W.append([j for _, j in ds[:k]])
    num = 0.0
    for i in range(n):
        if W[i]:
            num += sum(dev[i] * dev[j] for j in W[i]) / len(W[i])
    obs = num / denom   # 行标准化下 S0=n
    rng = random.Random(seed)
    cnt = 0
    for _ in range(perms):
        vals2 = vals[:]
        rng.shuffle(vals2)
        m2 = sum(vals2) / n
        d2 = [v - m2 for v in vals2]
        den2 = sum(d * d for d in d2)
        if den2 == 0:
            continue
        nm = 0.0
        for i in range(n):
            if W[i]:
                nm += sum(d2[i] * d2[j] for j in W[i]) / len(W[i])
        if nm / den2 >= obs:
            cnt += 1
    return {'value': round(obs, 4), 'p': round((cnt + 1) / (perms + 1), 3),
            'n': n, 'note': f'基于log注册资本，k近邻(k={k})行标准化权重，置换检验{perms}次'}


def main():
    a = parse_args()
    today = datetime.strptime(a.today, '%Y-%m-%d').date() if a.today else date.today()
    raw = load_json(a.input)
    if raw is None:
        print('无法读取输入数据', file=sys.stderr)
        sys.exit(1)
    recs = raw if isinstance(raw, list) else raw.get('enterprises', [])
    ind = load_json(a.industry)
    if isinstance(ind, dict) and 'chain' not in ind and ind.get('chainLinks'):
        ind['chain'] = [{'name': l, 'gbt': [], 'kw': []} for l in ind['chainLinks']]
    benchmark = load_json(a.benchmark)
    pois = load_json(a.poi) or []
    boundary = load_json(a.boundary)
    w1, w2, w3 = (x / 100.0 for x in (float(x) for x in a.weights.split(',')))
    ind_name = (ind or {}).get('name') or '目标产业'
    warns = []

    # 1. 预处理：状态过滤、年龄、资本、分类
    total_raw = len(recs)
    ents = []
    status_filtered = 0
    seen, dup = set(), 0
    _dedup = []
    for r in recs:
        key = (str(r.get('name') or '').strip(), r.get('lat'), r.get('lng'))
        if key[0] and key in seen:
            dup += 1
            continue
        seen.add(key)
        _dedup.append(r)
    if dup:
        warns.append(f'按名称+坐标去重剔除 {dup} 条重复记录。')
    recs = _dedup
    for r in recs:
        stt = str(r.get('status') or '在业')
        if stt and not any(x in stt for x in ACTIVE):
            status_filtered += 1
            continue
        e = dict(r)
        e['_age'] = to_age(r, today)
        e['_cap'] = parse_capital(r)
        e['_scale'] = scale_of(r)
        e['industry'], e['sub'] = classify(r, ind)
        try:
            e['_lat'] = float(r['lat']) if r.get('lat') not in (None, '') else None
            e['_lng'] = float(r['lng']) if r.get('lng') not in (None, '') else None
        except Exception:
            e['_lat'] = e['_lng'] = None
        ents.append(e)
    total = len(ents)
    if total == 0:
        print('无在业企业数据', file=sys.stderr)
        sys.exit(1)
    survival = total / max(total + status_filtered, 1)

    with_age = [e for e in ents if e['_age'] is not None]
    age_missing = 1 - len(with_age) / total
    if age_missing > 0.2:
        warns.append(f'成立日期/年龄字段缺失率 {age_missing*100:.0f}%，活力与象限结论稳健性受限。')
    coord_valid = [e for e in ents if e.get('_lat') is not None and e.get('_lng') is not None]
    if total - len(coord_valid) > total * 0.1:
        warns.append(f'{total - len(coord_valid)} 家企业缺少坐标，地图与聚类模块仅含 {len(coord_valid)} 家。')

    # 2. 地图/聚类抽样（超过上限时随机抽样，保持总量口径不变）
    sampled, sample_rate = False, 1.0
    cluster_src = coord_valid
    if len(coord_valid) > a.max_map_points:
        random.Random(42).shuffle(cluster_src)
        cluster_src = cluster_src[:a.max_map_points]
        sample_rate = len(cluster_src) / len(coord_valid)
        sampled = True
        warns.append(f'企业数 {len(coord_valid)} 超过 {a.max_map_points}，地图/聚类按 '
                     f'{sample_rate*100:.0f}% 抽样（活力/LQ/韧性/产业链仍为全量口径）。')

    # 3. DBSCAN（投影 + 网格加速）与集群画像
    phi0 = (sum(e['_lat'] for e in cluster_src) / len(cluster_src)) if cluster_src else 0.0
    pts = [project(e['_lat'], e['_lng'], phi0) for e in cluster_src]
    labels = grid_dbscan(pts, a.eps, a.minpts)
    groups = {}
    for i, lb in enumerate(labels):
        if lb >= 0:
            groups.setdefault(lb, []).append(cluster_src[i])
    clusters = []
    for members in groups.values():
        n = len(members)
        caps = [m['_cap'] for m in members if m['_cap'] is not None]
        avg_cap = sum(caps) / len(caps) if caps else 0.0
        aged = [m for m in members if m['_age'] is not None]
        new_r = (sum(1 for m in aged if m['_age'] <= 3) / len(aged)) if aged else 0.0
        new_caps = [m['_cap'] for m in aged if m['_age'] <= 3 and m['_cap'] is not None]
        new_cap = sum(new_caps) / len(new_caps) if new_caps else 0.0
        large_r = sum(1 for m in members if m['_scale'] in ('大型', '中型')) / n
        subs = {}
        for m in members:
            subs[m['sub']] = subs.get(m['sub'], 0) + 1
        dom_sub = max(subs.items(), key=lambda kv: kv[1])[0] if subs else '其他'
        clusters.append({'_members': members, 'n': n, 'avgCap': avg_cap, 'newR': new_r,
                         'newCap': new_cap, 'largeR': large_r, 'domInd': ind_name,
                         'domSub': dom_sub})
    clusters.sort(key=lambda c: -c['n'])
    nmax = max((c['n'] for c in clusters), default=0)
    capmax = max((c['avgCap'] for c in clusters), default=0.0) or 0.0
    for idx, c in enumerate(clusters):
        c['id'] = f'C{idx + 1}'
        c['clusterColor'] = CLUSTER_COLORS[idx % len(CLUSTER_COLORS)]
        size_n = c['n'] / nmax if nmax else 0
        cap_n = min(1.0, c['avgCap'] / capmax) if capmax else 0
        c['strength'] = round(min(1.0, 0.40 * size_n + 0.35 * cap_n + 0.25 * c['largeR']), 4)
        dens = math.log1p(c['n']) / math.log1p(nmax) if nmax else 0
        new_cap_n = min(1.0, c['newCap'] / capmax) if capmax else 0
        c['potential'] = round(min(1.0, 0.50 * c['newR'] + 0.30 * new_cap_n + 0.20 * dens), 4)
        lat = sum(m['_lat'] for m in c['_members']) / c['n']
        lng = sum(m['_lng'] for m in c['_members']) / c['n']
        c['centerLat'], c['centerLng'] = round(lat, 6), round(lng, 6)
        c['district'] = district_of(lat, lng, boundary)
        c['label'] = f"{c['district'] or ''}·{c['domSub']}集群".lstrip('·')
        for m in c['_members']:
            m['cluster'] = c['id']
            m['clusterColor'] = c['clusterColor']
    for e in ents:
        e.setdefault('cluster', None)
        e.setdefault('clusterColor', None)
    # 象限切割
    if clusters:
        xs = sorted(c['strength'] for c in clusters)
        ys = sorted(c['potential'] for c in clusters)
        k = len(clusters)
        mode = 'median' if k >= 4 else 'mean'
        med = lambda s: s[k // 2] if k % 2 else (s[k // 2 - 1] + s[k // 2]) / 2
        cut_x = med(xs) if mode == 'median' else sum(xs) / k
        cut_y = med(ys) if mode == 'median' else sum(ys) / k
        for c in clusters:
            c['quadrant'] = ('★明星' if c['strength'] > cut_x and c['potential'] > cut_y else
                             '◆成熟' if c['strength'] > cut_x else
                             '▲潜力' if c['potential'] > cut_y else '▽弱势')
        if k < 4:
            warns.append(f'集群数 {k} < 4，象限采用均值切割，结论稳健性有限。')
        quadrant_cut = {'mode': mode, 'x': round(cut_x, 4), 'y': round(cut_y, 4), 'nClusters': k}
    else:
        quadrant_cut = {'mode': 'none', 'x': 0, 'y': 0, 'nClusters': 0}
        warns.append('给定 DBSCAN 参数下未形成集群，可调小 eps 或 minPts 重试。')

    # 4. Moran's I（log 资本；大样本 500m 网格聚合）
    mi = None
    mv = [(math.log1p(e['_cap'] or 0), project(e['_lat'], e['_lng'], phi0))
          for e in coord_valid if e.get('_cap') is not None]
    if len(mv) > 1500:
        cell = 500.0
        agg = {}
        for v, (x, y) in mv:
            agg.setdefault((int(x // cell), int(y // cell)), []).append(v)
        mv = [(sum(g) / len(g), (key[0] * cell, key[1] * cell)) for key, g in agg.items()]
    if len(mv) >= 10:
        mi = morans_i([v for v, _ in mv], [p for _, p in mv])

    # 5. 活力（全量口径，log 归一化修正）
    groups_v = {}
    for e in ents:
        groups_v.setdefault(e['sub'], []).append(e)
    logs = [math.log1p(e['_cap']) for e in ents if e.get('_cap') is not None]
    lmin, lmax = (min(logs), max(logs)) if logs else (0, 0)
    items = []
    for cat, g in groups_v.items():
        if cat == '其他' and len(g) < 5:
            continue
        n = len(g)
        aged = [e for e in g if e['_age'] is not None]
        new_r = (sum(1 for e in aged if e['_age'] <= 3) / len(aged)) if aged else 0.0
        caps = [e['_cap'] for e in g if e['_cap'] is not None]
        avg_cap = sum(caps) / len(caps) if caps else 0
        cap_n = (math.log1p(avg_cap) - lmin) / (lmax - lmin) if lmax > lmin else 0
        share = (sum(1 for e in g if e.get('cluster')) / n) if clusters else 0.5
        avg_age = sum(e['_age'] for e in aged) / len(aged) if aged else 0
        vit = min(1.0, w1 * new_r + w2 * max(0.0, cap_n) + w3 * share)
        trend = '快增长' if new_r > .25 else ('稳增长' if new_r > .12 else ('成熟期' if avg_age > 10 else '缓慢'))
        items.append({'cat': cat, 'n': n, 'newR': round(new_r, 4), 'avgCap': round(avg_cap, 1),
                      'capNorm': round(max(0.0, cap_n), 4), 'clusterShare': round(share, 4),
                      'vitality': round(vit, 4), 'avgAge': round(avg_age, 1), 'trend': trend,
                      'small': n < 30})
    items.sort(key=lambda x: -x['vitality'])
    life = [0, 0, 0, 0]
    for e in with_age:
        ag = e['_age']
        life[0 if ag <= 3 else 1 if ag <= 8 else 2 if ag <= 15 else 3] += 1
    vitality = {'items': items, 'lifecycle': life,
                'weights': [round(w1 * 100), round(w2 * 100), round(w3 * 100)],
                'survivalRate': round(survival, 4)}
    if not clusters:
        vitality['note'] = '未形成集群，集群密度维度按 0.5 计。'

    # 6. LQ（三档降级：user / builtin（仅多产业数据集） / none）
    def cat_counts(key):
        d = {}
        for e in ents:
            d[e[key]] = d.get(e[key], 0) + 1
        return d
    multi_ind = len(set(e['industry'] for e in ents)) > 1
    if benchmark:
        lq_mode, bm_name, bm, cats = 'user', '用户提供的基准数据', benchmark, cat_counts('sub')
    elif multi_ind and any(k in BUILTIN_BENCHMARK for k in set(e['industry'] for e in ents)):
        lq_mode, bm_name, bm, cats = 'builtin', '内置参考基准（近似）', BUILTIN_BENCHMARK, cat_counts('industry')
    else:
        lq_mode, bm_name, bm, cats = 'none', '—', {}, cat_counts('sub')
    lq_items = []
    for kk, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        ls = n / total
        row = {'k': kk, 'n': n, 'localShare': round(ls, 4), 'small': n < 30}
        bs = bm.get(kk)
        if lq_mode != 'none' and bs is not None:
            lq = ls / bs if bs > 0 else 0
            row.update({'bmShare': bs, 'lq': round(lq, 3),
                        'level': '显著优势' if lq > 1.25 else ('均衡' if lq > 0.75 else '相对薄弱')})
        lq_items.append(row)
    lq = {'mode': lq_mode, 'benchmarkName': bm_name, 'items': lq_items}
    if lq_mode == 'builtin' and any(i.get('lq', 0) > 10 for i in lq_items):
        warns.append('内置基准下 LQ > 10：输入很可能是目标产业企业清单而非全行业名录，LQ 不具解释力，建议不勾选 LQ 模块或提供全行业数据/用户基准。')

    # 7. 产业链（GBT 优先 + 关键词命中，得分修正版）
    chain = {'links': [], 'missing': [], 'strong': []}
    if ind and ind.get('chain'):
        members = {l['name']: [] for l in ind['chain']}
        for e in ents:
            gbt = str(e.get('gbt_code') or '').strip()
            scope = str(e.get('scope') or e.get('business_scope') or '').lower()
            for l in ind['chain']:
                kws = l.get('kw') or []
                hit = gbt_prefix_match(gbt, l.get('gbt') or []) or \
                      (bool(kws) and any(kx.lower() in scope for kx in kws))
                if hit:
                    members[l['name']].append(e)
        nmax_l = max((len(v) for v in members.values()), default=0)
        cmax = max((sum(m['_cap'] or 0 for m in v) for v in members.values()), default=0) or 0
        for l in ind['chain']:
            v = members[l['name']]
            n = len(v)
            cap = sum(m['_cap'] or 0 for m in v)
            large = sum(1 for m in v if m['_scale'] in ('大型', '中型'))
            cnt_n = math.log1p(n) / math.log1p(nmax_l) if nmax_l else 0
            cap_n = cap / cmax if cmax else 0
            score = min(1.0, 0.45 * cnt_n + 0.35 * cap_n + 0.20 * (1 if large else 0))
            chain['links'].append({'name': l['name'], 'n': n, 'capital': round(cap, 1),
                                   'large': large, 'score': round(score, 4)})
        chain['missing'] = [l['name'] for l in chain['links'] if l['n'] == 0]
        chain['strong'] = [l['name'] for l in chain['links'] if l['score'] > 0.4]
        chain['note'] = '启发式匹配（GBT 代码优先、经营范围关键词补充），个体企业可能跨环节或漏配。'
    else:
        chain['note'] = '未提供产业定义（产业链环节），本模块未计算。'

    # 8. 网格（一遍分桶；POI 维度可选）
    grid = {'sizeM': int(a.grid), 'items': [], 'poiBased': bool(pois)}
    if coord_valid:
        dlat = a.grid / 110574.0
        dlng = a.grid / (111320.0 * math.cos(math.radians(phi0)) or 1.0)
        buckets = {}
        for e in coord_valid:
            buckets.setdefault((int(e['_lat'] // dlat), int(e['_lng'] // dlng)), []).append(e)
        poi_pts = []
        for p in pois:
            try:
                poi_pts.append((p.get('type', ''), p.get('name', ''), float(p['lat']), float(p['lng'])))
            except Exception:
                pass
        for (bi, bj), members in buckets.items():
            clat, clng = bi * dlat + dlat / 2, bj * dlng + dlng / 2
            n = len(members)
            ent_s = min(1.0, n / 20.0)
            metro_s = uni_s = com_s = 0.0
            if poi_pts:
                px, py = project(clat, clng, phi0)

                def near(types, radius):
                    c = 0
                    for t, _, la, ln in poi_pts:
                        if t in types:
                            qx, qy = project(la, ln, phi0)
                            if math.hypot(px - qx, py - qy) <= radius:
                                c += 1
                    return c
                metro_s = min(1.0, near(('地铁站', '地铁'), 2000) / 2)
                uni_s = min(1.0, near(('高校', '大学'), 3000) / 3)
                com_s = min(1.0, near(('商圈', '医院'), 2000) / 2)
            score = 0.30 * metro_s + 0.30 * uni_s + 0.25 * ent_s + 0.15 * com_s
            grade = '优先选址' if score > 0.6 else ('适宜选址' if score > 0.35 else '一般')
            grid['items'].append({'lat': round(clat, 5), 'lng': round(clng, 5),
                                  'score': round(score, 4), 'entScore': round(ent_s, 4),
                                  'uniScore': round(uni_s, 4), 'metroScore': round(metro_s, 4),
                                  'comScore': round(com_s, 4), 'grade': grade, 'n': n})
        grid['items'].sort(key=lambda g: -g['score'])
        grid['items'] = grid['items'][:10]
        grid['note'] = 'POI 维度' + ('按提供的 POI 数据计算。' if pois else
                                     '缺失（无 POI 数据），仅产业集聚维度有效，得分整体偏低属正常。')

    # 9. 韧性（双口径 HHI + CR4 + 均匀参照）
    shares_n = [n / total for n in cats.values()]
    bycat_cap = {}
    for e in ents:
        if e.get('_cap') is not None:
            bycat_cap[e['sub']] = bycat_cap.get(e['sub'], 0) + (e['_cap'] or 0)
    caps_all = [e['_cap'] for e in ents if e.get('_cap') is not None]
    cap_total = sum(caps_all) or 1.0
    shares_c = [bycat_cap.get(k, 0) / cap_total for k in cats]
    hhi_count = sum(s * s for s in shares_n)
    hhi_cap = sum(s * s for s in shares_c)
    top4 = sorted(caps_all, reverse=True)[:4]
    cr4 = (sum(top4) / cap_total) if caps_all else 0.0
    kcat = len(cats)
    uniform = 1 / kcat if kcat else 0.0
    dom, dom_n = max(cats.items(), key=lambda kv: kv[1]) if cats else ('—', 0)
    scale_cnt = {'大型': 0, '中型': 0, '小型': 0, '微型': 0, '未知': 0}
    for e in ents:
        scale_cnt[e['_scale'] or '未知'] += 1
    scale_missing = scale_cnt['未知'] / total
    ratio = (hhi_cap / uniform) if uniform else 0
    resilience = {'hhiCount': round(hhi_count, 4), 'hhiCapital': round(hhi_cap, 4),
                  'cr4': round(cr4, 4), 'catCount': kcat, 'uniformRef': round(uniform, 4),
                  'dom': dom, 'domShare': f'{dom_n / total * 100:.1f}',
                  'scaleCnt': scale_cnt, 'scaleMissing': round(scale_missing, 4),
                  'conclusion': f'资本口径集中度约为均匀参照线（1/{kcat}={uniform:.3f}）的{ratio:.1f}倍，'
                                f'{dom} 占比 {dom_n / total * 100:.1f}%，CR4={cr4 * 100:.0f}%。'
                                f'（双口径+参照线解读，替代原版 0.10/0.18 绝对阈值）'}
    if 0 < len(caps_all) < 30:
        warns.append('资本字段可用样本 < 30，资本口径集中度仅供参考。')

    # 10. 汇总输出
    analysis = {
        'clusters': [{k: c[k] for k in ('id', 'label', 'n', 'centerLat', 'centerLng', 'domInd',
                                        'domSub', 'strength', 'potential', 'quadrant', 'avgCap',
                                        'newR', 'largeR', 'district')} for c in clusters],
        'quadrantCut': quadrant_cut, 'moransI': mi,
        'vitality': vitality, 'lq': lq, 'chain': chain, 'grid': grid, 'resilience': resilience,
        'data': {'total': total, 'mapped': total, 'coordValid': len(coord_valid),
                 'sampled': sampled, 'sampleRate': round(sample_rate, 4),
                 'ageMissing': round(age_missing, 4), 'statusFiltered': status_filtered},
        'warnings': warns}
    out_ents = [{k: e.get(k) for k in ('name', 'lat', 'lng', 'capital', 'age', 'est_date', 'scale',
                                        'scope', 'gbt_code', 'status', 'industry', 'sub',
                                        'cluster', 'clusterColor')} for e in ents]
    json.dump({'analysis': analysis, 'enterprises': out_ents},
              open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'OK -> {a.out}')
    print(f'  企业: {total} 在业（剔除非在业 {status_filtered}）| 坐标有效 {len(coord_valid)}')
    print(f'  集群: {len(clusters)} | Moran\'s I: {mi["value"] if mi else "n/a"}'
          f' (p={mi["p"] if mi else "n/a"})')
    print(f'  LQ mode: {lq_mode} | 网格: {len(grid["items"])} | warnings: {len(warns)}')


if __name__ == '__main__':
    main()
