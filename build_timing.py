#!/usr/bin/env python3
"""把每轮的耗时汇总成 timing.js，记录页的「生成耗时」区块只读这份文件。加了新测试组后重跑一次：python3 build_timing.py

优先用中转存下的 rounds/<rid>.json（环节最全）；没有的组用卡片上的数字；两边都没有的环节留空，不估算。
口径（秒）：
  等规划 = director_at − at（点选项预先写好时接近 0）   规划耗时 = director_sec（大模型实际用时，预写时在后台）
  提交 = accepted_at − director_at   排队 = proc_at − accepted_at   生成 = gen_ready_at − proc_at
  就绪 = ready_at − director_at（从规划写好算，和卡片上一致）
  开播 = view.play_at − at（从发送算，只有真人在播放页看的轮次才有）   接缝 = view.stall
  配音 = tts_at − director_at
"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
# 结论区用的两组：目前的主力版本（ultrafast，5+15，真人测试）。换了主力版本就改这里
CURRENT = [('mii-open', '你的人工测试 · 第 5 次 · ultrafast · 5+15', '咪 · 开放式'),
           ('magnus-third', '你的人工测试 · 第 1 次 · ultrafast · 5+15', 'Magnus · 第三视角')]
FAMILY = {'mii': 'mii', 'mii-open': 'mii', 'magnus-preset': 'magnus', 'magnus-open': 'magnus', 'magnus-third': 'magnus'}
STAGES = ['wait', 'llm', 's1_submit', 's1_queue', 's1_gen', 's1_ready', 's2_submit', 's2_queue', 's2_gen', 's2_ready',
          'whole', 'tts', 'play', 'stall']
CARD_FIELD = {'导演层': 'llm', '第 1 段就绪': 's1_ready', '第 2 段就绪': 's2_ready', '整段就绪': 'whole', '发送到开播': 'play',
              '开播': 'play', '接缝停顿': 'stall', '配音': 'tts'}  # 早期卡片的「开播」也是从发送算起


def index_rounds():
    idx = {}
    for pat in ('output/*/rounds', 'projects/*/rounds', 'projects/*/*/rounds', 'projects/*/*/*/rounds'):
        for d in glob.glob(os.path.join(ROOT, pat)):
            for f in glob.glob(d + '/*.json'): idx.setdefault(os.path.basename(f)[:-5], []).append(f)
    return idx


def find_round(idx, rid, family):
    fs = idx.get(rid) or []
    fs = [f for f in fs if family in f.lower()] or fs
    if not fs: return None
    try:
        with open(fs[0], encoding='utf-8') as fp: return json.load(fp)
    except (OSError, ValueError): return None


def sec(a, b):
    """b − a，毫秒转秒；缺时刻或时间倒挂（重生成覆盖了时刻）都算没有。"""
    if not a or not b or b < a: return None
    return (b - a) / 1000


def from_round(r):
    out = {}
    at, dat = r.get('at'), r.get('director_at')
    out['wait'] = sec(at, dat)
    if out['wait'] is None and r.get('director_wait') is not None: out['wait'] = r['director_wait']
    if r.get('director_sec') is not None: out['llm'] = r['director_sec']
    segs = r.get('segs') or []
    if len(segs) == 1: names = ['whole']
    else: names = ['s1', 's2']
    for name, s in zip(names, segs):
        ready = sec(dat, s.get('ready_at'))
        if name == 'whole': out['whole'] = ready; continue
        out[name + '_ready'] = ready
        out[name + '_submit'] = sec(dat, s.get('accepted_at'))
        out[name + '_queue'] = sec(s.get('accepted_at'), s.get('proc_at'))
        out[name + '_gen'] = sec(s.get('proc_at'), s.get('gen_ready_at'))
    v = r.get('view') or {}
    out['play'] = sec(at, v.get('play_at'))
    if v.get('stall') is not None: out['stall'] = v['stall']
    if not r.get('tts_prefetched') or r.get('tts_at'): out['tts'] = sec(dat, r.get('tts_at'))
    return {k: v for k, v in out.items() if v is not None}


def from_card(c):
    out = {}
    for k, v in c.get('stats') or []:
        f = CARD_FIELD.get(k)
        m = re.match(r'\s*([\d.]+)\s*s', str(v))
        if f and m: out[f] = float(m.group(1))
    if '预先写好' in str(dict(map(tuple, c.get('stats') or [])).get('导演层', '')): out['wait'] = 0.0
    return out


def kind_of(c, r):
    k = (r or {}).get('kind')
    if k in ('option', 'free'): return k
    label = c.get('label') or ''
    if '点选项' in label: return 'option'
    if '自由输入' in label: return 'free'
    return 'other'


def agg(rows):
    """每个环节的 最快 / 平均 / 样本数 / 最慢。"""
    out = {}
    for st in STAGES:
        vals = [x[st] for x in rows if st in x]
        if vals: out[st] = [round(min(vals), 1), round(sum(vals) / len(vals), 1), len(vals), round(max(vals), 1)]
    return out


def main():
    src = open(os.path.join(HERE, 'data.js'), encoding='utf-8').read()
    log = json.loads(src[src.index('{'):src.rindex('}') + 1])
    idx = index_rounds()
    groups = []
    for d in log['dramas']:
        for g in d.get('groups', []):
            rows, models, modes, found = [], set(), set(), 0
            for c in g.get('cards', []):
                m = re.search(r'([A-Z]\d{6,}[0-9a-f]*)\.mp4', c.get('video') or '')
                r = find_round(idx, m.group(1), FAMILY[d['id']]) if m else None
                x = from_card(c)  # 卡片数字打底，round 记录有的环节覆盖它
                if r: found += 1; x.update(from_round(r))
                x['kind'] = kind_of(c, r)
                if r and r.get('model'): models.add(r['model'].replace('vivix-w1-', '').replace('vivix-w1', 'normal'))
                mode = (r or {}).get('mode') or dict(map(tuple, c.get('stats') or [])).get('方案')
                if mode: modes.add(mode)
                rows.append(x)
            if not rows: continue
            kinds = {k: agg([x for x in rows if x['kind'] == k]) for k in ('option', 'free', 'other')}
            counts = {k: sum(x['kind'] == k for x in rows) for k in ('option', 'free', 'other')}
            groups.append({'drama': d['id'], 'tab': g.get('tab') or g.get('title') or '', 'when': g.get('when') or '',
                           'n': len(rows), 'rounds': found, 'models': sorted(models), 'modes': sorted(modes),
                           'all': agg(rows), 'kinds': {k: v for k, v in kinds.items() if counts[k]},
                           'counts': counts})
    out = {'updated': log.get('updated'), 'current': [{'drama': a, 'tab': b, 'name': c} for a, b, c in CURRENT], 'groups': groups}
    with open(os.path.join(HERE, 'timing.js'), 'w', encoding='utf-8') as fp:
        fp.write('window.TIMING=' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print('groups', len(groups), 'rounds found', sum(g['rounds'] for g in groups), 'cards', sum(g['n'] for g in groups))


if __name__ == '__main__':
    main()
