#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国标产业选择器：基于 GB/T 4754-2017 分类树（assets/gbt_lib.json），
支持门类→大类→中类→小类逐级浏览，停在任意层级作为产业定义，支持同级多选。

用法：
  查找节点（按名称关键词）   python3 gbt_select.py --find 航空
  查看某节点子树             python3 gbt_select.py --show 374 [--full]
  生成产业定义               python3 gbt_select.py --codes 374 --out industry_def.json
  多选（同级或跨级）         python3 gbt_select.py --codes "37,39" --out industry_def.json

输出 industry_def.json 结构与 analyze.py --industry 兼容：
  {name, type:"gbt", path, gbt:[...], kw:[], subs:{...}, catchAllSub, chain:[]}
说明：gbt 前缀匹配天然向下兼容——选 37（大类）可匹配企业 3741（小类）。
"""
import argparse, json, os, sys

LEVEL_NAME = {1: '门类', 2: '大类', 3: '中类', 4: '小类'}


def load_lib():
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, '..', 'assets', 'gbt_lib.json')
    if not os.path.exists(p):
        print(f'未找到产业库: {p}', file=sys.stderr)
        sys.exit(1)
    lib = json.load(open(p, encoding='utf-8'))
    return lib['tree']


def level_of(code):
    return 1 if not code.isdigit() else min(len(code), 4)


def walk(nodes, depth=1):
    for n in nodes:
        yield n, depth
        yield from walk(n.get('k', []), depth + 1)


def find_node(tree, code):
    for n, d in walk(tree):
        if n['c'] == code:
            return n, d
    return None, None


def ancestors(tree, code):
    trail = []

    def rec(nodes):
        for n in nodes:
            if n['c'] == code:
                trail.append(n)
                return True
            if rec(n.get('k', [])):
                trail.append(n)
                return True
        return False
    rec(tree)
    return list(reversed(trail))


def count_desc(n):
    total = 0
    for ch in n.get('k', []):
        total += 1 + count_desc(ch)
    return total


def show_subtree(tree, code, full=False, limit=120):
    node, d = find_node(tree, code)
    if not node:
        print(f'编码 {code} 不存在，可用 --find 关键词 查找', file=sys.stderr)
        sys.exit(1)
    trail = ancestors(tree, code)
    print(' > '.join(f"{n['c']}·{n['n']}" for n in trail))
    kids = node.get('k', [])
    if not kids:
        print(f'（{LEVEL_NAME[d]}，无下级细分，可直接选定）')
        return
    total = count_desc(node)
    lines = []

    def rec(nodes, ind):
        for n in nodes:
            k = n.get('k', [])
            mark = '├─' if k else '· '
            lines.append(f"{'  '*ind}{mark}{n['c']}  {n['n']}" + (f"（{len(k)} 下级）" if k else ''))
            if k and (full or len(lines) < limit):
                rec(k, ind + 1)
    rec(kids, 0)
    shown = lines if full else lines[:limit]
    print('\n'.join(shown))
    if not full and len(lines) > limit:
        print(f'……（共 {len(lines)} 行已截断，加 --full 查看全部；下级共 {total} 个节点）')


def build_def(tree, codes):
    picked = []      # [(node, level, trail)]
    for c in codes:
        n, d = find_node(tree, c)
        if not n:
            print(f'编码 {c} 不存在，跳过', file=sys.stderr)
            sys.exit(1)
        picked.append((n, d, ancestors(tree, c)))
    # 去掉被其他已选节点覆盖的后代（保留较浅层级）
    sel = [n['c'] for n, _, _ in picked]
    kept = [(n, d, tr) for n, d, tr in picked
            if not any(o != n['c'] and n['c'].startswith(o) for o in sel)]
    dropped = [n['c'] for n, d, tr in picked if (n, d, tr) not in kept]
    if dropped:
        print(f'[提示] {", ".join(dropped)} 已被更上层的选择覆盖，忽略', file=sys.stderr)

    gbt = []
    for n, d, _ in kept:
        if d == 1:   # 门类 → 展开为其全部大类代码
            gbt += [ch['c'] for ch in n.get('k', [])]
        else:
            gbt.append(n['c'])
    subs, used_names = {}, set()
    for n, d, _ in kept:
        children = n.get('k', [])
        if children:
            for ch in children:
                nm = ch['n']
                if nm in used_names:
                    nm = f"{nm}（{ch['c']}）"
                used_names.add(nm)
                subs[nm] = {'gbt': [ch['c']], 'kw': []}
        else:        # 小类叶节点：以自身作为唯一细分
            nm = n['n'] if n['n'] not in used_names else f"{n['n']}（{n['c']}）"
            used_names.add(nm)
            subs[nm] = {'gbt': [n['c']], 'kw': []}
    if len(kept) == 1:
        n, d, tr = kept[0]
        name = n['n']
        path = ' > '.join(x['n'] for x in tr)
    else:
        name = '/'.join(n['n'] for n, d, _ in kept)
        path = '多选组合：' + '、'.join(f"{n['c']}·{n['n']}" for n, d, _ in kept)
    return {
        'name': name, 'type': 'gbt', 'path': path,
        'gbt': gbt, 'kw': [],
        'subs': subs, 'catchAllSub': None,
        'chain': [],
        'chainNote': '国标选择不自带产业链环节；如需产业链诊断，请由 AI 结合所选行业补充 chain（[{name,gbt,kw}]）后重跑分析。',
        'subNote': ('subs 为所选节点的下一级，企业按 GBT 前缀匹配归入。' if any(n.get('k') for n, _, _ in kept)
                    else '所选为小类叶节点，无下级细分，subs 仅含其自身。')
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--codes', help='一个或多个国标编码，逗号分隔（如 374 或 "37,39"）')
    p.add_argument('--find', help='按名称关键词查找节点')
    p.add_argument('--show', help='查看某编码的子树')
    p.add_argument('--full', action='store_true', help='--show 时不截断')
    p.add_argument('--out', help='输出 industry_def.json 路径')
    a = p.parse_args()
    tree = load_lib()
    if a.find:
        kw = a.find.strip()
        hits = [(n, d) for n, d in walk(tree) if kw in n['n']]
        if not hits:
            print(f'未找到名称含「{kw}」的节点', file=sys.stderr)
            sys.exit(1)
        for n, d in hits[:30]:
            print(f"{LEVEL_NAME[d]} {n['c']}  {n['n']}（下级 {len(n.get('k', []))}）")
        if len(hits) > 30:
            print(f'……共 {len(hits)} 条，请细化关键词')
        return
    if a.show:
        show_subtree(tree, a.show.strip(), a.full)
        return
    if not a.codes:
        print('用法：--find 关键词 / --show 编码 / --codes 编码[,编码2...] [--out 文件]', file=sys.stderr)
        sys.exit(1)
    codes = [c.strip() for c in a.codes.split(',') if c.strip()]
    d = build_def(tree, codes)
    text = json.dumps(d, ensure_ascii=False, indent=1)
    if a.out:
        json.dump(d, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'OK -> {a.out}')
    print(text if not a.out else f'  name: {d["name"]} | gbt: {len(d["gbt"])} 个代码 | subs: {len(d["subs"])} 个细分')


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        pass
