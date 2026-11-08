#!/usr/bin/env python3
"""Rule-based handling advice (运营处置建议). Pure table lookup; no network, no AI."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_RULES = Path(__file__).resolve().parents[1] / 'assets' / 'handling-rules.json'
_cache = {}


def load_rules(path=None):
    path = Path(path) if path else DEFAULT_RULES
    key = str(path)
    if key not in _cache:
        rules = json.loads(path.read_text(encoding='utf-8'))
        for name in ('levels', 'status', 'label_groups', 'categories', 'platform_notes'):
            if name not in rules:
                raise ValueError(f'处置规则缺少 {name}')
        _cache[key] = rules
    return _cache[key]


def _unique(values):
    seen, out = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def matched_groups(rules, labels):
    labels = [str(label or '') for label in labels]
    groups = []
    for group in rules['label_groups']:
        if any(token in label for token in group['match'] for label in labels):
            groups.append(group)
    return groups


def level_for(rules, score):
    for key in ('red', 'amber', 'green'):
        if score >= rules['levels'][key]['min']:
            return key
    return 'green'


def advise(status, record=None, rules=None):
    """Return a handling card for one query status and (optionally) its best matching record."""
    rules = rules or load_rules()
    status = status if status in rules['status'] else 'no_match'
    record = record or {}
    labels = record.get('labels') or []
    registrations = record.get('registrations') or []
    category = record.get('category') or ''
    groups = matched_groups(rules, labels) if status != 'no_match' else []
    score = rules['status'][status]['base']
    if groups:
        score += max(group['weight'] for group in groups)
    elif registrations and status != 'no_match':
        score += rules['registration_weight']
    score = max(0, min(100, score))
    level = level_for(rules, score)
    cat_rules = rules['categories'].get(category) or rules['categories']['_default']
    platform = rules['platform_notes'].get(cat_rules.get('platform'), {})
    card = dict(
        level=level,
        level_name=rules['levels'][level]['name'],
        score=score,
        headline=rules['status'][status]['headline'],
        title=rules['levels'][level]['title'],
        meaning=[g['meaning'] for g in groups],
        actions=[],
        docs=[],
        alternatives=[],
        platforms=dict(platform),
        checklist=list(rules.get('checklist', [])),
        notice=rules.get('notice', ''),
    )
    object_note = rules.get('object_types', {}).get(record.get('object_type') or '')
    if status != 'no_match' and object_note:
        card['meaning'].append(object_note)
    if not groups and registrations and status != 'no_match':
        card['meaning'].append(next((g['meaning'] for g in rules['label_groups'] if g['id'] == 'registered'), ''))
    if status == 'no_match':
        card['actions'] = list(rules['no_match']['actions'])
        card['platforms'] = {}
        card['meaning'] = []
    elif status in ('candidates', 'disguised'):
        card['actions'] = list(rules[status]['actions']) + list(cat_rules['forms'])[:1]
        if status == 'disguised':
            card['meaning'].insert(0, '输入疑似为伪装写法（谐音、拼音、拆字或中英混写），命中是候选不是结论。')
        card['docs'] = _unique(d for g in groups for d in g['docs'])
        card['alternatives'] = list(cat_rules.get('alternatives', []))
    else:
        card['actions'] = _unique([*(a for g in groups for a in g['actions']), *cat_rules['forms']])
        card['docs'] = _unique([*(d for g in groups for d in g['docs']), *cat_rules.get('docs', [])])
        card['alternatives'] = list(cat_rules.get('alternatives', []))
    card['meaning'] = _unique(card['meaning'])
    return card


def markdown(card, indent=''):
    lines = [f"{indent}**{card['level_name']} · {card['score']} 分 · {card['title']}**", f"{indent}{card['headline']}"]
    if card['meaning']:
        lines.append(f"{indent}这意味着：")
        lines += [f"{indent}- {m}" for m in card['meaning']]
    if card['actions']:
        lines.append(f"{indent}可行做法：")
        lines += [f"{indent}{i}. {a}" for i, a in enumerate(card['actions'], 1)]
    if card['docs']:
        lines.append(f"{indent}上架前要拿到：{'；'.join(card['docs'])}")
    if card['alternatives']:
        lines.append(f"{indent}换个方向：{'；'.join(card['alternatives'])}")
    if card['platforms']:
        lines.append(f"{indent}平台提示：")
        lines += [f"{indent}- {k}：{v}" for k, v in card['platforms'].items()]
    if card['checklist']:
        lines.append(f"{indent}合规四查：{' ｜ '.join(card['checklist'])}")
    lines.append(f"{indent}底线：{card['notice']}")
    return '\n'.join(lines)
