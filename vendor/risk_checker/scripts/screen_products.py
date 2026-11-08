#!/usr/bin/env python3
"""JSON batch interface for virtual-product selection workflows. Python 3.10+."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import risk_check as lookup
import lint_listing

QUERY_FIELDS = ('title', 'work_name', 'brand', 'author', 'company')
MATCH_FIELDS = ('id', 'name', 'category', 'object_type', 'companies', 'labels',
                'registrations', 'match_type', 'matched_term')
NEXT_STEPS = {
    'matched': ('review_rights', '命中历史风险线索；确认实际作品与命中对象的关系，核清权利及转售授权后再作上架决定。'),
    'candidates': ('confirm_identity', '存在近似候选；先确认是否为同一作品或主体，再核对权利及转售授权。'),
    'disguised': ('confirm_identity', '字段疑似为风险名称的伪装写法（谐音、拼音或拆写）；先按正写名称复查，再核对权利及转售授权。'),
    'no_match': ('check_authorization', '本地名单未命中；继续核对实际作品、权利人及转售授权，不能视为版权通过。'),
}


def read_candidates(data):
    candidates = data.get('candidates') if isinstance(data, dict) else data
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('输入必须是非空候选数组，或含 candidates 数组的对象')
    result, ids = [], set()
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            raise ValueError(f'第 {index} 条候选必须是对象')
        fields = {}
        for field in ('id', 'platform', *QUERY_FIELDS, 'url'):
            value = candidate.get(field, '')
            if not isinstance(value, str):
                raise ValueError(f'第 {index} 条的 {field} 必须是字符串')
            fields[field] = value
        if not lookup.normalize(fields['title']):
            raise ValueError(f'第 {index} 条缺少非空 title 商品标题')
        fields['id'] = fields['id'].strip() or f'candidate-{index:04d}'
        if fields['id'] in ids:
            raise ValueError(f'候选编号重复：{fields["id"]}')
        ids.add(fields['id'])
        result.append(fields)
    return result


def screen(db, data, limit=10):
    if limit < 1:
        raise ValueError('--limit 必须大于零')
    candidates = read_candidates(data)
    product_db = dict(db, records=[r for r in db['records']
                                  if r.get('object_type') != '店铺'
                                  and r.get('category') != '店铺来源提醒'])
    results, cache = [], {}
    for candidate in candidates:
        checks, raw = [], []
        for field in QUERY_FIELDS:
            term = candidate[field].strip()
            key = lookup.normalize(term)
            if not key:
                continue
            if key not in cache:
                cache[key] = lookup.query_one(product_db, term, limit)
            found = cache[key]
            raw.append(found)
            checks.append(dict(field=field, query=term, status=found['status'],
                               total_matches=found['total_matches'], truncated=found['truncated'],
                               matches=[{k:r[k] for k in MATCH_FIELDS if k in r} for r in found['matches']]))
        statuses = {check['status'] for check in checks}
        status = 'matched' if 'matched' in statuses else 'candidates' if 'candidates' in statuses else 'disguised' if 'disguised' in statuses else 'no_match'
        action, guidance = NEXT_STEPS[status]
        best = next((found['matches'][0] for found in raw if found['status'] == status and found['matches']), None)
        results.append(dict(candidate=candidate, list_status=status,
                            copyright_status='unverified', authorization_status='needs_verification',
                            suggested_action=action, guidance=guidance, checks=checks,
                            handling=lookup.handling.advise(status, best),
                            lint=lint_listing.compact(lint_listing.lint(product_db, candidate['title'])) if candidate.get('title', '').strip() else None))
    counts = Counter(r['list_status'] for r in results)
    return dict(schema_version=1, updated_at=db.get('updated_at', '未提供'),
                origin='来自于网络收集', notice='名单命中不是侵权结论，未命中不代表已获授权；本接口不核验版权或平台准售状态。',
                summary=dict(total=len(results), **{status:counts[status] for status in NEXT_STEPS}),
                results=results)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Candidate JSON file, or - for stdin')
    parser.add_argument('--database', default=str(lookup.DEFAULT_DB))
    parser.add_argument('--limit', type=int, default=10, help='Maximum matches per candidate field')
    parser.add_argument('--output')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8-sig')
        report = screen(lookup.load_db(args.database), json.loads(raw), args.limit)
        lookup.emit(json.dumps(report, ensure_ascii=False, indent=2)+'\n', args.output, args.overwrite)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
