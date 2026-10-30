#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告生成器：把 analyze.py 的输出注入 template.html，产出独立 HTML 报告。
用法：
  python3 build_report.py --template template.html \
      --analysis analysis.json --meta meta.json [--out report.html]
meta.json 结构见 references/report-spec.md（title/region/industry/dataSource/ai/demo/modules…）。
注入后自动校验：无残留占位标记、JS 语法（如环境有 node）。
"""
import argparse, json, re, subprocess, sys, shutil

def j(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--template', required=True)
    p.add_argument('--analysis', required=True, help='analyze.py 输出 {analysis, enterprises}')
    p.add_argument('--meta', required=True, help='报告元信息 JSON')
    p.add_argument('--out', default='report.html')
    a = p.parse_args()

    tpl = open(a.template, encoding='utf-8').read()
    if '__INJECTION_START__' not in tpl:
        print('模板缺少注入标记，可能已被生成过', file=sys.stderr)
        sys.exit(1)
    res = json.load(open(a.analysis, encoding='utf-8'))
    meta_in = json.load(open(a.meta, encoding='utf-8'))
    analysis, enterprises = res['analysis'], res['enterprises']

    modules = meta_in.get('modules') or ['map', 'cluster', 'vitality', 'chain', 'ai']
    mods = {m: (m in modules) for m in ('map', 'cluster', 'vitality', 'lq', 'grid', 'chain', 'resilience', 'ai')}
    if not analysis.get('lq', {}).get('items'):
        mods['lq'] = False
    if not analysis.get('grid', {}).get('items'):
        mods['grid'] = False
    if not analysis.get('chain', {}).get('links'):
        mods['chain'] = False
    if analysis.get('resilience', {}).get('hhiCapital') is None:
        mods['resilience'] = False

    META = {
        'title': meta_in.get('title', '产业智能分析报告'),
        'region': meta_in.get('region', {}),
        'industry': meta_in.get('industry', {}),
        'generatedAt': meta_in.get('generatedAt') or __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
        'dataSource': meta_in.get('dataSource', {'type': 'manual', 'note': '—'}),
        'demo': bool(meta_in.get('demo', False)),
        'modules': [m for m, on in mods.items() if on],
        'ai': meta_in.get('ai'),
        'analysis': analysis,
        'warnings': analysis.get('warnings', []),
    }
    CONFIG = {
        'coordSys': meta_in.get('coordSys', 'gcj02'),
        'baseMap': meta_in.get('baseMap', 'amap-dark'),
        'adcode': (meta_in.get('region') or {}).get('adcode', ''),
        'showBoundary': True,
        'modules': mods,
        'dbscan': {'eps': meta_in.get('eps', 800), 'minPts': meta_in.get('minPts', 5)},
        'weights': analysis.get('vitality', {}).get('weights', [40, 35, 25]),
        'gridSizeM': analysis.get('grid', {}).get('sizeM', 1000),
    }
    REPORT_DATA = {
        'enterprises': enterprises,
        'pois': meta_in.get('pois', []),
        'benchmark': meta_in.get('benchmark'),
        'industryLib': meta_in.get('industryLib'),
    }

    block = ('const META = ' + j(META) + ';\n'
             'const CONFIG = ' + j(CONFIG) + ';\n'
             'const REPORT_DATA = ' + j(REPORT_DATA) + ';\n')
    out_html = re.sub(r'// __INJECTION_START__[\s\S]*?// __INJECTION_END__', lambda _: block, tpl, count=1)

    # 校验
    for tok in ('__INJECTION_START__', '__INJECTION_END__', '__META__', '__CONFIG__', '__DATA__'):
        if tok in out_html:
            print(f'校验失败：残留占位标记 {tok}', file=sys.stderr)
            sys.exit(1)
    open(a.out, 'w', encoding='utf-8').write(out_html)

    scripts = re.findall(r'<script>([\s\S]*?)</script>', out_html)
    open('_check_tmp.js', 'w', encoding='utf-8').write('\n'.join(scripts))
    try:
        subprocess.run(['node', '--check', '_check_tmp.js'], check=True, capture_output=True)
        print('JS 语法校验通过')
    except FileNotFoundError:
        print('（未安装 node，跳过 JS 语法校验）')
    except subprocess.CalledProcessError as e:
        print('JS 语法校验失败：\n' + e.stderr.decode(errors='ignore'), file=sys.stderr)
        sys.exit(1)
    finally:
        import os
        if os.path.exists('_check_tmp.js'):
            os.remove('_check_tmp.js')

    kb = len(out_html.encode('utf-8')) / 1024
    print(f'OK -> {a.out}（{kb:.0f} KB，企业 {len(enterprises)} 家，模块：{META["modules"]}）')

if __name__ == '__main__':
    main()
