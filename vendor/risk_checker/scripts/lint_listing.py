#!/usr/bin/env python3
"""Listing lint (标题体检): scan a title or description for copyright names, slang, banned words.

Pure local table lookup over assets/risk-names.json, assets/slang-terms.json and
assets/banned-terms.json. Output is rewrite advice, never an approval guarantee.
Usage: python3 scripts/lint_listing.py --text "..." [--format markdown]
The same rules are mirrored in web/app.js.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disguise  # noqa: E402
import handling  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SLANG_PATH = ROOT / 'assets' / 'slang-terms.json'
BANNED_PATH = ROOT / 'assets' / 'banned-terms.json'
NOTICE = '标题体检给的是改写建议，不是过审保证；版权命中是核对线索，高危题材不提供替代词。'
LEVEL_NAMES = {'red': '红灯', 'amber': '黄灯', 'green': '绿灯'}
MODE_NAMES = {'exact_name': '名称精确命中', 'exact_company': '公司精确关联',
              'partial_candidate': '部分匹配候选', 'disguised_candidate': '疑似伪装命中'}
LIVENESS = {'killed': ('拦截词', '已被平台精准拦截'), 'watching': ('抽检', '平台抽检限流'), 'active': ('可搜', '当前可正常搜索')}
RISK = {'danger': '高危', 'warning': '警告', 'safe': '一般'}
_cache = {}


def norm(text):
    return unicodedata.normalize('NFKC', str(text or '')).lower()


def load_json(path):
    key = str(path)
    if key not in _cache:
        _cache[key] = json.loads(Path(path).read_text(encoding='utf-8'))
    return _cache[key]


def _variant_ok(v):
    return len(v) > 1 and not re.fullmatch(r'[a-z0-9]{1,3}', v)


def _slang_index(slang):
    key = ('slang', id(slang))
    if key not in _cache:
        items = []
        for t in slang['terms']:
            for v in t['variants']:
                lv = norm(v)
                if _variant_ok(lv):
                    items.append((lv, t, v))
        items.sort(key=lambda x: -len(x[0]))
        _cache[key] = items
    return _cache[key]


def _banned_index(banned):
    key = ('banned', id(banned))
    if key not in _cache:
        items = sorted(((norm(t['term']), t) for t in banned['terms']), key=lambda x: -len(x[0]))
        _cache[key] = items
    return _cache[key]


def _name_index(db):
    key = ('names', id(db))
    if key not in _cache:
        items = []
        for r in db['records']:
            if r.get('object_type') == '店铺' or r.get('category') == '店铺来源提醒':
                continue
            for term in dict.fromkeys([r['name'], *r.get('variants', [])]):
                lt = norm(term)
                if len(lt) >= 2:
                    items.append((lt, r, term, 'exact_name'))
            for company in r.get('companies', []):
                lc = norm(company)
                if len(lc) >= 3:
                    items.append((lc, r, company, 'exact_company'))
        items.sort(key=lambda x: -len(x[0]))
        _cache[key] = items
    return _cache[key]


def _find_all(hay, needle):
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return
        yield i
        start = i + 1


def _place(taken, start, end):
    if any(s < end and start < e for s, e in taken):
        return False
    taken.append((start, end))
    return True


def _record_view(r):
    return {k: r[k] for k in ('id', 'name', 'category', 'object_type', 'companies', 'labels', 'registrations') if k in r}


def copyright_hits(db, text):
    """Longest-first, non-overlapping copyright fragments: exact, company, disguised."""
    low = norm(text)
    taken, hits = [], []
    for lt, r, term, mode in _name_index(db):
        for i in _find_all(low, lt):
            if _place(taken, i, i + len(lt)):
                hits.append(dict(fragment=text[i:i + len(lt)], start=i, end=i + len(lt), kind='copyright',
                                 match_type=mode, matched_term=term, record=_record_view(r)))
                break
    index = disguise.index_records([r for r in db['records'] if r.get('object_type') != '店铺' and r.get('category') != '店铺来源提醒'])
    for start, end, r, term in disguise.scan_text(text, index):
        if _place(taken, start, end):
            hits.append(dict(fragment=text[start:end], start=start, end=end, kind='copyright',
                             match_type='disguised_candidate', matched_term=term, record=_record_view(r)))
    return hits, taken


def slang_hits(slang, text, taken):
    low = norm(text)
    hits = []
    for lv, t, v in _slang_index(slang):
        for i in _find_all(low, lv):
            if _place(taken, i, i + len(lv)):
                hits.append(dict(fragment=text[i:i + len(lv)], start=i, end=i + len(lv), kind='slang',
                                 variant=v, name=t['name'], category=t['category_name'], liveness=t.get('liveness', ''),
                                 risk=t.get('risk', ''), policy=t.get('policy', ''), explanation=t.get('explanation', ''),
                                 seller_intent=t.get('seller_intent', ''), successors=list(t.get('successors', [])),
                                 trend=t.get('trend', ''), source=t.get('source', '')))
                break
    return hits


def banned_hits(banned, text, taken):
    low = norm(text)
    hits = []
    for lt, t in _banned_index(banned):
        for i in _find_all(low, lt):
            if _place(taken, i, i + len(lt)):
                group = banned['groups'][t['group']]
                hits.append(dict(fragment=text[i:i + len(lt)], start=i, end=i + len(lt), kind='banned',
                                 term=t['term'], group=t['group'], group_name=group['name'], level=group['level'],
                                 advice=t.get('replace') or group['advice']))
                break
    return hits


def advise_copyright(hit, rules):
    r = hit['record']
    cat = rules['categories'].get(r.get('category')) or rules['categories']['_default']
    alt = '、'.join(cat.get('alternatives', [])[:2]) or '描述性说法'
    mode = hit['match_type']
    if mode == 'disguised_candidate':
        return f"疑似「{hit['matched_term']}」的伪装写法；改写躲不过稽查，去掉该名称，改为：{alt}。"
    if mode == 'exact_company':
        return f"命中公司「{hit['matched_term']}」；不要在标题提公司名，改为：{alt}。"
    return f"命中版权名「{hit['matched_term']}」（{'；'.join(r.get('labels', [])) or '待核对'}）；去掉名称，改为：{alt}。"


def advise_slang(hit):
    live = hit.get('liveness') or ''
    risk = hit.get('risk') or ''
    base = f"暗语「{hit['variant']}」＝{hit['name']}"
    if live == 'killed':
        base += '；已被平台拦截，写上会被下架'
    elif live == 'watching':
        base += '；平台抽检限流'
    if risk == 'danger' or hit.get('policy') == 'grey':
        return base + '；该题材高危，不提供替代词，建议不做。'
    if live in ('killed', 'watching') and hit.get('successors'):
        return base + '；可换：' + '、'.join(hit['successors'][:3]) + '。'
    if live == 'active':
        return base + '；当前可搜，但暗语本身说明该品类受审核关注，建议用正常描述。'
    return base + '；建议用正常描述替代。'


def score(hits):
    level = 'green'
    reasons = []
    for h in hits:
        if h['kind'] == 'copyright':
            if h['match_type'] in ('exact_name', 'exact_company'):
                level = 'red'
                reasons.append('版权精确命中')
            else:
                level = level if level == 'red' else 'amber'
                reasons.append('版权候选')
        elif h['kind'] == 'slang':
            if h.get('liveness') == 'killed' or h.get('risk') == 'danger' or h.get('policy') == 'grey':
                level = 'red'
                reasons.append('拦截词或高危暗语')
            else:
                level = level if level == 'red' else 'amber'
                reasons.append('暗语')
        elif h['kind'] == 'banned':
            if h['level'] == 'red':
                level = 'red'
                reasons.append('售假高频词')
            else:
                level = level if level == 'red' else 'amber'
                reasons.append(h['group_name'])
    return level, list(dict.fromkeys(reasons))


def lint(db, text, slang=None, banned=None, rules=None):
    text = unicodedata.normalize('NFKC', str(text or ''))[:2000]
    if not text.strip():
        raise ValueError('体检文本不能为空')
    slang = slang or load_json(SLANG_PATH)
    banned = banned or load_json(BANNED_PATH)
    rules = rules or handling.load_rules()
    c_hits, taken = copyright_hits(db, text)
    s_hits = slang_hits(slang, text, taken)
    b_hits = banned_hits(banned, text, taken)
    for h in c_hits:
        h['advice'] = advise_copyright(h, rules)
    for h in s_hits:
        h['advice'] = advise_slang(h)
    hits = sorted(c_hits + s_hits + b_hits, key=lambda h: h['start'])
    level, reasons = score(hits)
    worst = None
    for h in c_hits:
        rank = {'exact_name': 0, 'exact_company': 1, 'partial_candidate': 2, 'disguised_candidate': 3}[h['match_type']]
        if worst is None or rank < worst[0]:
            worst = (rank, h)
    if worst:
        status = 'matched' if worst[0] < 2 else 'candidates' if worst[0] == 2 else 'disguised'
        card = handling.advise(status, worst[1]['record'], rules)
    else:
        card = handling.advise('no_match', None, rules)
    return dict(text=text, level=level, level_name=LEVEL_NAMES[level], reasons=reasons,
                counts=dict(copyright=len(c_hits), slang=len(s_hits), banned=len(b_hits)),
                hits=hits, handling=card)


def compact(result):
    """Fragment verdicts and level only, for batch interfaces."""
    return dict(level=result['level'], level_name=result['level_name'], reasons=result['reasons'], counts=result['counts'],
                hits=[{k: h[k] for k in ('fragment', 'kind', 'advice') if k in h} |
                      ({'match_type': h['match_type'], 'matched_term': h['matched_term']} if h['kind'] == 'copyright' else
                       {'name': h['name'], 'liveness': h['liveness'], 'risk': h['risk']} if h['kind'] == 'slang' else
                       {'group': h['group']}) for h in result['hits']])


def _md(text):
    return str(text or '').replace('|', '&#124;').replace('\n', ' ')


def markdown(report):
    lines = ['# 标题体检', '', f"词库日期：{_md(report.get('updated_at', ''))}", '', NOTICE]
    for res in report['results']:
        lines += ['', f"## {_md(res['text'][:60])}", '',
                  f"**{res['level_name']}**" + (f"：{'、'.join(res['reasons'])}" if res['reasons'] else '：未发现版权、暗语或违禁词命中'), '']
        if res['hits']:
            lines += ['| 片段 | 判定 | 建议 |', '|---|---|---|']
            for h in res['hits']:
                if h['kind'] == 'copyright':
                    verdict = MODE_NAMES[h['match_type']] + '：' + _md(h['record']['name'])
                elif h['kind'] == 'slang':
                    tag = LIVENESS.get(h['liveness'], ('暗语', ''))[0]
                    verdict = f"{tag}（{RISK.get(h['risk'], '未分级')}）：{_md(h['name'])}"
                else:
                    verdict = h['group_name']
                lines.append(f"| {_md(h['fragment'])} | {verdict} | {_md(h['advice'])} |")
        lines += ['', '### 运营处置建议', '', handling.markdown(res['handling'])]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    import risk_check
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text', action='append', default=[])
    parser.add_argument('--input', help='UTF-8 text file, one title per line')
    parser.add_argument('--database', default=str(risk_check.DEFAULT_DB))
    parser.add_argument('--format', choices=['json', 'markdown'], default='json')
    parser.add_argument('--output')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    texts = list(args.text)
    if args.input:
        texts += [t for t in Path(args.input).read_text(encoding='utf-8-sig').splitlines() if t.strip()]
    if not texts:
        parser.error('请用 --text 或 --input 提供标题或详情文本')
    db = risk_check.load_db(args.database)
    report = dict(updated_at=db.get('updated_at', '未提供'), notice=NOTICE, results=[lint(db, t) for t in texts])
    out = markdown(report) if args.format == 'markdown' else json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    risk_check.emit(out, args.output, args.overwrite)
    return 0


if __name__ == '__main__':
    sys.exit(main())
