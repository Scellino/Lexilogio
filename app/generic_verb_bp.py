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
            e   = conj[inf] if isinstance(conj[inf], dict) else {}
            grp = e.get("group", "")
            mdl = e.get("model", "")
            tra = e.get("translation", "")
            result.append({"verb": inf, "done": done, "total": total, "group": grp, "model": mdl, "translation": tra})
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

    @bp.route("/api/quiz_data")
    def api_quiz_data():
        conj = _load()
        tense_arg = request.args.get("tenses", "")
        verb_arg  = request.args.get("verbs", "")
        sel_tenses = set(tense_arg.split(",")) if tense_arg else set(tenses)
        sel_infs   = set(verb_arg.split(","))  if verb_arg  else set(conj.keys())
        result = []
        for inf in sorted(conj.keys()):
            if inf not in sel_infs:
                continue
            data = conj.get(inf)
            if not data:
                continue
            tra = data.get("translation", "")
            for ti, tense in enumerate(tenses):
                if tense not in sel_tenses:
                    continue
                forms = data.get(tense, [""] * len(persons))
                cells = [{"person": p, "form": f} for p, f in zip(persons, forms)]
                result.append({
                    "inf": inf, "translation": tra,
                    "tense": tense, "tenseIdx": ti, "cells": cells,
                })
        return jsonify(result)

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
.card{{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;padding:20px;margin-bottom:12px;border:1px solid rgba(255,255,255,.07);transition:border-color .15s}}
.card.clickable{{cursor:pointer}}
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
.quiz-meta{{font-size:10px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-bottom:18px;text-transform:uppercase;letter-spacing:1px}}
.quiz-verb{{font-family:Georgia,serif;font-size:34px;color:#e8c98a;margin-bottom:6px;line-height:1.15}}
.quiz-en{{font-size:13px;color:rgba(255,255,255,.3);font-style:italic;font-family:sans-serif;margin-bottom:20px}}
.quiz-tense{{font-size:11px;color:rgba(201,169,110,.75);font-family:sans-serif;margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px}}
.quiz-person{{font-size:16px;color:#fff;font-family:Georgia,serif;margin-bottom:18px}}
.quiz-input{{width:100%;padding:13px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:19px;outline:none;margin-bottom:12px;font-family:Georgia,serif;box-sizing:border-box}}
.quiz-input:focus{{border-color:#c9a96e}}
.feedback{{border-radius:12px;padding:14px 16px;margin-bottom:14px}}
.feedback.correct{{background:rgba(122,196,154,.1);border:1px solid #7ac49a}}
.feedback.accent,.feedback.close{{background:rgba(230,180,80,.08);border:1px solid #e6b450}}
.feedback.wrong{{background:rgba(212,122,143,.1);border:1px solid #d47a8f}}
.feedback-verdict{{font-size:13px;font-weight:700;font-family:sans-serif;margin-bottom:5px}}
.feedback.correct .feedback-verdict{{color:#7ac49a}}
.feedback.accent .feedback-verdict,.feedback.close .feedback-verdict{{color:#e6b450}}
.feedback.wrong .feedback-verdict{{color:#d47a8f}}
.feedback-form{{font-size:16px;color:#f0ebe0;font-family:Georgia,serif;margin-top:6px}}
.score-big{{font-size:54px;font-weight:700;font-family:Georgia,serif;text-align:center;color:#e8c98a;margin-bottom:4px}}
.score-sub{{font-size:13px;color:rgba(255,255,255,.35);font-family:sans-serif;text-align:center;margin-bottom:20px}}
.result-row{{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.03);border-radius:10px;padding:8px 12px;margin-bottom:5px}}
.result-icon{{font-size:13px;min-width:16px;font-family:sans-serif;flex-shrink:0}}
.result-verb{{font-family:Georgia,serif;font-size:14px;color:#e8c98a;min-width:110px;flex-shrink:0}}
.result-tense{{font-size:11px;color:rgba(255,255,255,.28);font-family:sans-serif;min-width:90px;flex-shrink:0}}
.result-person{{font-size:11px;color:rgba(255,255,255,.28);font-family:sans-serif;min-width:60px;flex-shrink:0}}
.result-guess{{font-family:Georgia,serif;font-size:13px;flex:1}}
.result-guess.correct{{color:#7ac49a}}
.result-guess.accent,.result-guess.close{{color:#e6b450}}
.result-guess.wrong{{color:#d47a8f}}
.result-guess.skip{{color:rgba(255,255,255,.25)}}
.mini-table-wrap{{margin-top:22px;border-top:1px solid rgba(255,255,255,.07);padding-top:18px}}
.mini-label{{font-size:10px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:12px}}
.setup-tense-overview{{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}}
.tov-chip{{background:rgba(201,169,110,.12);border:1px solid rgba(201,169,110,.3);border-radius:10px;padding:4px 10px;font-size:11px;color:#c9a96e;font-family:sans-serif}}
.setup-summary{{text-align:center;padding:14px;background:rgba(255,255,255,.03);border-radius:10px;margin-top:6px;font-family:sans-serif}}
.setup-summary-big{{font-family:Georgia,serif;font-size:26px;color:#e8c98a}}
.setup-summary-sub{{font-size:11px;color:rgba(255,255,255,.3);margin-top:3px}}
.prog-wrap{{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:22px;overflow:hidden;width:100%;max-width:560px}}
.prog-bar{{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}}
.btn-row{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}}
.browse-toggle{{display:flex;gap:6px;margin-bottom:10px}}
.browse-toolbar{{display:flex;gap:6px;align-items:center;margin-bottom:10px;flex-wrap:wrap;min-height:28px}}
.verb-row{{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:9px;border:1px solid transparent;cursor:pointer;transition:background .12s,border-color .12s}}
.verb-row:hover{{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.07)}}
.verb-row.sel{{background:rgba(201,169,110,.07);border-color:rgba(201,169,110,.2)}}
.verb-row input[type=checkbox]{{accent-color:#c9a96e;cursor:pointer;width:14px;height:14px;flex-shrink:0}}
.vname{{font-size:15px;color:#fff;font-family:Georgia,serif;min-width:110px}}
.vtran{{font-size:12px;color:rgba(255,255,255,.35);font-family:sans-serif;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.vdot{{width:7px;height:7px;border-radius:50%;flex-shrink:0}}
.vdot.started{{background:#f0b429}}.vdot.mastered{{background:#4caf88}}
.btn-ghost{{padding:4px 10px;border-radius:7px;background:transparent;border:1px solid rgba(255,255,255,.12);color:rgba(255,255,255,.45);font-size:11px;font-family:sans-serif;cursor:pointer;white-space:nowrap;transition:all .12s}}
.btn-ghost:hover{{border-color:rgba(201,169,110,.5);color:#c9a96e}}
.group-major-header{{font-size:13px;color:#c9a96e;text-transform:uppercase;letter-spacing:2px;font-family:sans-serif;font-weight:700;padding:24px 0 6px;display:flex;align-items:center;gap:8px;border-bottom:2px solid rgba(201,169,110,.2);margin-bottom:8px;cursor:pointer;user-select:none}}
.group-header{{font-size:11px;color:rgba(201,169,110,.55);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;padding:12px 0 5px;border-bottom:1px solid rgba(255,255,255,.05);margin-bottom:6px;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}}
.group-header-count{{color:rgba(255,255,255,.3);font-weight:400;text-transform:none;letter-spacing:0;font-size:11px}}
.show-all-btn{{width:100%;padding:7px;border-radius:8px;background:rgba(255,255,255,.03);border:1px dashed rgba(255,255,255,.1);color:rgba(255,255,255,.35);font-size:11px;font-family:sans-serif;cursor:pointer;text-align:center;margin:4px 0 10px;transition:all .12s}}
.show-all-btn:hover{{border-color:rgba(201,169,110,.4);color:#c9a96e}}
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
let searchQ     = '';
let activeTense = null;
let browseMode  = 'alpha';
let selected    = new Set();
let expandedGroups = {{}};
let _lastFiltered = [];

// ── Quiz state ────────────────────────────────────────────────────────────────
const DEFAULT_QUIZ_TENSES = TENSES.slice(0, 3);
let quizPhase        = 'setup';
let quizTenses       = new Set(DEFAULT_QUIZ_TENSES);
let quizType         = 'standard';
let quizPersons      = 'all';
let quizRandom       = false;
let quizCount        = 20;
let quizSession      = {{ questions: [], idx: 0, results: [] }};
let quizFeedback     = null;
let quizTableSession = {{ pairs: [], idx: 0, results: [] }};
let quizTableFeedback = null;

function api(path, opts) {{
  return fetch('/' + LANG + '/verb' + path, opts).then(r => r.json());
}}

function switchTab(t) {{
  tab = t;
  document.querySelectorAll('.nav-tab').forEach((el, i) => {{
    el.classList.toggle('active', ['browse','study','quiz'][i] === t);
  }});
  if (t === 'quiz') quizPhase = 'setup';
  render();
}}

function render() {{
  const el = document.getElementById('content');
  el.innerHTML = '';
  if (tab === 'browse') {{ el.innerHTML = renderBrowse(); }}
  else if (tab === 'study') {{ el.innerHTML = renderStudy(); }}
  else {{
    const node = renderQuizPane();
    if (node) el.appendChild(node);
  }}
  attachBrowseHandlers();
}}

function renderQuizPane() {{
  if (quizPhase === 'setup')           return renderSetup();
  if (quizPhase === 'quiz')            return renderQuizQ();
  if (quizPhase === 'feedback')        return renderFeedback();
  if (quizPhase === 'results')         return renderResults();
  if (quizPhase === 'table-quiz')      return renderTableQuiz();
  if (quizPhase === 'table-feedback')  return renderTableFeedback();
  if (quizPhase === 'table-results')   return renderTableResults();
  return mkdiv('');
}}

// ── Browse helpers ────────────────────────────────────────────────────────────
function toggleSelect(inf) {{
  if (selected.has(inf)) selected.delete(inf); else selected.add(inf);
  render();
}}
function toggleSelectI(i) {{ toggleSelect(verbList[i].verb); }}
function viewVerbI(i)      {{ viewVerb(verbList[i].verb); }}
function selectAllVisible() {{ _lastFiltered.forEach(v => selected.add(v.verb)); render(); }}
function clearSelected()    {{ selected.clear(); render(); }}
function viewVerb(inf) {{
  activeVerb  = inf; activeTense = null; tab = 'study';
  document.querySelectorAll('.nav-tab').forEach((el, i) => el.classList.toggle('active', i === 1));
  loadVerbData(inf).then(render);
}}
function collapseGroup(model) {{
  expandedGroups[model] = expandedGroups[model] === 'hidden' ? undefined : 'hidden';
  render();
}}
function showAllGroup(model) {{ expandedGroups[model] = 'all'; render(); }}

function mkdiv(cls, text) {{
  const e = document.createElement('div');
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}}
function mkel(tag, attrs, text) {{
  const e = document.createElement(tag);
  if (attrs) Object.assign(e, attrs);
  if (text !== undefined) e.textContent = text;
  return e;
}}

function startQuizSelected() {{
  quizPhase = 'setup';
  tab = 'quiz';
  document.querySelectorAll('.nav-tab').forEach((el, i) => el.classList.toggle('active', i === 2));
  render();
}}

function dotStatus(v) {{
  if (!v.total) return '';
  const r = v.done / v.total;
  return r >= 0.8 ? 'mastered' : v.done > 0 ? 'started' : '';
}}

function renderVerbRow(v) {{
  const isSel = selected.has(v.verb);
  const dot   = dotStatus(v);
  const i     = v._i;
  return `<div class="verb-row${{isSel?' sel':''}}" onclick="toggleSelectI(${{i}})">
    <input type="checkbox" ${{isSel?'checked':''}} onclick="event.stopPropagation()" onchange="toggleSelectI(${{i}})">
    <span class="vname">${{v.verb}}</span>
    <span class="vtran">${{v.translation||''}}</span>
    ${{dot ? `<span class="vdot ${{dot}}"></span>` : '<span class="vdot"></span>'}}
    <button class="btn-ghost" onclick="event.stopPropagation();viewVerbI(${{i}})">Study →</button>
  </div>`;
}}

// ── Browse ────────────────────────────────────────────────────────────────────
function renderBrowse() {{
  if (!verbList.length) return `<div class="empty-msg">No verbs loaded yet.<br><br>Add conjugation data to<br><code>{lang}_verb_conjugations.json</code></div>`;
  const hasGroups = verbList.some(v => v.group);
  const q = searchQ.toLowerCase();
  _lastFiltered = verbList.filter(v =>
    v.verb.toLowerCase().includes(q) || (v.translation||'').toLowerCase().includes(q)
  );
  const totalDone = verbList.reduce((s, v) => s + v.done, 0);
  const totalAll  = verbList.reduce((s, v) => s + v.total, 0);
  document.getElementById('prog-bar').style.width = (totalAll ? Math.round(100*totalDone/totalAll) : 0) + '%';

  const toggle = hasGroups ? `<div class="browse-toggle">
    <button class="pill${{browseMode==='alpha'?' on':''}}" onclick="browseMode='alpha';render()">A – Z</button>
    <button class="pill${{browseMode==='group'?' on':''}}" onclick="browseMode='group';render()">By Group</button>
  </div>` : '';

  const toolbar = selected.size
    ? `<div class="browse-toolbar">
        <span style="font-size:12px;color:#c9a96e;font-family:sans-serif">${{selected.size}} selected</span>
        <button class="btn-secondary" onclick="startQuizSelected()">Quiz selected →</button>
        <button class="pill" onclick="clearSelected()">Clear</button>
       </div>`
    : `<div class="browse-toolbar">
        <button class="pill" onclick="selectAllVisible()">Select all</button>
        <span style="font-size:12px;color:rgba(255,255,255,.3);font-family:sans-serif">${{_lastFiltered.length}} verb${{_lastFiltered.length!==1?'s':''}}</span>
       </div>`;

  const list = hasGroups && browseMode === 'group'
    ? renderBrowseByGroup(_lastFiltered)
    : _lastFiltered.map(v => renderVerbRow(v)).join('');

  return `
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input type="text" placeholder="Search verbs or translation…" value="${{searchQ}}"
             oninput="searchQ=this.value;render()" id="search-inp">
    </div>
    ${{toggle}}
    ${{toolbar}}
    ${{list}}
  `;
}}

const MODEL_MAJOR = {{
  'AUX':'aux','AVOIR':'aux','ÊTRE':'aux',
  'ER':'1','GER':'1','CER':'1','YER':'1','EVER':'1','ENER':'1',
  'ÉDER':'1','ÉTER':'1','ÉRER':'1','ELER':'1','APPELER':'1',
  'ETER':'1','JETER':'1','CRÉER':'1','ESER':'1','ÉTRER':'1',
  'IR':'2',
}};
const MODEL_ORDER = [
  'AUX',
  'ER','GER','CER','YER','EVER','ENER','ÉDER','ÉTER','ÉRER','ELER','APPELER','ETER','JETER','CRÉER','ESER','ÉTRER',
  'IR',
  'ENIR','DRE','UIRE','TIR','AÎTRE','ÉRIR','VRIR','FRIR','FUIR','URE','AINDRE','EINDRE','CEVOIR','DIRE','COURIR',
  'ALLER','BATTRE','BOIRE','BOUILLIR','COUDRE','CROIRE','CUEILLIR','DEVOIR','DÉPLAIRE','DORMIR','ÉCRIRE',
  'ENVOYER','FAIRE','FALLOIR','HAÏR','LIRE','METTRE','MOURIR','NAÎTRE','OINDRE','PLEUVOIR',
  'POUVOIR','PRENDRE','RIRE','SAVOIR','SERVIR','SOUDRE','SUIVRE','VAINCRE','VIVRE','VOIR','VOULOIR','IRR',
];
const MODEL_LABELS = {{
  'AUX':'Auxiliaries',
  'ER':'Regular -ER','GER':'-GER · manger','CER':'-CER · avancer','YER':'-YER · employer',
  'EVER':'-EVER · lever','ENER':'-ENER · mener','ÉDER':'-ÉDER · céder',
  'ÉTER':'-ÉTER · compléter','ÉRER':'-ÉRER · considérer',
  'ELER':'-ELER · rappeler','APPELER':'-ELER doublement · appeler',
  'ETER':'-ETER · acheter','JETER':'-ETER doublement · jeter',
  'CRÉER':'-CRÉER · créer','ESER':'-ESER · peser','ÉTRER':'-ÉTRER',
  'IR':'Regular -IR',
  'ENIR':'-ENIR · tenir / venir','DRE':'-DRE · vendre','UIRE':'-UIRE · conduire',
  'TIR':'-TIR · partir','AÎTRE':'-AÎTRE · connaître','ÉRIR':'-ÉRIR · acquérir',
  'VRIR':'-VRIR · ouvrir','FRIR':'-FRIR · offrir','FUIR':'-FUIR · fuir',
  'URE':'-URE · conclure','AINDRE':'-AINDRE · contraindre','EINDRE':'-EINDRE · atteindre',
  'CEVOIR':'-CEVOIR · recevoir','DIRE':'-DIRE · contredire','COURIR':'Courir',
  'ALLER':'Aller','BATTRE':'Battre','BOIRE':'Boire','BOUILLIR':'Bouillir',
  'COUDRE':'Coudre','CROIRE':'Croire','CUEILLIR':'Cueillir','DEVOIR':'Devoir',
  'DÉPLAIRE':'Déplaire','DORMIR':'Dormir','ÉCRIRE':'Écrire','ENVOYER':'Envoyer',
  'FAIRE':'Faire','FALLOIR':'Falloir','HAÏR':'Haïr','LIRE':'Lire','METTRE':'Mettre',
  'MOURIR':'Mourir','NAÎTRE':'Naître','OINDRE':'-OINDRE · joindre','PLEUVOIR':'Pleuvoir',
  'POUVOIR':'Pouvoir','PRENDRE':'Prendre','RIRE':'Rire','SAVOIR':'Savoir',
  'SERVIR':'Servir','SOUDRE':'-SOUDRE · résoudre','SUIVRE':'Suivre',
  'VAINCRE':'Vaincre','VIVRE':'Vivre','VOIR':'Voir','VOULOIR':'Vouloir','IRR':'Other irregular',
}};
const MAJOR_LABELS = {{'aux':'Auxiliaries','1':'1st Group','2':'2nd Group','3':'3rd Group'}};

function modelMajor(m) {{ return MODEL_MAJOR[m] || '3'; }}

const GROUP_DEFAULT_SHOW = 3;

function renderBrowseByGroup(filtered) {{
  const byModel = {{}};
  filtered.forEach(v => {{
    const m = v.model || 'IRR';
    (byModel[m] = byModel[m] || []).push(v);
  }});

  const orderedModels = [
    ...MODEL_ORDER.filter(m => byModel[m]),
    ...Object.keys(byModel).filter(m => !MODEL_ORDER.includes(m)).sort(),
  ];

  const parts = [];
  let currentMajor = null;

  orderedModels.forEach(m => {{
    const major = modelMajor(m);
    if (major !== currentMajor) {{
      currentMajor = major;
      const majorCount = filtered.filter(v => modelMajor(v.model || 'IRR') === major).length;
      parts.push(`<div class="group-major-header">
        ${{MAJOR_LABELS[major] || major}}
        <span class="group-header-count">${{majorCount}} verb${{majorCount!==1?'s':''}}</span>
      </div>`);
    }}

    const verbs   = byModel[m];
    const state   = expandedGroups[m];   // 'all' | 'hidden' | undefined
    const label   = MODEL_LABELS[m] || m;
    const chevron = state === 'hidden' ? '▸' : '▾';

    parts.push(`<div class="group-header" onclick="collapseGroup('${{m}}')">
      <span style="font-size:9px;opacity:.5">${{chevron}}</span>
      ${{label}}
      <span class="group-header-count">${{verbs.length}}</span>
    </div>`);

    if (state === 'hidden') return;

    const show = state === 'all' ? verbs : verbs.slice(0, GROUP_DEFAULT_SHOW);
    parts.push(show.map(v => renderVerbRow(v)).join(''));

    if (state !== 'all' && verbs.length > GROUP_DEFAULT_SHOW) {{
      const remaining = verbs.length - GROUP_DEFAULT_SHOW;
      parts.push(`<button class="show-all-btn" onclick="showAllGroup('${{m}}')">
        Show all · ${{remaining}} more
      </button>`);
    }}
  }});

  return parts.join('');
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
  if (inp) {{ inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }}
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
    <div class="sec-label" style="margin-bottom:8px">Select a verb to study</div>
    ${{verbList.slice(0,12).map(v => renderVerbRow(v)).join('')}}
    ${{verbList.length > 12 ? `<div class="empty-msg" style="padding:16px 0">Switch to Browse to find all ${{verbList.length}} verbs</div>` : ''}}
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
    </div>`;
  }}).join('');
  return `
    <div class="btn-row" style="margin-bottom:14px">
      <button class="btn-secondary" onclick="activeVerb=null;verbData=null;render()">← All verbs</button>
      <button class="btn-secondary" onclick="selected.clear();selected.add('${{activeVerb}}');switchTab('quiz')">Quiz →</button>
    </div>
    <div class="sec-label">${{activeVerb}}</div>
    ${{blocks}}
  `;
}}

// ── Quiz setup ────────────────────────────────────────────────────────────────
function renderSetup() {{
  const wrap = mkdiv('');

  const c1 = mkdiv('card');
  c1.appendChild(mkdiv('sec-label', 'Verbs'));
  const totalVerbs = verbList.length;
  const usingAll = selected.size === 0;
  const verbCount = usingAll ? totalVerbs : selected.size;
  const vInfo = mkdiv('');
  const vSpan = mkel('span', {{}}, String(verbCount));
  vSpan.style.cssText = 'font-family:Georgia,serif;font-size:20px;color:#e8c98a';
  const vLabel = mkel('span', {{}});
  vLabel.style.cssText = 'font-size:13px;color:rgba(255,255,255,.4);font-family:sans-serif';
  vLabel.textContent = ' verb' + (verbCount !== 1 ? 's' : '') + (usingAll ? ' (all)' : '');
  const bLink = mkel('a', {{}});
  bLink.style.cssText = 'font-size:12px;color:#c9a96e;cursor:pointer;font-family:sans-serif;text-decoration:underline;margin-left:6px';
  bLink.textContent = 'browse';
  bLink.onclick = () => {{ switchTab('browse'); }};
  vInfo.appendChild(vSpan); vInfo.appendChild(vLabel); vInfo.appendChild(bLink);
  if (!usingAll) {{
    const uLink = mkel('a', {{}});
    uLink.style.cssText = 'font-size:12px;color:rgba(255,255,255,.3);cursor:pointer;font-family:sans-serif;text-decoration:underline;margin-left:6px';
    uLink.textContent = 'unselect all';
    uLink.onclick = () => {{ selected.clear(); render(); }};
    vInfo.appendChild(uLink);
  }}
  c1.appendChild(vInfo);
  wrap.appendChild(c1);

  const c2 = mkdiv('card');
  c2.appendChild(mkdiv('sec-label', 'Tenses to practise'));
  const chips = mkdiv('');
  chips.style.cssText = 'display:flex;flex-wrap:wrap;gap:3px;margin-bottom:10px';
  for (const t of TENSES) {{
    const chip = mkel('button', {{className: 'pill' + (quizTenses.has(t) ? ' on' : '')}}, t);
    chip.onclick = () => {{ quizTenses.has(t) ? quizTenses.delete(t) : quizTenses.add(t); render(); }};
    chips.appendChild(chip);
  }}
  c2.appendChild(chips);
  const qr = mkdiv('');
  qr.style.cssText = 'display:flex;gap:8px;margin-top:4px';
  [['All', () => {{ TENSES.forEach(t => quizTenses.add(t)); render(); }}],
   ['None', () => {{ quizTenses.clear(); render(); }}]].forEach(([lbl, act]) => {{
    const b = mkel('button', {{className:'btn-ghost'}}, lbl);
    b.onclick = act; qr.appendChild(b);
  }});
  c2.appendChild(qr);
  wrap.appendChild(c2);

  const cMode = mkdiv('card');
  cMode.appendChild(mkdiv('sec-label', 'Quiz mode'));
  const modeRow = mkdiv('');
  modeRow.style.cssText = 'display:flex;gap:8px;margin-top:6px';
  [['standard','Standard'],['table','Table']].forEach(([val, lbl]) => {{
    const b = mkel('button', {{className: quizType === val ? 'btn-primary' : 'btn-ghost'}}, lbl);
    b.style.flex = '1';
    b.onclick = () => {{ quizType = val; render(); }};
    modeRow.appendChild(b);
  }});
  cMode.appendChild(modeRow);
  const modeDesc = mkdiv('');
  modeDesc.style.cssText = 'font-size:12px;color:rgba(255,255,255,.3);margin-top:8px;font-family:sans-serif';
  modeDesc.textContent = quizType === 'table'
    ? 'Fill in all conjugations for each verb/tense at once.'
    : 'One conjugation prompt at a time, in random order.';
  cMode.appendChild(modeDesc);

  const cCount = mkdiv('card');
  cCount.appendChild(mkdiv('sec-label', 'Questions'));
  const countRow = mkdiv('');
  countRow.style.cssText = 'display:flex;gap:6px;margin-top:6px;flex-wrap:wrap';
  const allBtn = mkel('button', {{className: !quizRandom ? 'btn-primary' : 'btn-ghost'}}, 'All');
  allBtn.style.flex = '1';
  allBtn.onclick = () => {{ quizRandom = false; render(); }};
  countRow.appendChild(allBtn);
  [10, 20, 30, 50, 100].forEach(n => {{
    const b = mkel('button', {{className: quizRandom && quizCount === n ? 'btn-primary' : 'btn-ghost'}}, String(n));
    b.style.flex = '1';
    b.onclick = () => {{ quizRandom = true; quizCount = n; render(); }};
    countRow.appendChild(b);
  }});
  cCount.appendChild(countRow);
  cMode.appendChild(cCount);
  wrap.appendChild(cMode);

  const cPersons = mkdiv('card');
  cPersons.appendChild(mkdiv('sec-label', 'Persons'));
  const persRow = mkdiv('');
  persRow.style.cssText = 'display:flex;gap:8px;margin-top:6px';
  [['all','All'],['singular','Singular'],['plural','Plural']].forEach(([val, lbl]) => {{
    const b = mkel('button', {{className: quizPersons === val ? 'btn-primary' : 'btn-ghost'}}, lbl);
    b.style.flex = '1';
    b.onclick = () => {{ quizPersons = val; render(); }};
    persRow.appendChild(b);
  }});
  cPersons.appendChild(persRow);
  wrap.appendChild(cPersons);

  const c4 = mkdiv('card');
  c4.appendChild(mkdiv('sec-label', 'Session overview'));
  const tenseList = TENSES.filter(t => quizTenses.has(t));
  if (tenseList.length === 0) {{
    const msg = mkdiv('');
    msg.innerHTML = '<span style="font-size:13px;color:rgba(255,255,255,.3);font-family:sans-serif">No tenses selected.</span>';
    c4.appendChild(msg);
  }} else {{
    const overview = mkdiv('setup-tense-overview');
    tenseList.forEach(t => overview.appendChild(mkdiv('tov-chip', t)));
    c4.appendChild(overview);
    const isTable = quizType === 'table';
    const personsMult = quizPersons === 'all' ? 6 : 3;
    const personsLabel = quizPersons === 'all' ? '6 persons' : quizPersons === 'singular' ? '3 singular' : '3 plural';
    const rawCount = isTable ? verbCount * tenseList.length : verbCount * tenseList.length * personsMult;
    const count = quizRandom ? Math.min(quizCount, rawCount) : rawCount;
    const unit = isTable ? 'table' : 'question';
    const vStr = verbCount + ' verb' + (verbCount !== 1 ? 's' : '');
    const tStr = tenseList.length + ' tense' + (tenseList.length !== 1 ? 's' : '');
    const formula = isTable ? vStr + ' \xd7 ' + tStr : vStr + ' \xd7 ' + tStr + ' \xd7 ' + personsLabel;
    const summ = mkdiv('setup-summary');
    summ.innerHTML = '<div class="setup-summary-big">' + (quizRandom && count < rawCount ? '' : '∼') + count + '</div>'
      + '<div class="setup-summary-sub">' + (quizRandom ? 'random ' + unit + 's from ' : '') + formula + '</div>';
    c4.appendChild(summ);
  }}
  wrap.appendChild(c4);

  const startBtn = mkel('button', {{className:'btn-primary'}},
    quizTenses.size ? 'Start Quiz' : 'Select at least one tense');
  startBtn.disabled = !quizTenses.size;
  startBtn.onclick = () => {{
    startBtn.disabled = true;
    startBtn.textContent = 'Loading…';
    startQuiz();
  }};
  wrap.appendChild(startBtn);
  return wrap;
}}

// ── Build questions ────────────────────────────────────────────────────────────
async function startQuiz() {{
  if (quizType === 'table') {{ await startTableQuiz(); return; }}
  const tenseParam = [...quizTenses].join(',');
  const verbParam  = selected.size > 0 ? [...selected].join(',') : '';
  const qs = '/api/quiz_data?tenses=' + encodeURIComponent(tenseParam) + (verbParam ? '&verbs=' + encodeURIComponent(verbParam) : '');
  const entries = await api(qs);
  const questions = [];
  for (const entry of entries) {{
    const maxIdx = entry.cells.length;
    const personRange = quizPersons === 'singular' ? [0,1,2].filter(i => i < maxIdx)
                      : quizPersons === 'plural'   ? [3,4,5].filter(i => i < maxIdx)
                      : [...Array(maxIdx).keys()];
    for (const pi of personRange) {{
      const cell = entry.cells[pi];
      if (!cell || !cell.form) continue;
      questions.push({{
        inf: entry.inf, translation: entry.translation, tense: entry.tense,
        tenseIdx: entry.tenseIdx, personIdx: pi, person: cell.person, answer: cell.form,
      }});
    }}
  }}
  questions.sort(() => Math.random() - 0.5);
  const finalQ = quizRandom ? questions.slice(0, quizCount) : questions;
  quizSession = {{ questions: finalQ, idx: 0, results: [] }};
  quizPhase = 'quiz';
  render();
}}

async function startTableQuiz() {{
  const tenseParam = [...quizTenses].join(',');
  const verbParam  = selected.size > 0 ? [...selected].join(',') : '';
  const qs = '/api/quiz_data?tenses=' + encodeURIComponent(tenseParam) + (verbParam ? '&verbs=' + encodeURIComponent(verbParam) : '');
  const entries = await api(qs);
  const pairs = [];
  for (const entry of entries) {{
    if (!entry.cells.some(c => c.form)) continue;
    pairs.push({{ inf: entry.inf, translation: entry.translation, tense: entry.tense, tenseIdx: entry.tenseIdx, cells: entry.cells }});
  }}
  pairs.sort(() => Math.random() - 0.5);
  const finalPairs = quizRandom ? pairs.slice(0, quizCount) : pairs;
  quizTableSession = {{ pairs: finalPairs, idx: 0, results: [] }};
  quizPhase = 'table-quiz';
  render();
}}

// ── Standard quiz question ─────────────────────────────────────────────────────
function renderQuizQ() {{
  const {{ questions, idx }} = quizSession;
  if (!questions.length) {{ quizPhase = 'setup'; render(); return mkdiv(''); }}
  const q = questions[idx];
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round(idx / questions.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', (idx+1) + ' / ' + questions.length));
  card.appendChild(mkdiv('quiz-verb', q.inf));
  if (q.translation) card.appendChild(mkdiv('quiz-en', q.translation));
  card.appendChild(mkdiv('quiz-tense', q.tense));
  card.appendChild(mkdiv('quiz-person', q.person + ' →'));

  const inp = mkel('input', {{type:'text', className:'quiz-input', placeholder:'type the conjugation…'}});
  inp.setAttribute('autocorrect','off'); inp.setAttribute('autocapitalize','none');
  inp.setAttribute('autocomplete','off'); inp.spellcheck = false;
  setTimeout(() => inp.focus(), 0);
  card.appendChild(inp);

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;margin-top:0;flex-wrap:wrap';
  const checkBtn = mkel('button', {{className:'btn-primary'}}, 'Check');
  checkBtn.style.flex = '1';
  const skipBtn = mkel('button', {{className:'btn-secondary'}}, 'Skip');
  const endBtn = mkel('button', {{className:'btn-ghost'}}, 'End');

  async function submitQ() {{
    const guess = inp.value.trim();
    if (!guess) return;
    const res = await api('/api/check', {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{verb: q.inf, tense_idx: q.tenseIdx, person_idx: q.personIdx, guess, correct: q.answer}})
    }});
    quizSession.results.push({{...q, guess, result: res.result}});
    quizFeedback = {{q, guess, result: res.result}};
    quizPhase = 'feedback';
    render();
  }}

  checkBtn.onclick = submitQ;
  skipBtn.onclick = () => {{ quizSession.results.push({{...q, guess:'', result:'skip'}}); advanceQuiz(); }};
  endBtn.onclick = () => {{ quizPhase = 'results'; render(); }};
  inp.onkeydown = e => {{ if (e.key === 'Enter') {{ e.preventDefault(); submitQ(); }} }};
  br.appendChild(checkBtn); br.appendChild(skipBtn); br.appendChild(endBtn);
  card.appendChild(br);
  wrap.appendChild(card);
  return wrap;
}}

// ── Feedback ───────────────────────────────────────────────────────────────────
function renderFeedback() {{
  const {{ q, guess, result }} = quizFeedback;
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round((quizSession.idx+1) / quizSession.questions.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', (quizSession.idx+1) + ' / ' + quizSession.questions.length));
  card.appendChild(mkdiv('quiz-verb', q.inf));
  if (q.translation) card.appendChild(mkdiv('quiz-en', q.translation));
  card.appendChild(mkdiv('quiz-tense', q.tense));
  card.appendChild(mkdiv('quiz-person', q.person + ' →'));

  const msgs = {{correct:'✓ Correct!', accent:'⟳ Right word — check accent', close:'≈ Almost there', wrong:'✗ Wrong'}};
  const fb = mkdiv('feedback ' + result);
  fb.appendChild(mkdiv('feedback-verdict', msgs[result] || ''));
  if (result !== 'correct') {{
    const ff = mkdiv('feedback-form');
    ff.textContent = '→ ' + q.answer;
    fb.appendChild(ff);
  }}
  card.appendChild(fb);

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap';
  const nextBtn = mkel('button', {{className:'btn-primary'}}, 'Next →');
  nextBtn.style.flex = '1';
  nextBtn.onclick = advanceQuiz;
  const endBtn = mkel('button', {{className:'btn-ghost'}}, 'End session');
  endBtn.onclick = () => {{ quizPhase = 'results'; render(); }};
  br.appendChild(nextBtn); br.appendChild(endBtn);
  card.appendChild(br);
  wrap.appendChild(card);

  if (verbData && verbData.verb === q.inf) {{
    const tw = mkdiv('mini-table-wrap');
    tw.appendChild(mkdiv('mini-label', 'Full conjugation'));
    const tbl = document.createElement('table');
    tbl.className = 'conj-table';
    const tbody = document.createElement('tbody');
    verbData.tenses.forEach(td => {{
      if (td.tense !== q.tense) return;
      td.cells.forEach(c => {{
        const tr = document.createElement('tr');
        const td1 = document.createElement('td'); td1.textContent = c.person;
        const td2 = document.createElement('td');
        td2.textContent = c.form || '—';
        if (c.person === q.person) td2.style.color = '#e8c98a';
        tr.appendChild(td1); tr.appendChild(td2); tbody.appendChild(tr);
      }});
    }});
    tbl.appendChild(tbody);
    tw.appendChild(tbl);
    wrap.appendChild(tw);
  }}

  const handler = e => {{
    if (e.key === 'Enter') {{ e.preventDefault(); document.removeEventListener('keydown', handler); advanceQuiz(); }}
  }};
  document.addEventListener('keydown', handler);
  return wrap;
}}

function advanceQuiz() {{
  quizSession.idx++;
  const done = quizSession.idx >= quizSession.questions.length;
  quizPhase = done ? 'results' : 'quiz';
  if (done) api('/api/verb_list').then(l => {{ verbList = stampIndices(l); }});
  render();
}}

// ── Results ────────────────────────────────────────────────────────────────────
function renderResults() {{
  const {{ results }} = quizSession;
  const wrap = mkdiv('');
  const total   = results.length;
  const correct = results.filter(r => r.result === 'correct').length;
  const close   = results.filter(r => ['accent','close'].includes(r.result)).length;
  const wrong   = results.filter(r => r.result === 'wrong').length;
  const skipped = total - correct - close - wrong;

  wrap.appendChild(mkdiv('score-big', correct + '/' + total));
  wrap.appendChild(mkdiv('score-sub', correct + ' correct \xb7 ' + close + ' close \xb7 ' + wrong + ' wrong \xb7 ' + skipped + ' skipped'));

  const icons = {{correct:'✓', accent:'⟳', close:'≈', wrong:'✗', skip:'–'}};
  for (const r of results) {{
    const row = mkdiv('result-row');
    row.appendChild(mkdiv('result-icon', icons[r.result] || '?'));
    row.appendChild(mkdiv('result-verb', r.inf));
    row.appendChild(mkdiv('result-tense', r.tense));
    row.appendChild(mkdiv('result-person', r.person));
    const g = mkdiv('result-guess ' + r.result, r.guess || '(skipped)');
    if (r.result !== 'correct' && r.result !== 'skip') g.title = 'Correct: ' + r.answer;
    row.appendChild(g);
    wrap.appendChild(row);
  }}

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap';
  const retryBtn = mkel('button', {{className:'btn-primary'}}, 'New Quiz →');
  retryBtn.style.flex = '1';
  retryBtn.onclick = () => {{ quizPhase = 'setup'; render(); }};
  const browseBtn = mkel('button', {{className:'btn-secondary'}}, 'Browse verbs');
  browseBtn.onclick = () => {{ switchTab('browse'); }};
  br.appendChild(retryBtn); br.appendChild(browseBtn);
  wrap.appendChild(br);
  return wrap;
}}

// ── Table quiz ─────────────────────────────────────────────────────────────────
function renderTableQuiz() {{
  const {{ pairs, idx }} = quizTableSession;
  if (!pairs.length) {{ quizPhase = 'setup'; render(); return mkdiv(''); }}
  const q = pairs[idx];
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round(idx / pairs.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', (idx+1) + ' / ' + pairs.length));
  card.appendChild(mkdiv('quiz-verb', q.inf));
  if (q.translation) card.appendChild(mkdiv('quiz-en', q.translation));
  card.appendChild(mkdiv('quiz-tense', q.tense));

  const maxIdx = q.cells.length;
  const personRange = quizPersons === 'singular' ? [0,1,2].filter(i => i < maxIdx)
                    : quizPersons === 'plural'   ? [3,4,5].filter(i => i < maxIdx)
                    : [...Array(maxIdx).keys()];
  const grid = mkdiv('');
  grid.style.cssText = 'display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;margin:16px 0';
  const inputs = [];
  for (const pi of personRange) {{
    const cell = q.cells[pi];
    if (!cell || !cell.form) continue;
    const lbl = mkel('label', {{}}, cell.person);
    lbl.style.cssText = 'font-size:13px;color:rgba(255,255,255,.5);font-family:sans-serif;white-space:nowrap';
    grid.appendChild(lbl);
    const inp = mkel('input', {{type:'text', className:'quiz-input'}});
    inp.style.cssText = 'margin:0;width:100%';
    inp.placeholder = '…';
    inp.setAttribute('autocorrect','off'); inp.setAttribute('autocapitalize','none');
    inp.setAttribute('autocomplete','off'); inp.spellcheck = false;
    inputs.push({{inp, pi, answer: cell.form, person: cell.person}});
    grid.appendChild(inp);
  }}
  card.appendChild(grid);
  if (inputs.length) setTimeout(() => inputs[0].inp.focus(), 50);

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap';
  const checkBtn = mkel('button', {{className:'btn-primary'}}, 'Check');
  checkBtn.style.flex = '1';
  const skipBtn = mkel('button', {{className:'btn-secondary'}}, 'Skip');
  const endBtn = mkel('button', {{className:'btn-ghost'}}, 'End');

  async function submitTable() {{
    const checks = await Promise.all(inputs.map(item =>
      item.inp.value.trim()
        ? api('/api/check', {{method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{verb: q.inf, tense_idx: q.tenseIdx, person_idx: item.pi, guess: item.inp.value.trim(), correct: item.answer}})
          }}).then(res => ({{pi: item.pi, guess: item.inp.value.trim(), answer: item.answer, result: res.result, person: item.person}}))
        : Promise.resolve({{pi: item.pi, guess:'', answer: item.answer, result:'skip', person: item.person}})
    ));
    const tableResult = {{inf: q.inf, translation: q.translation, tense: q.tense, checks}};
    quizTableSession.results.push(tableResult);
    quizTableFeedback = tableResult;
    quizPhase = 'table-feedback';
    render();
  }}

  checkBtn.onclick = submitTable;
  skipBtn.onclick = () => {{ quizTableSession.results.push({{inf:q.inf, tense:q.tense, result:'skip'}}); advanceTableQuiz(); }};
  endBtn.onclick = () => {{ quizPhase = 'table-results'; render(); }};
  inputs.forEach((item, i) => {{
    item.inp.onkeydown = e => {{
      if (e.key === 'Enter') {{ e.preventDefault(); i < inputs.length-1 ? inputs[i+1].inp.focus() : submitTable(); }}
      if (e.key === 'Tab' && !e.shiftKey) {{ e.preventDefault(); if (i < inputs.length-1) inputs[i+1].inp.focus(); }}
    }};
  }});
  br.appendChild(checkBtn); br.appendChild(skipBtn); br.appendChild(endBtn);
  card.appendChild(br);
  wrap.appendChild(card);
  return wrap;
}}

// ── Table feedback ─────────────────────────────────────────────────────────────
function renderTableFeedback() {{
  const fb = quizTableFeedback;
  const {{ pairs, idx }} = quizTableSession;
  const wrap = mkdiv('');

  const progBar = mkdiv('prog-bar');
  progBar.style.width = Math.round((idx+1) / pairs.length * 100) + '%';
  const progWrap = mkdiv('prog-wrap');
  progWrap.appendChild(progBar);
  wrap.appendChild(progWrap);

  const card = mkdiv('card');
  card.appendChild(mkdiv('quiz-meta', (idx+1) + ' / ' + pairs.length));
  card.appendChild(mkdiv('quiz-verb', fb.inf));
  if (fb.translation) card.appendChild(mkdiv('quiz-en', fb.translation));
  card.appendChild(mkdiv('quiz-tense', fb.tense));

  const resColors = {{correct:'#4caf88', accent:'#f0b429', close:'#f0b429', wrong:'#e57373', skip:'#888'}};
  const resIcons  = {{correct:'✓', accent:'≈', close:'≈', wrong:'✗', skip:'–'}};
  const grid = mkdiv('');
  grid.style.cssText = 'display:grid;grid-template-columns:auto 1fr auto;gap:7px 12px;align-items:baseline;margin:14px 0';
  for (const chk of fb.checks) {{
    const lbl = mkel('span', {{}}, chk.person);
    lbl.style.cssText = 'font-size:12px;color:rgba(255,255,255,.4);font-family:sans-serif;white-space:nowrap';
    grid.appendChild(lbl);
    const ans = document.createElement('div');
    ans.style.cssText = 'font-family:Georgia,serif;font-size:15px;display:flex;align-items:baseline;gap:5px;flex-wrap:wrap';
    const needsCorrection = (chk.result === 'wrong' || chk.result === 'accent' || chk.result === 'close') && chk.guess;
    if (needsCorrection) {{
      const g = mkel('span', {{}}, chk.guess);
      g.style.cssText = 'color:#e57373;text-decoration:line-through;opacity:.75';
      const arr = mkel('span', {{}}, '→');
      arr.style.cssText = 'color:rgba(255,255,255,.3);font-size:11px;font-family:sans-serif';
      const cor = mkel('span', {{}}, chk.answer);
      cor.style.cssText = 'color:#e8c98a';
      ans.appendChild(g); ans.appendChild(arr); ans.appendChild(cor);
    }} else {{
      const cor = mkel('span', {{}}, chk.answer);
      cor.style.cssText = chk.result === 'correct' ? 'color:#7ac49a' : 'color:#e8c98a';
      ans.appendChild(cor);
    }}
    grid.appendChild(ans);
    const icon = mkel('span', {{}}, resIcons[chk.result] || '?');
    icon.style.cssText = 'color:' + (resColors[chk.result] || '#fff') + ';font-weight:bold;font-size:14px';
    grid.appendChild(icon);
  }}
  card.appendChild(grid);

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap';
  const nextBtn = mkel('button', {{className:'btn-primary'}},
    idx + 1 >= pairs.length ? 'See results →' : 'Next →');
  nextBtn.style.flex = '1';
  nextBtn.onclick = advanceTableQuiz;
  const endBtn = mkel('button', {{className:'btn-ghost'}}, 'End session');
  endBtn.onclick = () => {{ quizPhase = 'table-results'; render(); }};
  br.appendChild(nextBtn); br.appendChild(endBtn);
  card.appendChild(br);

  const handler = e => {{
    if (e.key==='Enter') {{ e.preventDefault(); document.removeEventListener('keydown', handler); advanceTableQuiz(); }}
  }};
  document.addEventListener('keydown', handler);

  wrap.appendChild(card);
  return wrap;
}}

function advanceTableQuiz() {{
  quizTableSession.idx++;
  const done = quizTableSession.idx >= quizTableSession.pairs.length;
  quizPhase = done ? 'table-results' : 'table-quiz';
  if (done) api('/api/verb_list').then(l => {{ verbList = stampIndices(l); }});
  render();
}}

// ── Table results ──────────────────────────────────────────────────────────────
function renderTableResults() {{
  const {{ results }} = quizTableSession;
  const wrap = mkdiv('');
  let totalItems = 0, correctItems = 0;
  for (const r of results) {{
    if (r.result === 'skip') continue;
    if (r.checks) {{
      for (const c of r.checks) {{
        totalItems++;
        if (c.result === 'correct' || c.result === 'accent') correctItems++;
      }}
    }}
  }}
  const pct = totalItems > 0 ? Math.round(correctItems / totalItems * 100) : 0;
  wrap.appendChild(mkdiv('score-big', pct + '%'));
  wrap.appendChild(mkdiv('score-sub', correctItems + ' / ' + totalItems + ' correct \xb7 ' + results.length + ' tables'));

  for (const r of results) {{
    if (r.result === 'skip') {{
      const row = mkdiv('result-row');
      row.appendChild(mkdiv('result-icon', '–'));
      row.appendChild(mkdiv('result-verb', r.inf));
      row.appendChild(mkdiv('result-tense', r.tense));
      row.appendChild(mkdiv('result-person', '(skipped)'));
      row.appendChild(mkdiv('result-guess skip', ''));
      wrap.appendChild(row);
      continue;
    }}
    if (!r.checks) continue;
    const allOk = r.checks.every(c => c.result === 'correct' || c.result === 'accent');
    const row = mkdiv('result-row');
    row.appendChild(mkdiv('result-icon', allOk ? '✓' : '✗'));
    row.appendChild(mkdiv('result-verb', r.inf));
    row.appendChild(mkdiv('result-tense', r.tense));
    row.appendChild(mkdiv('result-person', 'all'));
    const errors = r.checks.filter(c => c.result !== 'correct' && c.result !== 'accent').length;
    row.appendChild(mkdiv('result-guess ' + (allOk ? 'correct' : 'wrong'),
      allOk ? '✓ all correct' : errors + ' error' + (errors !== 1 ? 's' : '')));
    wrap.appendChild(row);
  }}

  const br = mkdiv('');
  br.style.cssText = 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap';
  const retryBtn = mkel('button', {{className:'btn-primary'}}, 'New Quiz →');
  retryBtn.style.flex = '1';
  retryBtn.onclick = () => {{ quizPhase = 'setup'; render(); }};
  const browseBtn = mkel('button', {{className:'btn-secondary'}}, 'Browse verbs');
  browseBtn.onclick = () => {{ switchTab('browse'); }};
  br.appendChild(retryBtn); br.appendChild(browseBtn);
  wrap.appendChild(br);
  return wrap;
}}

// ── Init ──────────────────────────────────────────────────────────────────────
function stampIndices(list) {{ list.forEach((v, i) => v._i = i); return list; }}

api('/api/verb_list').then(l => {{
  verbList = stampIndices(l);
  api('/api/progress').then(p => {{
    progress = p;
    render();
  }});
}});

render();
</script>
</body>
</html>"""
