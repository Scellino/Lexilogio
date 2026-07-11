#!/usr/bin/env python3
"""Greek Verb Conjugation Trainer — reads directly from PDF on demand."""

import json, re, threading, unicodedata
from pathlib import Path
from flask import Blueprint, Flask, jsonify, request
from flask_login import current_user
from models import db, Progress

verb_bp = Blueprint('verb', __name__)

PDF_PATH     = "/Users/Christoph/Documents/greek/600-modern-greek-verbs-fully-conjugated-in-all-the-tenses-alphabetically-arranged_compress.pdf"
APP_DIR      = Path(__file__).parent.parent / 'greek'
PROGRESS_FILE      = APP_DIR / "verb_progress.json"   # legacy — read only by migrate_verb_progress.py
INDEX_FILE         = APP_DIR / "verb_index.json"
CONJUGATIONS_FILE  = APP_DIR / "verb_conjugations.json"
PRESETS_FILE       = APP_DIR / "verb_presets.json"

# ── Tense schema ─────────────────────────────────────────────────────────────
# (name, row_count):  row_count=1 for non-conjugated forms (participles, infinitives)
TENSE_DEFS = [
    ("Present",              2),
    ("Present Subjunctive",  2),
    ("Cont. Imperative",     2),
    ("Present Participle",   1),
    ("Continuous Past",      2),
    ("Continuous Future",    2),
    ("Simple Future",        2),
    ("Simple Past",          2),
    ("Past Subjunctive",     2),
    ("Simple Imperative",    2),
    ("Simple Infinitive",    1),
    ("Present Perfect",      2),
    ("Perfect Subjunctive",  2),
    ("Perfect Participle",   1),   # passive only
    ("Past Perfect",         2),
    ("Future Perfect",       2),
]
TENSE_ORDER = [t[0] for t in TENSE_DEFS]
TENSE_ROWS  = dict(TENSE_DEFS)

# Tenses suitable for 6-person quiz (2-row tenses)
QUIZZABLE_TENSES = [t for t, r in TENSE_DEFS if r == 2]

# Regex: longest-first so "Present Perfect" doesn't match "Present" prefix
_tn = sorted(TENSE_ORDER, key=len, reverse=True)
TENSE_RE  = re.compile(r'^(' + '|'.join(re.escape(n) for n in _tn) + r')\s*(.*)?$')
HEADER_RE = re.compile(
    r'(Active|Passive)\s+Voice\s+'
    r'([Α-Ωά-ώἀ-῿A-Z\*]+(?:\s+[Α-Ωά-ώἀ-῿A-Z\*]+)*)'
    r'\s+to\s+(.*)',
    re.IGNORECASE
)

_LATIN_TO_GREEK = {
    'A': 'Α', 'B': 'Β', 'E': 'Ε', 'H': 'Η', 'I': 'Ι', 'K': 'Κ',
    'M': 'Μ', 'N': 'Ν', 'O': 'Ο', 'P': 'Ρ', 'T': 'Τ', 'Y': 'Υ',
    'X': 'Χ', 'Z': 'Ζ',
}

def _norm_verb(name: str) -> str:
    """Replace ASCII lookalikes with Greek equivalents and strip trailing *."""
    return ''.join(_LATIN_TO_GREEK.get(c, c) for c in name).rstrip('*')

def _has_greek(s: str) -> bool:
    return any('Ͱ' <= c <= 'Ͽ' or 'ἀ' <= c <= '῿' for c in s)

def _split_forms(row: str) -> list[str]:
    return [p.strip() for p in re.split(r',\s*', row.strip()) if p.strip()]

def _merge_wrapped_rows(rows: list[str]) -> list[str]:
    """Merge 2+1 wrapped rows — only for compound forms (multi-word like 'να έχουμε …').
    Simple single-word 2-form rows (imperatives) are never merged with the next row."""
    out = []
    i = 0
    while i < len(rows):
        f = _split_forms(rows[i])
        if len(f) == 2 and i + 1 < len(rows):
            nxt = _split_forms(rows[i + 1])
            # Only merge if the first form is compound (multi-word) — indicates a
            # wrapped perfect/subjunctive row, NOT an imperative + infinitive boundary.
            if len(nxt) == 1 and len(f) == 2 and not f[1].strip().startswith('ας'):
                out.append(rows[i].rstrip(', ') + ', ' + rows[i + 1])
                i += 2
                continue
        out.append(rows[i])
        i += 1
    return out

# ── PDF page parser ──────────────────────────────────────────────────────────

def parse_verb_page(text: str):
    """Parse one PDF page and return verb info + conjugation table."""
    _LINE_TYPOS = {
        'Past':            'Past Subjunctive',
        'Cont, Imperative':'Cont. Imperative',
        'Pasf Subjunctive':'Past Subjunctive',
        'Continuous Vast': 'Continuous Past',
        'Simple Perfect':  'Perfect Subjunctive',
        'Simple Subjunctive': 'Past Subjunctive',
    }
    def _norm(ln):
        ln = re.sub(r'  +', ' ', ln.strip())
        return _LINE_TYPOS.get(ln, ln)

    lines = [_norm(l) for l in text.split('\n') if l.strip()]

    header          = None
    header_line_idx = -1
    cat = []

    for i, line in enumerate(lines):
        if re.match(r'^\d+\s*[•·]', line):
            continue
        m = HEADER_RE.match(line)
        if m and header is None:
            header          = {'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()),
                               'english': m.group(3).strip()}
            header_line_idx = i
            cat.append((i, 'header', None, line))
            continue
        m = TENSE_RE.match(line)
        if m:
            label    = m.group(1)
            trailing = (m.group(2) or '').strip()
            tp       = 'label+form' if (trailing and _has_greek(trailing)) else 'label'
            cat.append((i, tp, label, trailing or None))
            continue
        if _has_greek(line):
            cat.append((i, 'form', None, line))

    # Handle multi-line header: "Active Voice\nVERB to english"
    if not header:
        voice_re = re.compile(r'^(Active|Passive)\s+Voice\s*$', re.I)
        for i, line in enumerate(lines[:-1]):
            if voice_re.match(line):
                # Combine with next 1-2 lines to get "VOICE VERB to english"
                for j in range(i + 1, min(i + 3, len(lines))):
                    candidate = line + ' ' + lines[j]
                    m = HEADER_RE.match(candidate)
                    if m:
                        header = {'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()),
                                  'english': m.group(3).strip()}
                        header_line_idx = i
                        cat.append((i, 'header', None, candidate))
                        break
                if header:
                    break

    if not header:
        return None

    is_passive  = header_line_idx >= 10
    all_sorted  = sorted(cat, key=lambda x: x[0])
    tenses      = {name: [] for name in TENSE_ORDER}

    if is_passive:
        form_rows    = [t for _, tp, _, t in all_sorted if tp == 'form']
        found_labels = [l for _, tp, l, _ in all_sorted if tp in ('label', 'label+form')]
        form_rows    = _merge_wrapped_rows(form_rows)
        expected = sum(TENSE_ROWS[l] for l in found_labels if l in TENSE_ROWS)
        if expected > len(form_rows):
            for opt in ('Perfect Participle', 'Present Participle'):
                if opt in found_labels and expected - TENSE_ROWS.get(opt, 1) == len(form_rows):
                    found_labels = [l for l in found_labels if l != opt]
                    break
        form_idx = 0
        for label in TENSE_ORDER:
            if label not in found_labels:
                continue
            n = TENSE_ROWS[label]
            tenses[label] = form_rows[form_idx:form_idx + n]
            form_idx += n

    else:
        items_list = list(all_sorted)
        consumed_form_idxs = set()
        inline_tense_forms = {}

        _IMP_LABELS = {'Cont. Imperative', 'Simple Imperative'}
        for idx, (_, tp, l, t) in enumerate(items_list):
            if tp != 'label+form':
                continue
            thresh = 2 if l in _IMP_LABELS else 3
            sg_parts = list(_split_forms(t)) if t else []
            j = idx + 1
            while len(sg_parts) < thresh and j < len(items_list):
                _, ntp, _, nt = items_list[j]
                if ntp == 'form':
                    sg_parts.extend(_split_forms(nt))
                    consumed_form_idxs.add(j)
                    j += 1
                else:
                    break
            sg_row = ', '.join(sg_parts[:thresh])
            pl_parts = []
            while len(pl_parts) < thresh and j < len(items_list):
                _, ntp, _, nt = items_list[j]
                if ntp == 'form':
                    pl_parts.extend(_split_forms(nt))
                    consumed_form_idxs.add(j)
                    j += 1
                else:
                    break
            rows = [sg_row] + ([', '.join(pl_parts[:thresh])] if pl_parts else [])
            inline_tense_forms[l] = rows

        remaining_raw = [t for i, (_, tp, _, t) in enumerate(items_list)
                         if tp == 'form' and i not in consumed_form_idxs]
        remaining = _merge_wrapped_rows(remaining_raw)

        all_found_labels = {l for _, tp, l, _ in items_list
                            if tp in ('label', 'label+form')}
        non_inline = [l for l in TENSE_ORDER
                      if l in all_found_labels and l not in inline_tense_forms]

        expected_rows = sum(TENSE_ROWS.get(l, 2) for l in non_inline)
        if expected_rows > len(remaining):
            for opt in ('Perfect Participle', 'Present Participle'):
                if opt in non_inline and expected_rows - TENSE_ROWS.get(opt, 1) == len(remaining):
                    non_inline = [l for l in non_inline if l != opt]
                    break

        form_idx = 0
        for label in non_inline:
            n = TENSE_ROWS.get(label, 2)
            tenses[label] = remaining[form_idx:form_idx + n]
            form_idx += n

        for label, rows in inline_tense_forms.items():
            tenses[label] = rows

    tenses = {k: v for k, v in tenses.items() if v}
    return {**header, 'tenses': tenses}


def parse_verb_page_v2(page):
    """Coordinate-based parser: uses x/y positions from pypdf visitor."""
    from collections import defaultdict

    frags = []
    def _visit(text, cm, tm, fontDict, fontSize):
        if text.strip():
            frags.append((float(tm[4]), float(tm[5]), text))
    page.extract_text(visitor_text=_visit)
    if not frags:
        return None

    _LINE_TYPOS = {
        'Past':               'Past Subjunctive',
        'Cont, Imperative':   'Cont. Imperative',
        'Pasf Subjunctive':   'Past Subjunctive',
        'Continuous Vast':    'Continuous Past',
        'Simple Perfect':     'Perfect Subjunctive',
        'Simple Subjunctive': 'Past Subjunctive',
    }
    def _norm(s):
        s = re.sub(r'  +', ' ', s.strip())
        return _LINE_TYPOS.get(s, s)

    label_x_max = 0
    for x, y, t in frags:
        if TENSE_RE.match(_norm(t)):
            label_x_max = max(label_x_max, x)
    if label_x_max == 0:
        return None

    greek_right_xs = [x for x, y, t in frags
                      if _has_greek(t) and not TENSE_RE.match(_norm(t)) and x > label_x_max]
    if not greek_right_xs:
        return None
    mid_x = (label_x_max + min(greek_right_xs)) / 2

    bands = defaultdict(list)
    for x, y, t in frags:
        band = round(y / 10) * 10
        bands[band].append((x, t))

    header = None
    header_band = None
    for band in sorted(bands.keys(), reverse=True):
        items = sorted(bands[band])
        full = re.sub(r'  +', ' ', ' '.join(t.strip() for x, t in items)).strip()
        m = HEADER_RE.match(full)
        if m and header is None:
            header = {'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()),
                      'english': m.group(3).strip()}
            header_band = band

    if not header:
        # Try combining adjacent bands for multi-line headers
        sorted_bands = sorted(bands.keys(), reverse=True)
        for i in range(len(sorted_bands) - 1):
            b1, b2 = sorted_bands[i], sorted_bands[i + 1]
            full1 = re.sub(r'  +', ' ', ' '.join(t for x, t in sorted(bands[b1]))).strip()
            full2 = re.sub(r'  +', ' ', ' '.join(t for x, t in sorted(bands[b2]))).strip()
            if re.match(r'^(Active|Passive)\s+Voice\s*$', full1, re.I):
                candidate = full1 + ' ' + full2
                m = HEADER_RE.match(candidate)
                if m:
                    header = {'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()),
                              'english': m.group(3).strip()}
                    header_band = b1
                    break

    if not header:
        return None

    tenses_raw = {}
    current_label = None

    for band in sorted(bands.keys(), reverse=True):
        if band == header_band:
            continue
        items = sorted(bands[band])

        left  = _norm(' '.join(t for x, t in items if x <  mid_x))
        right = _norm(' '.join(t for x, t in items if x >= mid_x))

        combined = (left + ' ' + right).strip()
        if re.search(r'\d+\s*[•·]|[•·]\s*\d+', combined):
            continue

        m = TENSE_RE.match(left) if left else None
        if m:
            current_label = m.group(1)
            tenses_raw.setdefault(current_label, [])

        if right and _has_greek(right) and current_label is not None:
            tenses_raw.setdefault(current_label, [])
            tenses_raw[current_label].append(right)

    tenses = {}
    for label in TENSE_ORDER:
        if label not in tenses_raw:
            continue
        rows = _merge_wrapped_rows(tenses_raw[label])
        tenses[label] = rows[:TENSE_ROWS[label]]

    tenses = {k: v for k, v in tenses.items() if v}
    return {**header, 'tenses': tenses}


_EXPECTED_FORMS = {
    'Present Participle': 1, 'Simple Infinitive': 1, 'Perfect Participle': 1,
    'Cont. Imperative': 2,   'Simple Imperative': 2,
}

def _parse_score(r: dict) -> int:
    s = 0
    for label, rows in r['tenses'].items():
        n   = TENSE_ROWS.get(label, 2)
        exp = _EXPECTED_FORMS.get(label, 3)
        if len(rows) >= n:
            for row in rows[:n]:
                s += min(len([p for p in row.split(',') if p.strip()]), exp)
    return s

def _parse_best(page, text: str):
    """Run both parsers and return whichever gives the more complete result."""
    r1 = parse_verb_page(text)
    try:
        r2 = parse_verb_page_v2(page)
    except Exception:
        r2 = None
    if r1 is None: return r2
    if r2 is None: return r1
    return r1 if _parse_score(r1) >= _parse_score(r2) else r2


def get_forms_for_person(tense_rows: list[str]) -> list[str]:
    """Return flat list of 6 forms [1sg,2sg,3sg,1pl,2pl,3pl] from two row strings."""
    if len(tense_rows) < 2:
        return []
    sg = _split_forms(tense_rows[0])
    pl = _split_forms(tense_rows[1])
    if len(sg) != 3 or len(pl) != 3:
        return []
    return sg + pl


# ── PDF / Index ───────────────────────────────────────────────────────────────

_reader     = None
_reader_lock = threading.Lock()

def get_reader():
    global _reader
    with _reader_lock:
        if _reader is None:
            import pypdf
            _reader = pypdf.PdfReader(PDF_PATH)
    return _reader

VERB_INDEX  = None
_index_ready = threading.Event()

def _build_index_bg():
    global VERB_INDEX
    if INDEX_FILE.exists():
        VERB_INDEX = json.loads(INDEX_FILE.read_text())
        _index_ready.set()
        print(f"Verb index loaded: {len(VERB_INDEX)} entries")
        return

    print("Building verb index from PDF (one-time, ~30 s)…")
    reader = get_reader()
    index  = []
    _voice_only_re = re.compile(r'^(Active|Passive)\s+Voice\s*$', re.I)
    for i, page in enumerate(reader.pages):
        text  = page.extract_text() or ""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        found = False
        for line in lines[:6]:
            m = HEADER_RE.match(line)
            if m:
                index.append({'page': i, 'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()), 'english': m.group(3).strip()})
                found = True
                break
        if not found:
            for line in lines[-6:]:
                m = HEADER_RE.match(line)
                if m:
                    index.append({'page': i, 'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()), 'english': m.group(3).strip()})
                    found = True
                    break
        if not found:
            # Multi-line header: "Active Voice" on one line, "VERB to english" on next
            for j, line in enumerate(lines[:5]):
                if _voice_only_re.match(line) and j + 1 < len(lines):
                    candidate = line + ' ' + lines[j + 1]
                    m = HEADER_RE.match(candidate)
                    if m:
                        index.append({'page': i, 'voice': m.group(1), 'verb': _norm_verb(m.group(2).strip()), 'english': m.group(3).strip()})
                        break

    index.sort(key=lambda x: x['page'])
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2))
    VERB_INDEX = index
    _index_ready.set()
    print(f"Verb index ready: {len(index)} entries")

threading.Thread(target=_build_index_bg, daemon=True).start()


# ── Conjugation cache ─────────────────────────────────────────────────────────

VERB_CONJUGATIONS = None
_conj_ready       = threading.Event()

def _build_conjugations_bg():
    global VERB_CONJUGATIONS
    if CONJUGATIONS_FILE.exists():
        VERB_CONJUGATIONS = json.loads(CONJUGATIONS_FILE.read_text())
        _conj_ready.set()
        print(f"Conjugation cache loaded: {len(VERB_CONJUGATIONS)} entries")
        return

    print("Building conjugation cache (one-time, ~60 s) …")
    import pypdf as _pypdf
    reader = _pypdf.PdfReader(PDF_PATH)
    cache  = {}
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
            r    = _parse_best(page, text)
            if r:
                cache[str(i)] = {**r, 'page': i}
        except Exception as e:
            print(f"  pg{i}: skipped ({e})")

    CONJUGATIONS_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    VERB_CONJUGATIONS = cache
    _conj_ready.set()
    print(f"Conjugation cache ready: {len(cache)} entries")

threading.Thread(target=_build_conjugations_bg, daemon=True).start()


# ── Progress ──────────────────────────────────────────────────────────────────
# Per-user rows in the Progress table (lang_code "el-verb"). card_id is the
# legacy key ("verb:tense:person" or "_t:page:tense"); the counts/history dict
# lives as JSON in the window column. The old shared verb_progress.json was
# migrated by migrate_verb_progress.py.

VERB_PROGRESS_LANG = "el-verb"
_MAX_PROGRESS_ROWS = 60_000   # ≈ full completion of all 600 verbs, per user

def load_progress() -> dict:
    """All verb progress for the current user, in the legacy dict shape."""
    if not current_user.is_authenticated:
        return {}
    rows = Progress.query.filter_by(
        user_id=current_user.id, lang_code=VERB_PROGRESS_LANG).all()
    out = {}
    for r in rows:
        try:
            v = json.loads(r.window or "{}")
        except ValueError:
            continue
        if isinstance(v, dict):
            out[r.card_id] = v
    return out

def _progress_row(key: str):
    """Fetch-or-create the current user's row for one progress key.
    Returns None when the per-user row cap is hit."""
    row = Progress.query.filter_by(
        user_id=current_user.id, lang_code=VERB_PROGRESS_LANG, card_id=key).first()
    if not row:
        n = Progress.query.filter_by(
            user_id=current_user.id, lang_code=VERB_PROGRESS_LANG).count()
        if n >= _MAX_PROGRESS_ROWS:
            return None
        row = Progress(user_id=current_user.id, lang_code=VERB_PROGRESS_LANG,
                       card_id=key, window="{}")
        db.session.add(row)
    return row

def _row_value(row, default: dict) -> dict:
    try:
        v = json.loads(row.window or "{}")
    except ValueError:
        v = None
    return v if isinstance(v, dict) and v else default

def load_presets() -> list:
    return json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []

def save_presets(p: list):
    PRESETS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=2))


# ── Answer checking ───────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    return unicodedata.normalize('NFC', s.strip().lower())

def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def check_answer(guess: str, correct: str) -> str:
    g, c = _normalize(guess), _normalize(correct)
    if g == c:
        return 'correct'
    if _strip_accents(g) == _strip_accents(c):
        return 'accent'
    if abs(len(g) - len(c)) <= 2:
        max_len = max(len(g), len(c))
        diffs = sum(1 for a, b in zip(g.ljust(max_len), c.ljust(max_len)) if a != b)
        if diffs <= 2 and max_len >= 6:
            return 'close'
    return 'wrong'


# ── Routes ────────────────────────────────────────────────────────────────────

@verb_bp.route('/api/status')
def api_status():
    return jsonify({
        'ready':      _index_ready.is_set() and _conj_ready.is_set(),
        'count':      len(VERB_INDEX) if VERB_INDEX else 0,
        'conj_ready': _conj_ready.is_set(),
    })

def _dot_status(page: int, progress: dict):
    """Compute verb dot status from tense-level sliding windows."""
    prefix = f"_t:{page}:"
    histories = [progress[k]['history'] for k in progress if k.startswith(prefix) and progress[k]['history']]
    if not histories:
        return None
    total   = sum(len(h) for h in histories)
    correct = sum(sum(h) for h in histories)
    if total < 12:
        return 'new'
    acc = correct / total
    if acc >= 0.8:
        return 'mastered'
    if acc >= 0.5:
        return 'learning'
    return 'struggling'

@verb_bp.route('/api/verb_list')
def api_verb_list():
    if not _index_ready.is_set():
        _index_ready.wait(timeout=120)
    progress = load_progress()
    result = []
    for entry in (VERB_INDEX or []):
        v    = entry['verb']
        page = entry['page']
        keys = [k for k in progress if k.startswith(f"{v}:") and not k.startswith(f"{v}:_")]
        att  = sum(progress[k]['attempts'] for k in keys)
        cor  = sum(progress[k]['correct']  for k in keys)
        result.append({**entry, 'attempts': att, 'correct': cor, 'dot': _dot_status(page, progress)})
    return jsonify(result)

@verb_bp.route('/api/verb/<int:page_num>')
def api_verb(page_num: int):
    parsed = None
    if _conj_ready.is_set() and VERB_CONJUGATIONS is not None:
        parsed = VERB_CONJUGATIONS.get(str(page_num))
    if parsed is None:
        reader = get_reader()
        if page_num < 0 or page_num >= len(reader.pages):
            return jsonify({'error': 'page out of range'}), 400
        page   = reader.pages[page_num]
        text   = page.extract_text() or ""
        parsed = _parse_best(page, text)
        if parsed:
            parsed['page'] = page_num

    if parsed:
        enriched = {}
        for tname, data in parsed['tenses'].items():
            if isinstance(data, dict):
                enriched[tname] = data
                continue
            rows   = data
            forms6 = get_forms_for_person(rows) if TENSE_ROWS.get(tname, 1) == 2 else []
            enriched[tname] = {'rows': rows, 'forms': forms6}
        parsed = {**parsed, 'tenses': enriched}
    return jsonify(parsed)

@verb_bp.route('/api/check', methods=['POST'])
def api_check():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        d = {}
    result = check_answer(str(d.get('guess', '')), str(d.get('correct', '')))

    verb   = d.get('verb')
    tense  = d.get('tense')
    person = d.get('person')
    page   = d.get('page')
    valid = (isinstance(verb, str) and isinstance(tense, str) and isinstance(person, str)
             and 0 < len(verb) <= 64 and 0 < len(tense) <= 64 and 0 < len(person) <= 64)
    if valid and current_user.is_authenticated:
        # Per-person counts (legacy key format, kept for backward compat)
        row = _progress_row(f"{verb}:{tense}:{person}")
        if row is not None:
            e = _row_value(row, {'attempts': 0, 'correct': 0, 'close': 0})
            e['attempts'] += 1
            if result == 'correct':
                e['correct'] += 1
            elif result in ('accent', 'close'):
                e['close'] += 1
            row.window = json.dumps(e, ensure_ascii=False)

        # Tense-level sliding window (last 12 results for this verb+tense)
        if isinstance(page, int):
            trow = _progress_row(f"_t:{page}:{tense}")
            if trow is not None:
                win = _row_value(trow, {'history': []})
                hist = win.get('history') or []
                hist.append(1 if result in ('correct', 'accent') else 0)
                win['history'] = hist[-12:]
                trow.window = json.dumps(win)

        db.session.commit()

    return jsonify({'result': result, 'correct_form': _normalize(str(d.get('correct', '')))})

@verb_bp.route('/api/progress')
def api_progress():
    return jsonify(load_progress())

@verb_bp.route('/api/presets', methods=['GET'])
def api_get_presets():
    return jsonify(load_presets())

def _presets_writable():
    # Presets are a single global file shown to every visitor — only an
    # admin may change them
    return current_user.is_authenticated and current_user.is_admin

@verb_bp.route('/api/presets/save', methods=['POST'])
def api_save_preset():
    if not _presets_writable():
        return jsonify({'error': 'admin required'}), 403
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    presets = [p for p in load_presets() if p['name'] != name]
    presets.append({'name': name, 'pages': d.get('pages', []), 'verbs': d.get('verbs', [])})
    save_presets(presets)
    return jsonify({'ok': True})

@verb_bp.route('/api/presets/delete', methods=['POST'])
def api_delete_preset():
    if not _presets_writable():
        return jsonify({'error': 'admin required'}), 403
    name = (request.get_json(silent=True) or {}).get('name', '')
    save_presets([p for p in load_presets() if p['name'] != name])
    return jsonify({'ok': True})

@verb_bp.route('/')
def index():
    return HTML


# ── Frontend ──────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Greek Verb Trainer</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f0f1a">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Λεξιλόγιο">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f1a;font-family:Georgia,serif;color:#fff;min-height:100vh}

/* Header */
.app-header{text-align:center;padding:28px 20px 0}
.home-link{position:fixed;top:10px;left:10px;z-index:100;font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;text-decoration:none;letter-spacing:.5px;padding:6px 10px;border-radius:8px;transition:color .15s,background .15s}
.home-link:hover{color:rgba(201,169,110,.8);background:rgba(201,169,110,.08)}
.app-header-sub{font-size:10px;letter-spacing:4px;color:#c9a96e;text-transform:uppercase;margin-bottom:4px;font-family:sans-serif;opacity:.8}
.app-header-title{font-size:22px;color:#fff;font-weight:normal;letter-spacing:1px}

/* Nav */
.nav-tabs{display:flex;gap:4px;background:rgba(255,255,255,.04);border-radius:14px;padding:4px;margin:18px auto 0;width:calc(100% - 28px);max-width:560px}
.nav-tab{flex:1;padding:8px;border-radius:10px;border:none;background:transparent;color:rgba(255,255,255,.4);font-size:12px;font-weight:700;font-family:sans-serif;letter-spacing:1px;text-transform:uppercase;cursor:pointer;transition:all .15s}
.nav-tab.active{background:rgba(201,169,110,.2);color:#c9a96e}

/* Layout */
#app{padding:18px 14px 60px;display:flex;flex-direction:column;align-items:center}
.content{width:100%;max-width:560px}
.loading{color:rgba(255,255,255,.3);text-align:center;padding:60px 0;font-family:sans-serif;font-size:13px}

/* Cards */
.card{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;padding:22px;margin-bottom:14px;border:1px solid rgba(255,255,255,.07)}

/* Section labels */
.sec-label{font-size:10px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:8px}

/* Inputs */
input[type=text]{width:100%;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:16px;outline:none;font-family:Georgia,serif}
input[type=text]:focus{border-color:#c9a96e}
input[type=checkbox]{accent-color:#c9a96e;cursor:pointer;width:15px;height:15px}

/* Buttons */
.btn-primary{width:100%;padding:14px;border-radius:12px;background:linear-gradient(135deg,#c9a96e,#e8c98a);border:none;color:#1a1a2e;font-size:15px;font-weight:700;font-family:sans-serif;letter-spacing:1px;cursor:pointer;text-transform:uppercase;margin-top:10px;transition:opacity .15s}
.btn-primary:disabled{background:rgba(255,255,255,.07);color:rgba(255,255,255,.2);cursor:default}
.btn-primary:not(:disabled):hover{opacity:.9}
.btn-secondary{padding:10px 18px;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);color:rgba(255,255,255,.6);font-size:12px;font-weight:700;font-family:sans-serif;cursor:pointer;text-transform:uppercase;transition:all .15s}
.btn-secondary:hover{border-color:rgba(201,169,110,.6);color:#c9a96e}
.btn-ghost{padding:7px 14px;border-radius:8px;background:transparent;border:1px solid rgba(255,255,255,.1);color:rgba(255,255,255,.4);font-size:12px;font-family:sans-serif;cursor:pointer;transition:all .15s}
.btn-ghost:hover{border-color:rgba(255,255,255,.3);color:rgba(255,255,255,.7)}
.btn-row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}

/* Pills */
.pill{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;transition:all .15s;display:inline-block;margin:2px}
.pill.on{background:rgba(201,169,110,.18);border-color:#c9a96e;color:#c9a96e}
.pill:hover:not(.on){border-color:rgba(255,255,255,.25);color:rgba(255,255,255,.7)}

/* Progress bar */
.prog-wrap{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:22px;overflow:hidden;width:100%;max-width:560px}
.prog-bar{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}

/* Browse */
.search-wrap{position:relative;margin-bottom:10px}
.search-wrap input{padding-left:36px}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:15px;opacity:.35;pointer-events:none;font-family:sans-serif}
.browse-meta{font-size:11px;color:rgba(255,255,255,.2);font-family:sans-serif;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.verb-list{border:1px solid rgba(255,255,255,.07);border-radius:12px;overflow:hidden}
.verb-row{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .1s}
.verb-row:last-child{border-bottom:none}
.verb-row:hover{background:rgba(255,255,255,.03)}
.verb-row.selected{background:rgba(201,169,110,.07)}
.verb-name{font-family:Georgia,serif;font-size:15px;color:#e8c98a;min-width:150px;flex-shrink:0}
.verb-en{font-size:12px;color:rgba(255,255,255,.38);font-style:italic;flex:1;font-family:sans-serif;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.verb-voice{font-size:10px;color:rgba(255,255,255,.18);font-family:sans-serif;min-width:14px;flex-shrink:0}
.mastery-bar{width:32px;height:3px;background:rgba(255,255,255,.1);border-radius:2px;overflow:hidden;flex-shrink:0}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-right:2px}
.dot-new{background:#4a9eff}
.dot-learning{background:#f0b429}
.dot-struggling{background:#e57373}
.dot-mastered{background:#4caf88}
.dot-none{background:transparent}
.mastery-fill{height:100%;background:#7ac49a;border-radius:2px}

/* Table view */
.verb-header{font-family:Georgia,serif;font-size:26px;color:#e8c98a;margin-bottom:4px}
.verb-header-sub{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;text-transform:uppercase;letter-spacing:1px;margin-bottom:18px}
.conj-table{width:100%;border-collapse:collapse;font-size:13px}
.conj-table th{padding:7px 10px;text-align:left;border-bottom:1px solid rgba(255,255,255,.1);color:rgba(201,169,110,.55);font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;font-family:sans-serif}
.conj-table td{padding:7px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:sans-serif;font-size:13px}
.conj-table tr:hover td{background:rgba(255,255,255,.02)}
.conj-table td:first-child{color:rgba(255,255,255,.35);font-size:11px;white-space:nowrap;font-family:sans-serif}
.conj-table .hl-row td{background:rgba(201,169,110,.05)}
.conj-table .hl{color:#e8c98a;font-weight:bold;font-family:Georgia,serif}
.conj-table .pl-row td{border-bottom:1px solid rgba(255,255,255,.09)}
.special-form{color:rgba(255,255,255,.45);font-style:italic;font-family:Georgia,serif}

/* Quiz */
.quiz-meta{font-size:10px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-bottom:18px;text-transform:uppercase;letter-spacing:1px}
.quiz-verb{font-family:Georgia,serif;font-size:34px;color:#e8c98a;margin-bottom:6px;line-height:1.15}
.quiz-en{font-size:13px;color:rgba(255,255,255,.3);font-style:italic;font-family:sans-serif;margin-bottom:20px}
.quiz-tense{font-size:11px;color:rgba(201,169,110,.75);font-family:sans-serif;margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px}
.quiz-person{font-size:16px;color:#fff;font-family:Georgia,serif;margin-bottom:18px}
.quiz-input{width:100%;padding:13px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:19px;outline:none;margin-bottom:12px;font-family:Georgia,serif}
.quiz-input:focus{border-color:#c9a96e}

/* Feedback */
.feedback{border-radius:12px;padding:14px 16px;margin-bottom:14px}
.feedback.correct{background:rgba(122,196,154,.1);border:1px solid #7ac49a}
.feedback.accent{background:rgba(230,180,80,.08);border:1px solid #e6b450}
.feedback.close{background:rgba(230,180,80,.08);border:1px solid #e6b450}
.feedback.wrong{background:rgba(212,122,143,.1);border:1px solid #d47a8f}
.feedback-verdict{font-size:13px;font-weight:700;font-family:sans-serif;margin-bottom:5px}
.feedback.correct .feedback-verdict{color:#7ac49a}
.feedback.accent .feedback-verdict,.feedback.close .feedback-verdict{color:#e6b450}
.feedback.wrong .feedback-verdict{color:#d47a8f}
.feedback-form{font-size:16px;color:#f0ebe0;font-family:Georgia,serif;margin-top:6px}

/* Results */
.score-big{font-size:54px;font-weight:700;font-family:Georgia,serif;text-align:center;color:#e8c98a;margin-bottom:4px}
.score-sub{font-size:13px;color:rgba(255,255,255,.35);font-family:sans-serif;text-align:center;margin-bottom:20px}
.result-row{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.03);border-radius:10px;padding:8px 12px;margin-bottom:5px}
.result-icon{font-size:13px;min-width:16px;font-family:sans-serif;flex-shrink:0}
.result-verb{font-family:Georgia,serif;font-size:14px;color:#e8c98a;min-width:120px;flex-shrink:0}
.result-tense{font-size:11px;color:rgba(255,255,255,.28);font-family:sans-serif;min-width:110px;flex-shrink:0}
.result-person{font-size:11px;color:rgba(255,255,255,.28);font-family:sans-serif;min-width:60px;flex-shrink:0}
.result-guess{font-family:Georgia,serif;font-size:13px;flex:1}
.result-guess.correct{color:#7ac49a}
.result-guess.accent,.result-guess.close{color:#e6b450}
.result-guess.wrong{color:#d47a8f}
.result-guess.skip{color:rgba(255,255,255,.25)}

/* Mini conjugation table in feedback */
.mini-table-wrap{margin-top:22px;border-top:1px solid rgba(255,255,255,.07);padding-top:18px}
.mini-label{font-size:10px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:12px}

/* Theory */
.theory-tabs{display:flex;gap:6px;margin-bottom:18px}
.theory-tab{flex:1;padding:9px;border-radius:10px;border:1px solid rgba(255,255,255,.1);background:transparent;color:rgba(255,255,255,.35);font-size:12px;font-weight:700;font-family:sans-serif;cursor:pointer;text-transform:uppercase;letter-spacing:1px;transition:all .15s}
.theory-tab.active{background:rgba(201,169,110,.18);border-color:#c9a96e;color:#c9a96e}
.theory-h1{font-family:Georgia,serif;font-size:20px;color:#e8c98a;margin-bottom:10px}
.theory-h2{font-size:10px;color:rgba(201,169,110,.75);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:10px}
.theory-p{font-size:13px;color:rgba(255,255,255,.55);font-family:sans-serif;line-height:1.75;margin-bottom:10px}
.theory-note{font-size:12px;color:#c9a96e;font-style:italic;font-family:sans-serif;margin-top:8px;line-height:1.6}
.paradigm{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:14px 16px;margin:10px 0}
.paradigm-title{font-size:10px;color:rgba(201,169,110,.65);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:10px}
.paradigm table{width:100%;border-collapse:collapse}
.paradigm td{padding:3px 6px;font-family:sans-serif;font-size:12px;color:rgba(255,255,255,.45)}
.paradigm td.lbl{color:rgba(255,255,255,.25);width:72px}
.paradigm td.gr{font-family:Georgia,serif;color:#e8c98a;font-size:13px}
.paradigm td.end{font-family:Georgia,serif;color:#c9a96e}
.paradigm td.tr{font-size:11px;color:rgba(255,255,255,.25);font-style:italic}
.tense-card{border-left:2px solid rgba(201,169,110,.3);padding-left:14px;margin-bottom:18px}
.tense-card-name{font-size:11px;color:#c9a96e;text-transform:uppercase;letter-spacing:.5px;font-family:sans-serif;font-weight:700;margin-bottom:3px}
.tense-card-greek{font-family:Georgia,serif;font-size:17px;color:#e8c98a;margin-bottom:6px}
.tense-card-meaning{font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;font-style:italic;margin-bottom:6px}
.tense-card-desc{font-size:13px;color:rgba(255,255,255,.55);font-family:sans-serif;line-height:1.7}
.tense-card-ex{font-size:12px;color:rgba(255,255,255,.35);font-family:sans-serif;margin-top:6px;font-style:italic}
.tense-card-ex .gr{font-family:Georgia,serif;color:rgba(232,201,138,.6);font-style:normal}

/* Lists tab */
.list-card{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:14px;padding:18px;margin-bottom:12px;border:1px solid rgba(255,255,255,.07)}
.list-card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}
.list-card-name{font-family:Georgia,serif;font-size:18px;color:#e8c98a}
.list-card-count{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-top:3px}
.list-card-verbs{font-size:12px;color:rgba(255,255,255,.4);font-family:Georgia,serif;margin-bottom:12px;line-height:1.6}
.list-card-actions{display:flex;gap:6px}
.list-new-form{background:rgba(255,255,255,.03);border:1px solid rgba(201,169,110,.25);border-radius:14px;padding:18px;margin-bottom:14px}
.list-new-form input[type=text]{margin-bottom:10px}
.list-verb-pick{max-height:260px;overflow-y:auto;border:1px solid rgba(255,255,255,.07);border-radius:10px;margin-top:8px}
.list-verb-row{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .1s}
.list-verb-row:last-child{border-bottom:none}
.list-verb-row:hover{background:rgba(255,255,255,.03)}
.list-verb-row.in-list{background:rgba(201,169,110,.07)}
.lv-name{font-family:Georgia,serif;font-size:14px;color:#e8c98a;min-width:140px}
.lv-en{font-size:12px;color:rgba(255,255,255,.35);font-style:italic;flex:1;font-family:sans-serif}
.setup-preset-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.setup-tense-overview{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
.tov-chip{background:rgba(201,169,110,.12);border:1px solid rgba(201,169,110,.3);border-radius:10px;padding:4px 10px;font-size:11px;color:#c9a96e;font-family:sans-serif}
.setup-summary{text-align:center;padding:14px;background:rgba(255,255,255,.03);border-radius:10px;margin-top:6px;font-family:sans-serif}
.setup-summary-big{font-family:Georgia,serif;font-size:26px;color:#e8c98a}
.setup-summary-sub{font-size:11px;color:rgba(255,255,255,.3);margin-top:3px}
button,a{-webkit-tap-highlight-color:transparent}
#app{padding-bottom:calc(60px + env(safe-area-inset-bottom,0px))}
.conj-table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;width:100%}
@media(max-width:430px){
  #app{padding-left:10px;padding-right:10px;padding-top:14px}
  .card{padding:16px}
  .verb-name{min-width:110px}
  .result-verb{min-width:90px}
  .result-tense{min-width:80px;font-size:10px}
  .result-person{min-width:45px;font-size:10px}
  .quiz-verb{font-size:26px}
  .quiz-input{font-size:16px}
  .score-big{font-size:40px}
  .btn-primary{padding:13px}
  .nav-tab{padding:10px 4px;font-size:11px}
}
</style>
</head>
<body>
<a class="home-link" href="/">🧿 Home</a>

<div class="app-header">
  <div class="app-header-sub">ΕΛΛΗΝΙΚΑ · ΡΗΜΑΤΑ</div>
  <div class="app-header-title">Greek Verb Trainer</div>
</div>

<div class="nav-tabs" id="nav-tabs">
  <button class="nav-tab" id="tab-browse"  onclick="setTab('browse')">Browse</button>
  <button class="nav-tab" id="tab-quiz"    onclick="setTab('quiz')">Quiz</button>
  <button class="nav-tab" id="tab-lists"   onclick="setTab('lists')">Lists</button>
  <button class="nav-tab" id="tab-theory"  onclick="setTab('theory')">Theory</button>
</div>

<div id="app"></div>

<script>
// ── Constants ────────────────────────────────────────────────────────────────
const TENSE_ORDER = [
  'Present','Present Subjunctive','Cont. Imperative','Present Participle',
  'Continuous Past','Continuous Future','Simple Future','Simple Past',
  'Past Subjunctive','Simple Imperative','Simple Infinitive',
  'Present Perfect','Perfect Subjunctive','Perfect Participle',
  'Past Perfect','Future Perfect'
];
const ALL_QUIZZABLE = [
  'Present','Present Subjunctive','Cont. Imperative',
  'Continuous Past','Continuous Future','Simple Future','Simple Past',
  'Past Subjunctive','Present Perfect','Past Perfect','Future Perfect'
];
const DEFAULT_TENSES = ['Present','Simple Past','Simple Future','Present Perfect'];
const PERSON_LABELS  = ['εγώ (1sg)','εσύ (2sg)','αυτός (3sg)','εμείς (1pl)','εσείς (2pl)','αυτοί (3pl)'];
const PERSON_KEYS    = ['1sg','2sg','3sg','1pl','2pl','3pl'];

const TENSE_RULES = {
  'Present': {
    rule: 'pres.stem + ω/εις/ει · ουμε/ετε/ουν  (B: ώ/άς/ά · άμε/άτε/άν)',
    ex:   'γράφω · γράφεις · γράφει · γράφουμε · γράφετε · γράφουν',
  },
  'Present Subjunctive': {
    rule: 'να + Present (same endings)',
    ex:   'να γράφω · να γράφεις · να γράφει · να γράφουμε · να γράφετε · να γράφουν',
  },
  'Cont. Imperative': {
    rule: '2sg: stem+ε (A) or stem+α (B)  ·  2pl: pres.2pl  ·  3rd: ας + pres.',
    ex:   'γράφε · ας γράφει · γράφετε · ας γράφουν',
  },
  'Continuous Past': {
    rule: 'ε- + pres.stem + α/ες/ε · αμε/ατε/αν  (stress shifts back)',
    ex:   'έγραφα · έγραφες · έγραφε · γράφαμε · γράφατε · έγραφαν',
  },
  'Continuous Future': {
    rule: 'θα + Present',
    ex:   'θα γράφω · θα γράφεις · θα γράφει · θα γράφουμε · θα γράφετε · θα γράφουν',
  },
  'Simple Future': {
    rule: 'θα + aor.stem + ω/εις/ει · ουμε/ετε/ουν',
    ex:   'θα γράψω · θα γράψεις · θα γράψει · θα γράψουμε · θα γράψετε · θα γράψουν',
  },
  'Simple Past': {
    rule: 'ε- + aor.stem + α/ες/ε · αμε/ατε/αν',
    ex:   'έγραψα · έγραψες · έγραψε · γράψαμε · γράψατε · έγραψαν',
  },
  'Past Subjunctive': {
    rule: 'να + aor.stem (no augment) + same endings as Simple Past',
    ex:   'να γράψω · να γράψεις · να γράψει · να γράψουμε · να γράψετε · να γράψουν',
  },
  'Present Perfect': {
    rule: 'έχω/έχεις/έχει · έχουμε/έχετε/έχουν + simple infinitive (aor.stem+ει)',
    ex:   'έχω γράψει · έχεις γράψει · έχει γράψει · έχουμε γράψει · έχετε γράψει · έχουν γράψει',
  },
  'Past Perfect': {
    rule: 'είχα/είχες/είχε · είχαμε/είχατε/είχαν + simple infinitive',
    ex:   'είχα γράψει · είχες γράψει · είχε γράψει · είχαμε γράψει · είχατε γράψει · είχαν γράψει',
  },
  'Future Perfect': {
    rule: 'θα έχω/έχεις/έχει · έχουμε/έχετε/έχουν + simple infinitive',
    ex:   'θα έχω γράψει · θα έχεις γράψει · θα έχει γράψει · θα έχουμε γράψει · θα έχετε γράψει · θα έχουν γράψει',
  },
};

const PRESETS = [
  {label:'Beginner',       tenses:['Present','Simple Past','Simple Future']},
  {label:'Core',           tenses:['Present','Continuous Past','Simple Past','Present Perfect','Simple Future']},
  {label:'All indicative', tenses:['Present','Continuous Past','Continuous Future','Simple Past','Simple Future','Present Perfect','Past Perfect','Future Perfect']},
  {label:'Subjunctive',    tenses:['Present Subjunctive','Past Subjunctive']},
  {label:'Everything',     tenses:[...ALL_QUIZZABLE]},
];

// ── Built-in preset lists ─────────────────────────────────────────────────────
const BUILTIN_LISTS = [
  {
    name: 'Common Regular Verbs I',
    pages: [11,13,18,19,21,39,62,65,111,135,158,185,289,294,327,348,378,390,429,538],
  },
  {
    name: 'Common Regular Verbs II',
    pages: [233,252,301,338,341,367,370,373,376,418,432,461,481,491,534,542,547,585,598,601],
  },
  {
    name: 'Common Irregular Verbs I',
    pages: [37,71,79,85,94,151,171,216,224,262,268,275,316,330,355,391,423,428,550,568],
  },
  {
    name: 'Common Irregular Verbs II',
    pages: [233,242,287,293,338,341,356,373,376,380,418,461,491,534,547,565,570,590,598,600],
  },
  {
    name: 'Medio-Passive Verbs',
    pages: [16,67,72,93,105,119,128,210,216,217,245,253,293,300,364,393,469,476,573,590],
  },
  {
    name: 'Common Active Verbs',
    pages: [11,19,85,111,135,158,224,242,262,268,316,348,356,390,391,423,428,547,550,570],
  },
];

const COMPOUND_TENSES = new Set(['Present Perfect','Past Perfect','Future Perfect']);
const AUX_PARADIGMS = {
  'Present Perfect': ['έχω','έχεις','έχει','έχουμε','έχετε','έχουν'],
  'Past Perfect':    ['είχα','είχες','είχε','είχαμε','είχατε','είχαν'],
  'Future Perfect':  ['θα έχω','θα έχεις','θα έχει','θα έχουμε','θα έχετε','θα έχουν'],
};

// ── State ────────────────────────────────────────────────────────────────────
const state = {
  phase:      'browse',
  verbList:   null,
  search:     '',
  browseLimit: 300,
  browseFilter: 'all',   // 'all' | 'selected' | 'unselected'
  selected:   new Set(),
  currentVerb: null,
  quizTenses:  new Set(DEFAULT_TENSES),
  quizType:    'standard',  // 'standard' | 'table'
  quizPersons: 'all',       // 'all' | 'singular' | 'plural'
  quizRandom: false,
  quizCount:  20,
  session:    { questions: [], idx: 0, results: [] },
  feedback:   null,
  tableSession: null,
  tableFeedback: null,
  theoryTab:  'patterns',
  // Lists tab
  savedLists:    null,   // [{name, pages, verbs}]
  listPhase:     'view', // 'view' | 'new' | 'edit'
  editingList:   null,   // {name, pages:Set}
  listSearch:    '',
  activatedLists: new Set(),  // list names checked for concatenation
  expandedLists:  new Set(),  // list names with full verb preview open
  showTenseRef:  false,       // tense reference card in quiz setup
};

// ── Helpers ──────────────────────────────────────────────────────────────────
const API_BASE = '/verb';
async function api(path, opts) {
  const r = await fetch(API_BASE + path, opts);
  return r.json();
}

function mkdiv(cls, ...children) {
  const e = document.createElement('div');
  if (cls) e.className = cls;
  for (const c of children) {
    if (typeof c === 'string') e.innerHTML += c;
    else if (c) e.appendChild(c);
  }
  return e;
}

function mkel(tag, attrs, text) {
  const e = document.createElement(tag);
  if (attrs) Object.assign(e, attrs);
  if (text !== undefined) e.textContent = text;
  return e;
}

function masteryPct(entry) {
  if (!entry.attempts) return 0;
  return Math.round(entry.correct / entry.attempts * 100);
}

function syncTabs() {
  const tabPhases = {
    browse: ['browse','table'],
    quiz:   ['quiz-setup','quiz','feedback','results','table-quiz','table-feedback','table-results'],
    lists:  ['lists'],
    theory: ['theory'],
  };
  ['browse','quiz','lists','theory'].forEach(t => {
    const el = document.getElementById('tab-'+t);
    if (el) el.classList.toggle('active', !!(tabPhases[t]?.includes(state.phase)));
  });
}

function setTab(t) {
  if (t === 'browse')      state.phase = 'browse';
  else if (t === 'quiz')   state.phase = 'quiz-setup';
  else if (t === 'lists')  { state.phase = 'lists'; state.listPhase = 'view'; }
  else if (t === 'theory') state.phase = 'theory';
  render();
}

// ── Render dispatcher ────────────────────────────────────────────────────────
function render() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  const wrap = mkdiv('content');
  const phase = state.phase;
  if      (phase === 'browse')     wrap.appendChild(renderBrowse());
  else if (phase === 'table')      wrap.appendChild(renderTable());
  else if (phase === 'quiz-setup') wrap.appendChild(renderSetup());
  else if (phase === 'quiz')       wrap.appendChild(renderQuiz());
  else if (phase === 'feedback')   wrap.appendChild(renderFeedback());
  else if (phase === 'results')       wrap.appendChild(renderResults());
  else if (phase === 'table-quiz')    wrap.appendChild(renderTableQuiz());
  else if (phase === 'table-feedback') wrap.appendChild(renderTableFeedback());
  else if (phase === 'table-results') wrap.appendChild(renderTableResults());
  else if (phase === 'lists')         wrap.appendChild(renderLists());
  else if (phase === 'theory')     wrap.appendChild(renderTheory());
  app.appendChild(wrap);
  syncTabs();
}

// ── Browse ───────────────────────────────────────────────────────────────────
function renderBrowse() {
  const wrap = mkdiv('');

  if (!state.verbList) {
    wrap.appendChild(mkdiv('loading', 'Loading verbs…'));
    api('/api/verb_list').then(list => { state.verbList = list; if (state.phase === 'browse') render(); });
    return wrap;
  }

  const q = state.search.toLowerCase();
  let visible = state.verbList.filter(v =>
    !q || v.verb.toLowerCase().includes(q) || v.english.toLowerCase().includes(q)
  );
  if (state.browseFilter === 'selected')   visible = visible.filter(v => state.selected.has(v.page));
  if (state.browseFilter === 'unselected') visible = visible.filter(v => !state.selected.has(v.page));

  // Search bar
  const sw = mkdiv('search-wrap');
  sw.innerHTML = '<span class="search-icon">⌕</span>';
  const inp = mkel('input', {type:'text', placeholder:'Search verb or meaning…', value: state.search});
  inp.oninput = e => { state.search = e.target.value; state.browseLimit = 300; render(); };
  setTimeout(() => inp.focus(), 0);
  sw.appendChild(inp);
  wrap.appendChild(sw);

  // Filter row (all / selected / unselected)
  const filterRow = mkdiv('btn-row');
  filterRow.style.cssText = 'margin-bottom:8px;gap:4px';
  for (const [val, lbl] of [['all','All'], ['selected','Selected'], ['unselected','Unselected']]) {
    const b = mkel('button', {className: state.browseFilter === val ? 'btn-primary' : 'btn-ghost'}, lbl);
    b.style.cssText = 'font-size:12px;padding:3px 10px;flex:1';
    b.onclick = () => { state.browseFilter = val; state.browseLimit = 300; render(); };
    filterRow.appendChild(b);
  }
  wrap.appendChild(filterRow);

  // Meta row
  const meta = mkdiv('browse-meta');
  meta.appendChild(mkdiv('', `<span>${visible.length} verbs${state.selected.size ? ' · <b style="color:#c9a96e">' + state.selected.size + ' selected</b>' : ''}</span>`));

  const selAllBtn = mkel('button', {className:'btn-ghost'}, 'Select all');
  selAllBtn.style.cssText = 'font-size:12px;padding:3px 8px';
  selAllBtn.onclick = () => { visible.forEach(v => state.selected.add(v.page)); render(); };
  meta.appendChild(selAllBtn);

  if (state.selected.size) {
    const unselBtn = mkel('button', {className:'btn-ghost'}, 'Unselect all');
    unselBtn.style.cssText = 'font-size:12px;padding:3px 8px';
    unselBtn.onclick = () => { state.selected.clear(); render(); };
    meta.appendChild(unselBtn);

    const qBtn = mkel('button', {className:'btn-secondary'}, `Quiz (${state.selected.size}) →`);
    qBtn.onclick = () => { state.phase = 'quiz-setup'; render(); };
    meta.appendChild(qBtn);
  }
  wrap.appendChild(meta);

  // List
  const list = mkdiv('verb-list');
  for (const entry of visible.slice(0, state.browseLimit)) {
    const isSel = state.selected.has(entry.page);
    const row = mkdiv('verb-row' + (isSel ? ' selected' : ''));

    const chk = mkel('input', {type:'checkbox', checked: isSel});
    chk.onchange = () => {
      if (chk.checked) state.selected.add(entry.page);
      else state.selected.delete(entry.page);
      render();
    };

    row.appendChild(chk);
    row.appendChild(mkdiv('verb-name', entry.verb));
    row.appendChild(mkdiv('verb-en', entry.english));
    row.appendChild(mkdiv('verb-voice', entry.voice === 'Passive' ? 'P' : ''));
    const dotCls = entry.dot ? `dot dot-${entry.dot}` : 'dot dot-none';
    const dotEl  = mkdiv(dotCls);
    if (entry.dot) {
      const tipMap = {new:'New — keep practicing', learning:'Learning', struggling:'Struggling — needs work', mastered:'Mastered'};
      dotEl.title = tipMap[entry.dot] || '';
    }
    row.appendChild(dotEl);

    const viewBtn = mkel('button', {className:'btn-ghost'}, 'View');
    viewBtn.onclick = e => { e.stopPropagation(); loadTable(entry); };
    row.appendChild(viewBtn);

    row.onclick = e => {
      if (e.target === chk || e.target === viewBtn) return;
      chk.checked = !chk.checked; chk.onchange();
    };
    list.appendChild(row);
  }
  if (visible.length > state.browseLimit) {
    const moreRow = mkdiv('verb-row');
    const remaining = visible.length - state.browseLimit;
    const moreBtn = mkel('button', {className:'btn-ghost'}, `Show 100 more  (${remaining} remaining)`);
    moreBtn.style.cssText = 'font-size:12px;padding:4px 12px;width:100%';
    moreBtn.onclick = () => { state.browseLimit += 100; render(); };
    moreRow.appendChild(moreBtn);
    list.appendChild(moreRow);
  }
  wrap.appendChild(list);

  return wrap;
}

// ── Table view ───────────────────────────────────────────────────────────────
async function loadTable(entry) {
  state.phase = 'table';
  state.currentVerb = null;
  render();
  state.currentVerb = await api(`/api/verb/${entry.page}`);
  render();
}

function renderConjTable(verbData, hlTense, hlPerson) {
  const mobile = window.innerWidth <= 480;
  const tbl = mkel('table', {className:'conj-table'});
  const head = tbl.createTHead().insertRow();
  const headers = mobile
    ? ['Tense','','εγώ / εμείς','εσύ / εσείς','αυτός/ή / αυτοί/ές']
    : ['Tense','εγώ','εσύ','αυτός/ή','εμείς','εσείς','αυτοί/ές'];
  for (const h of headers) {
    const th = document.createElement('th');
    th.textContent = h;
    head.appendChild(th);
  }
  const body = tbl.createTBody();
  for (const tname of TENSE_ORDER) {
    const td = verbData.tenses[tname];
    if (!td) continue;

    if (td.forms && td.forms.length === 6) {
      if (mobile) {
        // Singular row — tense cell spans both rows
        const trSg = body.insertRow();
        if (tname === hlTense) trSg.className = 'hl-row';
        const nc = trSg.insertCell();
        nc.textContent = tname;
        nc.rowSpan = 2;
        nc.style.verticalAlign = 'middle';
        if (tname === hlTense) nc.className = 'hl';
        const sgLbl = trSg.insertCell();
        sgLbl.textContent = 'sg';
        sgLbl.style.cssText = 'font-size:9px;color:rgba(255,255,255,.25);font-family:sans-serif;vertical-align:middle;padding-right:2px;white-space:nowrap';
        for (let fi = 0; fi < 3; fi++) {
          const c = trSg.insertCell();
          c.textContent = td.forms[fi];
          c.style.fontFamily = 'Georgia,serif';
          const isHl = tname === hlTense && PERSON_KEYS[fi] === hlPerson;
          if (isHl) { c.className = 'hl'; c.style.fontWeight = 'bold'; }
        }
        // Plural row
        const trPl = body.insertRow();
        trPl.className = (tname === hlTense ? 'hl-row ' : '') + 'pl-row';
        const plLbl = trPl.insertCell();
        plLbl.textContent = 'pl';
        plLbl.style.cssText = 'font-size:9px;color:rgba(255,255,255,.25);font-family:sans-serif;vertical-align:middle;padding-right:2px;white-space:nowrap';
        for (let fi = 3; fi < 6; fi++) {
          const c = trPl.insertCell();
          c.textContent = td.forms[fi];
          c.style.cssText = 'font-family:Georgia,serif;color:rgba(255,255,255,.45)';
          const isHl = tname === hlTense && PERSON_KEYS[fi] === hlPerson;
          if (isHl) { c.className = 'hl'; c.style.color = ''; c.style.fontWeight = 'bold'; }
        }
      } else {
        // Desktop: single row, all 6 forms
        const tr = body.insertRow();
        if (tname === hlTense) tr.className = 'hl-row';
        const nc = tr.insertCell();
        nc.textContent = tname;
        if (tname === hlTense) nc.className = 'hl';
        td.forms.forEach((f, fi) => {
          const c = tr.insertCell();
          c.textContent = f;
          c.style.fontFamily = 'Georgia,serif';
          const isHl = tname === hlTense && PERSON_KEYS[fi] === hlPerson;
          if (isHl) { c.className = 'hl'; c.style.fontWeight = 'bold'; }
          else if (fi >= 3) c.style.color = 'rgba(255,255,255,.45)';
        });
      }
    } else {
      const tr = body.insertRow();
      if (tname === hlTense) tr.className = 'hl-row';
      const nc = tr.insertCell();
      nc.textContent = tname;
      if (tname === hlTense) nc.className = 'hl';
      const c = tr.insertCell();
      c.colSpan = mobile ? 3 : 6;
      c.className = 'special-form';
      c.textContent = td.rows ? td.rows.join(' / ') : '';
    }
  }
  const scroll = document.createElement('div');
  scroll.className = 'conj-table-scroll';
  scroll.appendChild(tbl);
  return scroll;
}

function renderTable() {
  const wrap = mkdiv('');
  if (!state.currentVerb) {
    const br = mkdiv('btn-row');
    const bk = mkel('button', {className:'btn-ghost'}, '← Back');
    bk.onclick = () => { state.phase = 'browse'; render(); };
    br.appendChild(bk);
    wrap.appendChild(br);
    wrap.appendChild(mkdiv('loading', 'Loading…'));
    return wrap;
  }
  const vd = state.currentVerb;

  const topRow = mkdiv('btn-row');
  const bk = mkel('button', {className:'btn-ghost'}, '← Back');
  bk.onclick = () => { state.phase = 'browse'; render(); };
  const qBtn = mkel('button', {className:'btn-secondary'}, 'Quiz this verb');
  qBtn.onclick = () => { state.selected.add(vd.page); state.phase = 'quiz-setup'; render(); };
  topRow.appendChild(bk);
  topRow.appendChild(qBtn);
  wrap.appendChild(topRow);

  wrap.appendChild(mkdiv('verb-header', vd.verb));
  wrap.appendChild(mkdiv('verb-header-sub', `${vd.voice} voice · to ${vd.english}`));
  wrap.appendChild(renderConjTable(vd, null, null));
  return wrap;
}

// ── Quiz Setup ───────────────────────────────────────────────────────────────
function renderSetup() {
  const wrap = mkdiv('');

  const topRow = mkdiv('btn-row');
  const bk = mkel('button', {className:'btn-ghost'}, '← Browse');
  bk.onclick = () => { state.phase = 'browse'; render(); };
  topRow.appendChild(bk);
  wrap.appendChild(topRow);

  // ── Verbs card ──
  const totalVerbs = (state.verbList || []).length;
  const usingAll   = state.selected.size === 0;
  const verbCount  = usingAll ? totalVerbs : state.selected.size;

  const c1 = mkdiv('card');
  c1.appendChild(mkdiv('sec-label', 'Verbs'));
  const vInfo = mkdiv('');
  vInfo.innerHTML = `<span style="font-family:Georgia,serif;font-size:20px;color:#e8c98a">${verbCount}</span>`
    + `<span style="font-size:13px;color:rgba(255,255,255,.4);font-family:sans-serif"> verb${verbCount!==1?'s':''}`
    + (usingAll ? ' <span style="color:rgba(201,169,110,.5)">(all)</span>' : '') + `</span>`
    + ` <a style="font-size:12px;color:#c9a96e;cursor:pointer;font-family:sans-serif;text-decoration:underline" onclick="state.phase='browse';render()">browse</a>`;
  if (!usingAll) {
    vInfo.innerHTML += ` <a style="font-size:12px;color:rgba(255,255,255,.3);cursor:pointer;font-family:sans-serif;text-decoration:underline" onclick="state.selected.clear();render()">unselect all</a>`;
  }
  c1.appendChild(vInfo);

  // Load from list
  const allSetupLists = [...BUILTIN_LISTS, ...(state.savedLists || [])];
  if (allSetupLists.length) {
    const ld = mkdiv('');
    ld.style.cssText = 'margin-top:12px;border-top:1px solid rgba(255,255,255,.06);padding-top:12px';
    ld.appendChild(mkdiv('sec-label', 'Load from list'));
    const lr = mkdiv('setup-preset-row');
    lr.style.cssText = 'flex-wrap:wrap;gap:6px';
    const byPage2 = {};
    if (state.verbList) state.verbList.forEach(v => { byPage2[v.page] = v.verb; });
    for (const lst of allSetupLists) {
      const b = mkel('button', {className:'btn-ghost'}, `${lst.name} (${lst.pages.length})`);
      b.onclick = () => {
        // Toggle: if exactly this list is selected, deselect; otherwise replace
        const cur = [...state.selected].sort().join();
        const next = [...lst.pages].sort().join();
        if (cur === next) { state.selected.clear(); }
        else { state.selected = new Set(lst.pages); }
        render();
      };
      const isActive = lst.pages.length === state.selected.size && lst.pages.every(p => state.selected.has(p));
      if (isActive) b.className = 'btn-secondary';
      lr.appendChild(b);
    }
    ld.appendChild(lr);
    c1.appendChild(ld);
  }
  wrap.appendChild(c1);

  // ── Tenses card ──
  const c2 = mkdiv('card');
  c2.appendChild(mkdiv('sec-label', 'Tenses to practise'));
  const chips = mkdiv('');
  chips.style.cssText = 'display:flex;flex-wrap:wrap;gap:3px;margin-bottom:10px';
  for (const t of ALL_QUIZZABLE) {
    const chip = mkel('button', {className:'pill' + (state.quizTenses.has(t) ? ' on' : '')}, t);
    chip.onclick = () => { state.quizTenses.has(t) ? state.quizTenses.delete(t) : state.quizTenses.add(t); render(); };
    chips.appendChild(chip);
  }
  c2.appendChild(chips);
  const qr = mkdiv('btn-row');
  qr.style.marginTop = '4px';
  for (const [lbl, act] of [
    ['All',  () => { ALL_QUIZZABLE.forEach(t => state.quizTenses.add(t)); render(); }],
    ['None', () => { state.quizTenses.clear(); render(); }],
  ]) {
    const b = mkel('button', {className:'btn-ghost'}, lbl);
    b.onclick = act; qr.appendChild(b);
  }
  c2.appendChild(qr);
  wrap.appendChild(c2);

  // ── Tense reference card ──
  if (state.quizTenses.size > 0) {
    const cRef = mkdiv('card');
    const refHdr = mkdiv('');
    refHdr.style.cssText = 'display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none';
    refHdr.onclick = () => { state.showTenseRef = !state.showTenseRef; render(); };
    const refLbl = mkdiv('sec-label', 'Tense reference (regular verbs)');
    refLbl.style.marginBottom = '0';
    const refTog = mkdiv('');
    refTog.style.cssText = 'font-size:12px;color:#c9a96e;font-family:sans-serif;flex-shrink:0';
    refTog.textContent = state.showTenseRef ? 'hide ▲' : 'show ▼';
    refHdr.appendChild(refLbl);
    refHdr.appendChild(refTog);
    cRef.appendChild(refHdr);

    if (state.showTenseRef) {
      const body = mkdiv('');
      body.style.cssText = 'margin-top:14px;display:flex;flex-direction:column;gap:14px';
      for (const t of ALL_QUIZZABLE) {
        if (!state.quizTenses.has(t)) continue;
        const info = TENSE_RULES[t];
        if (!info) continue;
        const row = mkdiv('');
        row.style.cssText = 'border-left:2px solid rgba(201,169,110,.3);padding-left:10px';
        const name = mkdiv('');
        name.style.cssText = 'font-size:12px;color:#c9a96e;font-family:sans-serif;font-weight:600;margin-bottom:2px';
        name.textContent = t;
        const rule = mkdiv('');
        rule.style.cssText = 'font-size:11px;color:rgba(255,255,255,.45);font-family:sans-serif;margin-bottom:4px';
        rule.textContent = info.rule;
        const ex = mkdiv('');
        ex.style.cssText = 'font-size:12px;color:rgba(255,255,255,.25);font-family:Georgia,serif;line-height:1.5';
        ex.textContent = info.ex;
        row.appendChild(name); row.appendChild(rule); row.appendChild(ex);
        body.appendChild(row);
      }
      cRef.appendChild(body);
    }
    wrap.appendChild(cRef);
  }

  // ── Quiz mode card ──
  const cMode = mkdiv('card');
  cMode.appendChild(mkdiv('sec-label', 'Quiz mode'));
  const modeRow = mkdiv('btn-row');
  modeRow.style.marginTop = '6px';
  for (const [val, lbl, desc] of [
    ['standard', 'Standard', 'One question at a time'],
    ['table',    'Table',    'Fill in all 6 forms at once'],
  ]) {
    const b = mkel('button', {className: state.quizType === val ? 'btn-primary' : 'btn-ghost'}, lbl);
    b.style.cssText = 'flex:1;font-size:13px';
    b.title = desc;
    b.onclick = () => { state.quizType = val; render(); };
    modeRow.appendChild(b);
  }
  cMode.appendChild(modeRow);
  const modeDesc = mkdiv('');
  modeDesc.style.cssText = 'font-size:12px;color:rgba(255,255,255,.3);margin-top:8px;font-family:sans-serif';
  modeDesc.textContent = state.quizType === 'table'
    ? 'Fill in all conjugations for each verb/tense at once. Compound tenses: choose the auxiliary + type the verb form once.'
    : 'One conjugation prompt at a time, in random order.';
  cMode.appendChild(modeDesc);

  // ── Question count ──
  const cCount = mkdiv('card');
  cCount.appendChild(mkdiv('sec-label', 'Questions'));
  const countRow = mkdiv('btn-row');
  countRow.style.marginTop = '6px';
  const allBtn = mkel('button', {className: !state.quizRandom ? 'btn-primary' : 'btn-ghost'}, 'All');
  allBtn.style.flex = '1';
  allBtn.onclick = () => { state.quizRandom = false; render(); };
  countRow.appendChild(allBtn);
  for (const n of [10, 20, 30, 50, 100]) {
    const b = mkel('button', {className: state.quizRandom && state.quizCount === n ? 'btn-primary' : 'btn-ghost'}, String(n));
    b.style.flex = '1';
    b.onclick = () => { state.quizRandom = true; state.quizCount = n; render(); };
    countRow.appendChild(b);
  }
  cCount.appendChild(countRow);
  cMode.appendChild(cCount);
  wrap.appendChild(cMode);

  // ── Persons filter ──
  const cPersons = mkdiv('card');
  cPersons.style.marginTop = '10px';
  cPersons.appendChild(mkdiv('sec-label', 'Persons'));
  const persRow = mkdiv('btn-row');
  persRow.style.marginTop = '6px';
  for (const [val, lbl, sub] of [
    ['all',      'All',      'εγώ · εσύ · αυτός · εμείς · εσείς · αυτοί'],
    ['singular', 'Singular', 'εγώ · εσύ · αυτός/ή/ό'],
    ['plural',   'Plural',   'εμείς · εσείς · αυτοί/ές/ά'],
  ]) {
    const b = mkel('button', {className: state.quizPersons === val ? 'btn-primary' : 'btn-ghost'}, lbl);
    b.style.cssText = 'flex:1;font-size:13px';
    b.title = sub;
    b.onclick = () => { state.quizPersons = val; render(); };
    persRow.appendChild(b);
  }
  cPersons.appendChild(persRow);
  wrap.appendChild(cPersons);

  // ── Tense overview box ──
  const c4 = mkdiv('card');
  c4.appendChild(mkdiv('sec-label', 'Session overview'));

  const tenseList = [...state.quizTenses].filter(t => ALL_QUIZZABLE.includes(t));
  if (tenseList.length === 0) {
    c4.appendChild(mkdiv('', '<span style="font-size:13px;color:rgba(255,255,255,.3);font-family:sans-serif">No tenses selected.</span>'));
  } else {
    const overview = mkdiv('setup-tense-overview');
    for (const t of TENSE_ORDER) {
      if (!state.quizTenses.has(t)) continue;
      overview.appendChild(mkdiv('tov-chip', t));
    }
    c4.appendChild(overview);

    const isTable = state.quizType === 'table';
    const personsMult = state.quizPersons === 'all' ? 6 : 3;
    const personsLabel = state.quizPersons === 'all' ? '6 persons' : state.quizPersons === 'singular' ? '3 singular' : '3 plural';
    const rawCount = isTable ? verbCount * tenseList.length : verbCount * tenseList.length * personsMult;
    const count = state.quizRandom ? Math.min(state.quizCount, rawCount) : rawCount;
    const unit = isTable ? 'table' : 'question';
    const formula = isTable
      ? `${verbCount} verb${verbCount!==1?'s':''} × ${tenseList.length} tense${tenseList.length!==1?'s':''}`
      : `${verbCount} verb${verbCount!==1?'s':''} × ${tenseList.length} tense${tenseList.length!==1?'s':''} × ${personsLabel}`;
    const summ = mkdiv('setup-summary');
    summ.innerHTML = `<div class="setup-summary-big">${state.quizRandom && count < rawCount ? '' : '~'}${count}</div>`
      + `<div class="setup-summary-sub">${state.quizRandom ? `random ${unit}s from ` : ''}${formula}</div>`;
    c4.appendChild(summ);
  }
  wrap.appendChild(c4);

  // ── Start button ──
  const startBtn = mkel('button', {className:'btn-primary'},
    state.quizTenses.size ? 'Start Quiz' : 'Select at least one tense');
  startBtn.disabled = !state.quizTenses.size;
  startBtn.onclick = startQuiz;
  wrap.appendChild(startBtn);
  return wrap;
}

// ── Build questions ───────────────────────────────────────────────────────────
async function startQuiz() {
  if (state.quizType === 'table') { await startTableQuiz(); return; }

  const pages = state.selected.size > 0
    ? [...state.selected]
    : (state.verbList || []).map(v => v.page);
  const verbData = await Promise.all(pages.map(p => api(`/api/verb/${p}`)));
  const questions = [];
  for (const vd of verbData) {
    if (!vd || !vd.tenses) continue;
    for (const tname of TENSE_ORDER) {
      if (!state.quizTenses.has(tname)) continue;
      const td = vd.tenses[tname];
      if (!td || !td.forms || td.forms.length !== 6) continue;
      const personRange = state.quizPersons === 'singular' ? [0,1,2]
                        : state.quizPersons === 'plural'   ? [3,4,5]
                        : [0,1,2,3,4,5];
      for (const pi of personRange) {
        if (!td.forms[pi]) continue;
        questions.push({
          verb: vd.verb, english: vd.english, page: vd.page, voice: vd.voice,
          tense: tname, personIdx: pi, person: PERSON_KEYS[pi],
          personLabel: PERSON_LABELS[pi], answer: td.forms[pi], verbData: vd,
        });
      }
    }
  }
  questions.sort(() => Math.random() - 0.5);
  const finalQ = state.quizRandom ? questions.slice(0, state.quizCount) : questions;
  state.session = { questions: finalQ, idx: 0, results: [] };
  state.phase = 'quiz';
  render();
}

// ── Table quiz ────────────────────────────────────────────────────────────────
async function startTableQuiz() {
  const pages = state.selected.size > 0
    ? [...state.selected]
    : (state.verbList || []).map(v => v.page);
  const verbData = await Promise.all(pages.map(p => api(`/api/verb/${p}`)));
  const pairs = [];
  for (const vd of verbData) {
    if (!vd || !vd.tenses) continue;
    for (const tname of TENSE_ORDER) {
      if (!state.quizTenses.has(tname)) continue;
      const td = vd.tenses[tname];
      if (!td || !td.forms || !td.forms.some(f => f)) continue;
      pairs.push({ verb: vd.verb, english: vd.english, page: vd.page, voice: vd.voice, tense: tname, forms: td.forms, verbData: vd });
    }
  }
  pairs.sort(() => Math.random() - 0.5);
  const finalPairs = state.quizRandom ? pairs.slice(0, state.quizCount) : pairs;
  state.tableSession = { pairs: finalPairs, idx: 0, results: [] };
  state.phase = 'table-quiz';
  render();
}

function renderTableQuiz() {
  const { pairs, idx } = state.tableSession;
  if (!pairs.length) { state.phase = 'browse'; render(); return mkdiv(''); }
  const q = pairs[idx];
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round(idx / pairs.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', `${idx+1} / ${pairs.length} · ${q.voice.toLowerCase()} voice`));
  card.appendChild(mkdiv('quiz-verb', q.verb));
  card.appendChild(mkdiv('quiz-en', 'to ' + q.english));
  card.appendChild(mkdiv('quiz-tense', q.tense));

  const isCompound = COMPOUND_TENSES.has(q.tense);

  if (isCompound) {
    // Extract invariant verb form (last word of first non-empty form)
    const firstForm = q.forms.find(f => f) || '';
    const verbForm = firstForm.split(' ').pop();

    // Auxiliary multiple choice (all 3 compound paradigms)
    const auxSec = mkdiv('sec-label', 'Auxiliary paradigm');
    auxSec.style.marginTop = '14px';
    card.appendChild(auxSec);
    const auxRow = mkdiv('');
    auxRow.style.cssText = 'display:flex;flex-direction:column;gap:7px;margin:8px 0 14px';
    let selectedAux = null;
    const auxBtns = [];
    const auxOptions = Object.keys(AUX_PARADIGMS).sort(() => Math.random() - 0.5);
    for (const opt of auxOptions) {
      const p = AUX_PARADIGMS[opt];
      const btn = mkel('button', {className:'btn-ghost'}, `${p[0]} / ${p[1]} / ${p[2]} / …`);
      btn.style.cssText = 'text-align:left;font-family:Georgia,serif;font-size:14px;padding:9px 14px;border-radius:8px;transition:border-color .15s,background .15s';
      btn.onclick = () => {
        selectedAux = opt;
        auxBtns.forEach(b => { b.style.background = ''; b.style.borderColor = ''; });
        btn.style.background = 'rgba(201,169,110,.13)';
        btn.style.borderColor = '#c9a96e';
      };
      auxBtns.push(btn);
      auxRow.appendChild(btn);
    }
    card.appendChild(auxRow);

    const vfSec = mkdiv('sec-label', 'Verb form (same for all persons)');
    card.appendChild(vfSec);
    const vfInp = mkel('input', {type:'text', className:'quiz-input', placeholder:'type the verb form…'});
    vfInp.setAttribute('autocorrect','off'); vfInp.setAttribute('autocapitalize','none'); vfInp.setAttribute('autocomplete','off'); vfInp.spellcheck = false;
    card.appendChild(vfInp);
    setTimeout(() => vfInp.focus(), 50);

    const br = mkdiv('btn-row');
    const checkBtn = mkel('button', {className:'btn-primary'}, 'Check');
    checkBtn.style.cssText = 'flex:1;margin-top:0';
    const skipBtn = mkel('button', {className:'btn-secondary'}, 'Skip');
    const endBtn  = mkel('button', {className:'btn-ghost'}, 'End');

    async function submitCompound() {
      const guess = vfInp.value.trim();
      if (!guess && selectedAux === null) return;
      const res = guess ? await api('/api/check', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({guess, correct: verbForm, verb: q.verb, tense: q.tense, person: 'form', page: q.page})
      }) : {result:'wrong'};
      const result = { verb: q.verb, english: q.english, tense: q.tense, voice: q.voice,
        auxCorrect: selectedAux === q.tense, auxSelected: selectedAux, auxCorrectTense: q.tense,
        verbFormGuess: guess, verbForm, verbResult: res.result, isCompound: true };
      state.tableSession.results.push(result);
      state.tableFeedback = result;
      state.phase = 'table-feedback';
      render();
    }

    checkBtn.onclick = submitCompound;
    skipBtn.onclick  = () => { state.tableSession.results.push({verb:q.verb,tense:q.tense,result:'skip',isCompound:true}); advanceTableQuiz(); };
    endBtn.onclick   = () => { state.phase = 'table-results'; render(); };
    vfInp.onkeydown  = e => { if (e.key==='Enter') { e.preventDefault(); submitCompound(); } };
    br.appendChild(checkBtn); br.appendChild(skipBtn); br.appendChild(endBtn);
    card.appendChild(br);

  } else {
    // Simple tenses: 6 inputs in a grid (filtered by person selection)
    const personRange = state.quizPersons === 'singular' ? [0,1,2]
                      : state.quizPersons === 'plural'   ? [3,4,5]
                      : [0,1,2,3,4,5];
    const grid = mkdiv('');
    grid.style.cssText = 'display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;margin:16px 0';
    const inputs = [];
    for (const i of personRange) {
      if (!q.forms[i]) continue;
      const lbl = mkel('label', {}, PERSON_LABELS[i]);
      lbl.style.cssText = 'font-size:13px;color:rgba(255,255,255,.5);font-family:sans-serif;white-space:nowrap';
      grid.appendChild(lbl);
      const inp = mkel('input', {type:'text', className:'quiz-input'});
      inp.style.cssText = 'margin:0;width:100%';
      inp.placeholder = '…';
      inp.setAttribute('autocorrect','off'); inp.setAttribute('autocapitalize','none'); inp.setAttribute('autocomplete','off'); inp.spellcheck = false;
      inputs.push({inp, idx: i, answer: q.forms[i]});
      grid.appendChild(inp);
    }
    card.appendChild(grid);
    if (inputs.length) setTimeout(() => inputs[0].inp.focus(), 50);

    const br = mkdiv('btn-row');
    const checkBtn = mkel('button', {className:'btn-primary'}, 'Check');
    checkBtn.style.cssText = 'flex:1;margin-top:0';
    const skipBtn = mkel('button', {className:'btn-secondary'}, 'Skip');
    const endBtn  = mkel('button', {className:'btn-ghost'}, 'End');

    async function submitTable() {
      const checks = await Promise.all(inputs.map(({inp, answer, idx: pi}) =>
        inp.value.trim()
          ? api('/api/check', {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({guess: inp.value.trim(), correct: answer, verb: q.verb, tense: q.tense, person: PERSON_KEYS[pi], page: q.page})
            }).then(res => ({pi, guess: inp.value.trim(), answer, result: res.result, label: PERSON_LABELS[pi]}))
          : Promise.resolve({pi, guess:'', answer, result:'skip', label: PERSON_LABELS[pi]})
      ));
      const tableResult = {verb:q.verb, english:q.english, tense:q.tense, checks, isCompound:false};
      state.tableSession.results.push(tableResult);
      state.tableFeedback = tableResult;
      state.phase = 'table-feedback';
      render();
    }

    checkBtn.onclick = submitTable;
    skipBtn.onclick  = () => { state.tableSession.results.push({verb:q.verb,tense:q.tense,result:'skip',isCompound:false}); advanceTableQuiz(); };
    endBtn.onclick   = () => { state.phase = 'table-results'; render(); };
    inputs.forEach(({inp}, i) => {
      inp.onkeydown = e => {
        if (e.key === 'Enter') { e.preventDefault(); i < inputs.length-1 ? inputs[i+1].inp.focus() : submitTable(); }
        if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); if (i < inputs.length-1) inputs[i+1].inp.focus(); }
      };
    });
    br.appendChild(checkBtn); br.appendChild(skipBtn); br.appendChild(endBtn);
    card.appendChild(br);
  }

  wrap.appendChild(card);
  return wrap;
}

function renderTableFeedback() {
  const fb = state.tableFeedback;
  const { pairs, idx } = state.tableSession;
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round((idx+1) / pairs.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', `${idx+1} / ${pairs.length}`));
  card.appendChild(mkdiv('quiz-verb', fb.verb));
  card.appendChild(mkdiv('quiz-en', 'to ' + fb.english));
  card.appendChild(mkdiv('quiz-tense', fb.tense));

  if (fb.isCompound) {
    const auxDiv = mkdiv('');
    auxDiv.style.cssText = 'margin:14px 0 6px';
    const auxColor = fb.auxCorrect ? '#4caf88' : '#e57373';
    const auxIcon  = fb.auxCorrect ? '✓' : '✗';
    const correctPar = AUX_PARADIGMS[fb.auxCorrectTense];
    auxDiv.innerHTML = `<div style="font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;margin-bottom:4px">Auxiliary</div>`
      + `<span style="color:${auxColor};font-weight:bold;margin-right:6px">${auxIcon}</span>`
      + `<span style="font-family:Georgia,serif;font-size:14px;color:#e8c98a">${correctPar.join(' / ')}</span>`;
    if (!fb.auxCorrect && fb.auxSelected && AUX_PARADIGMS[fb.auxSelected]) {
      const wp = AUX_PARADIGMS[fb.auxSelected];
      auxDiv.innerHTML += `<div style="font-size:12px;color:#e57373;margin-top:3px">You chose: ${wp[0]} / ${wp[1]} / …</div>`;
    } else if (!fb.auxCorrect) {
      auxDiv.innerHTML += `<div style="font-size:12px;color:rgba(255,255,255,.3);margin-top:3px">(nothing selected)</div>`;
    }
    card.appendChild(auxDiv);

    const msgs = {correct:'✓ Correct!', accent:'⟳ Check accent', close:'≈ Almost', wrong:'✗ Wrong'};
    const vfDiv = mkdiv('');
    vfDiv.style.cssText = 'margin:10px 0';
    vfDiv.innerHTML = `<div style="font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;margin-bottom:4px">Verb form</div>`;
    const fbEl = mkdiv('feedback ' + fb.verbResult);
    fbEl.appendChild(mkdiv('feedback-verdict', msgs[fb.verbResult] || ''));
    if (fb.verbResult !== 'correct') fbEl.appendChild(mkdiv('feedback-form', '→ ' + fb.verbForm));
    else fbEl.appendChild(mkdiv('feedback-form', fb.verbFormGuess));
    vfDiv.appendChild(fbEl);
    card.appendChild(vfDiv);

  } else {
    const resColors = {correct:'#4caf88', accent:'#f0b429', close:'#f0b429', wrong:'#e57373', skip:'#888'};
    const resIcons  = {correct:'✓', accent:'≈', close:'≈', wrong:'✗', skip:'–'};
    const grid = mkdiv('');
    grid.style.cssText = 'display:grid;grid-template-columns:auto 1fr auto;gap:7px 12px;align-items:baseline;margin:14px 0';
    for (const chk of fb.checks) {  // checks only includes the practiced persons
      const lbl = mkel('span', {}, chk.label);
      lbl.style.cssText = 'font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;white-space:nowrap';
      grid.appendChild(lbl);
      const ans = document.createElement('div');
      ans.style.cssText = 'font-family:Georgia,serif;font-size:15px;display:flex;align-items:baseline;gap:5px;flex-wrap:wrap';
      const needsCorrection = (chk.result === 'wrong' || chk.result === 'accent' || chk.result === 'close') && chk.guess;
      if (needsCorrection) {
        const g = mkel('span', {}, chk.guess);
        g.style.cssText = 'color:#e57373;text-decoration:line-through;opacity:.75';
        const arr = mkel('span', {}, '→');
        arr.style.cssText = 'color:rgba(255,255,255,.3);font-size:11px;font-family:sans-serif';
        const cor = mkel('span', {}, chk.answer);
        cor.style.cssText = 'color:#e8c98a';
        ans.appendChild(g); ans.appendChild(arr); ans.appendChild(cor);
      } else {
        const cor = mkel('span', {}, chk.answer);
        cor.style.cssText = chk.result === 'correct' ? 'color:#7ac49a' : 'color:#e8c98a';
        ans.appendChild(cor);
      }
      grid.appendChild(ans);
      const icon = mkel('span', {}, resIcons[chk.result] || '?');
      icon.style.cssText = `color:${resColors[chk.result]||'#fff'};font-weight:bold;font-size:14px`;
      grid.appendChild(icon);
    }
    card.appendChild(grid);
  }

  const br = mkdiv('btn-row');
  const nextBtn = mkel('button', {className:'btn-primary'},
    idx + 1 >= pairs.length ? 'See results →' : 'Next →');
  nextBtn.style.cssText = 'flex:1;margin-top:0';
  nextBtn.onclick = advanceTableQuiz;
  const endBtn = mkel('button', {className:'btn-ghost'}, 'End session');
  endBtn.onclick = () => { state.phase = 'table-results'; render(); };
  br.appendChild(nextBtn); br.appendChild(endBtn);
  card.appendChild(br);

  const handler = e => {
    if (e.key==='Enter') { e.preventDefault(); document.removeEventListener('keydown', handler); advanceTableQuiz(); }
  };
  document.addEventListener('keydown', handler);

  wrap.appendChild(card);
  return wrap;
}

function advanceTableQuiz() {
  state.tableSession.idx++;
  const done = state.tableSession.idx >= state.tableSession.pairs.length;
  if (done) state.verbList = null; // force refresh so dots update in Browse
  state.phase = done ? 'table-results' : 'table-quiz';
  render();
}

function renderTableResults() {
  const { results } = state.tableSession;
  const wrap = mkdiv('');
  let totalItems = 0, correctItems = 0;
  for (const r of results) {
    if (r.result === 'skip') continue;
    if (r.isCompound) {
      totalItems += 2;
      if (r.auxCorrect) correctItems++;
      if (r.verbResult === 'correct' || r.verbResult === 'accent') correctItems++;
    } else if (r.checks) {
      for (const c of r.checks) {
        totalItems++;
        if (c.result === 'correct' || c.result === 'accent') correctItems++;
      }
    }
  }
  const pct = totalItems > 0 ? Math.round(correctItems / totalItems * 100) : 0;
  wrap.appendChild(mkdiv('score-big', `${pct}%`));
  wrap.appendChild(mkdiv('score-sub', `${correctItems} / ${totalItems} correct · ${results.length} tables`));

  const icons = {correct:'✓', accent:'≈', close:'≈', wrong:'✗', skip:'–'};
  for (const r of results) {
    if (r.result === 'skip') {
      const row = mkdiv('result-row');
      row.appendChild(mkdiv('result-icon', '–'));
      row.appendChild(mkdiv('result-verb', r.verb));
      row.appendChild(mkdiv('result-tense', r.tense));
      row.appendChild(mkdiv('result-person', '(skipped)'));
      row.appendChild(mkdiv('result-guess skip', ''));
      wrap.appendChild(row);
      continue;
    }
    if (r.isCompound) {
      const row = mkdiv('result-row');
      const ok = r.auxCorrect && (r.verbResult === 'correct' || r.verbResult === 'accent');
      row.appendChild(mkdiv('result-icon', ok ? '✓' : '✗'));
      row.appendChild(mkdiv('result-verb', r.verb));
      row.appendChild(mkdiv('result-tense', r.tense));
      row.appendChild(mkdiv('result-person', 'compound'));
      const g = mkdiv('result-guess ' + (ok ? 'correct' : 'wrong'), r.verbFormGuess || '(blank)');
      if (!ok) g.title = `Correct form: ${r.verbForm}`;
      row.appendChild(g);
      wrap.appendChild(row);
    } else if (r.checks) {
      let allOk = true;
      for (const c of r.checks) {
        if (c.result !== 'correct' && c.result !== 'accent') { allOk = false; break; }
      }
      const row = mkdiv('result-row');
      row.appendChild(mkdiv('result-icon', allOk ? '✓' : '✗'));
      row.appendChild(mkdiv('result-verb', r.verb));
      row.appendChild(mkdiv('result-tense', r.tense));
      row.appendChild(mkdiv('result-person', 'all persons'));
      row.appendChild(mkdiv('result-guess ' + (allOk ? 'correct' : 'wrong'),
        allOk ? '✓ all correct' : r.checks.filter(c => c.result !== 'correct' && c.result !== 'accent').length + ' error(s)'));
      wrap.appendChild(row);
    }
  }

  const br = mkdiv('btn-row');
  const retryBtn = mkel('button', {className:'btn-primary'}, 'New Quiz →');
  retryBtn.style.cssText = 'flex:1;margin-top:0';
  retryBtn.onclick = () => { state.phase = 'quiz-setup'; render(); };
  const browseBtn = mkel('button', {className:'btn-secondary'}, 'Browse verbs');
  browseBtn.onclick = () => { state.phase = 'browse'; render(); };
  br.appendChild(retryBtn); br.appendChild(browseBtn);
  wrap.appendChild(br);
  return wrap;
}

// ── Quiz question ─────────────────────────────────────────────────────────────
function renderQuiz() {
  const { questions, idx } = state.session;
  if (!questions.length) { state.phase = 'browse'; render(); return mkdiv(''); }
  const q = questions[idx];
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round(idx / questions.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', `${idx+1} / ${questions.length} · ${q.voice.toLowerCase()} voice`));
  card.appendChild(mkdiv('quiz-verb', q.verb));
  card.appendChild(mkdiv('quiz-en', 'to ' + q.english));
  card.appendChild(mkdiv('quiz-tense', q.tense));
  card.appendChild(mkdiv('quiz-person', q.personLabel));

  const inp = mkel('input', {type:'text', className:'quiz-input', placeholder:'type the form…'});
  inp.setAttribute('autocorrect','off'); inp.setAttribute('autocapitalize','none'); inp.setAttribute('autocomplete','off'); inp.spellcheck = false;
  setTimeout(() => inp.focus(), 0);
  card.appendChild(inp);

  const br = mkdiv('btn-row');
  const checkBtn = mkel('button', {className:'btn-primary'}, 'Check');
  checkBtn.style.flex = '1'; checkBtn.style.marginTop = '0';
  const skipBtn = mkel('button', {className:'btn-secondary'}, 'Skip');
  const endBtn  = mkel('button', {className:'btn-ghost'}, 'End');

  async function submit() {
    const guess = inp.value.trim();
    if (!guess) return;
    const res = await api('/api/check', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({guess, correct: q.answer, verb: q.verb, tense: q.tense, person: q.person, page: q.page})
    });
    state.session.results.push({...q, guess, result: res.result});
    state.feedback = {q, guess, result: res.result};
    state.phase = 'feedback';
    render();
  }

  checkBtn.onclick = submit;
  skipBtn.onclick  = () => { state.session.results.push({...q, guess:'', result:'skip'}); advanceQuiz(); };
  endBtn.onclick   = () => { state.phase = 'results'; render(); };
  inp.onkeydown    = e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } };

  br.appendChild(checkBtn); br.appendChild(skipBtn); br.appendChild(endBtn);
  card.appendChild(br);
  wrap.appendChild(card);
  return wrap;
}

// ── Feedback ──────────────────────────────────────────────────────────────────
function renderFeedback() {
  const { q, guess, result } = state.feedback;
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round((state.session.idx+1) / state.session.questions.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', `${state.session.idx+1} / ${state.session.questions.length}`));
  card.appendChild(mkdiv('quiz-verb', q.verb));
  card.appendChild(mkdiv('quiz-en', 'to ' + q.english));
  card.appendChild(mkdiv('quiz-tense', q.tense));
  card.appendChild(mkdiv('quiz-person', q.personLabel));

  const msgs = {correct:'✓ Correct!', accent:'⟳ Right word — check accent', close:'≈ Almost there', wrong:'✗ Wrong'};
  const fb = mkdiv('feedback ' + result);
  fb.appendChild(mkdiv('feedback-verdict', msgs[result] || ''));
  if (result !== 'correct') fb.appendChild(mkdiv('feedback-form', '→ ' + q.answer));
  card.appendChild(fb);

  const br = mkdiv('btn-row');
  const nextBtn = mkel('button', {className:'btn-primary'}, 'Next →');
  nextBtn.style.cssText = 'flex:1;margin-top:0';
  nextBtn.onclick = advanceQuiz;
  const endBtn = mkel('button', {className:'btn-ghost'}, 'End session');
  endBtn.onclick = () => { state.phase = 'results'; render(); };
  br.appendChild(nextBtn); br.appendChild(endBtn);
  card.appendChild(br);
  wrap.appendChild(card);

  if (q.verbData) {
    const tw = mkdiv('mini-table-wrap');
    tw.appendChild(mkdiv('mini-label', 'Full Conjugation'));
    tw.appendChild(renderConjTable(q.verbData, q.tense, q.person));
    wrap.appendChild(tw);
  }

  const handler = e => {
    if (e.key === 'Enter') { e.preventDefault(); document.removeEventListener('keydown', handler); advanceQuiz(); }
  };
  document.addEventListener('keydown', handler);
  return wrap;
}

function advanceQuiz() {
  state.session.idx++;
  const done = state.session.idx >= state.session.questions.length;
  if (done) state.verbList = null; // force refresh so dots update in Browse
  state.phase = done ? 'results' : 'quiz';
  render();
}

// ── Results ───────────────────────────────────────────────────────────────────
function renderResults() {
  const { results } = state.session;
  const wrap = mkdiv('');
  const total   = results.length;
  const correct = results.filter(r => r.result === 'correct').length;
  const close   = results.filter(r => ['accent','close'].includes(r.result)).length;
  const wrong   = results.filter(r => r.result === 'wrong').length;

  wrap.appendChild(mkdiv('score-big', `${correct}/${total}`));
  wrap.appendChild(mkdiv('score-sub', `${correct} correct · ${close} close · ${wrong} wrong · ${total-correct-close-wrong} skipped`));

  const icons = {correct:'✓', accent:'⟳', close:'≈', wrong:'✗', skip:'–'};
  for (const r of results) {
    const row = mkdiv('result-row');
    row.appendChild(mkdiv('result-icon', icons[r.result] || '?'));
    row.appendChild(mkdiv('result-verb', r.verb));
    row.appendChild(mkdiv('result-tense', r.tense));
    row.appendChild(mkdiv('result-person', r.personLabel));
    const g = mkdiv('result-guess ' + r.result, r.guess || '(skipped)');
    if (r.result !== 'correct' && r.result !== 'skip') g.title = 'Correct: ' + r.answer;
    row.appendChild(g);
    wrap.appendChild(row);
  }

  const br = mkdiv('btn-row');
  const retryBtn = mkel('button', {className:'btn-primary'}, 'Retry session');
  retryBtn.style.cssText = 'flex:1;margin-top:0';
  retryBtn.onclick = startQuiz;
  const browseBtn = mkel('button', {className:'btn-secondary'}, 'Browse verbs');
  browseBtn.onclick = () => { state.phase = 'browse'; render(); };
  br.appendChild(retryBtn); br.appendChild(browseBtn);
  wrap.appendChild(br);
  return wrap;
}

// ── Lists tab ─────────────────────────────────────────────────────────────────
function renderLists() {
  const wrap = mkdiv('');

  if (!state.verbList) {
    wrap.appendChild(mkdiv('loading', 'Loading verbs…'));
    api('/api/verb_list').then(list => { state.verbList = list; if (state.phase === 'lists') render(); });
    return wrap;
  }

  // ── New / edit form ──
  if (state.listPhase === 'new' || state.listPhase === 'edit') {
    const editing = state.editingList;
    const isEdit  = state.listPhase === 'edit';

    const form = mkdiv('list-new-form');
    form.appendChild(mkdiv('sec-label', isEdit ? `Edit list: ${editing.name}` : 'New verb list'));

    // Name input (read-only in edit mode)
    const nameInp = mkel('input', {type:'text', placeholder:'List name…', value: editing.name});
    if (isEdit) nameInp.readOnly = true;
    form.appendChild(nameInp);

    // Verb count badge
    const badge = mkdiv('');
    badge.style.cssText = 'font-size:12px;color:#c9a96e;font-family:sans-serif;margin-bottom:8px';
    const updateBadge = () => { badge.textContent = `${editing.pages.size} verb${editing.pages.size!==1?'s':''} in list`; };
    updateBadge();
    form.appendChild(badge);

    // Search within picker
    const listSW = mkdiv('search-wrap');
    listSW.innerHTML = '<span class="search-icon">⌕</span>';
    const listSrch = mkel('input', {type:'text', placeholder:'Filter verbs…', value: state.listSearch});
    listSrch.oninput = e => { state.listSearch = e.target.value; renderPickerRows(); };
    listSW.appendChild(listSrch);
    form.appendChild(listSW);

    const picker = mkdiv('list-verb-pick');
    form.appendChild(picker);

    function renderPickerRows() {
      const q = state.listSearch.toLowerCase();
      const filtered = state.verbList.filter(v =>
        !q || v.verb.toLowerCase().includes(q) || v.english.toLowerCase().includes(q)
      ).slice(0, 200);
      picker.innerHTML = '';
      for (const v of filtered) {
        const inList = editing.pages.has(v.page);
        const row = mkdiv('list-verb-row' + (inList ? ' in-list' : ''));
        const chk = mkel('input', {type:'checkbox', checked: inList});
        chk.onchange = () => {
          if (chk.checked) editing.pages.add(v.page);
          else editing.pages.delete(v.page);
          row.className = 'list-verb-row' + (chk.checked ? ' in-list' : '');
          updateBadge();
        };
        row.appendChild(chk);
        row.appendChild(mkdiv('lv-name', v.verb));
        row.appendChild(mkdiv('lv-en', v.english));
        row.onclick = e => { if (e.target === chk) return; chk.checked = !chk.checked; chk.onchange(); };
        picker.appendChild(row);
      }
      if (state.verbList.length > 200 && filtered.length === 200) {
        picker.appendChild(mkdiv('list-verb-row', '<span style="color:rgba(255,255,255,.25);font-family:sans-serif;font-size:11px">Filter to see more…</span>'));
      }
    }
    renderPickerRows();
    setTimeout(() => listSrch.focus(), 0);

    const br = mkdiv('btn-row');
    const saveBtn = mkel('button', {className:'btn-primary'}, isEdit ? 'Save changes' : 'Save list');
    saveBtn.style.cssText = 'flex:1;margin-top:0';
    saveBtn.onclick = async () => {
      const name = nameInp.value.trim();
      if (!name) { nameInp.focus(); return; }
      const pages   = [...editing.pages];
      const verbs   = (state.verbList || []).filter(v => editing.pages.has(v.page)).map(v => v.verb);
      await api('/api/presets/save', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name, pages, verbs})
      });
      state.savedLists = await api('/api/presets');
      state.listPhase = 'view';
      state.listSearch = '';
      render();
    };
    const cancelBtn = mkel('button', {className:'btn-ghost'}, 'Cancel');
    cancelBtn.onclick = () => { state.listPhase = 'view'; state.listSearch = ''; render(); };
    br.appendChild(saveBtn); br.appendChild(cancelBtn);
    form.appendChild(br);
    wrap.appendChild(form);
    return wrap;
  }

  // ── List view ──

  // Build page→verb lookup from verbList
  const byPage = {};
  if (state.verbList) state.verbList.forEach(v => { byPage[v.page] = v.verb; });

  function verbNamesForList(lst) {
    return lst.pages.map(p => byPage[p]).filter(Boolean);
  }

  function renderListCard(lst, opts = {}) {
    const { isBuiltin, quizOnly } = opts;
    const card = mkdiv('list-card');
    const hdr = mkdiv('list-card-header');

    // Checkbox for concatenation
    const chkWrap = mkdiv('');
    chkWrap.style.cssText = 'display:flex;align-items:center;gap:10px;flex:1;min-width:0';
    const chk = mkel('input', {type:'checkbox', checked: state.activatedLists.has(lst.name)});
    chk.style.cssText = 'width:16px;height:16px;flex-shrink:0;cursor:pointer;accent-color:#c9a96e';
    chk.onchange = () => {
      if (chk.checked) state.activatedLists.add(lst.name);
      else state.activatedLists.delete(lst.name);
      render();
    };
    const nameWrap = mkdiv('');
    nameWrap.appendChild(mkdiv('list-card-name', lst.name));
    nameWrap.appendChild(mkdiv('list-card-count', `${lst.pages.length} verb${lst.pages.length!==1?'s':''}`));
    chkWrap.appendChild(chk);
    chkWrap.appendChild(nameWrap);
    hdr.appendChild(chkWrap);
    card.appendChild(hdr);

    // Verb preview — all verbs, expandable
    const verbNames = verbNamesForList(lst);
    const FOLD = 8;
    const isExpanded = state.expandedLists.has(lst.name);
    const shown = isExpanded ? verbNames : verbNames.slice(0, FOLD);
    const verbDiv = mkdiv('list-card-verbs', shown.join(', ') + (verbNames.length > FOLD && !isExpanded ? ', …' : ''));
    card.appendChild(verbDiv);
    if (verbNames.length > FOLD) {
      const tog = mkel('a', {}, isExpanded ? 'show less ▲' : `+${verbNames.length - FOLD} more ▼`);
      tog.style.cssText = 'cursor:pointer;font-size:11px;color:#c9a96e;font-family:sans-serif;display:inline-block;margin-bottom:10px';
      tog.onclick = e => {
        e.stopPropagation();
        if (isExpanded) state.expandedLists.delete(lst.name);
        else state.expandedLists.add(lst.name);
        render();
      };
      card.appendChild(tog);
    }

    // Actions
    const actions = mkdiv('list-card-actions');
    const quizBtn = mkel('button', {className:'btn-secondary'}, 'Quiz →');
    quizBtn.onclick = () => { state.selected = new Set(lst.pages); state.phase = 'quiz-setup'; render(); };
    actions.appendChild(quizBtn);
    if (!isBuiltin) {
      const browseBtn = mkel('button', {className:'btn-ghost'}, 'Browse');
      browseBtn.onclick = () => { state.selected = new Set(lst.pages); state.phase = 'browse'; render(); };
      actions.appendChild(browseBtn);
    }
    if (!quizOnly) {
      const editBtn = mkel('button', {className:'btn-ghost'}, 'Edit');
      editBtn.onclick = () => { state.listPhase = 'edit'; state.editingList = {name: lst.name, pages: new Set(lst.pages)}; state.listSearch = ''; render(); };
      actions.appendChild(editBtn);
      const delBtn = mkel('button', {className:'btn-ghost'}, 'Delete');
      delBtn.style.color = 'rgba(212,122,143,.7)';
      delBtn.onclick = async () => {
        if (!confirm(`Delete "${lst.name}"?`)) return;
        await api('/api/presets/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:lst.name})});
        state.activatedLists.delete(lst.name);
        state.savedLists = await api('/api/presets');
        render();
      };
      actions.appendChild(delBtn);
    }
    card.appendChild(actions);
    return card;
  }

  // ── Quiz N lists banner ──
  if (state.activatedLists.size > 0) {
    const banner = mkdiv('');
    banner.style.cssText = 'background:rgba(201,169,110,.1);border:1px solid rgba(201,169,110,.3);border-radius:12px;padding:12px 14px;margin-bottom:16px;display:flex;align-items:center;gap:10px';
    const allLists = [...BUILTIN_LISTS, ...(state.savedLists || [])];
    const totalPages = new Set(allLists.filter(l => state.activatedLists.has(l.name)).flatMap(l => l.pages));
    const info = mkdiv('');
    info.style.cssText = 'flex:1;font-family:sans-serif';
    info.innerHTML = `<span style="color:#e8c98a;font-size:14px">${totalPages.size} verbs</span>`
      + `<span style="color:rgba(255,255,255,.4);font-size:12px"> from ${state.activatedLists.size} list${state.activatedLists.size>1?'s':''}</span>`;
    const quizAllBtn = mkel('button', {className:'btn-primary'}, 'Quiz selection →');
    quizAllBtn.style.cssText = 'flex-shrink:0';
    quizAllBtn.onclick = () => {
      state.selected = totalPages;
      state.activatedLists.clear();
      state.phase = 'quiz-setup';
      render();
    };
    const clearBtn = mkel('button', {className:'btn-ghost'}, '✕');
    clearBtn.onclick = () => { state.activatedLists.clear(); render(); };
    banner.appendChild(info);
    banner.appendChild(quizAllBtn);
    banner.appendChild(clearBtn);
    wrap.appendChild(banner);
  }

  // ── Built-in lists ──
  wrap.appendChild(mkdiv('sec-label', 'Built-in Lists'));
  for (const lst of BUILTIN_LISTS) {
    wrap.appendChild(renderListCard(lst, {isBuiltin: true, quizOnly: true}));
  }

  // ── User lists ──
  const topRow = mkdiv('btn-row');
  topRow.style.cssText = 'margin-top:20px;margin-bottom:14px';
  wrap.appendChild(mkdiv('sec-label', 'Your Lists'));
  const newBtn = mkel('button', {className:'btn-secondary'}, '+ New list');
  newBtn.onclick = () => { state.listPhase = 'new'; state.editingList = {name:'',pages:new Set()}; state.listSearch=''; render(); };
  topRow.appendChild(newBtn);
  wrap.appendChild(topRow);

  if (!state.savedLists || !state.savedLists.length) {
    const empty = mkdiv('card');
    empty.innerHTML = `<div style="padding:14px 0">
      <div style="font-size:13px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-bottom:6px">No saved lists yet.</div>
      <div style="font-size:12px;color:rgba(255,255,255,.2);font-family:sans-serif">Create a list to quickly load a custom set of verbs for quiz practice.</div>
    </div>`;
    wrap.appendChild(empty);
    return wrap;
  }

  for (const lst of state.savedLists) {
    wrap.appendChild(renderListCard(lst));
  }
  return wrap;
}

// ── Theory ────────────────────────────────────────────────────────────────────
function renderTheory() {
  const wrap = mkdiv('');
  const tabs = mkdiv('theory-tabs');
  for (const [id, label] of [['patterns','Conjugation Patterns'],['tenses','Tense Usage']]) {
    const btn = mkel('button', {className:'theory-tab' + (state.theoryTab === id ? ' active' : '')}, label);
    btn.onclick = () => { state.theoryTab = id; render(); };
    tabs.appendChild(btn);
  }
  wrap.appendChild(tabs);
  wrap.appendChild(state.theoryTab === 'patterns' ? renderPatterns() : renderTenseUsage());
  return wrap;
}

// ── Theory: Conjugation Patterns ─────────────────────────────────────────────
function renderPatterns() {
  const wrap = mkdiv('');

  const intro = mkdiv('card');
  intro.innerHTML = `
    <div class="theory-h1">Conjugation Patterns</div>
    <p class="theory-p">Greek verbs belong to two groups based on their first-person singular. Knowing the group gives you the endings for every tense — most irregularity is in the stem, not the endings.</p>`;
  wrap.appendChild(intro);

  // Group 1
  const g1 = mkdiv('card');
  g1.innerHTML = `
    <div class="theory-h2">Group 1 · Active  (-ω, unstressed)</div>
    <p class="theory-p">Ends in unaccented <b style="color:#e8c98a">-ω</b>. Stress stays on the same syllable throughout the present. Examples: <span style="font-family:Georgia,serif;color:#e8c98a">γράφω</span> write, <span style="font-family:Georgia,serif;color:#e8c98a">πίνω</span> drink, <span style="font-family:Georgia,serif;color:#e8c98a">βλέπω</span> see.</p>
    <div class="paradigm">
      <div class="paradigm-title">Present — γράφω</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">γράφ<span class="end">ω</span></td><td class="tr">I write / am writing</td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">γράφ<span class="end">εις</span></td><td class="tr">you write</td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">γράφ<span class="end">ει</span></td><td class="tr">he/she writes</td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">γράφ<span class="end">ουμε</span></td><td class="tr">we write</td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">γράφ<span class="end">ετε</span></td><td class="tr">you (pl) write</td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">γράφ<span class="end">ουν</span></td><td class="tr">they write</td></tr>
      </table>
    </div>
    <div class="paradigm">
      <div class="paradigm-title">Simple Past (Aorist) — έγραψα  ·  aorist stem: γραψ-</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">έγραψ<span class="end">α</span></td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">έγραψ<span class="end">ες</span></td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">έγραψ<span class="end">ε</span></td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">γράψ<span class="end">αμε</span></td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">γράψ<span class="end">ατε</span></td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">έγραψ<span class="end">αν</span></td></tr>
      </table>
    </div>
    <p class="theory-note">The aorist stem (γραψ-) often differs from the present stem (γραφ-). The augment ε- moves stress to the front in the singular.</p>`;
  wrap.appendChild(g1);

  // Group 2
  const g2 = mkdiv('card');
  g2.innerHTML = `
    <div class="theory-h2">Group 2 · Active  (-ώ / -άω, stressed)</div>
    <p class="theory-p">Ends in accented <b style="color:#e8c98a">-ώ</b> or <b style="color:#e8c98a">-άω</b>. Examples: <span style="font-family:Georgia,serif;color:#e8c98a">αγαπώ</span> love, <span style="font-family:Georgia,serif;color:#e8c98a">μιλώ</span> speak, <span style="font-family:Georgia,serif;color:#e8c98a">περνώ</span> pass.</p>
    <div class="paradigm">
      <div class="paradigm-title">Present — αγαπώ</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">αγαπ<span class="end">ώ</span></td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">αγαπ<span class="end">άς</span></td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">αγαπ<span class="end">ά</span></td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">αγαπ<span class="end">άμε</span></td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">αγαπ<span class="end">άτε</span></td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">αγαπ<span class="end">άν(ε)</span></td></tr>
      </table>
    </div>
    <div class="paradigm">
      <div class="paradigm-title">Continuous Past — αγαπούσα</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">αγαπ<span class="end">ούσα</span></td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">αγαπ<span class="end">ούσες</span></td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">αγαπ<span class="end">ούσε</span></td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">αγαπ<span class="end">ούσαμε</span></td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">αγαπ<span class="end">ούσατε</span></td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">αγαπ<span class="end">ούσαν</span></td></tr>
      </table>
    </div>`;
  wrap.appendChild(g2);

  // Passive
  const gp = mkdiv('card');
  gp.innerHTML = `
    <div class="theory-h2">Passive Voice  (-ομαι / -ιέμαι)</div>
    <p class="theory-p">Passive verbs can be truly passive (being done to), reflexive (doing to oneself), or deponent (passive form, active meaning). Group 1 passive ends in <b style="color:#e8c98a">-ομαι</b>, Group 2 in <b style="color:#e8c98a">-ιέμαι</b>.</p>
    <div class="paradigm">
      <div class="paradigm-title">Group 1 Passive — έρχομαι (to come)</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">έρχ<span class="end">ομαι</span></td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">έρχ<span class="end">εσαι</span></td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">έρχ<span class="end">εται</span></td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">ερχ<span class="end">όμαστε</span></td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">έρχ<span class="end">εστε</span></td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">έρχ<span class="end">ονται</span></td></tr>
      </table>
    </div>
    <div class="paradigm">
      <div class="paradigm-title">Group 2 Passive — αγαπιέμαι (to be loved)</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr">αγαπ<span class="end">ιέμαι</span></td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr">αγαπ<span class="end">ιέσαι</span></td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr">αγαπ<span class="end">ιέται</span></td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr">αγαπ<span class="end">ιόμαστε</span></td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr">αγαπ<span class="end">ιέστε</span></td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr">αγαπ<span class="end">ιούνται</span></td></tr>
      </table>
    </div>`;
  wrap.appendChild(gp);

  // Perfect tenses
  const gperf = mkdiv('card');
  gperf.innerHTML = `
    <div class="theory-h2">Perfect Tenses — έχω + Simple Infinitive</div>
    <p class="theory-p">All perfect tenses are formed with <span style="font-family:Georgia,serif;color:#e8c98a">έχω</span> (have) + the <b>Simple Infinitive</b> — the 3sg aorist form without augment, e.g. <span style="font-family:Georgia,serif;color:#e8c98a">γράψει</span>, <span style="font-family:Georgia,serif;color:#e8c98a">αγαπήσει</span>. Only the auxiliary changes.</p>
    <div class="paradigm">
      <div class="paradigm-title">Present Perfect — έχω γράψει (I have written)</div>
      <table>
        <tr><td class="lbl">εγώ</td><td class="gr"><span class="end">έχω</span> γράψει</td></tr>
        <tr><td class="lbl">εσύ</td><td class="gr"><span class="end">έχεις</span> γράψει</td></tr>
        <tr><td class="lbl">αυτός</td><td class="gr"><span class="end">έχει</span> γράψει</td></tr>
        <tr><td class="lbl">εμείς</td><td class="gr"><span class="end">έχουμε</span> γράψει</td></tr>
        <tr><td class="lbl">εσείς</td><td class="gr"><span class="end">έχετε</span> γράψει</td></tr>
        <tr><td class="lbl">αυτοί</td><td class="gr"><span class="end">έχουν</span> γράψει</td></tr>
      </table>
    </div>
    <p class="theory-note">Past Perfect: <span style="font-family:Georgia,serif">είχα/είχες/είχε/είχαμε/είχατε/είχαν</span> + infinitive.<br>Future Perfect: <span style="font-family:Georgia,serif">θα έχω/θα έχεις…</span> + infinitive.</p>`;
  wrap.appendChild(gperf);

  // Stem changes
  const gstem = mkdiv('card');
  gstem.innerHTML = `
    <div class="theory-h2">Common Stem Changes (Present → Aorist)</div>
    <p class="theory-p">The main irregularity in Greek verbs is the aorist stem. Here are the most common patterns:</p>
    <div class="paradigm">
      <table>
        <tr><td class="lbl" style="width:90px;color:rgba(201,169,110,.6);font-family:sans-serif;font-size:10px;text-transform:uppercase">Pattern</td><td class="lbl" style="color:rgba(201,169,110,.6);font-family:sans-serif;font-size:10px;text-transform:uppercase">Present</td><td class="lbl" style="color:rgba(201,169,110,.6);font-family:sans-serif;font-size:10px;text-transform:uppercase">Aorist stem</td></tr>
        <tr><td class="lbl">-φ- → -ψ-</td><td class="gr">γράφω</td><td class="gr">γραψ-</td></tr>
        <tr><td class="lbl">-π- → -ψ-</td><td class="gr">κόβω</td><td class="gr">κοψ-</td></tr>
        <tr><td class="lbl">-β- → -ψ-</td><td class="gr">βλέπω</td><td class="gr">δ-</td></tr>
        <tr><td class="lbl">-κ- → -ξ-</td><td class="gr">ανοίγω</td><td class="gr">ανοιξ-</td></tr>
        <tr><td class="lbl">-γ- → -ξ-</td><td class="gr">λέγω</td><td class="gr">ειπ-</td></tr>
        <tr><td class="lbl">-ζ- → -σ-</td><td class="gr">αρπάζω</td><td class="gr">αρπαξ-</td></tr>
        <tr><td class="lbl">-άω → -ησ-</td><td class="gr">αγαπώ</td><td class="gr">αγαπησ-</td></tr>
        <tr><td class="lbl">-ώ → -ησ-</td><td class="gr">μιλώ</td><td class="gr">μιλησ-</td></tr>
      </table>
    </div>`;
  wrap.appendChild(gstem);

  return wrap;
}

// ── Theory: Tense Usage ───────────────────────────────────────────────────────
function renderTenseUsage() {
  const wrap = mkdiv('');

  const intro = mkdiv('card');
  intro.innerHTML = `
    <div class="theory-h1">Tense Usage</div>
    <p class="theory-p">The most important distinction in Greek grammar is <b style="color:#e8c98a">aspect</b>: the difference between a <b>continuous</b> action (ongoing, habitual) and a <b>simple/perfective</b> action (completed, single occurrence). This runs through past, present, and future alike.</p>`;
  wrap.appendChild(intro);

  const tenses = [
    {
      name: 'Present', greek: 'γράφω',
      meaning: 'I write / I am writing',
      desc: 'Greek has one present tense covering both "I write" (habitual) and "I am writing" (right now). Context determines which reading applies.',
      example: 'Γράφω ένα γράμμα. — I am writing a letter.',
    },
    {
      name: 'Continuous Past', greek: 'έγραφα',
      meaning: 'I was writing / I used to write',
      desc: 'The Imperfect. Expresses an action that was ongoing, repeated, or habitual in the past. Use it for background action ("what was happening"), habits, or states.',
      example: 'Έγραφα όταν χτύπησε το τηλέφωνο. — I was writing when the phone rang.',
    },
    {
      name: 'Simple Past', greek: 'έγραψα',
      meaning: 'I wrote',
      desc: 'The Aorist. A completed, single, or punctual past action — "it happened and is done." The workhorse of narration. Most frequent past tense in spoken Greek.',
      example: 'Έγραψα το γράμμα χθες. — I wrote the letter yesterday.',
    },
    {
      name: 'Present Perfect', greek: 'έχω γράψει',
      meaning: 'I have written',
      desc: 'A past action whose result is relevant now. Less common in speech than in English — Greeks often prefer the Simple Past. Used more in formal writing and to emphasise the current state.',
      example: 'Έχω γράψει τρία βιβλία. — I have written three books (so far).',
    },
    {
      name: 'Past Perfect', greek: 'είχα γράψει',
      meaning: 'I had written',
      desc: 'A past action completed before another past event. Used in narration and reported speech ("he said that he had already written it").',
      example: 'Είχα ήδη γράψει τη λίστα. — I had already written the list.',
    },
    {
      name: 'Continuous Future', greek: 'θα γράφω',
      meaning: 'I will be writing',
      desc: 'θα + present stem. An ongoing or repeated action in the future — "what I will be doing." Used for duration, schedules, or habitual future actions.',
      example: 'Αύριο στις 3 θα γράφω ακόμα. — Tomorrow at 3 I will still be writing.',
    },
    {
      name: 'Simple Future', greek: 'θα γράψω',
      meaning: 'I will write',
      desc: 'θα + aorist subjunctive stem. A single, specific future action — "what I will do." The standard future for one-off events or decisions.',
      example: 'Θα σου γράψω σύντομα. — I will write to you soon.',
    },
    {
      name: 'Future Perfect', greek: 'θα έχω γράψει',
      meaning: 'I will have written',
      desc: 'θα + έχω + infinitive. An action that will be completed by a specific future point. Relatively formal; more common in writing.',
      example: 'Μέχρι Παρασκευή θα έχω γράψει το κεφάλαιο. — By Friday I will have written the chapter.',
    },
    {
      name: 'Past Subjunctive', greek: 'να γράψω',
      meaning: 'to write (once)',
      desc: 'να + aorist stem. The most important "dependent" form — essentially the Greek infinitive. Used after verbs of wanting, ability, intention, and in purpose clauses (για να). Also forms the Simple Future when θα replaces να.',
      example: 'Θέλω να γράψω ένα βιβλίο. — I want to write a book.',
    },
    {
      name: 'Present Subjunctive', greek: 'να γράφω',
      meaning: 'to be writing / to keep writing',
      desc: 'να + present stem. Dependent form for ongoing or habitual actions. Used when the action itself is open-ended rather than a single event.',
      example: 'Μου αρέσει να γράφω κάθε πρωί. — I like writing every morning.',
    },
    {
      name: 'Cont. Imperative', greek: 'γράφε!',
      meaning: 'keep writing! / write (habitually)!',
      desc: 'Commands for ongoing, repeated, or habitual actions. Use when you want something to continue happening or to happen regularly.',
      example: 'Γράφε κάθε μέρα! — Write every day!',
    },
    {
      name: 'Simple Imperative', greek: 'γράψε!',
      meaning: 'write! (do it now)',
      desc: 'Commands for a single, specific action. Use for immediate one-time requests.',
      example: 'Γράψε μου αύριο. — Write to me tomorrow.',
    },
    {
      name: 'Present Participle', greek: 'γράφοντας',
      meaning: 'writing / while writing',
      desc: 'Invariable — never changes for gender or number. Expresses a simultaneous action, equivalent to "while -ing" or "by -ing". Always refers to the subject of the main verb.',
      example: 'Γράφοντας το γράμμα, άκουσε μουσική. — While writing the letter, he listened to music.',
    },
    {
      name: 'Simple Infinitive', greek: 'γράψει',
      meaning: '(to have) written',
      desc: 'The bare aorist stem form used in perfect tenses after έχω/είχα/θα έχω. Not used independently; always preceded by the auxiliary.',
      example: 'Έχει γράψει πολλά ποιήματα. — He has written many poems.',
    },
  ];

  const card = mkdiv('card');
  card.appendChild(mkdiv('sec-label', 'All tenses — click to expand'));

  for (const t of tenses) {
    const tc = mkdiv('tense-card');
    tc.style.cursor = 'pointer';
    const header = mkdiv('');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:baseline';
    header.appendChild(mkdiv('tense-card-name', t.name));
    header.appendChild(mkdiv('tense-card-greek', t.greek));
    tc.appendChild(header);
    tc.appendChild(mkdiv('tense-card-meaning', t.meaning));

    const body = mkdiv('');
    body.style.display = 'none';
    body.appendChild(mkdiv('tense-card-desc', t.desc));
    body.appendChild(mkdiv('tense-card-ex', '<span class="gr">' + t.example.split(' — ')[0] + '</span> — ' + t.example.split(' — ')[1]));
    tc.appendChild(body);

    tc.onclick = () => { body.style.display = body.style.display === 'none' ? 'block' : 'none'; };
    card.appendChild(tc);
  }
  wrap.appendChild(card);
  return wrap;
}

// ── Init ──────────────────────────────────────────────────────────────────────
render();
api('/api/verb_list').then(list => {
  state.verbList = list;
  if (['browse','lists'].includes(state.phase)) render();
});
api('/api/presets').then(lists => {
  state.savedLists = lists;
  if (state.phase === 'lists') render();
});
</script>
</body>
</html>"""

if __name__ == '__main__':
    print("Starting Greek Verb Trainer on http://localhost:5002")
    app.run(debug=False, port=5002)
