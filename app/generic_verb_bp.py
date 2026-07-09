"""
generic_verb_bp.py — Language-agnostic verb conjugation trainer.

Data format  ({lang}_verb_conjugations.json):
{
  "infinitive": {
    "TenseName": ["form1", "form2", "form3", "form4", "form5", "form6"],
    ...
  }
}
Forms are ordered to match the language's persons list.
Progress is stored in the Progress table with lang_code='{lang}_verbs'
and card_id='{infinitive}|{tense_idx}|{person_idx}'.
"""
import json, unicodedata
from pathlib import Path
from flask import Blueprint, jsonify, request
from flask_login import current_user

BASE = Path(__file__).parent.parent

# ── Language configs ───────────────────────────────────────────────────────────

VERB_LANG_CONFIGS = {
    "fr": {
        "name": "French",
        "flag": "🇫🇷",
        "persons": ["je", "tu", "il/elle", "nous", "vous", "ils/elles"],
        "tenses": [
            "Présent",
            "Passé composé",
            "Imparfait",
            "Plus-que-parfait",
            "Futur simple",
            "Futur antérieur",
            "Conditionnel présent",
            "Conditionnel passé",
            "Subjonctif présent",
        ],
    },
    "de": {
        "name": "German",
        "flag": "🇩🇪",
        "persons": ["ich", "du", "er/sie/es", "wir", "ihr", "sie/Sie"],
        "tenses": [
            "Präsens",
            "Präteritum",
            "Perfekt",
            "Futur I",
            "Konjunktiv II",
            "Plusquamperfekt",
        ],
    },
    "it": {
        "name": "Italian",
        "flag": "🇮🇹",
        "persons": ["io", "tu", "lui/lei", "noi", "voi", "loro"],
        "tenses": [
            "Presente",
            "Imperfetto",
            "Passato prossimo",
            "Passato remoto",
            "Futuro semplice",
            "Condizionale presente",
            "Congiuntivo presente",
        ],
    },
    "es": {
        "name": "Spanish",
        "flag": "🇪🇸",
        "persons": ["yo", "tú", "él/ella", "nosotros", "vosotros", "ellos/ellas"],
        "tenses": [
            "Presente",
            "Pretérito indefinido",
            "Pretérito imperfecto",
            "Futuro simple",
            "Condicional simple",
            "Subjuntivo presente",
            "Pretérito perfecto",
        ],
    },
    "nl": {
        "name": "Dutch",
        "flag": "🇳🇱",
        "persons": ["ik", "jij/je", "hij/zij", "wij/we", "jullie", "zij/ze"],
        "tenses": [
            "Tegenwoordige tijd",
            "Verleden tijd",
            "Voltooid tegenwoordige tijd",
            "Toekomende tijd",
            "Verleden toekomende tijd",
        ],
    },
}

_LANG_FOLDERS = {
    "fr": BASE / "french",
    "de": BASE / "german",
    "it": BASE / "italian",
    "es": BASE / "spanish",
    "nl": BASE / "dutch",
}

# ── Answer checking ────────────────────────────────────────────────────────────

def _norm(s):
    s = unicodedata.normalize("NFKD", s.lower().strip())
    return "".join(c for c in s if not unicodedata.combining(c))

def _check(guess, correct):
    g, c = _norm(guess), _norm(correct)
    if g == c:
        return "correct"
    if abs(len(g) - len(c)) <= 1:
        diffs = sum(a != b for a, b in zip(g, c)) + abs(len(g) - len(c))
        if diffs <= 1:
            return "close"
    return "wrong"

# ── Blueprint factory ──────────────────────────────────────────────────────────

def make_verb_blueprint(lang):
    cfg    = VERB_LANG_CONFIGS[lang]
    name   = cfg["name"]
    flag   = cfg["flag"]
    persons = cfg["persons"]
    tenses  = cfg["tenses"]
    folder  = _LANG_FOLDERS[lang]
    cfile   = folder / f"{lang}_verb_conjugations.json"
    lcode   = f"{lang}_verbs"

    bp = Blueprint(f"{lang}_verb", __name__)

    def _load():
        try:
            return json.loads(cfile.read_text(encoding="utf-8")) if cfile.exists() else {}
        except Exception:
            return {}

    def _get_progress():
        if not current_user.is_authenticated:
            return {}
        from models import Progress
        rows = Progress.query.filter_by(user_id=current_user.id, lang_code=lcode).all()
        return {r.card_id: json.loads(r.window or "[]") for r in rows}

    # ── API ──────────────────────────────────────────────────────────────────

    @bp.route("/api/verb_list")
    def api_verb_list():
        conj     = _load()
        progress = _get_progress()
        result   = []
        for inf in sorted(conj.keys()):
            total = len(tenses) * len(persons)
            done  = sum(
                1 for ti in range(len(tenses)) for pi in range(len(persons))
                if len(progress.get(f"{inf}|{ti}|{pi}", [])) >= 3
            )
            e = conj[inf]
            grp = e.get("group", "") if isinstance(e, dict) else ""
            result.append({"verb": inf, "done": done, "total": total, "group": grp})
        return jsonify(result)

    @bp.route("/api/verb/<path:inf>")
    def api_verb(inf):
        conj = _load()
        data = conj.get(inf)
        if data is None:
            return jsonify({"error": "not_found"}), 404
        progress  = _get_progress()
        tense_out = []
        for ti, tense in enumerate(tenses):
            forms = data.get(tense, [""] * len(persons))
            cells = []
            for pi, (person, form) in enumerate(zip(persons, forms)):
                w = progress.get(f"{inf}|{ti}|{pi}", [])
                cells.append({"person": person, "form": form, "window": w})
            tense_out.append({"tense": tense, "cells": cells})
        return jsonify({"verb": inf, "tenses": tense_out, "persons": persons})

    @bp.route("/api/check", methods=["POST"])
    def api_check():
        data       = request.get_json(force=True)
        inf        = data.get("verb", "")
        tense_idx  = int(data.get("tense_idx", 0))
        person_idx = int(data.get("person_idx", 0))
        guess      = data.get("guess", "")
        correct    = data.get("correct", "")

        result       = _check(guess, correct)
        correct_bool = result == "correct"

        if current_user.is_authenticated:
            from models import db, Progress
            from datetime import date
            card_id = f"{inf}|{tense_idx}|{person_idx}"
            row = Progress.query.filter_by(
                user_id=current_user.id, lang_code=lcode, card_id=card_id
            ).first()
            if not row:
                row = Progress(user_id=current_user.id, lang_code=lcode, card_id=card_id, window="[]")
                db.session.add(row)
            w = json.loads(row.window or "[]")
            w.append(correct_bool)
            if len(w) > 5:
                w = w[-5:]
            row.window   = json.dumps(w)
            row.last_day = date.today().isoformat()
            db.session.commit()

        return jsonify({"result": result, "correct": correct})

    @bp.route("/api/progress")
    def api_progress():
        return jsonify(_get_progress())

    # ── Page ─────────────────────────────────────────────────────────────────

    @bp.route("/")
    def index():
        return _build_page(lang, name, flag, persons, tenses)

    return bp


# ── HTML / JS ─────────────────────────────────────────────────────────────────

def _build_page(lang, name, flag, persons, tenses):
    persons_js = json.dumps(persons)
    tenses_js  = json.dumps(tenses)
    vocab_url  = f"/{lang}/vocab/"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} Verb Trainer</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f0f1a">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Λεξιλόγιο">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f0f1a;font-family:Georgia,serif;color:#fff;min-height:100vh}}
.home-link{{position:fixed;top:10px;left:10px;z-index:100;font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;text-decoration:none;letter-spacing:.5px;padding:6px 10px;border-radius:8px;transition:color .15s,background .15s}}
.home-link:hover{{color:rgba(201,169,110,.8);background:rgba(201,169,110,.08)}}
.vocab-link{{position:fixed;top:10px;right:10px;z-index:100;font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;text-decoration:none;letter-spacing:.5px;padding:6px 10px;border-radius:8px;transition:color .15s,background .15s}}
.vocab-link:hover{{color:rgba(201,169,110,.8);background:rgba(201,169,110,.08)}}
.app-header{{text-align:center;padding:28px 20px 0}}
.app-header-sub{{font-size:10px;letter-spacing:4px;color:#c9a96e;text-transform:uppercase;margin-bottom:4px;font-family:sans-serif;opacity:.8}}
.app-header-title{{font-size:22px;color:#fff;font-weight:normal;letter-spacing:1px}}
.nav-tabs{{display:flex;gap:4px;background:rgba(255,255,255,.04);border-radius:14px;padding:4px;margin:18px auto 0;width:calc(100% - 28px);max-width:560px}}
.nav-tab{{flex:1;padding:8px;border-radius:10px;border:none;background:transparent;color:rgba(255,255,255,.4);font-size:12px;font-weight:700;font-family:sans-serif;letter-spacing:1px;text-transform:uppercase;cursor:pointer;transition:all .15s}}
.nav-tab.active{{background:rgba(201,169,110,.2);color:#c9a96e}}
#app{{padding:18px 14px 60px;display:flex;flex-direction:column;align-items:center}}
.content{{width:100%;max-width:560px}}
.empty-msg{{color:rgba(255,255,255,.3);text-align:center;padding:60px 0;font-family:sans-serif;font-size:13px;line-height:1.7}}
.card{{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;padding:20px;margin-bottom:12px;border:1px solid rgba(255,255,255,.07);cursor:pointer;transition:border-color .15s}}
.card:hover{{border-color:rgba(201,169,110,.3)}}
.card.active{{border-color:#c9a96e}}
.card-head{{display:flex;justify-content:space-between;align-items:center}}
.card-inf{{font-size:18px;color:#fff}}
.card-prog{{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif}}
.card-dots{{display:flex;gap:3px;flex-wrap:wrap;margin-top:8px}}
.dot{{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.1)}}
.dot.done{{background:#c9a96e}}
.sec-label{{font-size:10px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:10px}}
.search-wrap{{position:relative;margin-bottom:12px}}
.search-wrap input{{padding-left:36px}}
.search-icon{{position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:15px;opacity:.35;pointer-events:none}}
input[type=text]{{width:100%;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:16px;outline:none;font-family:Georgia,serif}}
input[type=text]:focus{{border-color:#c9a96e}}
.btn-primary{{width:100%;padding:14px;border-radius:12px;background:linear-gradient(135deg,#c9a96e,#e8c98a);border:none;color:#1a1a2e;font-size:15px;font-weight:700;font-family:sans-serif;letter-spacing:1px;cursor:pointer;text-transform:uppercase;margin-top:10px;transition:opacity .15s}}
.btn-primary:disabled{{background:rgba(255,255,255,.07);color:rgba(255,255,255,.2);cursor:default}}
.btn-primary:not(:disabled):hover{{opacity:.9}}
.btn-secondary{{padding:10px 18px;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);color:rgba(255,255,255,.6);font-size:12px;font-weight:700;font-family:sans-serif;cursor:pointer;text-transform:uppercase;transition:all .15s}}
.btn-secondary:hover{{border-color:rgba(201,169,110,.6);color:#c9a96e}}
.pill{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:5px 11px;cursor:pointer;font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;transition:all .15s;display:inline-block;margin:2px}}
.pill.on{{background:rgba(201,169,110,.18);border-color:#c9a96e;color:#c9a96e}}
.pill:hover:not(.on){{border-color:rgba(255,255,255,.25);color:rgba(255,255,255,.7)}}
.conj-table{{width:100%;border-collapse:collapse;margin-top:6px}}
.conj-table td{{padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.06);font-size:14px}}
.conj-table td:first-child{{color:rgba(255,255,255,.4);font-family:sans-serif;font-size:12px;width:35%}}
.conj-table tr:last-child td{{border-bottom:none}}
.tense-block{{margin-bottom:16px;background:rgba(255,255,255,.03);border-radius:12px;padding:14px;border:1px solid rgba(255,255,255,.06)}}
.tense-name{{font-size:11px;color:#c9a96e;text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:8px}}
.quiz-panel{{background:linear-gradient(145deg,#1a1a2e,#16213e);border-radius:16px;padding:22px;border:1px solid rgba(255,255,255,.07)}}
.quiz-prompt{{font-size:22px;text-align:center;margin-bottom:6px}}
.quiz-sub{{font-size:13px;color:rgba(255,255,255,.4);text-align:center;font-family:sans-serif;margin-bottom:20px}}
.result-correct{{color:#4caf50;font-family:sans-serif;font-size:14px;text-align:center;margin-top:8px}}
.result-close{{color:#ff9800;font-family:sans-serif;font-size:14px;text-align:center;margin-top:8px}}
.result-wrong{{color:#f44336;font-family:sans-serif;font-size:14px;text-align:center;margin-top:8px}}
.correct-show{{font-size:15px;text-align:center;color:#c9a96e;margin-top:4px}}
.prog-wrap{{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:22px;overflow:hidden;width:100%;max-width:560px}}
.prog-bar{{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}}
.btn-row{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}}
.browse-toggle{{display:flex;gap:6px;margin-bottom:12px}}
.group-header{{font-size:11px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;padding:18px 0 8px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:10px;display:flex;align-items:baseline;gap:8px}}
.group-header-count{{color:rgba(255,255,255,.3);font-weight:400;text-transform:none;letter-spacing:0;font-size:11px}}
</style>
</head>
<body>
<a class="home-link" href="/">← Home</a>
<a class="vocab-link" href="{vocab_url}">Vocab →</a>
<div class="app-header">
  <div class="app-header-sub">Λεξιλόγιο</div>
  <div class="app-header-title">{flag} {name} Verbs</div>
</div>
<div class="nav-tabs">
  <button class="nav-tab active" onclick="switchTab('browse')">Browse</button>
  <button class="nav-tab" onclick="switchTab('study')">Study</button>
  <button class="nav-tab" onclick="switchTab('quiz')">Quiz</button>
</div>
<div class="prog-wrap"><div class="prog-bar" id="prog-bar" style="width:0%"></div></div>
<div id="app"><div class="content" id="content"></div></div>

<script>
const PERSONS = {persons_js};
const TENSES  = {tenses_js};
const LANG    = '{lang}';

let tab        = 'browse';
let verbList   = [];
let progress   = {{}};
let activeVerb = null;
let verbData   = null;
let quizState  = null;
let searchQ    = '';
let activeTense = null;
let browseMode = 'alpha';

function api(path, opts) {{
  return fetch('/' + LANG + '/verb' + path, opts).then(r => r.json());
}}

function switchTab(t) {{
  tab = t;
  document.querySelectorAll('.nav-tab').forEach((el, i) => {{
    el.classList.toggle('active', ['browse','study','quiz'][i] === t);
  }});
  render();
}}

function render() {{
  const el = document.getElementById('content');
  if (tab === 'browse') el.innerHTML = renderBrowse();
  else if (tab === 'study') el.innerHTML = renderStudy();
  else el.innerHTML = renderQuiz();
  attachBrowseHandlers();
}}

// ── Browse ────────────────────────────────────────────────────────────────────
function renderBrowse() {{
  if (!verbList.length) return `<div class="empty-msg">No verbs loaded yet.<br><br>Add conjugation data to<br><code>{lang}_verb_conjugations.json</code></div>`;
  const hasGroups = verbList.some(v => v.group);
  const q = searchQ.toLowerCase();
  const filtered = verbList.filter(v => v.verb.toLowerCase().includes(q));
  const totalDone = verbList.reduce((s, v) => s + v.done, 0);
  const totalAll  = verbList.reduce((s, v) => s + v.total, 0);
  const pct = totalAll ? Math.round(100 * totalDone / totalAll) : 0;
  document.getElementById('prog-bar').style.width = pct + '%';

  const toggle = hasGroups ? `<div class="browse-toggle">
    <button class="pill${{browseMode==='alpha'?' on':''}}" onclick="browseMode='alpha';render()">A – Z</button>
    <button class="pill${{browseMode==='group'?' on':''}}" onclick="browseMode='group';render()">By Group</button>
  </div>` : '';

  const list = hasGroups && browseMode === 'group'
    ? renderBrowseByGroup(filtered)
    : `<div class="sec-label">${{filtered.length}} verb${{filtered.length!==1?'s':''}}</div>` + filtered.map(v => verbCard(v)).join('');

  return `
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input type="text" placeholder="Search verbs…" value="${{searchQ}}" oninput="searchQ=this.value;render()" id="search-inp">
    </div>
    ${{toggle}}
    ${{list}}
  `;
}}

function renderBrowseByGroup(filtered) {{
  const ORDER  = ['aux', '1', '2', '3'];
  const LABELS = {{
    'aux': 'Auxiliaries',
    '1':   '1st Group · -ER',
    '2':   '2nd Group · -IR',
    '3':   '3rd Group · Irregular',
  }};
  const buckets = {{}};
  filtered.forEach(v => {{ const g = v.group || '3'; (buckets[g] = buckets[g] || []).push(v); }});
  return ORDER
    .filter(g => buckets[g])
    .map(g => `
      <div class="group-header">${{LABELS[g] || g}}<span class="group-header-count">${{buckets[g].length}} verb${{buckets[g].length!==1?'s':''}}</span></div>
      ${{buckets[g].map(v => verbCard(v)).join('')}}
    `).join('');
}}

function verbCard(v) {{
  const dots = Array.from({{length: v.total}}, (_, i) => `<div class="dot${{i < v.done?' done':''}}"></div>`).join('');
  return `<div class="card" data-inf="${{v.verb}}" onclick="selectVerb('${{v.verb}}')">
    <div class="card-head">
      <span class="card-inf">${{v.verb}}</span>
      <span class="card-prog">${{v.done}}/${{v.total}}</span>
    </div>
    <div class="card-dots">${{dots}}</div>
  </div>`;
}}

function attachBrowseHandlers() {{
  const inp = document.getElementById('search-inp');
  if (inp) inp.focus();
}}

function selectVerb(inf) {{
  activeVerb = inf;
  activeTense = null;
  tab = 'study';
  document.querySelectorAll('.nav-tab').forEach((el, i) => {{
    el.classList.toggle('active', i === 1);
  }});
  loadVerbData(inf).then(render);
}}

// ── Study ─────────────────────────────────────────────────────────────────────
function loadVerbData(inf) {{
  return api('/api/verb/' + encodeURIComponent(inf)).then(d => {{ verbData = d; }});
}}

function renderStudy() {{
  if (!verbList.length) return `<div class="empty-msg">No verbs loaded yet.</div>`;
  if (!activeVerb || !verbData) return `
    <div class="sec-label">Select a verb</div>
    ${{verbList.slice(0,8).map(v => `<div class="card" onclick="selectVerb('${{v.verb}}')"><div class="card-head"><span class="card-inf">${{v.verb}}</span></div></div>`).join('')}}
    ${{verbList.length > 8 ? `<div class="empty-msg" style="padding:20px 0">Switch to Browse to see all ${{verbList.length}} verbs</div>` : ''}}
  `;
  const blocks = verbData.tenses.map((t, ti) => {{
    const rows = t.cells.map((c, pi) => {{
      const w = c.window || [];
      const acc = w.length ? (w.filter(Boolean).length / w.length) : null;
      const dot = acc === null ? '' : acc >= 0.8 ? ' ✓' : acc < 0.4 ? ' ✗' : ' ~';
      return `<tr><td>${{c.person}}</td><td>${{c.form || '—'}}${{dot}}</td></tr>`;
    }}).join('');
    const isActive = activeTense === ti;
    return `<div class="tense-block${{isActive?' active':''}}">
      <div class="tense-name" style="cursor:pointer" onclick="activeTense=${{isActive?'null':ti}};render()">${{t.tense}}</div>
      <table class="conj-table"><tbody>${{rows}}</tbody></table>
      <button class="btn-secondary" style="margin-top:10px;width:100%" onclick="startQuiz('${{activeVerb}}',${{ti}})">Quiz this tense</button>
    </div>`;
  }}).join('');
  return `
    <div class="btn-row" style="margin-bottom:14px">
      <button class="btn-secondary" onclick="activeVerb=null;verbData=null;render()">← All verbs</button>
      <button class="btn-secondary" onclick="startQuiz('${{activeVerb}}',null)">Quiz all tenses</button>
    </div>
    <div class="sec-label">${{activeVerb}}</div>
    ${{blocks}}
  `;
}}

// ── Quiz ──────────────────────────────────────────────────────────────────────
function startQuiz(inf, tenseIdx) {{
  if (!inf) return;
  const load = verbData && verbData.verb === inf ? Promise.resolve() : loadVerbData(inf);
  load.then(() => {{
    const pairs = [];
    verbData.tenses.forEach((t, ti) => {{
      if (tenseIdx !== null && ti !== tenseIdx) return;
      t.cells.forEach((c, pi) => {{
        if (c.form) pairs.push({{inf, ti, pi, tense: t.tense, person: c.person, form: c.form}});
      }});
    }});
    if (!pairs.length) return;
    quizState = {{
      items: pairs.sort(() => Math.random() - 0.5),
      idx: 0,
      result: null,
      revealed: false,
    }};
    tab = 'quiz';
    document.querySelectorAll('.nav-tab').forEach((el, i) => el.classList.toggle('active', i === 2));
    render();
  }});
}}

function renderQuiz() {{
  if (!verbList.length) return `<div class="empty-msg">No verbs loaded yet.</div>`;
  if (!quizState) return `
    <div class="empty-msg">Choose a verb in Study tab<br>and click "Quiz this tense".</div>
  `;
  const qs = quizState;
  if (qs.idx >= qs.items.length) {{
    const correct = qs.items.filter((_, i) => qs.results && qs.results[i]).length;
    return `
      <div class="quiz-panel">
        <div class="quiz-prompt">Done!</div>
        <div class="quiz-sub">${{correct}}/${{qs.items.length}} correct</div>
        <button class="btn-primary" onclick="quizState=null;tab='study';render()">Back to Study</button>
        <button class="btn-primary" style="margin-top:8px" onclick="restartQuiz()">Retry</button>
      </div>
    `;
  }}
  const item = qs.items[qs.idx];
  const resultEl = qs.result
    ? (qs.result === 'correct'
        ? `<div class="result-correct">✓ Correct</div>`
        : qs.result === 'close'
          ? `<div class="result-close">Almost! <span class="correct-show">${{item.form}}</span></div>`
          : `<div class="result-wrong">✗ <span class="correct-show">${{item.form}}</span></div>`)
    : '';
  const pct = Math.round(100 * qs.idx / qs.items.length);
  document.getElementById('prog-bar').style.width = pct + '%';
  return `
    <div class="quiz-panel">
      <div class="quiz-sub" style="margin-bottom:4px">${{item.tense}}</div>
      <div class="quiz-prompt">${{item.person}} — ${{item.inf}}</div>
      <div class="quiz-sub">${{qs.idx + 1}} / ${{qs.items.length}}</div>
      <input type="text" id="quiz-inp" placeholder="Type the form…" onkeydown="if(event.key==='Enter')submitQuiz()"
             ${{qs.result ? 'disabled' : ''}}>
      ${{resultEl}}
      ${{qs.result
        ? `<button class="btn-primary" onclick="nextQuestion()">Next</button>`
        : `<button class="btn-primary" onclick="submitQuiz()">Check</button>`
      }}
    </div>
  `;
}}

function submitQuiz() {{
  if (!quizState || quizState.result) return;
  const guess = (document.getElementById('quiz-inp')?.value || '').trim();
  if (!guess) return;
  const item = quizState.items[quizState.idx];
  api('/api/check', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{verb: item.inf, tense_idx: item.ti, person_idx: item.pi, guess, correct: item.form}}),
  }}).then(d => {{
    if (!quizState.results) quizState.results = {{}};
    quizState.results[quizState.idx] = d.result === 'correct';
    quizState.result = d.result;
    render();
    // refresh verb list progress
    api('/api/verb_list').then(l => {{
      verbList = l;
      if (verbData && verbData.verb === item.inf) loadVerbData(item.inf);
    }});
  }});
}}

function nextQuestion() {{
  if (!quizState) return;
  quizState.idx++;
  quizState.result = null;
  render();
  setTimeout(() => document.getElementById('quiz-inp')?.focus(), 50);
}}

function restartQuiz() {{
  quizState.items.sort(() => Math.random() - 0.5);
  quizState.idx = 0;
  quizState.result = null;
  quizState.results = {{}};
  render();
}}

// ── Init ──────────────────────────────────────────────────────────────────────
api('/api/verb_list').then(l => {{
  verbList = l;
  api('/api/progress').then(p => {{
    progress = p;
    render();
  }});
}});

render();
</script>
</body>
</html>"""
