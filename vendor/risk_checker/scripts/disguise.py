#!/usr/bin/env python3
"""Disguised-name normalisation (伪装名识别). Standard library only.

Names and queries are reduced to pinyin token lists so that homophones, pinyin,
pinyin initials, inserted spaces/symbols and mixed Chinese/Latin spellings all
compare equal: 粉笔课堂 / 粉bi课堂 / fenbi课堂 / 粉 笔 课 堂 / fbkt.
A disguised match is a *candidate* for human review, never a conclusion.
The same rules are mirrored in web/app.js.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parents[1] / 'assets' / 'pinyin-table.json'
# Generic words stripped before "core" comparison; they carry no identity.
FILLERS = ('的', '全套', '课程', '网课', '资料', '视频', '教程', '合集', '打包', '完整版', '最新版', '最新', '高清', '系列')
MIN_FULL_SYLLABLES = 4   # full-pinyin / core comparisons need at least this many tokens
MIN_INITIALS = 3         # initials comparisons need at least this many letters
_cache = {}


def load_table(path=None):
    path = Path(path) if path else DEFAULT_TABLE
    key = str(path)
    if key not in _cache:
        data = json.loads(path.read_text(encoding='utf-8'))
        table = data['table']
        syllables = set(table.values())
        longest = max(len(s) for s in syllables)
        _cache[key] = (table, syllables, longest)
    return _cache[key]


def _segment_latin(run, syllables, longest):
    """Greedy longest-match pinyin segmentation; unsegmentable runs stay whole."""
    out, i = [], 0
    while i < len(run):
        for size in range(min(longest, len(run) - i), 0, -1):
            if run[i:i + size] in syllables:
                out.append(run[i:i + size])
                i += size
                break
        else:
            return [run]
    return out


def tokenize(text, table=None, with_spans=False):
    """Return pinyin tokens (optionally with [start, end) spans in the original text)."""
    table, syllables, longest = table or load_table()
    text = unicodedata.normalize('NFKC', str(text or ''))
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if '一' <= c <= '鿿':
            py = table.get(c)
            tokens.append((py if py else c, i, i + 1))
            i += 1
        elif c.isascii() and c.isalpha():
            j = i
            while j < len(text) and text[j].isascii() and text[j].isalpha():
                j += 1
            run = text[i:j].lower()
            pos = i
            for part in _segment_latin(run, syllables, longest):
                tokens.append((part, pos, pos + len(part)))
                pos += len(part)
            i = j
        elif c.isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            tokens.append((text[i:j], i, j))
            i = j
        else:
            i += 1
    return tokens if with_spans else [t[0] for t in tokens]


def strip_fillers(text):
    text = unicodedata.normalize('NFKC', str(text or ''))
    for filler in FILLERS:
        text = text.replace(filler, ' ')
    return text


def forms(text, table=None):
    """full tokens, core tokens (fillers removed), initials string, latin-only flag."""
    full = tokenize(text, table)
    core = tokenize(strip_fillers(text), table)
    initials = ''.join(t[0] for t in full)
    raw = str(text or '')
    has_han = any('一' <= c <= '鿿' for c in raw)
    has_latin = any(c.isascii() and c.isalpha() for c in raw)
    return dict(full=full, core=core, initials=initials, latin_only=bool(full) and not has_han, mixed=has_han and has_latin)


def _contains(haystack, needle):
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def is_disguise(query_forms, name_forms):
    """True when the query looks like a disguised spelling of the name."""
    q, n = query_forms, name_forms
    if len(''.join(q['full'])) < MIN_INITIALS:
        return False
    if len(q['full']) >= MIN_FULL_SYLLABLES and q['full'] == n['full']:
        return True
    # Mixed Chinese/Latin spelling of a short name (粉bi, fen笔) is itself a disguise signal.
    if q.get('mixed') and len(q['full']) >= 2 and q['full'] == n['full']:
        return True
    shorter, longer = (q['core'], n['core']) if len(q['core']) <= len(n['core']) else (n['core'], q['core'])
    if len(shorter) >= MIN_FULL_SYLLABLES and _contains(longer, shorter):
        return True
    if q['latin_only']:
        letters = ''.join(q['full'])
        if len(letters) >= MIN_INITIALS and letters == n['initials'] and len(n['full']) >= MIN_INITIALS:
            return True
    return False


def index_records(records, table=None):
    """Precompute forms for every searchable name of each record: [(record, term, forms)]."""
    table = table or load_table()
    out = []
    for r in records:
        seen = set()
        for term in [r.get('name', ''), *r.get('variants', [])]:
            if not term or term in seen:
                continue
            seen.add(term)
            f = forms(term, table)
            if len(f['full']) >= 2:
                out.append((r, term, f))
    return out


def find_disguised(query, index, table=None):
    """Yield (record, matched_term) for disguised matches, in index order."""
    qf = forms(query, table or load_table())
    hits, seen = [], set()
    for r, term, f in index:
        if r.get('id') in seen:
            continue
        if is_disguise(qf, f):
            seen.add(r.get('id'))
            hits.append((r, term))
    return hits


def scan_text(text, index, table=None):
    """Find disguised spellings inside a longer text. Returns [(start, end, record, term)]."""
    table = table or load_table()
    spans = tokenize(text, table, with_spans=True)
    tokens = [t[0] for t in spans]
    raw = unicodedata.normalize('NFKC', str(text or ''))
    hits = []

    def mixed(start, end):
        piece = raw[start:end]
        return any('\u4e00' <= c <= '\u9fff' for c in piece) and any(c.isascii() and c.isalpha() for c in piece)

    for r, term, f in index:
        pattern = f['core'] if len(f['core']) >= MIN_FULL_SYLLABLES else f['full']
        n = len(pattern)
        if n >= 2:
            for i in range(len(tokens) - n + 1):
                if tokens[i:i + n] == pattern:
                    start, end = spans[i][1], spans[i + n - 1][2]
                    if n >= MIN_FULL_SYLLABLES or mixed(start, end):
                        hits.append((start, end, r, term))
                        break
        if len(f['initials']) >= MIN_INITIALS and len(f['full']) >= MIN_INITIALS:
            for m in re.finditer(r'[A-Za-z]{3,}', unicodedata.normalize('NFKC', str(text or ''))):
                if m.group().lower() == f['initials']:
                    hits.append((m.start(), m.end(), r, term))
                    break
    return hits
