"""
generic_vocab_bp.py — Language-agnostic vocabulary Blueprint factory.

Usage:
    from generic_vocab_bp import make_vocab_blueprint
    from pathlib import Path

    IT_LANG = {
        "code":         "it",
        "name":         "Italian",
        "header_sub":   "ITALIANO · VOCABOLARIO",
        "header_title": "Italian Vocab Trainer",
        "data_dir":     Path(__file__).parent.parent / "italian",
        "data_module":  "it_vocab_data",   # must export CARDS list
        "word_types":   ["noun", "verb", "adjective", "adverb", "phrase"],
        "grammar_fields": {               # type → list of field defs (see schema below)
            "noun": [...],
            "verb": [...],
        },
        "article_rule": {                 # optional — drives article display on flashcard front
            "based_on": "gender",         # top-level card field to read
            "vowels":   "aeiou",
            "rules": {
                "m": {"vowel_start": "l'", "otherwise": "il"},
                "f": {"vowel_start": "l'", "otherwise": "la"},
            },
        },
        "article_colors": {              # optional — hex colour per article key / gender key
            "m": "#7ab3d4",
            "f": "#d47a8f",
        },
    }

    it_vocab_bp = make_vocab_blueprint(IT_LANG)
    app.register_blueprint(it_vocab_bp, url_prefix="/it/vocab")

Field definition schema (grammar_fields values):
    {
        "name":       str,          # HTML field name + key used when saving
        "label":      str,          # displayed label
        "widget":     "text" | "radio" | "select",
        "options":    [{"value": str, "label": str}, ...],  # radio / select only
        "placeholder": str,         # text only
        "hint":       str,          # grey helper text below the field
        "top_level":  bool,         # also store as a top-level card property (default False)
        "in_grammar": bool,         # store in grammar[] array (default True)
    }
"""

import json, sys, importlib, unicodedata, uuid
from datetime import date
from pathlib import Path
from flask import Blueprint, jsonify, request
from flask_login import current_user
from models import db, Progress, UserCard, CardSubmission


DEPARTURE_NAMES = {
    'en': 'English', 'de': 'German', 'el': 'Greek',
    'fr': 'French',  'nl': 'Dutch',  'es': 'Spanish', 'it': 'Italian',
    'pt': 'Portuguese', 'pl': 'Polish', 'sv': 'Swedish',
}

# ── Normalisation & checking ───────────────────────────────────────────────────

def _normalise(s):
    s = unicodedata.normalize("NFD", s.lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], (prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1]))
    return dp[n]


def _strip_to(s):
    return s[3:] if s.startswith("to ") else s


def _check(guess, correct, direction='word→en'):
    g    = _strip_to(_normalise(guess))
    alts = [_strip_to(_normalise(a.strip())) for a in correct.split(",")]
    if g in alts:
        return "correct"
    for a in alts:
        if len(g) < 3 or len(a) < 3:
            continue
        threshold = 1 if len(a) <= 5 else 2
        if _edit_distance(g, a) <= threshold:
            # word→en: English typo forgiveness → auto-correct
            # en→word: recall matters → retry prompt
            return "correct" if direction.startswith('word→') else "close"
    return "wrong"


# ── HTML template ──────────────────────────────────────────────────────────────
# Placeholders replaced at blueprint-creation time (not at request time):
#   __TITLE__          → lang["header_title"]
#   __HEADER_SUB__     → lang["header_sub"]
#   __HEADER_TITLE__   → lang["header_title"]
#   <!-- __LANG__ -->  → <script>const LANG = {...};</script>

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f0f1a">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Λεξιλόγιο">
<!-- __LANG__ -->
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f1a;font-family:Georgia,serif;color:#fff;min-height:100vh}
button,a{-webkit-tap-highlight-color:transparent;touch-action:manipulation}
.app{padding:60px 14px 0;display:flex;flex-direction:column;align-items:center}
.home-link{position:fixed;top:10px;left:10px;z-index:100;font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;text-decoration:none;letter-spacing:.5px;padding:6px 10px;border-radius:8px;transition:color .15s,background .15s}
.home-link:hover{color:rgba(201,169,110,.8);background:rgba(201,169,110,.08)}
.menu-btn{position:fixed;top:8px;right:8px;z-index:200;background:transparent;border:1px solid rgba(255,255,255,.12);border-radius:9px;color:rgba(255,255,255,.45);font-size:18px;line-height:1;padding:5px 10px;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:color .15s,border-color .15s;font-family:sans-serif}
.menu-btn:hover{color:#c9a96e;border-color:rgba(201,169,110,.35)}
.menu-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:300;backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px)}
.menu-overlay.open{display:block}
.menu-drawer{position:fixed;top:0;right:-300px;width:270px;height:100dvh;background:#111128;border-left:1px solid rgba(255,255,255,.08);z-index:310;display:flex;flex-direction:column;transition:right .25s cubic-bezier(.4,0,.2,1);overflow-y:auto;-webkit-overflow-scrolling:touch}
.menu-drawer.open{right:0}
.menu-spacer{height:52px;flex-shrink:0}
.menu-account-info{padding:12px 20px 12px}
.menu-account-name{font-size:14px;font-weight:600;color:#e8c98a;font-family:sans-serif;margin-bottom:2px}
.menu-account-email{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif}
.menu-divider{height:1px;background:rgba(255,255,255,.06);margin:4px 0;flex-shrink:0}
.menu-item{display:flex;align-items:center;gap:10px;padding:13px 20px;font-size:13px;font-family:sans-serif;color:rgba(255,255,255,.6);text-decoration:none;transition:color .15s,background .15s;border:none;background:transparent;cursor:pointer;text-align:left;width:100%;-webkit-tap-highlight-color:transparent;box-sizing:border-box}
.menu-item:hover,.menu-item:active{color:#c9a96e;background:rgba(201,169,110,.06)}
.menu-item.cta{color:#c9a96e;font-weight:600}
.menu-item-icon{font-size:15px;width:22px;text-align:center;flex-shrink:0;line-height:1}
.preview-banner{background:rgba(201,169,110,.08);border:1px solid rgba(201,169,110,.2);border-radius:12px;padding:10px 16px;font-family:sans-serif;font-size:12px;color:rgba(201,169,110,.8);text-align:center;margin-bottom:18px;width:100%;max-width:520px}
.preview-banner a{color:#c9a96e;font-weight:600}
.header{text-align:center;margin-bottom:20px}
.header-sub{font-size:10px;letter-spacing:4px;color:#c9a96e;text-transform:uppercase;margin-bottom:4px;font-family:sans-serif;opacity:.8}
.header-title{font-size:22px;color:#fff;font-weight:normal;letter-spacing:1px}
/* Pills */
.pill{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:6px 12px;cursor:pointer;font-size:12px;color:rgba(255,255,255,.45);font-family:sans-serif;transition:all .15s;display:inline-block;margin:3px}
.pill.active{background:rgba(201,169,110,.2);border-color:#c9a96e;color:#c9a96e}
.pill:hover{border-color:rgba(255,255,255,.3)}
/* Sections */
.sec{margin-bottom:18px}
.sec-label{font-size:10px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.sec-label a{color:rgba(255,255,255,.3);font-size:10px;cursor:pointer;text-decoration:underline;text-transform:none;letter-spacing:0;font-weight:400}
.pills{display:flex;flex-wrap:wrap;gap:2px;margin-top:6px;margin-bottom:4px}
/* Big buttons */
.btn-primary{width:100%;padding:14px;border-radius:12px;background:linear-gradient(135deg,#c9a96e,#e8c98a);border:none;color:#1a1a2e;font-size:15px;font-weight:700;font-family:sans-serif;letter-spacing:1px;cursor:pointer;text-transform:uppercase;margin-top:8px}
.btn-primary:disabled{background:rgba(255,255,255,.05);color:rgba(255,255,255,.2);cursor:default}
.btn-secondary{padding:12px;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);color:rgba(255,255,255,.6);font-size:12px;font-weight:700;font-family:sans-serif;cursor:pointer;text-transform:uppercase}
/* Progress bar */
.progress-wrap{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:20px;overflow:hidden}
.progress-bar{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}
.progress-label{display:flex;justify-content:space-between;margin-bottom:8px;font-family:sans-serif;font-size:11px;color:rgba(255,255,255,.3)}
/* Prompt card (quiz question) */
.prompt-card{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;padding:36px 24px 28px;text-align:center;margin-bottom:16px;position:relative;border:1px solid rgba(255,255,255,.06)}
.prompt-word{font-family:Georgia,serif;color:#e8c98a;line-height:1.3;margin-bottom:4px}
.prompt-sub{font-family:monospace;font-size:12px;color:rgba(255,255,255,.35);margin-bottom:4px}
.prompt-group{font-size:9px;color:rgba(255,255,255,.2);margin-top:8px;font-family:sans-serif}
.mastery-badge{position:absolute;top:12px;right:14px;font-size:10px;font-family:sans-serif;opacity:.7}
.star-badge{position:absolute;top:11px;left:14px;font-size:12px;opacity:.8}
/* Quiz input */
input[type=text]{width:100%;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:16px;outline:none;margin-bottom:12px;font-family:sans-serif}
input[type=text]:focus{border-color:rgba(201,169,110,.5)}
/* Feedback */
.feedback{border-radius:12px;padding:14px 16px;margin-bottom:14px}
.feedback.correct{background:rgba(122,196,154,.12);border:1px solid #7ac49a}
.feedback.wrong{background:rgba(212,122,143,.12);border:1px solid #d47a8f}
.feedback.close{background:rgba(230,180,80,.10);border:1px solid #e6b450}
.feedback-verdict{font-size:13px;font-weight:700;font-family:sans-serif;margin-bottom:6px}
.feedback.correct .feedback-verdict{color:#7ac49a}
.feedback.wrong   .feedback-verdict{color:#d47a8f}
.feedback.close   .feedback-verdict{color:#e6b450}
.feedback-answer{font-size:13px;color:#f0ebe0;font-family:sans-serif;margin-bottom:4px}
.feedback-yours{font-size:12px;color:rgba(255,255,255,.35);font-family:sans-serif}
.feedback-correction{font-size:12px;color:rgba(230,180,80,.75);font-family:sans-serif;margin-top:5px}
.feedback-correction em{font-style:italic;font-family:Georgia,serif}
.window-dots{display:flex;gap:3px;align-items:center;margin-top:8px}
.dot{width:8px;height:8px;border-radius:50%}
/* Results */
.result-row{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.04);border-radius:10px;padding:8px 12px;margin-bottom:5px}
.result-word{font-family:Georgia,serif;font-size:15px;color:#e8c98a;flex-shrink:0}
.result-trans{font-size:12px;color:rgba(255,255,255,.4);font-style:italic;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;font-family:sans-serif}
.result-acc{font-size:9px;font-family:sans-serif;flex-shrink:0}
/* Stats */
.stats-grid{display:flex;gap:6px;margin-top:8px}
.stat-box{flex:1;text-align:center;background:rgba(255,255,255,.04);border-radius:10px;padding:10px 4px}
.stat-num{font-size:20px;font-weight:700;font-family:Georgia,serif}
.stat-lbl{font-size:9px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-top:2px;line-height:1.3}
.meta{font-size:10px;color:rgba(255,255,255,.18);text-align:center;margin-top:8px;font-family:sans-serif}
.pool-info{font-size:11px;color:rgba(255,255,255,.3);text-align:center;margin-bottom:14px;font-family:sans-serif}
.row-btns{display:flex;gap:8px;margin-top:8px}
.score-big{font-size:52px;font-weight:700;font-family:Georgia,serif;text-align:center;margin-bottom:4px}
.score-sub{font-size:13px;color:rgba(255,255,255,.4);font-family:sans-serif;text-align:center}
.score-verdict{font-size:13px;font-weight:600;font-family:sans-serif;text-align:center;margin-top:6px;margin-bottom:20px}
/* Tabs */
.tabs{display:flex;gap:4px;background:rgba(255,255,255,.04);border-radius:14px;padding:4px;margin-bottom:20px;width:100%;max-width:480px}
.tab{flex:1;padding:8px;border-radius:10px;border:none;background:transparent;color:rgba(255,255,255,.4);font-size:12px;font-weight:700;font-family:sans-serif;letter-spacing:1px;text-transform:uppercase;cursor:pointer;transition:all .15s}
.tab.active{background:rgba(201,169,110,.2);color:#c9a96e}
/* Browse */
.search-wrap{position:relative;margin-bottom:14px}
.search-wrap input{margin-bottom:0;padding-left:36px}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:14px;opacity:.4;pointer-events:none}
.browse-count{font-size:11px;color:rgba(255,255,255,.25);text-align:center;margin-bottom:12px;font-family:sans-serif}
.view-toggle{display:flex;gap:4px;justify-content:center;margin-bottom:16px}
.view-btn{padding:5px 14px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:transparent;color:rgba(255,255,255,.35);font-size:11px;font-family:sans-serif;font-weight:700;cursor:pointer;transition:all .15s}
.view-btn.active{background:rgba(201,169,110,.15);border-color:#c9a96e;color:#c9a96e}
/* Flashcard */
.fc-wrap{perspective:1000px;width:100%;margin-bottom:20px;cursor:pointer;user-select:none}
.fc-inner{position:relative;width:100%;transform-style:preserve-3d;transition:transform .5s cubic-bezier(.4,0,.2,1)}
.fc-inner.flipped{transform:rotateY(180deg)}
.fc-front,.fc-back{width:100%;border-radius:18px;padding:36px 24px 28px;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.fc-front{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border:1px solid rgba(255,255,255,.08);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:220px;position:relative}
.fc-back{background:linear-gradient(145deg,#16213e 0%,#1a2a1e 100%);border:1px solid rgba(201,169,110,.2);position:absolute;top:0;left:0;transform:rotateY(180deg);overflow-y:auto;max-height:420px}
.fc-article{font-size:13px;color:rgba(255,255,255,.35);font-family:sans-serif;margin-bottom:4px}
.fc-word{font-family:Georgia,serif;font-size:38px;color:#e8c98a;line-height:1.2;margin-bottom:8px}
.fc-pron{font-family:monospace;font-size:13px;color:rgba(255,255,255,.3)}
.fc-type{font-size:10px;color:rgba(255,255,255,.18);margin-top:6px;font-family:sans-serif}
.fc-hint{font-size:10px;color:rgba(255,255,255,.15);margin-top:20px;font-family:sans-serif;letter-spacing:1px}
.fc-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.fc-nav-btn{padding:8px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:rgba(255,255,255,.5);font-size:13px;cursor:pointer;font-family:sans-serif}
.fc-nav-btn:disabled{opacity:.2;cursor:default}
.fc-counter{font-size:12px;color:rgba(255,255,255,.3);font-family:sans-serif}
/* Browse cards (list view) */
.bcard{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:12px;margin-bottom:8px;overflow:hidden;cursor:pointer;transition:border-color .15s}
.bcard:hover{border-color:rgba(201,169,110,.3)}
.bcard.open{border-color:rgba(201,169,110,.4)}
.bcard-head{display:flex;align-items:center;gap:10px;padding:10px 14px}
.bcard-left{display:flex;align-items:center;gap:10px;flex:1;min-width:0;overflow:hidden}
.bcard-word{font-family:Georgia,serif;font-size:17px;color:#e8c98a;flex-shrink:0;white-space:nowrap}
.bcard-pron{font-family:monospace;font-size:11px;color:rgba(255,255,255,.3);flex-shrink:0;white-space:nowrap}
.bcard-trans{font-size:12px;color:rgba(255,255,255,.5);font-style:italic;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:sans-serif}
.bcard-badges{display:flex;gap:4px;flex-shrink:0;align-items:center}
.bcard-body{padding:0 14px 14px;display:none}
.bcard.open .bcard-body{display:block}
/* Browse card back sections */
.bcard-section{margin-top:10px}
.bcard-section-label{font-size:9px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:4px}
.bcard-def{font-size:12px;color:rgba(255,255,255,.6);font-family:sans-serif;line-height:1.5}
.bcard-example{font-size:12px;font-family:sans-serif;line-height:1.7}
.bcard-ex-native{color:#e8c98a;font-family:Georgia,serif}
.bcard-ex-en{color:rgba(255,255,255,.35);font-style:italic}
.bcard-grammar{display:flex;flex-direction:column;gap:3px}
.bcard-grammar-row{display:flex;gap:8px;font-size:11px;font-family:sans-serif}
.bcard-grammar-lbl{color:rgba(201,169,110,.5);min-width:80px;flex-shrink:0}
.bcard-grammar-val{color:rgba(255,255,255,.55)}
.bcard-note{font-size:11px;color:#c9a96e;font-style:italic;font-family:sans-serif;line-height:1.5}
.bcard-etym{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;line-height:1.5}
.mastery-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
/* Edit / delete icon buttons */
.icon-btn{background:none;border:1px solid rgba(255,255,255,.1);cursor:pointer;padding:3px 10px;border-radius:6px;font-size:11px;color:rgba(255,255,255,.4);transition:all .15s;line-height:1.4;flex-shrink:0;font-family:system-ui,sans-serif}
.icon-btn:hover{color:rgba(255,255,255,.8);border-color:rgba(255,255,255,.25)}
.icon-btn.del:hover{color:#ff8a8a;border-color:rgba(220,60,60,.4)}
/* Study counter */
.counter{font-size:12px;color:rgba(255,255,255,.3);font-family:sans-serif;text-align:center;margin-bottom:8px}
.prog-wrap{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:20px;overflow:hidden;width:100%}
.prog-bar{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}
.nav-btn{padding:8px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:rgba(255,255,255,.5);font-size:13px;cursor:pointer;font-family:sans-serif}
/* Add tab */
textarea{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);border-radius:10px;color:#fff;font-family:monospace;font-size:12px;padding:12px 14px;resize:vertical;outline:none;min-height:200px;line-height:1.6}
textarea::placeholder{color:rgba(255,255,255,.2)}
.sec-field-label{font-size:10px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:6px;margin-top:14px}
select,input[type=text]{margin-bottom:8px}
select{width:100%;padding:10px 12px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:14px;outline:none;font-family:sans-serif}
select option{background:#1a1a2e;color:#fff}
.field-hint{font-size:11px;color:rgba(255,255,255,.2);font-family:sans-serif;margin:-4px 0 8px;line-height:1.5}
.radio-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.radio-btn{padding:6px 14px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:transparent;color:rgba(255,255,255,.4);font-size:12px;font-family:sans-serif;cursor:pointer;transition:all .15s}
.radio-btn.selected{border-color:#c9a96e;color:#c9a96e;background:rgba(201,169,110,.08)}
.add-mode-toggle{display:flex;gap:4px;background:rgba(255,255,255,.04);border-radius:12px;padding:4px;margin-bottom:14px}
.add-mode-btn{flex:1;padding:7px;border-radius:8px;border:none;background:transparent;color:rgba(255,255,255,.4);font-size:11px;font-weight:700;font-family:sans-serif;letter-spacing:.5px;text-transform:uppercase;cursor:pointer;transition:all .15s}
.add-mode-btn.active{background:rgba(201,169,110,.15);color:#c9a96e}
.add-preview-area{margin:12px 0;display:flex;flex-direction:column;gap:8px}
.gen-preview-card{padding:10px 14px;border-radius:10px;font-family:sans-serif}
.gen-preview-card.valid{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08)}
.gen-preview-card.invalid{background:rgba(212,122,143,.06);border:1px solid rgba(212,122,143,.25);color:rgba(212,122,143,.7);font-size:12px}
.add-hint{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px;font-family:sans-serif;font-size:12px;line-height:1.7;color:rgba(255,255,255,.5);margin-top:14px}
.add-tips{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05);border-radius:12px;padding:14px 16px;font-family:sans-serif;font-size:11px;line-height:1.6;margin-top:10px}
.add-tips-title{font-size:10px;color:rgba(201,169,110,.6);letter-spacing:1px;text-transform:uppercase;font-weight:700;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
@media(max-width:430px){
  .app{padding-left:10px;padding-right:10px;padding-top:56px}
  .prompt-card{padding:22px 14px 18px}
  .fc-front,.fc-back{padding:22px 14px 18px}
  .fc-word{font-size:30px}
  .score-big{font-size:40px}
  .tab{padding:10px 4px;font-size:11px}
  .btn-primary{padding:13px}
}
</style>
</head>
<body>
<a class="home-link" href="/">🧿 Home</a>
<button class="menu-btn" id="menu-btn" onclick="toggleMenu()" aria-label="Menu">&#9776;</button>
<div class="menu-overlay" id="menu-overlay" onclick="toggleMenu()"></div>
<nav class="menu-drawer" id="menu-drawer" aria-label="Navigation">
  <div class="menu-spacer"></div>
  <div id="menu-account"></div>
  <div class="menu-divider"></div>
  <a href="/community" class="menu-item"><span class="menu-item-icon">&#127757;</span>Community &amp; Presets</a>
  <div class="menu-divider"></div>
  <a href="/about" class="menu-item"><span class="menu-item-icon">&#127760;</span>About Lexilogio</a>
  <a href="/tutorial" class="menu-item"><span class="menu-item-icon">&#128218;</span>Tutorial</a>
  <a href="/donate" class="menu-item"><span class="menu-item-icon">&#9749;</span>Donate</a>
  <a href="/impressum" class="menu-item"><span class="menu-item-icon">&#128196;</span>Legal Notice</a>
</nav>
<div class="app">
  <div class="header">
    <div class="header-sub">__HEADER_SUB__</div>
    <div class="header-title">__HEADER_TITLE__</div>
  </div>
  <div id="preview-banner"></div>
</div>
<div class="tabs" id="tabs">
  <button class="tab active" onclick="switchTab('browse')">&#128218; Browse</button>
  <button class="tab" onclick="switchTab('study')">&#128221; Study</button>
  <button class="tab" onclick="switchTab('add')">&#10133; Add</button>
</div>
<div style="width:100%;max-width:480px;margin:12px auto 0;padding:0 14px 80px" id="content"></div>
<style>.tabs{width:100%;max-width:480px;margin:0 auto}</style>

<script>
// ── Helpers ────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function pill(key, label, active, onclick) {
  return `<span class="pill${active?' active':''}" onclick="${onclick}">${esc(String(label))}</span>`;
}
function tagLabel(t)   { return (LANG.tag_labels||{})[t]   || t; }
function groupLabel(g) { return (LANG.group_labels||{})[g] || g; }
function pillsWithExpand(items, renderFn, id) {
  if(items.length<=10) return '<div class="pills">'+items.map(renderFn).join('')+'</div>';
  const vis=items.slice(0,10).map(renderFn).join('');
  const hid=items.slice(10).map(renderFn).join('');
  const n=items.length-10;
  return `<div class="pills">${vis}<span id="${id}-x" style="display:none">${hid}</span><button class="pill" id="${id}-b" onclick="xPills('${id}',${n})">+${n} &#9660;</button></div>`;
}
function xPills(id,n){
  const x=document.getElementById(id+'-x'),b=document.getElementById(id+'-b');
  if(!x||!b) return;
  const open=x.style.display!=='none';
  x.style.display=open?'none':'contents';
  b.innerHTML=open?`+${n} &#9660;`:'&#9650; less';
}
function shuffle(a) {
  for (let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]];}
  return a;
}
function mkel(tag, cls, text) {
  const el=document.createElement(tag);
  if(cls) el.className=cls;
  if(text!=null) el.textContent=text;
  return el;
}

// ── Constants ─────────────────────────────────────────────────────────────────
const MASTERY_COLORS = {new:'#7ab3d4',learning:'#c9a96e',struggling:'#d47a8f',mastered:'#7ac49a'};
const MASTERY_LABELS = {new:'🆕 New',learning:'📘 Learning',struggling:'⚠️ Struggling',mastered:'✅ Mastered'};

// ── State ─────────────────────────────────────────────────────────────────────
let allCards=[], progress={}, tab='browse';
let studyFlipped=false;
let browseView='list', browseSearch='';
let browseGroups=new Set(), browseTags=new Set(), browseMastery='all';
let browseIdx=0, browseFlipped=false, browseOpen=new Set();
let quizPhase='setup';
let quizDir;
let quizGroups=new Set(), quizTags=new Set(), quizMastery=new Set(), quizCount=10;
let quizWords=[], quizIdx=0, quizResults=[], quizRetrying=false;
let quizOrigSet=[], droppedCards=new Set();
let quizPickMode=false, manualCards=new Set(), pickSearch='', pickGroups=new Set(), pickTags=new Set();
let addType=(LANG.word_types||['noun'])[0];
let genAddMode='bulk', genBulkParsed=null;

// ── Auth / user state ─────────────────────────────────────────────────────────
/* __USER__ */
const isGuest=!USER.id;
const DEP      = (USER && USER.departure_lang) || 'en';
const DEP_NAME = (USER && USER.departure_name) || 'English';
const DIR_FWD  = 'word→' + DEP;   // e.g. 'word→en', 'word→de'
const DIR_REV  = DEP + '→word';   // e.g. 'en→word', 'de→word'
quizDir = DIR_FWD;

(function _initMenu(){
  const mac=document.getElementById('menu-account');
  if(mac){
    if(isGuest){
      mac.innerHTML=
        '<a href="/auth/login" class="menu-item cta"><span class="menu-item-icon">&#128100;</span>Sign in</a>'+
        '<a href="/auth/signup" class="menu-item"><span class="menu-item-icon">&#10133;</span>Create account</a>';
    } else {
      const name=esc(USER.name||USER.email||'');
      const email=USER.name&&USER.email?`<div class="menu-account-email">${esc(USER.email)}</div>`:'';
      const admin=USER.is_admin?'<a href="/admin/submissions" class="menu-item"><span class="menu-item-icon">&#9881;&#65039;</span>Admin</a>':'';
      mac.innerHTML=
        `<div class="menu-account-info"><div class="menu-account-name">&#128100; ${name}</div>${email}</div>`+
        `<a href="/auth/logout" class="menu-item"><span class="menu-item-icon">&#128682;</span>Sign out</a>${admin}`;
    }
  }
  const b=document.getElementById('preview-banner');
  if(b&&isGuest){
    b.innerHTML='&#128065;&#128068;&#128065; Preview mode &mdash; <a href="/auth/signup">create a free account</a> to save progress and add cards';
  }
})();

function toggleMenu(){
  document.getElementById('menu-drawer').classList.toggle('open');
  document.getElementById('menu-overlay').classList.toggle('open');
}

// ── API ────────────────────────────────────────────────────────────────────────
const API_BASE=location.pathname.replace(/\\/$/,'');
async function api(path,opts){
  const r=await fetch(API_BASE+path,opts);
  return r.json();
}
async function apiPost(path,body){
  return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}

async function init(){
  [allCards,progress]=await Promise.all([
    api('/api/cards').then(d=>d.cards),
    api('/api/progress').then(d=>d.progress),
  ]);
  render();
}

// ── Mastery ────────────────────────────────────────────────────────────────────
function cardMastery(c){
  const e=(progress[String(c.id)]||{});
  const rw=e.rev_window||[];
  const rn=rw.length,racc=rn?rw.filter(Boolean).length/rn:0;
  const sd=e.spaced_days||0;
  if(rn>=5&&racc>=0.8&&sd>=3) return 'mastered';
  const w=e.window||[];
  if(w.length>=3&&w.filter(Boolean).length/w.length<0.4) return 'struggling';
  if(w.length>0)     return 'learning';
  return 'new';
}
function allGroups(){
  const s=new Set();allCards.forEach(c=>{if(c.group)s.add(c.group);});return[...s].sort();
}
function _stripGroup(s){
  return s.replace(/^\S+\s+/,'').trim().toLowerCase()||s.replace(/[^\w\sÀ-ɏͰ-Ͽ]/g,'').trim().toLowerCase();
}
function resolveGroup(raw){
  if(!raw) return raw;
  const t=_stripGroup(raw);
  if(!t) return raw;
  const match=allGroups().find(g=>_stripGroup(g)===t);
  return match||raw;
}
function allTagsList(){
  const m=new Map();
  allCards.forEach(c=>(c.tags||[]).forEach(t=>m.set(t,(m.get(t)||0)+1)));
  return[...m.entries()].sort((a,b)=>b[1]-a[1]).map(([t])=>t);
}

// ── Article / colour helpers ───────────────────────────────────────────────────
function _resolveArticleField(card){
  const rule=LANG.article_rule; if(!rule) return '';
  const direct=card[rule.based_on];
  if(direct&&rule.rules[direct]) return direct;
  if(direct){
    const k=Object.keys(rule.rules).find(k=>direct.toLowerCase().startsWith(k.toLowerCase()));
    if(k) return k;
  }
  if(card.grammar&&LANG.grammar_fields){
    const allFlds=Object.values(LANG.grammar_fields).flat();
    const topFld=allFlds.find(f=>f.top_level&&f.name===rule.based_on);
    if(topFld){
      const gEntry=card.grammar.find(g=>g.label===topFld.label);
      if(gEntry){
        const k=Object.keys(rule.rules).find(k=>gEntry.value.toLowerCase().startsWith(k.toLowerCase()));
        if(k) return k;
      }
    }
  }
  return '';
}
function articleFor(card){
  const rule=LANG.article_rule; if(!rule) return '';
  if(card.article) return card.article;
  const fv=_resolveArticleField(card); if(!fv) return '';
  const r=rule.rules[fv]; if(!r) return '';
  const word=(card.word||'').toLowerCase();
  const first=word[0]||'';
  if(r.vowel_start&&(rule.vowels||'aeiou').includes(first)) return r.vowel_start;
  if(r.prefix_overrides){
    const keys=Object.keys(r.prefix_overrides).sort((a,b)=>b.length-a.length);
    for(const pfx of keys){ if(word.startsWith(pfx)) return r.prefix_overrides[pfx]; }
  }
  return r.otherwise;
}
function articleColorFor(card){
  const colors=LANG.article_colors; if(!colors) return '';
  const fv=LANG.article_rule?_resolveArticleField(card):'';
  if(fv&&colors[fv]) return colors[fv];
  const w=(card.word||'').toLowerCase();
  for(const[art,col]of Object.entries(colors)){
    const a=art.toLowerCase();
    if(w.startsWith(a+' ')||(a.endsWith("'")&&w.startsWith(a))) return col;
  }
  return '';
}

// ── Card back HTML (shared by browse, study, quiz feedback) ───────────────────
function cardBackHTML(c){
  const gramRows=(c.grammar||[]).map(g=>
    `<div class="bcard-grammar-row"><span class="bcard-grammar-lbl">${esc(g.label)}</span><span class="bcard-grammar-val">${esc(g.value)}</span></div>`
  ).join('');
  let exHTML='';
  if(c.example){
    if(typeof c.example==='object'){
      const l=c.example[LANG.code]||'', e=c.example[DEP]||c.example.en||'';
      exHTML='<div class="bcard-example">';
      if(l) exHTML+=`<div class="bcard-ex-native">${esc(l)}</div>`;
      if(e) exHTML+=`<div class="bcard-ex-en">${esc(e)}</div>`;
      exHTML+='</div>';
    } else {
      exHTML=`<div class="bcard-example"><div class="bcard-ex-native">${esc(String(c.example))}</div></div>`;
    }
  }
  return (c.type?`<div style="font-size:10px;color:rgba(255,255,255,.2);font-family:sans-serif;margin-bottom:6px">${esc(c.type)}</div>`:'')
    +(gramRows?`<div class="bcard-section"><div class="bcard-section-label">Grammar</div><div class="bcard-grammar">${gramRows}</div></div>`:'')
    +(exHTML?`<div class="bcard-section"><div class="bcard-section-label">Example</div>${exHTML}</div>`:'')
    +(c.note?`<div class="bcard-section"><div class="bcard-section-label">Note</div><div class="bcard-note">&#128161; ${esc(c.note)}</div></div>`:'')
    +(c.etymology?`<div class="bcard-section"><div class="bcard-section-label">Etymology</div><div class="bcard-etym">&#128279; ${esc(c.etymology)}</div></div>`:'');
}

// ── Navigation history (mouse back/forward) ────────────────────────────────────
let _navHist=[],_navFwd=[];
function _navSnap(){return{tab,browseIdx};}
function _navPush(){_navHist.push(_navSnap());_navFwd=[];}
function _navRestore(s){
  tab=s.tab;browseIdx=s.browseIdx;
  document.querySelectorAll('.tab').forEach((b,i)=>b.classList.toggle('active',['browse','study','add'][i]===tab));
  render();
}
function navBack(){if(!_navHist.length)return;_navFwd.push(_navSnap());_navRestore(_navHist.pop());}
function navForward(){if(!_navFwd.length)return;_navHist.push(_navSnap());_navRestore(_navFwd.pop());}
document.addEventListener('mouseup',e=>{if(e.button===3){e.preventDefault();navBack();}else if(e.button===4){e.preventDefault();navForward();}});
document.addEventListener('mousedown',e=>{if(e.button===3||e.button===4)e.preventDefault();});

// ── Routing ────────────────────────────────────────────────────────────────────
function switchTab(t){
  _navPush();
  tab=t;
  document.querySelectorAll('.tab').forEach((b,i)=>
    b.classList.toggle('active',['browse','study','add'][i]===t));
  if(t==='study'){quizPhase='setup';studyFlipped=false;}
  render();
}
function render(){
  if(tab==='study')       renderQuiz();
  else if(tab==='browse') renderBrowse();
  else if(tab==='add')    { const el=document.getElementById('content');el.innerHTML='';el.appendChild(renderAdd()); }
}


// ── Browse ─────────────────────────────────────────────────────────────────────
function browseFilter(){
  const q=browseSearch.toLowerCase();
  return allCards.filter(c=>{
    if(browseGroups.size&&!browseGroups.has(c.group||'')) return false;
    if(browseTags.size){for(const t of browseTags){if(!(c.tags||[]).includes(t))return false;}}
    if(browseMastery!=='all'&&cardMastery(c)!==browseMastery) return false;
    if(q&&!(c.word+' '+c.translation+(c.pronunciation||'')).toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderBrowse(){
  const filtered=browseFilter(), groups=allGroups(), tags=allTagsList();
  const filters=`
    ${groups.length?`<div class="sec">
      <div class="sec-label">Groups <a onclick="browseGroups=new Set();browseIdx=0;renderBrowse()">clear</a></div>
      ${pillsWithExpand(groups,g=>pill(g,groupLabel(g),browseGroups.has(g),`toggleBrowseGroup('${esc(g)}')`), 'bg-g')}
    </div>`:''}
    ${tags.length?`<div class="sec">
      <div class="sec-label">Tags <a onclick="browseTags=new Set();renderBrowse()">clear</a></div>
      ${pillsWithExpand(tags,t=>pill(t,tagLabel(t),browseTags.has(t),`toggleBrowseTag('${esc(t)}')`), 'bg-t')}
    </div>`:''}
    <div class="sec">
      <div class="sec-label">Knowledge level</div>
      <div class="pills">
        ${['all','new','learning','struggling','mastered'].map(k=>pill(k,k==='all'?'🎲 All':MASTERY_LABELS[k],browseMastery===k,`setBrowseMastery('${k}')`)).join('')}
      </div>
    </div>
    <div class="search-wrap">
      <span class="search-icon">&#128269;</span>
      <input type="text" placeholder="Search words, translations&hellip;" value="${esc(browseSearch)}"
        oninput="browseSearch=this.value;browseIdx=0;browseFlipped=false;renderBrowse()">
    </div>
    <div class="view-toggle">
      <button class="view-btn${browseView==='list'?' active':''}" onclick="setBrowseView('list')">&#9776; List</button>
      <button class="view-btn${browseView==='cards'?' active':''}" onclick="setBrowseView('cards')">&#9634; Cards</button>
    </div>
    <div class="browse-count">${filtered.length} word${filtered.length!==1?'s':''}</div>`;

  if(browseView==='cards'){
    const idx=Math.min(browseIdx,Math.max(filtered.length-1,0));browseIdx=idx;
    if(!filtered.length){
      document.getElementById('content').innerHTML=filters+'<div style="text-align:center;color:rgba(255,255,255,.2);font-family:sans-serif;padding:40px 0">No words match</div>';
      return;
    }
    const c=filtered[idx];
    const art=articleFor(c),gc=articleColorFor(c);
    const lvl=cardMastery(c),borderColor=gc||(MASTERY_COLORS[lvl]+'55');
    document.getElementById('content').innerHTML=filters+`
      <div class="fc-nav">
        <button class="fc-nav-btn" onclick="fcNav(-1)" ${idx===0?'disabled':''}>&#8592; Prev</button>
        <span class="fc-counter">${idx+1} / ${filtered.length}</span>
        ${c._user?`<button class="icon-btn" title="Edit" onclick="editBrowseCard('${c.id}')">Edit</button>
        <button class="icon-btn del" title="Delete" onclick="deleteBrowseCard('${c.id}')">Delete</button>`:''}
        <button class="fc-nav-btn" onclick="fcNav(1)" ${idx>=filtered.length-1?'disabled':''}>Next &#8594;</button>
      </div>
      <div class="fc-wrap" onclick="fcFlip()">
        <div class="fc-inner${browseFlipped?' flipped':''}" id="fc-inner">
          <div class="fc-front" style="border-color:${borderColor};${gc?'border-width:2px':''}">
            ${c.priority?'<div class="star-badge">&#11088;</div>':''}
            ${art?`<div class="fc-article" style="${gc?`color:${gc};font-size:15px;font-weight:700`:''}">${esc(art)}</div>`:''}
            <div class="fc-word" style="${gc?`color:${gc}`:''}">${esc(c.word)}</div>
            ${c.pronunciation?`<div class="fc-pron">[${esc(c.pronunciation)}]</div>`:''}
            ${c.type?`<div class="fc-type">${esc(c.type)}</div>`:''}
            <div class="fc-hint">tap to flip</div>
          </div>
          <div class="fc-back">
            <div style="font-family:Georgia,serif;font-size:22px;color:#e8c98a;margin-bottom:12px">${esc(c.translation)}</div>
            ${cardBackHTML(c)}
          </div>
        </div>
      </div>`;
    requestAnimationFrame(()=>{
      const inner=document.getElementById('fc-inner');
      if(inner) inner.style.height=inner.querySelector('.fc-front').offsetHeight+'px';
    });
  } else {
    const rows=filtered.map(c=>{
      const gc=articleColorFor(c),art=articleFor(c),lvl=cardMastery(c),isOpen=browseOpen.has(String(c.id));
      const tagBadges=(c.tags||[]).map(t=>`<span style="font-size:9px;opacity:.5">${esc(t.split(':').pop())}</span>`).join('');
      return `<div class="bcard${isOpen?' open':''}" style="${gc?`border-left:3px solid ${gc}`:''}" onclick="toggleBCard('${c.id}')">
        <div class="bcard-head">
          <div class="mastery-dot" style="background:${MASTERY_COLORS[lvl]}" title="${lvl}"></div>
          ${c.priority?'<span style="font-size:11px;flex-shrink:0">&#11088;</span>':''}
          <div class="bcard-left">
            <span class="bcard-word" style="${gc?`color:${gc}`:''}">${art?esc(art)+' ':''}${esc(c.word)}</span>
            ${c.pronunciation?`<span class="bcard-pron">[${esc(c.pronunciation)}]</span>`:''}
            <span class="bcard-trans">${esc(c.translation)}</span>
          </div>
          <span class="bcard-badges">${tagBadges}</span>
          ${c._user?`<button class="icon-btn" title="Edit" onclick="event.stopPropagation();editBrowseCard('${c.id}')">Edit</button>
          <button class="icon-btn del" title="Delete" onclick="event.stopPropagation();deleteBrowseCard('${c.id}')">Delete</button>`:''}
          <span style="font-size:10px;color:rgba(255,255,255,.2);flex-shrink:0">${isOpen?'&#9650;':'&#9660;'}</span>
        </div>
        ${isOpen?`<div class="bcard-body">${cardBackHTML(c)}</div>`:''}
      </div>`;
    }).join('');
    document.getElementById('content').innerHTML=filters+(rows||'<div style="text-align:center;color:rgba(255,255,255,.2);font-family:sans-serif;padding:40px 0">No words match</div>');
  }
}

function setBrowseView(v){browseView=v;browseFlipped=false;browseIdx=0;renderBrowse();}
function fcFlip(){browseFlipped=!browseFlipped;const el=document.getElementById('fc-inner');if(el)el.classList.toggle('flipped',browseFlipped);}
function fcNav(dir){_navPush();const f=browseFilter();browseIdx=Math.max(0,Math.min(browseIdx+dir,f.length-1));browseFlipped=false;renderBrowse();}
function toggleBCard(id){browseOpen.has(id)?browseOpen.delete(id):browseOpen.add(id);renderBrowse();}
function toggleBrowseGroup(g){browseGroups.has(g)?browseGroups.delete(g):browseGroups.add(g);browseIdx=0;renderBrowse();}
function toggleBrowseTag(t){browseTags.has(t)?browseTags.delete(t):browseTags.add(t);browseIdx=0;renderBrowse();}
function setBrowseMastery(m){browseMastery=m;browseIdx=0;renderBrowse();}

function deleteBrowseCard(id){
  const card=allCards.find(c=>String(c.id)===id);
  const word=card?.word||'this card';
  const overlay=document.createElement('div');
  overlay.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:24px';
  overlay.innerHTML=`<div style="background:#1a1a2e;border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:28px 24px;max-width:320px;width:100%;font-family:system-ui,sans-serif">
    <div style="font-size:15px;color:#fff;margin-bottom:8px;font-weight:600">Delete card?</div>
    <div style="font-size:13px;color:rgba(255,255,255,.45);margin-bottom:24px">&#8220;${word}&#8221; will be permanently removed.</div>
    <div style="display:flex;gap:10px">
      <button id="del-cancel" style="flex:1;padding:10px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:transparent;color:rgba(255,255,255,.5);font-size:13px;cursor:pointer;font-family:system-ui,sans-serif">Cancel</button>
      <button id="del-confirm" style="flex:1;padding:10px;border-radius:10px;border:none;background:rgba(220,60,60,.25);color:#ff8a8a;font-size:13px;font-weight:600;cursor:pointer;font-family:system-ui,sans-serif">Delete</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#del-cancel').onclick=()=>overlay.remove();
  overlay.querySelector('#del-confirm').onclick=async()=>{
    overlay.remove();
    await apiPost('/api/delete',{id});
    allCards=(await api('/api/cards')).cards;
    browseOpen.delete(id);renderBrowse();
  };
}

function editBrowseCard(id){
  const card=allCards.find(c=>String(c.id)===id);
  if(!card){alert('Only user-added cards can be edited.');return;}
  // Switch to Add tab in edit mode
  switchTab('add');
  const el=document.getElementById('content');
  el.innerHTML='';
  el.appendChild(renderAdd(card));
}

// ── Quiz ──────────────────────────────────────────────────────────────────────
function quizPool(){
  return allCards.filter(c=>{
    if(quizGroups.size&&!quizGroups.has(c.group||'')) return false;
    if(quizTags.size){for(const t of quizTags){if(!(c.tags||[]).includes(t))return false;}}
    if(quizMastery.size&&!quizMastery.has(cardMastery(c))) return false;
    return true;
  });
}

function renderQuiz(){
  if(quizPhase==='setup')   renderQuizSetup();
  else if(quizPhase==='study') renderStudyCards();
  else if(quizPhase==='quiz')  renderQuizQuestion();
  else                         renderQuizResults();
}

function renderQuizSetup(){
  const groups=allGroups(),tags=allTagsList(),pool=quizPool();
  const actualCount=Math.min(quizCount,pool.length);
  const ms={};
  ['new','learning','struggling','mastered'].forEach(k=>ms[k]=allCards.filter(c=>cardMastery(c)===k).length);
  const canGo=quizPickMode?manualCards.size>0:pool.length>0;

  document.getElementById('content').innerHTML=`
    <div class="sec">
      <div class="sec-label">Direction</div>
      <div class="pills">
        ${pill('w→e',LANG.name+' → '+DEP_NAME,quizDir===DIR_FWD,'setQuizDir(DIR_FWD)')}
        ${pill('e→w',DEP_NAME+' → '+LANG.name,quizDir===DIR_REV,'setQuizDir(DIR_REV)')}
      </div>
    </div>
    <div class="sec" style="border-top:1px solid rgba(255,255,255,.06);padding-top:16px">
      <div class="sec-label">Selection mode</div>
      <div class="pills">
        ${pill('filter','🔍 Filter',!quizPickMode,"setPickMode(false)")}
        ${pill('pick','☑ Browse & pick',quizPickMode,"setPickMode(true)")}
      </div>
    </div>
    ${quizPickMode ? renderPickPanel() : `
    ${groups.length?`<div class="sec">
      <div class="sec-label">Groups <a onclick="quizGroups=new Set();renderQuiz()">clear</a></div>
      ${pillsWithExpand(groups,g=>pill(g,groupLabel(g),quizGroups.has(g),`toggleQuizGroup('${esc(g)}')`), 'qz-g')}
    </div>`:''}
    ${tags.length?`<div class="sec">
      <div class="sec-label">Tags</div>
      ${pillsWithExpand(tags,t=>pill(t,tagLabel(t),quizTags.has(t),`toggleQuizTag('${esc(t)}')`), 'qz-t')}
    </div>`:''}
    <div class="sec">
      <div class="sec-label">Knowledge level</div>
      <div class="pills">
        ${pill('all','🎲 All',quizMastery.size===0,"quizMastery=new Set();renderQuiz()")}
        ${['new','learning','struggling','mastered'].map(k=>pill(k,MASTERY_LABELS[k],quizMastery.has(k),`toggleQuizMastery('${k}')`)).join('')}
      </div>
    </div>
    <div class="sec">
      <div class="sec-label">Number of words</div>
      <div class="pills">${[5,10,15,20].map(n=>pill(n,n,quizCount===n,`setQuizCount(${n})`)).join('')}</div>
    </div>
    <div class="pool-info">
      ${pool.length} word${pool.length!==1?'s':''} match your filters
      ${pool.length>0&&actualCount<quizCount?' &mdash; quiz will use '+actualCount:''}
    </div>`}
    ${canGo?`<div class="row-btns" style="margin-top:0">
      <button class="btn-secondary" style="flex:1" onclick="startStudy()">&#128218; Study</button>
      <button class="btn-primary" style="margin-top:0;flex:2" onclick="startQuiz()">&#127919; Start Quiz &#8594;${quizPickMode?' ('+manualCards.size+')':''}</button>
    </div>`:''}
    <div style="font-size:10px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;text-align:center;margin-top:28px;margin-bottom:8px">Your Progress</div>
    <div class="stats-grid">
      ${['new','learning','struggling','mastered'].map(lvl=>`
        <div class="stat-box" style="border:1px solid ${MASTERY_COLORS[lvl]}33">
          <div class="stat-num" style="color:${MASTERY_COLORS[lvl]}">${ms[lvl]}</div>
          <div class="stat-lbl">${MASTERY_LABELS[lvl]}</div>
        </div>`).join('')}
    </div>
    <div class="meta">Based on last 10 attempts &middot; ${allCards.length} words total</div>`;
}

function setQuizDir(d){quizDir=d;renderQuiz();}
function setQuizCount(n){quizCount=n;renderQuiz();}
function toggleQuizGroup(g){quizGroups.has(g)?quizGroups.delete(g):quizGroups.add(g);renderQuiz();}
function toggleQuizTag(t){quizTags.has(t)?quizTags.delete(t):quizTags.add(t);renderQuiz();}
function toggleQuizMastery(k){quizMastery.has(k)?quizMastery.delete(k):quizMastery.add(k);renderQuiz();}
function setPickMode(on){quizPickMode=on;pickSearch='';pickGroups=new Set();pickTags=new Set();renderQuiz();}
function togglePickCard(id,checked){if(checked)manualCards.add(id);else manualCards.delete(id);renderQuiz();}
function togglePickGroup(g){pickGroups.has(g)?pickGroups.delete(g):pickGroups.add(g);renderQuiz();}
function togglePickTag(t){pickTags.has(t)?pickTags.delete(t):pickTags.add(t);renderQuiz();}
function _pickFiltered(){
  const q=pickSearch.toLowerCase();
  return allCards.filter(c=>{
    if(pickGroups.size&&!pickGroups.has(c.group||'')) return false;
    if(pickTags.size){for(const t of pickTags){if(!(c.tags||[]).includes(t))return false;}}
    if(q&&!(c.word+' '+c.translation).toLowerCase().includes(q)) return false;
    return true;
  });
}
function renderPickPanel(){
  const filtered=_pickFiltered();
  const groups=allGroups(),tags=allTagsList();
  const groupPills=groups.map(g=>`<span class="pill${pickGroups.has(g)?' active':''}" onclick="togglePickGroup('${esc(g)}')">${esc(groupLabel(g))}</span>`).join('');
  const tagPills=tags.map(t=>`<span class="pill${pickTags.has(t)?' active':''}" onclick="togglePickTag('${esc(t)}')">${esc(tagLabel(t))}</span>`).join('');
  const rows=filtered.slice(0,100).map(c=>{
    const checked=manualCards.has(c.id);
    return `<label style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;font-family:sans-serif">
      <input type="checkbox" ${checked?'checked':''} onchange="togglePickCard(${c.id},this.checked)" style="accent-color:#c9a96e;flex-shrink:0">
      <span style="font-family:Georgia,serif;font-size:14px;color:#e8c98a;flex-shrink:0">${esc(c.word)}</span>
      <span style="font-size:11px;color:rgba(255,255,255,.4);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.translation)}</span>
      ${c.group?`<span style="font-size:10px;color:rgba(255,255,255,.2);flex-shrink:0;font-family:sans-serif">${esc(groupLabel(c.group))}</span>`:''}
    </label>`;
  }).join('');
  const more=filtered.length>100?`<div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;padding:4px 0">${filtered.length-100} more — refine to see them</div>`:'';
  return `<div style="border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px;margin-top:8px">
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
      <span style="font-size:14px;color:rgba(255,255,255,.4)">&#128269;</span>
      <input type="text" placeholder="Search words…" value="${esc(pickSearch)}"
        oninput="pickSearch=this.value;renderQuiz()"
        style="flex:1;background:transparent;border:none;outline:none;color:#fff;font-size:13px;font-family:sans-serif">
    </div>
    ${groups.length?`<div class="pills" style="margin-bottom:6px">${groupPills}</div>`:''}
    ${tags.length?`<div class="pills" style="margin-bottom:6px">${tagPills}</div>`:''}
    <div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-bottom:6px">
      ${filtered.length} word${filtered.length!==1?'s':''} &middot; ${manualCards.size} selected
      ${manualCards.size?'<a onclick="manualCards=new Set();renderQuiz()" style="color:#c9a96e;cursor:pointer;margin-left:8px">clear</a>':''}
    </div>
    <div style="max-height:260px;overflow-y:auto">${rows}${more}</div>
  </div>`;
}

function _buildQuizWords(){
  if(quizPickMode&&manualCards.size)
    return shuffle(allCards.filter(c=>manualCards.has(c.id)));
  return shuffle([...quizPool()]).slice(0,quizCount);
}
function startQuiz(){
  quizWords=_buildQuizWords();
  quizOrigSet=[...quizWords];
  droppedCards=new Set();
  quizIdx=0;quizResults=[];quizRetrying=false;quizPhase='quiz';renderQuizQuestion();
}
function startStudy(){
  quizWords=_buildQuizWords();
  quizIdx=0;studyFlipped=false;quizPhase='study';renderStudyCards();
}
function startQuizFromStudy(){
  quizIdx=0;quizResults=[];quizRetrying=false;studyFlipped=false;
  quizPhase='quiz';renderQuiz();
}
function sNav(dir){
  quizIdx=Math.max(0,Math.min(quizIdx+dir,quizWords.length-1));
  studyFlipped=false;renderStudyCards();
}
function sFlip(){
  studyFlipped=!studyFlipped;
  const el=document.getElementById('s-inner');
  if(el) el.classList.toggle('flipped',studyFlipped);
}
function renderStudyCards(){
  if(!quizWords.length){quizPhase='setup';renderQuizSetup();return;}
  const card=quizWords[quizIdx];
  const art=articleFor(card),gc=articleColorFor(card);
  const lvl=cardMastery(card),borderColor=gc||(MASTERY_COLORS[lvl]+'55');
  const mastLbl=lvl!=='new'?MASTERY_LABELS[lvl]:null;
  const pct=Math.round((quizIdx+1)/quizWords.length*100);
  const atEnd=quizIdx>=quizWords.length-1;

  document.getElementById('content').innerHTML=`
    <div class="prog-wrap"><div class="prog-bar" style="width:${pct}%"></div></div>
    <div class="fc-nav">
      <button class="fc-nav-btn" onclick="sNav(-1)" ${quizIdx===0?'disabled':''}>&#8592; Prev</button>
      <span class="fc-counter">${quizIdx+1} / ${quizWords.length}</span>
      <button class="fc-nav-btn" onclick="sNav(1)" ${atEnd?'disabled':''}>Next &#8594;</button>
    </div>
    <div class="fc-wrap" onclick="sFlip()">
      <div class="fc-inner${studyFlipped?' flipped':''}" id="s-inner">
        <div class="fc-front" style="border-color:${borderColor};${gc?'border-width:2px':''}">
          ${card.priority?'<div class="star-badge">&#11088;</div>':''}
          ${mastLbl?`<div class="mastery-badge" style="color:${MASTERY_COLORS[lvl]}">${mastLbl}</div>`:''}
          ${art?`<div class="fc-article" style="${gc?`color:${gc};font-size:15px;font-weight:700`:''}">${esc(art)}</div>`:''}
          <div class="fc-word" style="${gc?`color:${gc}`:''}"> ${esc(card.word)}</div>

          ${card.pronunciation?`<div class="fc-pron">[${esc(card.pronunciation)}]</div>`:''}
          ${card.type?`<div class="fc-type">${esc(card.type)}</div>`:''}
          <div class="fc-hint">tap to flip</div>
        </div>
        <div class="fc-back">
          <div style="font-family:Georgia,serif;font-size:22px;color:#e8c98a;margin-bottom:12px">${esc(card.translation)}</div>
          ${cardBackHTML(card)}
        </div>
      </div>
    </div>
    <div class="row-btns" style="margin-top:0">
      <button class="btn-secondary" style="flex:1" onclick="quizPhase='setup';studyFlipped=false;renderQuiz()">&#8592; Back</button>
      <button class="btn-primary" style="margin-top:0;flex:2" onclick="startQuizFromStudy()">&#127919; Start Quiz &#8594;</button>
    </div>`;
  requestAnimationFrame(()=>{
    const inner=document.getElementById('s-inner');
    if(inner){const f=inner.querySelector('.fc-front');if(f)inner.style.height=f.offsetHeight+'px';}
  });
}

function renderQuizQuestion(){
  const card=quizWords[quizIdx];
  const isW2E=quizDir===DIR_FWD;
  const art=articleFor(card),gc=articleColorFor(card);
  const prompt=isW2E?(art?art+' ':'')+card.word:card.translation;
  const promptSize=isW2E?'36px':'22px';
  const promptColor=(isW2E&&gc)?gc:'#e8c98a';
  const promptSub=isW2E?(card.pronunciation?'['+card.pronunciation+']':''):(card.type||'');
  const lvl=cardMastery(card);
  const mastLbl=lvl!=='new'?MASTERY_LABELS[lvl]:null;
  const pct=((quizIdx+1)/quizWords.length*100).toFixed(1);

  document.getElementById('content').innerHTML=`
    <div class="progress-label">
      <span>${isW2E?LANG.name+' → '+DEP_NAME:DEP_NAME+' → '+LANG.name}</span>
      <span>${quizIdx+1} / ${quizWords.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="prompt-card" style="border-color:${gc?gc+'33':'rgba(255,255,255,.06)'}">
      ${card.priority?'<div class="star-badge">&#11088;</div>':''}
      ${mastLbl?`<div class="mastery-badge" style="color:${MASTERY_COLORS[lvl]}">${mastLbl}</div>`:''}
      <div class="prompt-word" style="font-size:${promptSize};color:${promptColor}">${esc(prompt)}</div>
      ${promptSub?`<div class="prompt-sub">${esc(promptSub)}</div>`:''}
      ${card.group?`<div class="prompt-group">${esc(groupLabel(card.group))}</div>`:''}
    </div>
    <input type="text" id="answer-input" autofocus
      placeholder="${isW2E?'Type the '+DEP_NAME+' translation…':'Type the '+LANG.name+' word…'}"
      onkeydown="if(event.key==='Enter')checkAnswer()">
    <div class="row-btns">
      <button class="btn-primary" style="margin-top:0" onclick="checkAnswer()">Check &#8629;</button>
      <button class="btn-secondary" onclick="skipWord()">Skip</button>
      <button class="btn-secondary" onclick="dropWord()" title="Remove this word from this direction">Drop &#10005;</button>
    </div>
    <div style="text-align:center;margin-top:8px">
      <a onclick="switchQuizDir()" style="font-size:12px;color:rgba(255,255,255,.35);cursor:pointer;text-decoration:underline">
        &#8645; Switch to ${isW2E?DEP_NAME+' &rarr; '+LANG.name:LANG.name+' &rarr; '+DEP_NAME}
      </a>
    </div>`;
  document.getElementById('answer-input')?.focus();
}

async function checkAnswer(){
  const input=document.getElementById('answer-input');
  if(!input) return;
  const guess=input.value.trim();
  const card=quizWords[quizIdx];
  const correct=quizDir===DIR_FWD?card.translation:card.word;
  const res=await apiPost('/api/check',{id:card.id,guess,correct,direction:quizDir});
  progress=(await api('/api/progress')).progress;

  if(res.result==='close'&&!quizRetrying){
    quizRetrying=true;
    showCloseFeedback(card,guess);
    return;
  }
  const score=res.result==='correct'?1:quizRetrying?0.5:0;
  quizRetrying=false;
  quizResults.push({card,guess,score,result:res.result});
  showFeedback(card,guess,res.result);
}

async function skipWord(){
  const card=quizWords[quizIdx];
  const correct=quizDir===DIR_FWD?card.translation:card.word;
  await apiPost('/api/check',{id:card.id,guess:'',correct,direction:quizDir});
  progress=(await api('/api/progress')).progress;
  const score=quizRetrying?0.5:0;
  quizRetrying=false;
  quizResults.push({card,guess:'',score,result:'wrong'});
  showFeedback(card,'','wrong');
}

function dropWord(){
  const card=quizWords[quizIdx];
  droppedCards.add(card.id);
  quizWords.splice(quizIdx,1);quizRetrying=false;
  if(!quizWords.length){quizPhase='results';renderQuiz();return;}
  if(quizIdx>=quizWords.length) quizIdx=quizWords.length-1;
  renderQuizQuestion();
}
function switchQuizDir(){
  quizDir=quizDir===DIR_FWD?DIR_REV:DIR_FWD;
  droppedCards=new Set();
  quizWords=shuffle([...quizOrigSet]);
  quizIdx=0;quizRetrying=false;
  renderQuizQuestion();
}

function showCloseFeedback(card,guess){
  const isW2E=quizDir===DIR_FWD;
  const pct=((quizIdx+1)/quizWords.length*100).toFixed(1);
  document.getElementById('content').innerHTML=`
    <div class="progress-label">
      <span>${quizIdx+1} / ${quizWords.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="feedback close">
      <div class="feedback-verdict">&#126; Almost &mdash; check your spelling</div>
      <div class="feedback-yours">You wrote: ${esc(guess)}</div>
    </div>
    <input type="text" id="answer-input" autofocus
      placeholder="${isW2E?'Try again in '+DEP_NAME+'…':'Try again in '+LANG.name+'…'}"
      onkeydown="if(event.key==='Enter')checkAnswer()">
    <div class="row-btns">
      <button class="btn-primary" style="margin-top:0" onclick="checkAnswer()">Try Again &#8629;</button>
      <button class="btn-secondary" onclick="skipWord()">Give Up</button>
    </div>`;
  document.getElementById('answer-input')?.focus();
}

function _normStr(s){
  return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');
}
function _spellNote(guess,correctAnswer){
  if(!guess) return '';
  const g=guess.trim();
  const alts=correctAnswer.split(/[,\/]/).map(s=>s.trim()).filter(Boolean);
  if(alts.some(a=>a.toLowerCase()===g.toLowerCase())) return '';
  const gn=_normStr(g);
  const accentMatch=alts.find(a=>_normStr(a)===gn);
  if(accentMatch){
    return `<div class="feedback-correction">Watch the spelling: you typed <em>${esc(g)}</em>, correct form is <em>${esc(accentMatch)}</em></div>`;
  }
  return `<div class="feedback-correction">You typed <em>${esc(g)}</em>, correct spelling is <em>${esc(alts[0])}</em></div>`;
}
function showFeedback(card,guess,result){
  const isW2E=quizDir===DIR_FWD;
  const correct=result==='correct';
  const gc=articleColorFor(card),art=articleFor(card);
  const correctAnswer=isW2E?card.translation:(art?art+' ':'')+card.word;
  const w=(progress[String(card.id)]||{}).window||[];
  const dots=w.map((v,i)=>{
    const opacity=0.3+0.7*(i/Math.max(w.length-1,1));
    const color=v?'#7ac49a':'#d47a8f';
    return `<div class="dot" style="background:${color};opacity:${opacity}"></div>`;
  }).join('');
  const fbClass=correct?'correct':result==='close'?'close':'wrong';
  const verdict=correct?'&#10003; Correct!':'&#10007; Not quite';
  const pct=((quizIdx+1)/quizWords.length*100).toFixed(1);

  document.getElementById('content').innerHTML=`
    <div class="progress-label">
      <span>${quizIdx+1} / ${quizWords.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="feedback ${fbClass}">
      <div class="feedback-verdict">${verdict}</div>
      <div class="feedback-answer">
        <span style="color:rgba(255,255,255,.4);font-size:11px">${isW2E?LANG.name:DEP_NAME}: </span>
        <strong style="font-family:Georgia,serif;${gc&&!isW2E?`color:${gc}`:''}">${esc(isW2E?(art?art+' ':'')+card.word:card.translation)}</strong>
      </div>
      <div class="feedback-answer">
        <span style="color:rgba(255,255,255,.4);font-size:11px">${isW2E?DEP_NAME:LANG.name}: </span>
        <strong style="font-family:Georgia,serif;${gc&&isW2E?`color:${gc}`:''}">${esc(correctAnswer)}</strong>
      </div>
      ${correct?_spellNote(guess,correctAnswer):guess?`<div class="feedback-yours">You wrote: ${esc(guess)}</div>`:''}
      ${w.length?`<div class="window-dots"><span style="font-size:9px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-right:2px">last ${w.length}:</span>${dots}</div>`:''}
    </div>
    <div style="padding:0 0 12px">${cardBackHTML(card)}</div>
    <button class="btn-primary" id="next-btn" onclick="nextWord()">
      ${quizIdx+1>=quizWords.length?'See Results &#8594;':'Next Word &#8594;'}
    </button>
    <div style="font-size:10px;color:rgba(255,255,255,.2);font-family:sans-serif;text-align:center;margin-top:6px">press Enter to continue</div>`;
  document.addEventListener('keydown',_quizEnter);
}

function _quizEnter(e){if(e.key==='Enter'){e.preventDefault();document.removeEventListener('keydown',_quizEnter);nextWord();}}
function nextWord(){
  document.removeEventListener('keydown',_quizEnter);
  quizIdx++;
  if(quizIdx>=quizWords.length){quizPhase='results';renderQuizResults();}
  else renderQuizQuestion();
}

function renderQuizResults(){
  const total=quizResults.length;
  const totalScore=quizResults.reduce((s,r)=>s+r.score,0);
  const pct=total?Math.round(totalScore/total*100):0;
  let verdict,color;
  if(pct>=90){verdict='Excellent!';color='#7ac49a';}
  else if(pct>=70){verdict='Well done!';color='#c9a96e';}
  else if(pct>=50){verdict='Keep going!';color='#e8c98a';}
  else{verdict='More practice needed.';color='#d47a8f';}

  const rows=quizResults.map(r=>{
    const stat=progress[String(r.card.id)];
    const n=stat?.window?.length||0;
    const acc=n?Math.round(stat.window.filter(Boolean).length/n*100):null;
    const lbl=acc!==null?`${acc}% (${n})`:null;
    const lvl=cardMastery(r.card);
    const gc=articleColorFor(r.card);
    return `<div class="result-row" style="border-color:${r.score===1?'rgba(122,196,154,.3)':r.score===0.5?'rgba(230,180,80,.3)':'rgba(212,122,143,.3)'}">
      <span>${r.score===1?'&#9989;':r.score===0.5?'&#12336;&#65038;':'&#10060;'}</span>
      <span class="result-word"${gc?` style="color:${gc}"`:''}">${esc(r.card.word)}</span>
      <span class="result-trans">${esc(r.card.translation)}</span>
      ${lbl?`<span class="result-acc" style="color:${MASTERY_COLORS[lvl]}">${lbl}</span>`:''}
    </div>`;
  }).join('');

  document.getElementById('content').innerHTML=`
    <div class="score-big" style="color:${color}">${pct}%</div>
    <div class="score-sub">${totalScore.toFixed(1)} / ${total} points</div>
    <div class="score-verdict" style="color:${color}">${verdict}</div>
    ${rows}
    <div class="row-btns" style="margin-top:16px;flex-wrap:wrap">
      <button class="btn-secondary" style="flex:1" onclick="quizPhase='setup';renderQuiz()">&#8592; New Setup</button>
      <button class="btn-secondary" style="flex:1" onclick="flipQuizDir()">&#8644; Flip direction</button>
      <button class="btn-primary" style="margin-top:0;flex:1" onclick="restartQuiz()">Again &#8594;</button>
    </div>`;
}

function restartQuiz(){quizWords=shuffle([...quizWords]);quizIdx=0;quizResults=[];quizRetrying=false;quizPhase='quiz';renderQuizQuestion();}
function flipQuizDir(){
  quizDir=quizDir===DIR_FWD?DIR_REV:DIR_FWD;
  quizWords=shuffle([...quizWords]);quizIdx=0;quizResults=[];quizRetrying=false;quizPhase='quiz';renderQuizQuestion();
}

// ── Add ────────────────────────────────────────────────────────────────────────
let addEditingCard=null;

function renderAdd(prefillCard){
  if(isGuest){
    const wrap=mkel('div','');
    wrap.innerHTML=`<div style="text-align:center;padding:48px 20px;color:rgba(255,255,255,.4);font-family:sans-serif">
      <div style="font-size:32px;margin-bottom:16px">&#128274;</div>
      <div style="font-size:15px;margin-bottom:8px;color:rgba(255,255,255,.6)">Sign in to add cards</div>
      <div style="font-size:13px;margin-bottom:24px">Create a free account to build your own word list and track your progress.</div>
      <a href="/auth/signup" style="background:#c9a96e;color:#0f0f1a;padding:12px 28px;border-radius:10px;font-weight:700;font-size:14px;text-decoration:none;display:inline-block">Create account</a>
      &nbsp; <a href="/auth/login" style="color:rgba(201,169,110,.7);font-size:13px">Sign in</a>
    </div>`;
    return wrap;
  }
  if(prefillCard) addEditingCard=prefillCard;
  const isEditing=addEditingCard!=null;
  const wrap=mkel('div','');

  // Departure language banner
  if(!isEditing&&!isGuest){
    const depBanner=mkel('div','');
    depBanner.style.cssText='font-size:12px;color:rgba(255,255,255,.35);font-family:sans-serif;text-align:center;margin-bottom:10px';
    depBanner.innerHTML='Adding cards in <strong style="color:rgba(255,255,255,.55)">'+esc(DEP_NAME)+'</strong> &nbsp;·&nbsp; <a href="/settings" style="color:rgba(201,169,110,.6);text-decoration:none">change</a>';
    wrap.appendChild(depBanner);
  }

  // Community browse link (not shown when editing)
  if(!isEditing){
    const commLink=mkel('a','');
    commLink.href='/community?lang='+LANG.code;
    commLink.style.cssText='display:block;text-align:center;padding:10px;margin-bottom:12px;border-radius:10px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);font-size:13px;font-family:sans-serif;color:rgba(201,169,110,.7);text-decoration:none;transition:background .15s';
    commLink.onmouseenter=()=>commLink.style.background='rgba(255,255,255,.07)';
    commLink.onmouseleave=()=>commLink.style.background='rgba(255,255,255,.04)';
    commLink.textContent='🌍 Browse community & preset cards';
    wrap.appendChild(commLink);
  }

  // Mode toggle (not shown when editing)
  if(!isEditing){
    const modeRow=mkel('div','add-mode-toggle');
    const mkBtn=(label,mode)=>{
      const btn=mkel('button','add-mode-btn'+(genAddMode===mode?' active':''));
      btn.type='button';btn.textContent=label;
      btn.onclick=()=>{genAddMode=mode;genBulkParsed=null;render();};
      return btn;
    };
    modeRow.appendChild(mkBtn('✏️ Manual / Bulk','bulk'));
    modeRow.appendChild(mkBtn('📋 Form','form'));
    wrap.appendChild(modeRow);
  } else {
    const lbl=mkel('div','');
    lbl.style.cssText='font-size:11px;color:#c9a96e;font-family:sans-serif;font-weight:700;text-transform:uppercase;letter-spacing:1px;text-align:center;margin-bottom:12px';
    lbl.textContent='Editing: '+addEditingCard.word;
    wrap.appendChild(lbl);
    genAddMode='form';
  }

  if(genAddMode==='form'){
    if(!isEditing){
      wrap.appendChild(mkel('div','sec-field-label','Word type'));
      const typeRow=mkel('div','radio-row');
      (LANG.word_types||['noun','verb']).forEach(t=>{
        const btn=mkel('button','radio-btn'+(addType===t?' selected':''));
        btn.type='button';btn.textContent=t;
        btn.onclick=()=>{addType=t;render();if(_wikiData)_applyWikiData();};
        typeRow.appendChild(btn);
      });
      wrap.appendChild(typeRow);
    }

    const form=document.createElement('form');
    form.onsubmit=async e=>{e.preventDefault();await saveCard(form);};

    const prefill=isEditing?addEditingCard:null;
    form.appendChild(mkel('div','sec-field-label',LANG.name+' word'));
    const _wWrap=document.createElement('div');
    _wWrap.style.cssText='display:flex;gap:6px;align-items:center';
    const _wInp=document.createElement('input');
    _wInp.type='text';_wInp.name='word';_wInp.placeholder='Dictionary / base form';
    _wInp.style.flex='1';
    _wInp.setAttribute('autocorrect','off');_wInp.setAttribute('autocapitalize','none');
    _wInp.setAttribute('autocomplete','off');_wInp.spellcheck=false;
    if(prefill?.word) _wInp.value=prefill.word;
    const _wBtn=mkel('button','btn-ghost',LANG.has_lookup?'🔍 Wiktionary':'🔍');
    _wBtn.type='button';_wBtn.title='Look up on Wiktionary';
    _wBtn.style.cssText='padding:5px 8px;font-size:13px;line-height:1;flex-shrink:0;white-space:nowrap';
    if(LANG.has_lookup){
      const _wSt=mkel('span','');
      _wSt.id='wiki-lookup-status';
      _wSt.style.cssText='font-size:10px;font-family:sans-serif;color:rgba(255,255,255,.3);margin-top:3px;display:block';
      _wBtn.onclick=()=>wikiLookupFill(_wInp,form,_wSt);
      _wWrap.appendChild(_wInp);_wWrap.appendChild(_wBtn);
      form.appendChild(_wWrap);form.appendChild(_wSt);
    }else{
      _wBtn.onclick=()=>{const w=_wInp.value.trim();window.open('https://en.wiktionary.org/wiki/'+encodeURIComponent(w||''),'_blank');};
      _wWrap.appendChild(_wInp);_wWrap.appendChild(_wBtn);
      form.appendChild(_wWrap);
    }
    addFormText(form,DEP_NAME+' translation','translation','Separate alternatives with a comma',prefill?.translation||'');
    if(!isEditing){
      form.appendChild(mkel('div','sec-field-label','Group'));
      const _gWrap=document.createElement('div');
      _gWrap.style.cssText='position:relative;display:flex;gap:6px;align-items:center';
      const _gInp=document.createElement('input');
      _gInp.type='text';_gInp.name='group';_gInp.placeholder='e.g. 📚 Reading, 🍽️ Food…';
      _gInp.style.flex='1';
      _gInp.setAttribute('autocorrect','off');_gInp.setAttribute('autocapitalize','none');
      _gInp.setAttribute('autocomplete','off');_gInp.spellcheck=false;
      if(prefill?.group) _gInp.value=prefill.group;
      const _gBtn=mkel('button','btn-ghost','🙂');
      _gBtn.type='button';_gBtn.title='Pick emoji';
      _gBtn.style.cssText='padding:5px 8px;font-size:16px;line-height:1;flex-shrink:0';
      const _gPicker=document.createElement('div');
      _gPicker.style.cssText='display:none;position:absolute;top:calc(100% + 4px);left:0;z-index:99;background:#1e1e30;border:1px solid rgba(255,255,255,.15);border-radius:10px;padding:8px;flex-wrap:wrap;gap:2px;width:216px';
      ['📚','📖','🎭','📰','🎬','🎮','🗺️','🏛️',
       '🍽️','🥘','🍕','☕','🍷','🥂',
       '🏠','🏙️','🛒','💊','🧹',
       '✈️','🌍','🚂','⛵',
       '💼','🔧','💻','🎓','🔬',
       '❤️','🌿','⭐','🐾','💰','🎵',
       '🏃','⚽','⚡','🌅','🎯','🌸'
      ].forEach(em=>{
        const b=document.createElement('button');b.type='button';b.textContent=em;
        b.style.cssText='background:none;border:none;font-size:18px;cursor:pointer;padding:3px;border-radius:4px;width:28px;height:28px;line-height:1';
        b.onmouseenter=()=>{b.style.background='rgba(255,255,255,.1)';};
        b.onmouseleave=()=>{b.style.background='none';};
        b.onclick=()=>{
          let cur=_gInp.value.trim();
          if(cur&&!/^[a-zA-Z0-9\xC0-ɏͰ-Ͽ]/.test(cur)) cur=cur.replace(/^\S+\s*/,'');
          _gInp.value=em+' '+cur;
          _gPicker.style.display='none';
          _gInp.focus();
        };
        _gPicker.appendChild(b);
      });
      _gBtn.onclick=e=>{e.stopPropagation();_gPicker.style.display=_gPicker.style.display==='none'?'flex':'none';};
      document.addEventListener('click',()=>{_gPicker.style.display='none';});
      _gWrap.appendChild(_gInp);_gWrap.appendChild(_gBtn);_gWrap.appendChild(_gPicker);
      form.appendChild(_gWrap);
    }

    renderGrammarFields(addType,form,prefill);

    addFormText(form,'Pronunciation','pronunciation','IPA or phonetic',prefill?.pronunciation||'');

    const exLbl=mkel('div','sec-field-label','Example ('+LANG.name+')');form.appendChild(exLbl);
    const exNative=document.createElement('input');exNative.type='text';exNative.name='example_native';
    exNative.placeholder='Example sentence in '+LANG.name;
    if(prefill?.example) exNative.value=(typeof prefill.example==='object'?(prefill.example[LANG.code]||''):prefill.example)||'';
    form.appendChild(exNative);

    const exEnLbl=mkel('div','sec-field-label','Example (English)');form.appendChild(exEnLbl);
    const exEn=document.createElement('input');exEn.type='text';exEn.name='example_en';
    exEn.placeholder=DEP_NAME+' translation of example';
    if(prefill?.example&&typeof prefill.example==='object') exEn.value=prefill.example[DEP]||prefill.example.en||'';
    form.appendChild(exEn);

    addFormText(form,'Note','note','Usage notes, register, idioms',prefill?.note||'');
    addFormText(form,'Etymology','etymology','Word origin',prefill?.etymology||'');
    addFormText(form,'Tags','tags','Comma-separated',(prefill?.tags||[]).join(', '));

    const sub=mkel('button','btn-primary');sub.type='submit';
    sub.textContent=isEditing?'Update Card':'Save Card';form.appendChild(sub);

    if(isEditing){
      const cancel=mkel('button','btn-secondary');cancel.type='button';cancel.textContent='Cancel';
      cancel.style.cssText='margin-top:6px;width:100%';
      cancel.onclick=()=>{addEditingCard=null;render();};form.appendChild(cancel);
    }
    wrap.appendChild(form);

  } else {
    // Bulk / manual mode
    const ta=document.createElement('textarea');
    ta.id='gen-bulk-textarea';ta.spellcheck=false;ta.style.minHeight='180px';
    ta.placeholder='word: example word\\ntranslation: meaning\\ntype: noun\\ngroup: daily life\\ngrammar.Gender: de\\nexample.'+LANG.code+': Example sentence.\\nexample.'+DEP+': '+DEP_NAME+' translation.\\nnote: usage note\\netymology: word origin\\ntags: common\\npriority: yes\\n//\\n(next card here)';
    ta.oninput=()=>{
      genBulkParsed=null;
      const pb=document.getElementById('gen-bulk-preview');if(pb)pb.innerHTML='';
      const sb=document.getElementById('gen-bulk-save-btn');if(sb)sb.disabled=true;
    };
    wrap.appendChild(ta);

    const actRow=mkel('div','row-btns');actRow.style.marginTop='8px';
    const prevBtn=mkel('button','btn-secondary','Preview &#8594;');prevBtn.type='button';prevBtn.onclick=previewGenBulk;
    const saveBtn=mkel('button','btn-primary','Save to Library');
    saveBtn.id='gen-bulk-save-btn';saveBtn.type='button';saveBtn.disabled=true;
    saveBtn.style.cssText='margin-top:0;flex:1';saveBtn.onclick=saveGenBulk;
    actRow.appendChild(prevBtn);actRow.appendChild(saveBtn);wrap.appendChild(actRow);

    const preview=mkel('div','add-preview-area');preview.id='gen-bulk-preview';wrap.appendChild(preview);

    // Field reference
    const gramLines=Object.entries(LANG.grammar_fields||{}).map(([type,fields])=>
      fields.length?`<strong style="color:rgba(201,169,110,.5)">${esc(type)}:</strong> `+
        fields.map(f=>`<span style="color:#c9a96e">grammar.${esc(f.label)}:</span>`).join(' '):''
    ).filter(Boolean).join('<br>');

    const hint=mkel('div','add-hint');
    hint.innerHTML=
      '<strong style="color:rgba(255,255,255,.4)">Fields &mdash; separate cards with <code style="color:#c9a96e">---</code> or <code style="color:#c9a96e">//</code></strong><br><br>'+
      '<span style="color:rgba(255,255,255,.35)">Required</span><br>'+
      '<span style="color:#c9a96e">word:</span> '+esc(LANG.name)+' dictionary form<br>'+
      '<span style="color:#c9a96e">translation:</span> '+DEP_NAME+' gloss &mdash; comma-separate multiple meanings<br>'+
      '<br><span style="color:rgba(255,255,255,.35)">Optional</span><br>'+
      '<span style="color:rgba(201,169,110,.7)">type:</span> '+esc((LANG.word_types||[]).join(' &middot; '))+'<br>'+
      '<span style="color:rgba(201,169,110,.7)">group:</span> topic group name<br>'+
      '<span style="color:rgba(201,169,110,.7)">pronunciation:</span> IPA or phonetic<br>'+
      '<span style="color:rgba(201,169,110,.7)">example.'+esc(LANG.code)+':</span> example sentence in '+esc(LANG.name)+'<br>'+
      '<span style="color:rgba(201,169,110,.7)">example.'+DEP+':</span> '+DEP_NAME+' translation of example<br>'+
      '<span style="color:rgba(201,169,110,.7)">note:</span> usage notes or register<br>'+
      '<span style="color:rgba(201,169,110,.7)">etymology:</span> word origin<br>'+
      '<span style="color:rgba(201,169,110,.7)">tags:</span> comma-separated<br>'+
      '<span style="color:rgba(201,169,110,.7)">priority:</span> yes / no<br>'+
      (gramLines?'<br><span style="color:rgba(255,255,255,.35)">Grammar fields</span><br>'+gramLines:'');

    // AI prompt
    const gramInstr=Object.entries(LANG.grammar_fields||{}).filter(([,f])=>f.length).map(([type,fields])=>
      '  '+type.toUpperCase()+'\\n'+fields.map(f=>{
        const opts=((f.widget==='radio'||f.widget==='select')&&f.options)
          ?f.options.map(o=>o.value).join(' | ')
          :(f.placeholder||'').slice(0,45)||'text';
        return '    grammar.'+f.label+': '+opts+(f.top_level?'  ← key field: drives colour-coding':'');
      }).join('\\n')
    ).join('\\n\\n');

    const nounFlds=(LANG.grammar_fields||{}).noun||[];
    const exGram=nounFlds.map(f=>{
      const eg=((f.widget==='radio'||f.widget==='select')&&f.options)?f.options[0].value:'';
      return eg?'grammar.'+f.label+': '+eg:'';
    }).filter(Boolean).join('\\n');

    // Collect existing tags from the user's cards for prompt context
    const existingTagsSet=new Set();
    allCards.forEach(c=>(c.tags||[]).forEach(t=>existingTagsSet.add(t)));
    const existingTags=[...existingTagsSet].slice(0,30);
    const tagsGuidance=existingTags.length>0
      ?'Reuse existing tags where they fit: '+existingTags.join(', ')+'\\n'+
       'You may add new tags if none fit — always prefix each tag with a fitting emoji (e.g. 🍕 food, ✈️ travel, 💼 work).'
      :'Always prefix each tag with a fitting emoji (e.g. ⭐ common, 🍽️ food, ✈️ travel, 💼 work, 🌿 nature).';

    const aiPrompt=
      'Generate input for '+LANG.name+' vocabulary flashcards for '+DEP_NAME+' speakers. Separate multiple cards with // on its own line.\\n'+
      'Fill in ALL applicable fields, especially grammar fields — they drive colour-coding.\\n\\n'+
      '── RULES ────────────────────────────────────\\n'+
      '• group: the user provides the group for their request (use it for all cards in the batch).\\n'+
      '  If no group is specified, pick the most fitting one. Keep it short (1-3 words).\\n'+
      '• tags: prefix every tag with a fitting Unicode emoji. '+tagsGuidance+'\\n'+
      '• type: use "phrase" for multi-word expressions and idioms, even if grammatically nominal.\\n'+
      '• word: dictionary/base form only — never include the article in this field.\\n'+
      '  For nouns whose standard/citation form is the plural (e.g. Greek πληροφορίες — "information"),\\n'+
      '  use the plural as the main word and note the singular in grammar or note fields.\\n'+
      '• grammar fields: use EXACTLY the label names listed in the GRAMMAR FIELDS section below.\\n'+
      '  Do not translate them, rename them, or add parenthetical clarifications.\\n\\n'+
      '── EXAMPLE CARD (noun) ─────────────────────\\n'+
      'word: [dictionary form — no article prefix]\\n'+
      'translation: [primary '+DEP_NAME+' meaning; comma-separate up to 3 senses]\\n'+
      'type: noun\\n'+
      'group: [provided by user, e.g. Daily life / Food / Travel]\\n'+
      'pronunciation: [IPA or phonetic]\\n'+
      'example.'+LANG.code+': [one natural '+LANG.name+' sentence using the word]\\n'+
      'example.'+DEP+': ['+DEP_NAME+' translation of that sentence]\\n'+
      'etymology: [word origin — omit if uncertain]\\n'+
      'note: [register, collocations, set phrases, pitfalls]\\n'+
      'tags: ⭐ common, [🏷️ topic-tag]\\n'+
      'priority: yes\\n'+
      (exGram?exGram+'\\n':'')+
      '\\n── WORD TYPES ────────────────────────────\\n'+
      '  '+(LANG.word_types||[]).join(' · ')+'\\n'+
      '\\n── GRAMMAR FIELDS (fill for each word type) ───────\\n'+
      gramInstr+'\\n\\n'+
      '── WORD LIST ────────────────────────────────';

    const tipsDiv=mkel('div','add-tips');tipsDiv.style.marginTop='10px';
    const tipsTitle=mkel('div','add-tips-title');
    const titleSpan=mkel('span','','AI prompt template');
    const copyBtn=mkel('button','btn-secondary','Copy');
    copyBtn.style.cssText='font-size:10px;padding:3px 10px;margin:0;height:auto;text-transform:none';
    copyBtn.type='button';
    copyBtn.onclick=()=>{
      navigator.clipboard.writeText(aiPrompt).then(()=>{
        copyBtn.textContent='Copied ✓';
        setTimeout(()=>{copyBtn.textContent='Copy';},1600);
      });
    };
    tipsTitle.appendChild(titleSpan);tipsTitle.appendChild(copyBtn);tipsDiv.appendChild(tipsTitle);
    const aiTa=document.createElement('textarea');
    aiTa.style.cssText='min-height:120px;font-size:10px;margin-top:8px;color:rgba(255,255,255,.35);cursor:text;line-height:1.5';
    aiTa.readOnly=true;aiTa.value=aiPrompt;
    tipsDiv.appendChild(aiTa);wrap.appendChild(tipsDiv);
    wrap.appendChild(hint);
  }

  return wrap;
}

function addFormText(form,label,name,hint,value){
  form.appendChild(mkel('div','sec-field-label',label));
  const inp=document.createElement('input');inp.type='text';inp.name=name;
  inp.setAttribute('autocorrect','off');inp.setAttribute('autocapitalize','none');
  inp.setAttribute('autocomplete','off');inp.spellcheck=false;
  if(hint) inp.placeholder=hint;
  if(value) inp.value=value;
  form.appendChild(inp);
  return inp;
}

function renderGrammarFields(type,form,prefill){
  ((LANG.grammar_fields||{})[type]||[]).forEach(field=>{
    form.appendChild(mkel('div','sec-field-label',field.label));
    if(field.widget==='text'){
      const inp=document.createElement('input');inp.type='text';inp.name=field.name;
      inp.placeholder=field.placeholder||'';
      inp.setAttribute('autocorrect','off');inp.setAttribute('autocapitalize','none');
      inp.setAttribute('autocomplete','off');inp.spellcheck=false;
      if(prefill){
        const gEntry=(prefill.grammar||[]).find(g=>g.label===field.label);
        if(gEntry) inp.value=gEntry.value;
      }
      form.appendChild(inp);
      if(field.hint) form.appendChild(mkel('div','field-hint',field.hint));
    } else if(field.widget==='radio'){
      const row=mkel('div','radio-row');row.dataset.fieldname=field.name;
      let prefillVal='';
      if(prefill){
        const gEntry=(prefill.grammar||[]).find(g=>g.label===field.label);
        prefillVal=gEntry?.value||prefill[field.name]||'';
      }
      (field.options||[]).forEach((opt,i)=>{
        const isSelected=prefillVal?opt.value===prefillVal:i===0;
        const btn=mkel('button','radio-btn'+(isSelected?' selected':''));
        btn.type='button';btn.textContent=opt.label;btn.dataset.value=opt.value;
        btn.onclick=()=>{row.querySelectorAll('.radio-btn').forEach(b=>b.classList.remove('selected'));btn.classList.add('selected');};
        row.appendChild(btn);
      });
      form.appendChild(row);
    } else if(field.widget==='select'){
      const sel=document.createElement('select');sel.name=field.name;
      let prefillVal='';
      if(prefill){
        const gEntry=(prefill.grammar||[]).find(g=>g.label===field.label);
        prefillVal=gEntry?.value||prefill[field.name]||'';
      }
      (field.options||[]).forEach(opt=>{
        const o=document.createElement('option');o.value=opt.value;o.textContent=opt.label;
        if(prefillVal&&opt.value===prefillVal) o.selected=true;
        sel.appendChild(o);
      });
      form.appendChild(sel);
    }
  });
}

function collectGrammar(form,type){
  const grammar=[],topLevel={};
  ((LANG.grammar_fields||{})[type]||[]).forEach(field=>{
    let value='';
    if(field.widget==='radio'){
      const row=form.querySelector('[data-fieldname="'+field.name+'"]');
      value=row?.querySelector('.radio-btn.selected')?.dataset.value||'';
    } else {
      value=(form.elements[field.name]?.value||'').trim();
    }
    if(!value) return;
    if(field.top_level) topLevel[field.name]=value;
    if(field.in_grammar!==false) grammar.push({label:field.label,value});
  });
  return{grammar,topLevel};
}

// ── Wiktionary prefill ─────────────────────────────────────────────────────────
let _wikiData=null;
async function wikiLookupFill(wordInp,form,statusEl){
  const w=(wordInp?.value||'').trim();
  if(!w){if(wordInp)wordInp.focus();return;}
  if(statusEl){statusEl.textContent='Looking up…';statusEl.style.color='rgba(255,255,255,.3)';}
  try{
    const res=await api('/api/lookup?word='+encodeURIComponent(w));
    if(res.error){
      if(statusEl){statusEl.textContent=res.error;statusEl.style.color='#d47a8f';}
      return;
    }
    _wikiData=res;
    if(res.type&&res.type!==addType&&(LANG.word_types||[]).includes(res.type)){
      addType=res.type;render();
    }
    _applyWikiData();
    if(statusEl){statusEl.textContent='✓ Prefilled from Wiktionary';statusEl.style.color='rgba(122,196,154,.7)';}
  }catch(e){
    if(statusEl){statusEl.textContent='Lookup failed';statusEl.style.color='#d47a8f';}
  }
}
function _applyWikiData(){
  if(!_wikiData)return;
  const form=document.querySelector('#content form');
  if(!form)return;
  const setInp=(name,val)=>{
    if(!val)return;
    const el=form.querySelector('input[name="'+name+'"],textarea[name="'+name+'"]');
    if(el&&!el.value)el.value=val;
  };
  setInp('translation',_wikiData.translation);
  setInp('example_native',_wikiData.example_native);
  setInp('example_en',_wikiData.example_en);
  setInp('etymology',_wikiData.etymology);
  setInp('plural',_wikiData.plural);
  setInp('past',_wikiData.past);
  setInp('masculine',_wikiData.masculine);
  setInp('feminine',_wikiData.feminine);
  setInp('neuter',_wikiData.neuter);
  if(_wikiData.grammar_gender){
    const row=form.querySelector('[data-fieldname="gender"]');
    if(row)row.querySelectorAll('.radio-btn').forEach(b=>{
      b.classList.toggle('selected',b.dataset.value===_wikiData.grammar_gender);
    });
  }
}

async function saveCard(form){
  const d=Object.fromEntries(new FormData(form));
  const{grammar,topLevel}=collectGrammar(form,addType);
  const primaryMeaning=(d.translation||'').split(',')[0].trim().toLowerCase();
  const exNative=(d.example_native||'').trim();
  const exEn=(d.example_en||'').trim();
  let example=exNative||'';
  if(exNative&&exEn) example={[LANG.code]:exNative,en:exEn};
  else if(exNative) example=exNative;
  else if(exEn) example={[LANG.code]:'',en:exEn};

  const isEditing=addEditingCard!=null;
  const card={
    id:          isEditing?addEditingCard.id:(Date.now()+Math.random()),
    concept_ids: primaryMeaning?[primaryMeaning]:[],
    language:    LANG.code,
    word:        (d.word||'').trim(),
    translation: (d.translation||'').trim(),
    type:        addType,
    group:       isEditing?(addEditingCard.group||''):resolveGroup((d.group||'').trim()),
    pronunciation:(d.pronunciation||'').trim(),
    grammar,
    example,
    note:        (d.note||'').trim(),
    etymology:   (d.etymology||'').trim(),
    tags:        (d.tags||'').split(',').map(t=>t.trim()).filter(Boolean),
    priority:    /yes|true|1/i.test(d.priority||'')?1:0,
    ...topLevel,
  };
  if(!card.word||!card.translation) return;

  if(!isEditing){
    const dupe=allCards.find(c=>c.word.trim().toLowerCase()===card.word.toLowerCase());
    if(dupe&&!confirm('"'+card.word+'" is already in your library ('+dupe.group+' — '+dupe.translation+'). Save anyway?')) return;
  }

  if(isEditing){
    await apiPost('/api/edit',card);
  } else {
    await apiPost('/api/save',card);
  }
  allCards=(await api('/api/cards')).cards;
  addEditingCard=null;
  _wikiData=null;
  // Show success then return to browse
  document.getElementById('content').innerHTML=
    `<div style="background:rgba(122,196,154,.12);border:1px solid #7ac49a;border-radius:10px;padding:14px;font-family:sans-serif;font-size:13px;color:#7ac49a;text-align:center;margin-bottom:16px">
      &#10003; "${esc(card.word)}" ${isEditing?'updated':'saved'}
    </div>`;
  setTimeout(()=>{switchTab(isEditing?'browse':'add');},900);
}

function setGenAddMode(mode){genAddMode=mode;genBulkParsed=null;render();}

function parseGenVocabText(text){
  const blocks=text.trim().split(/\\n[ \\t]*(?:---|\\/\\/)[ \\t]*(?:\\n|$)/).filter(b=>b.trim());
  const parsed=[];
  blocks.forEach((block,bi)=>{
    const card={grammar:[],tags:[],type:addType};
    block.trim().split('\\n').forEach(line=>{
      const m=line.match(/^([^:]+):\\s*(.*)/);
      if(!m) return;
      const key=m[1].trim().toLowerCase();
      const val=m[2].trim();
      if     (key==='word')         card.word=val;
      else if(key==='translation')  card.translation=val;
      else if(key==='type')         card.type=val;
      else if(key==='group')        card.group=resolveGroup(val);
      else if(key==='pronunciation') card.pronunciation=val;
      else if(key==='example'||key==='example.'+LANG.code){
        if(typeof card.example==='object') card.example[LANG.code]=val;
        else card.example=val;
      }
      else if(key==='example.en'){
        const nl=typeof card.example==='string'?card.example:(card.example?card.example[LANG.code]||'':'');
        card.example={[LANG.code]:nl,en:val};
      }
      else if(key==='note')         card.note=val;
      else if(key==='etymology')    card.etymology=val;
      else if(key==='priority')     card.priority=/yes|true|1/i.test(val)?1:0;
      else if(key==='tags')         card.tags=val.split(',').map(t=>t.trim()).filter(Boolean);
      else if(key.startsWith('grammar.')&&val){
        const label=m[1].slice(8).trim();
        card.grammar.push({label,value:val});
        const allFlds=Object.values(LANG.grammar_fields||{}).flat();
        const fld=allFlds.find(f=>f.label===label);
        if(fld&&fld.top_level) card[fld.name]=val;
      }
    });
    const errs=[];
    if(!card.word)        errs.push('missing: word');
    if(!card.translation) errs.push('missing: translation');
    parsed.push({n:bi+1,errs,card});
  });
  return{parsed,cards:parsed.filter(p=>!p.errs.length).map(p=>p.card)};
}

function previewGenBulk(){
  const ta=document.getElementById('gen-bulk-textarea');
  if(!ta||!ta.value.trim()) return;
  genBulkParsed=parseGenVocabText(ta.value);
  const rows=genBulkParsed.parsed.map(({n,errs,card})=>{
    if(errs.length) return `<div class="gen-preview-card invalid">Card ${n}: ${errs.join(' · ')}</div>`;
    return `<div class="gen-preview-card valid">
      <div style="font-size:15px;color:#e8c98a;font-family:Georgia,serif">${esc(card.word)}</div>
      <div style="font-size:12px;color:rgba(255,255,255,.5)">${esc(card.translation)}</div>
      <div style="font-size:10px;color:rgba(255,255,255,.25)">${esc(card.type||'')}${card.group?' · '+esc(card.group):''}</div>
    </div>`;
  }).join('');
  const area=document.getElementById('gen-bulk-preview');
  if(!area) return;
  area.innerHTML=`<div style="font-size:11px;font-family:sans-serif;color:rgba(255,255,255,.3);margin-bottom:10px">
    ${genBulkParsed.cards.length} of ${genBulkParsed.parsed.length} card(s) valid</div>`+rows;
  const sb=document.getElementById('gen-bulk-save-btn');
  if(sb) sb.disabled=genBulkParsed.cards.length===0;
}

async function saveGenBulk(){
  if(!genBulkParsed||!genBulkParsed.cards.length) return;
  const sb=document.getElementById('gen-bulk-save-btn');
  if(sb){sb.disabled=true;sb.textContent='Saving…';}
  let saved=0;
  for(const card of genBulkParsed.cards){
    const primaryMeaning=(card.translation||'').split(',')[0].trim().toLowerCase();
    const full={
      id:           Date.now()+Math.random(),
      concept_ids:  primaryMeaning?[primaryMeaning]:[],
      language:     LANG.code,
      word:         card.word||'',
      translation:  card.translation||'',
      type:         card.type||addType,
      group:        resolveGroup(card.group||''),
      pronunciation:card.pronunciation||'',
      grammar:      card.grammar||[],
      example:      card.example||'',
      note:         card.note||'',
      etymology:    card.etymology||'',
      tags:         card.tags||[],
      priority:     card.priority||0,
      ...Object.fromEntries(Object.values(LANG.grammar_fields||{}).flat()
        .filter(f=>f.top_level&&card[f.name]).map(f=>[f.name,card[f.name]])),
    };
    const res=await apiPost('/api/save',full);
    if(res.ok) saved++;
  }
  allCards=(await api('/api/cards')).cards;
  const area=document.getElementById('gen-bulk-preview');
  if(area) area.innerHTML=`<div style="background:rgba(122,196,154,.12);border:1px solid #7ac49a;border-radius:10px;padding:12px;font-family:sans-serif;font-size:13px;color:#7ac49a;text-align:center">&#10003; Saved ${saved} card${saved!==1?'s':''}</div>`;
  const ta=document.getElementById('gen-bulk-textarea');if(ta) ta.value='';
  genBulkParsed=null;
  if(sb){sb.textContent='Save to Library';sb.disabled=true;}
}

init();
</script>
</body>
</html>"""


# ── Factory ────────────────────────────────────────────────────────────────────

def make_vocab_blueprint(lang, check_fn=None):
    """Create and return a Flask Blueprint for a vocabulary trainer language."""
    data_dir = Path(lang["data_dir"])

    # Import preset cards from the language data module
    if str(data_dir) not in sys.path:
        sys.path.insert(0, str(data_dir))
    data_mod     = importlib.import_module(lang["data_module"])
    preset_cards = list(data_mod.CARDS)

    # Build JS config (JSON-serialisable subset of lang dict)
    skip = {"data_dir", "data_module"}
    lang_js = {k: v for k, v in lang.items()
               if k not in skip and isinstance(v, (str, int, float, bool, list, dict, type(None)))}

    lang_script = "<script>const LANG = " + json.dumps(lang_js, ensure_ascii=False) + ";</script>"

    html = (_HTML
            .replace("__TITLE__",        lang["header_title"])
            .replace("__HEADER_SUB__",   lang["header_sub"])
            .replace("__HEADER_TITLE__", lang["header_title"])
            .replace("<!-- __LANG__ -->", lang_script))

    # ── Blueprint ──────────────────────────────────────────────────────────────
    bp = Blueprint("vocab_" + lang["code"], __name__)

    lang_code = lang["code"]

    # ── Card normalization ─────────────────────────────────────────────────────
    def _normalize_cards(cards):
        """Apply field-name mapping for languages that use non-standard field names."""
        word_field  = lang.get("word_field")
        group_field = lang.get("group_field")
        ex_code     = lang.get("example_native_code")
        gender_lbl  = lang.get("gender_grammar_label")
        if not (word_field or group_field or ex_code or gender_lbl):
            return cards
        normalized = []
        for card in cards:
            c = dict(card)
            if word_field and word_field in c and "word" not in c:
                c["word"] = c[word_field]
            if group_field and group_field in c and "group" not in c:
                c["group"] = c[group_field]
            if ex_code and ex_code != lang_code:
                ex = c.get("example")
                if isinstance(ex, dict) and ex_code in ex and lang_code not in ex:
                    c["example"] = dict(ex)
                    c["example"][lang_code] = ex[ex_code]
            if gender_lbl and "gender" not in c:
                for g in (c.get("grammar") or []):
                    if g.get("label") == gender_lbl:
                        c["gender"] = g["value"]
                        break
            normalized.append(c)
        return normalized

    def _all_cards():
        dep = (current_user.departure_lang or 'en') if current_user.is_authenticated else 'en'
        # Filter preset cards for the current departure language.
        # Cards without a departure_lang field are treated as English departure.
        dep_preset = [c for c in preset_cards if c.get('departure_lang', 'en') == dep]
        if not dep_preset:
            # Fall back to English if no preset cards exist for this departure language.
            dep_preset = [c for c in preset_cards if c.get('departure_lang', 'en') == 'en']
        preset_ids = {str(p["id"]) for p in dep_preset}
        # User cards: logged-in user's own cards + community-approved cards (user_id=0)
        user_rows = []
        if current_user.is_authenticated:
            user_rows = UserCard.query.filter(
                UserCard.lang_code == lang_code,
                UserCard.departure_lang == dep,
                db.or_(UserCard.user_id == current_user.id, UserCard.user_id == 0)
            ).all()
        else:
            user_rows = UserCard.query.filter_by(lang_code=lang_code, user_id=0, departure_lang='en').all()
        user_cards = []
        for r in user_rows:
            c = r.card()
            if str(c.get("id")) not in preset_ids:
                c["_user"] = True
                user_cards.append(c)
        return _normalize_cards(dep_preset + user_cards)

    # ── Routes ─────────────────────────────────────────────────────────────────
    @bp.route("/")
    def index():
        user_data = {}
        if current_user.is_authenticated:
            dep = current_user.departure_lang or 'en'
            user_data = {
                "id":             current_user.id,
                "name":           current_user.display_name,
                "email":          current_user.email,
                "is_admin":       current_user.is_admin,
                "departure_lang": dep,
                "departure_name": DEPARTURE_NAMES.get(dep, dep.upper()),
            }
        page = html.replace("/* __USER__ */",
                            "const USER = " + json.dumps(user_data, ensure_ascii=False) + ";")
        return page

    @bp.route("/api/cards")
    def get_cards():
        return jsonify({"cards": _all_cards()})

    @bp.route("/api/progress")
    def get_progress():
        if not current_user.is_authenticated:
            return jsonify({"progress": {}})
        rows = Progress.query.filter_by(user_id=current_user.id, lang_code=lang_code).all()
        return jsonify({"progress": {r.card_id: r.to_dict() for r in rows}})

    @bp.route("/api/check", methods=["POST"])
    def check():
        body      = request.get_json()
        guess     = body.get("guess", "")
        correct   = body.get("correct", "")
        direction = body.get("direction", "word→en")
        cid       = str(body.get("id", ""))
        if check_fn:
            cards_by_id = {str(c["id"]): c for c in _all_cards()}
            card        = cards_by_id.get(cid, {})
            result      = check_fn(guess, correct, direction, card)
        else:
            result = _check(guess, correct, direction)
        # Save progress only for logged-in users
        if cid and current_user.is_authenticated:
            today = date.today().isoformat()
            row = Progress.query.filter_by(
                user_id=current_user.id, lang_code=lang_code, card_id=cid
            ).first()
            if not row:
                row = Progress(user_id=current_user.id, lang_code=lang_code,
                               card_id=cid, window="[]", dirs="[]")
                # Carry-over: copy progress from same word in another departure lang
                parts = cid.split('-', 2)
                if len(parts) == 3:
                    slug = parts[2]
                    prior = Progress.query.filter(
                        Progress.user_id == current_user.id,
                        Progress.lang_code == lang_code,
                        Progress.card_id.like(f'%-{slug}'),
                        Progress.card_id != cid,
                    ).first()
                    if prior:
                        row.window      = prior.window
                        row.spaced_days = prior.spaced_days
                        row.last_day    = prior.last_day
                db.session.add(row)
            window      = json.loads(row.window or "[]")
            rev_window  = json.loads(row.rev_window or "[]")
            spaced_days = row.spaced_days or 0
            dirs        = json.loads(row.dirs or "[]")
            correct_bool = result == "correct"
            window.append(correct_bool)
            if len(window) > 10:
                window = window[-10:]
            if not direction.startswith('word→'):
                rev_window.append(correct_bool)
                if len(rev_window) > 10:
                    rev_window = rev_window[-10:]
            if row.last_day != today:
                spaced_days = min(spaced_days + 1, 3)
                row.last_day = today
            if direction not in dirs and len(dirs) < 2:
                dirs.append(direction)
            row.window      = json.dumps(window)
            row.rev_window  = json.dumps(rev_window)
            row.spaced_days = spaced_days
            row.dirs        = json.dumps(dirs)
            db.session.commit()
        return jsonify({"result": result})

    @bp.route("/api/save", methods=["POST"])
    def save():
        if not current_user.is_authenticated:
            return jsonify({"error": "login required"}), 401
        card = request.get_json()
        card["id"] = str(uuid.uuid4())[:8]   # server-side unique ID
        dep = current_user.departure_lang or 'en'
        card["departure_lang"] = dep
        row = UserCard(
            user_id=current_user.id,
            lang_code=lang_code,
            card_id=card["id"],
            card_data=json.dumps(card, ensure_ascii=False),
            departure_lang=dep,
        )
        db.session.add(row)
        db.session.commit()
        return jsonify({"ok": True, "id": card["id"]})

    @bp.route("/api/edit", methods=["POST"])
    def edit():
        if not current_user.is_authenticated:
            return jsonify({"error": "login required"}), 401
        card = request.get_json()
        cid  = str(card.get("id"))
        row  = UserCard.query.filter_by(
            user_id=current_user.id, lang_code=lang_code, card_id=cid
        ).first()
        if row:
            row.card_data = json.dumps(card, ensure_ascii=False)
        else:
            row = UserCard(user_id=current_user.id, lang_code=lang_code,
                           card_id=cid, card_data=json.dumps(card, ensure_ascii=False))
            db.session.add(row)
        db.session.commit()
        return jsonify({"ok": True})

    @bp.route("/api/delete", methods=["POST"])
    def delete():
        if not current_user.is_authenticated:
            return jsonify({"error": "login required"}), 401
        cid = str(request.get_json().get("id"))
        UserCard.query.filter_by(
            user_id=current_user.id, lang_code=lang_code, card_id=cid
        ).delete()
        db.session.commit()
        return jsonify({"ok": True})

    @bp.route("/api/submit", methods=["POST"])
    def submit_card():
        """Submit a card to the community pool for admin review."""
        if not current_user.is_authenticated:
            return jsonify({"error": "login required"}), 401
        card = request.get_json()
        sub  = CardSubmission(
            user_id=current_user.id,
            lang_code=lang_code,
            card_data=json.dumps(card, ensure_ascii=False),
        )
        db.session.add(sub)
        db.session.commit()
        return jsonify({"ok": True, "id": sub.id})

    return bp
