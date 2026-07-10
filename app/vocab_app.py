"""
trainer_app.py — Greek Vocabulary Trainer (Flask)
=================================================
Usage:
    python trainer_app.py

Then open http://localhost:5001 in your browser.

Files:
    vocab_data.py   — word data (replace this when Claude syncs)
    progress.json   — your learning stats (never replaced by syncs)
"""

import html as html_mod
import json
import os
import random
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from flask import Blueprint, Flask, jsonify, render_template_string, request

# ── Data ─────────────────────────────────────────────────────────────────────
import sys
_VOCAB_DIR = Path(__file__).parent.parent / 'greek'
sys.path.insert(0, str(_VOCAB_DIR))
from vocab_data import CARDS, CARDS_BY_ID, SCENES_META, ACCEPTED_ALTS

USER_CARDS_FILE = _VOCAB_DIR / "user_cards.json"


def _load_user_data():
    if not USER_CARDS_FILE.exists():
        return {"scenes": [], "cards": [], "alts": {}, "deleted_ids": [], "overrides": {}}
    with open(USER_CARDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("scenes", [])
    data.setdefault("cards", [])
    data.setdefault("alts", {})
    data.setdefault("deleted_ids", [])
    data.setdefault("overrides", {})
    return data


def _save_user_data(data):
    with open(USER_CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ensure_meta(card):
    """Inject concept_id and language into preset cards that predate the schema."""
    if "concept_id" not in card:
        card["concept_id"] = f"c_el_{card['id']}"
    if "language" not in card:
        card["language"] = "el"
    return card

def _merge_user_cards():
    data = _load_user_data()
    deleted = set(data["deleted_ids"])
    overrides = {int(k): v for k, v in data["overrides"].items()}
    existing_scene_ids = {s["id"] for s in SCENES_META}

    # Ensure all preset cards have meta fields
    for card in CARDS:
        _ensure_meta(card)

    # Apply overrides to base cards first
    for card in CARDS:
        if card["id"] in overrides:
            card.update(overrides[card["id"]])

    # Remove deleted cards
    to_remove = [c for c in CARDS if c["id"] in deleted]
    for c in to_remove:
        CARDS.remove(c)
        CARDS_BY_ID.pop(c["id"], None)

    # Add new scenes
    for s in data["scenes"]:
        if s["id"] not in existing_scene_ids:
            SCENES_META.append(s)
            existing_scene_ids.add(s["id"])

    # Add user cards (skip deleted)
    existing_ids = {c["id"] for c in CARDS}
    for c in data["cards"]:
        if c["id"] not in deleted and c["id"] not in existing_ids:
            CARDS.append(c)
            CARDS_BY_ID[c["id"]] = c
            existing_ids.add(c["id"])

    for card_id, alts in data["alts"].items():
        ACCEPTED_ALTS.setdefault(int(card_id), []).extend(alts)


_merge_user_cards()

# ── Progress persistence ──────────────────────────────────────────────────────
PROGRESS_FILE = _VOCAB_DIR / "progress.json"
WINDOW_SIZE = 10


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(data):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


progress = load_progress()


def add_attempt(card_id, score):
    """Record one attempt (score: 1=correct, 0.5=close, 0=wrong)."""
    sid = str(card_id)
    existing = progress.get(sid, {"window": [], "total": 0})
    window = existing["window"] + [score]
    window = window[-WINDOW_SIZE:]
    progress[sid] = {
        "window": window,
        "total": existing["total"] + 1,
    }
    save_progress(progress)
    return progress[sid]


def mastery_level(card_id):
    stat = progress.get(str(card_id))
    if not stat or not stat["window"]:
        return "new"
    n = len(stat["window"])
    acc = sum(stat["window"]) / n
    if n >= 5 and acc >= 0.8:
        return "mastered"
    if n >= 3 and acc < 0.4:
        return "struggling"
    return "learning"


def mastery_label(card_id):
    stat = progress.get(str(card_id))
    if not stat or not stat["window"]:
        return None
    n = len(stat["window"])
    acc = sum(stat["window"]) / n
    pct = round(acc * 100)
    n_str = f"last {n}" if n == WINDOW_SIZE else f"{n} attempt{'s' if n > 1 else ''}"
    return f"{pct}% ({n_str})"


# ── Answer checking ───────────────────────────────────────────────────────────

def edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1])
            prev = temp
    return dp[n]


def is_close_enough(guess, target):
    """True if guess looks like a spelling mistake rather than a wrong word."""
    if not guess or not target:
        return False
    d = edit_distance(guess, target)
    max_len = max(len(guess), len(target))
    if max_len <= 4:
        return d == 1
    if max_len <= 9:
        return d <= 2
    return d <= 3


def normalize(s):
    """Lowercase, strip accents (handles Greek tonos), strip punctuation."""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/]", "", s, flags=re.UNICODE)
    return s.strip()


# Definite and indefinite articles (normalized, accent-stripped)
_GREEK_ARTICLES = frozenset(["ο", "η", "το", "οι", "τα", "ενας", "μια", "ενα"])

def _strip_article(s):
    """Split 'η θαλασσα' → ('η', 'θαλασσα'); 'θαλασσα' → (None, 'θαλασσα')."""
    parts = s.split(None, 1)
    if len(parts) == 2 and parts[0] in _GREEK_ARTICLES:
        return parts[0], parts[1]
    return None, s

def _card_article(card):
    """Return the expected article from grammar data (normalized), or None."""
    for g in (card.get("grammar") or []):
        if g.get("label") == "Article":
            parts = normalize(g["value"]).split()
            return parts[0] if parts else None
    return None


def _grammar_forms(card):
    """Return normalized M/F/N form variants from grammar fields (for adjectives)."""
    forms = []
    for entry in (card.get("grammar") or []):
        lbl = entry.get("label", "").lower()
        if any(x in lbl for x in ("masculine", "feminine", "neuter", "masc", "fem", "neut")):
            val = entry.get("value", "")
            val = val.split("←")[0].split("·")[0].split(",")[0].strip()
            if val:
                forms.append(normalize(val))
    return forms


def check_answer(guess, card, direction):
    """Return 'correct', 'close', or 'wrong'."""
    norm_guess = normalize(guess)
    if not norm_guess:
        return "wrong"

    if direction == "gr-en":
        options = [normalize(o) for o in re.split(r"[/,]", card["translation"]) if o.strip()]
        for opt in options:
            if norm_guess == opt:
                return "correct"
            # First meaningful word matches
            opt_words = [w for w in opt.split() if len(w) > 2]
            guess_words = [w for w in norm_guess.split() if len(w) > 2]
            if opt_words and guess_words and opt_words[0] == guess_words[0]:
                return "correct"
        # Spelling tolerance for English: close to any option = fully correct
        for opt in options:
            if is_close_enough(norm_guess, opt):
                return "correct"
            opt_words = [w for w in opt.split() if len(w) > 2]
            guess_words = [w for w in norm_guess.split() if len(w) > 2]
            if opt_words and guess_words and is_close_enough(guess_words[0], opt_words[0]):
                return "correct"
        return "wrong"
    else:
        correct = normalize(card["greek"])
        # Include stored alts + all M/F/N grammar variants as equally valid answers
        alts = [normalize(a) for a in ACCEPTED_ALTS.get(card["id"], [])]
        alts += _grammar_forms(card)
        alts = list(dict.fromkeys(alts))  # deduplicate, preserve order

        # Strip article from guess so "η θάλασσα" and "θάλασσα" both match
        guess_art, guess_word = _strip_article(norm_guess)

        def _word_matches(w):
            if w == correct or w in alts:
                return True
            # First word match for short compound headwords
            cw = [x for x in correct.split() if len(x) > 3]
            gw = [x for x in w.split() if len(x) > 3]
            return bool(cw and gw and cw[0] == gw[0] and len(correct) < 20)

        if _word_matches(norm_guess) or _word_matches(guess_word):
            # Word is right — check article if one was provided
            if guess_art is not None:
                expected_art = _card_article(card)
                if expected_art and guess_art != expected_art:
                    return "close"  # right word, wrong article → half point
            return "correct"

        # Spelling mistake (check both with and without article in guess)
        for w in {norm_guess, guess_word}:
            if is_close_enough(w, correct):
                return "close"
            for alt in alts:
                if is_close_enough(w, alt):
                    return "close"
        return "wrong"


# ── Wiktionary lookup ────────────────────────────────────────────────────────

_POS_MAP = {
    "Noun": "Ουσιαστικό · Noun",
    "Verb": "Ρήμα · Verb",
    "Adjective": "Επίθετο · Adjective",
    "Adverb": "Επίρρημα · Adverb",
    "Conjunction": "Σύνδεσμος · Conjunction",
    "Pronoun": "Αντωνυμία · Pronoun",
    "Preposition": "Πρόθεση · Preposition",
    "Interjection": "Επιφώνημα · Interjection",
    "Particle": "Μόριο · Particle",
    "Article": "Άρθρο · Article",
    "Numeral": "Αριθμητικό · Numeral",
}


def _strip_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    return html_mod.unescape(text).strip()


def _clean_wikitext(text):
    def sub_template(m):
        parts = [p.strip() for p in m.group(1).split("|")]
        # Keep gloss arguments (contain spaces or non-lang chars)
        meaningful = [p for p in parts[1:] if " " in p or not re.match(r"^[a-z\-]{2,8}$", p) and "=" not in p]
        return meaningful[-1] if meaningful else ""
    text = re.sub(r"\{\{([^{}]+)\}\}", sub_template, text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"'''?([^']+)'''?", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_GRAM_FORM_RE = re.compile(
    r"\b(?:variant|form|spelling|synonym|diminutive|augmentative|feminine|masculine|"
    r"plural|singular|genitive|dative|accusative|nominative|vocative|participle|"
    r"imperative)\s+of\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r'[“”‘’"]([^“”‘’"]+)[“”‘’"]')


def _clean_definition(text):
    """
    Returns (translation, note_or_None).
    - Extracts quoted meanings from "variant/form of X (rom, "m1, m2")" patterns.
    - Strips explanatory parentheticals from normal definitions.
    - Returns (None, None) for pure grammatical forms with no extractable meaning.
    """
    text = text.strip().rstrip(".")

    # Pattern 1: contains "X of" grammatical-form language anywhere in the text
    if _GRAM_FORM_RE.search(text):
        quotes = _QUOTED.findall(text)
        english = [q for q in quotes if not re.search(r"[Ͱ-Ͽ]", q) and len(q) < 100]
        if english:
            meanings = [m.strip() for m in re.split(r"[,/]", english[0]) if m.strip()]
            # Extract a short descriptor for the note ("informal variant of X", etc.)
            note_m = re.search(
                r"(?:(?:a|an)\s+)?(?:[\w\s]+\s+)?(?:variant|form|spelling|synonym|"
                r"diminutive|augmentative)\s+of\s+\S+", text, re.IGNORECASE)
            note = note_m.group(0).strip() if note_m else None
            return " / ".join(meanings[:4]), note
        return None, None   # pure grammatical form, no usable translation

    # Pattern 2: "meaning1, meaning2 (explanatory note)" → strip the parenthetical
    def strip_paren(m):
        content = m.group(1)
        # Quoted meaning inside paren → keep it unwrapped
        english = [q for q in _QUOTED.findall(content) if not re.search(r"[Ͱ-Ͽ]", q)]
        if english:
            return " / ".join(p.strip() for p in re.split(r"[,/]", english[0]) if p.strip())
        # Romanization or English-only explanation → remove
        if re.match(r"^[A-Za-zÀ-ÿ\s,;.\-'']+$", content):
            return ""
        return m.group(0)  # unknown — keep it

    cleaned = re.sub(r"\s*\(([^)]{3,150})\)", strip_paren, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".,;")
    if not cleaned:
        return None, None

    # Normalise: convert comma-separated short words to /
    parts = [p.strip() for p in re.split(r"[,/]", cleaned) if p.strip()]
    if all(len(p) < 35 for p in parts):
        return " / ".join(parts[:5]), None

    return cleaned, None


def _fetch_wiktionary(word):
    encoded = urllib.parse.quote(word)
    headers = {"User-Agent": "GreekVocabTrainer/1.0 (personal learning app)"}
    result = {}

    # ── 1. Definitions via REST API ───────────────────────────────────────────
    try:
        url = f"https://en.wiktionary.org/api/rest_v1/page/definition/{encoded}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=7) as r:
            data = json.loads(r.read())
        entries = data.get("el", [])
        if not entries:
            return {"not_found": True}
        entry = entries[0]
        result["type"] = _POS_MAP.get(entry.get("partOfSpeech", ""), entry.get("partOfSpeech", ""))
        defs = entry.get("definitions", [])
        if defs:
            translations, notes = [], []
            for d in defs[:5]:
                raw = _strip_html(d.get("definition", ""))
                if not raw:
                    continue
                trans, note = _clean_definition(raw)
                if trans:
                    translations.append(trans)
                if note and note not in notes:
                    notes.append(note)
            if translations:
                # Flatten and deduplicate meanings
                all_parts = []
                seen = set()
                for t in translations:
                    for part in t.split(" / "):
                        part = part.strip()
                        if part and part.lower() not in seen:
                            seen.add(part.lower())
                            all_parts.append(part)
                result["translation"] = " / ".join(all_parts[:3])
                result["definition"]  = " / ".join(all_parts[:6])
            if notes:
                result["auto_note"] = "; ".join(notes)
            # Example from first definition that has one
            for d in defs:
                examples = d.get("examples", [])
                if examples:
                    ex = examples[0]
                    gr = _strip_html(ex.get("example", ""))
                    en = _strip_html(ex.get("translation", ""))
                    if gr: result["example_gr"] = gr
                    if en: result["example_en"] = en
                    break
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"not_found": True}
    except Exception:
        pass

    # ── 2. Etymology via wikitext API ─────────────────────────────────────────
    try:
        url = (f"https://en.wiktionary.org/w/api.php?action=parse&page={encoded}"
               f"&prop=wikitext&format=json")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=7) as r:
            data = json.loads(r.read())
        wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
        # Find the Greek section
        gm = re.search(r"==Greek==(.+?)(?:\n==[^=]|\Z)", wikitext, re.DOTALL)
        if gm:
            greek_sec = gm.group(1)
            # Etymology
            em = re.search(r"===Etymology(?:\s*\d*)?===\s*\n(.+?)(?:\n===|\Z)", greek_sec, re.DOTALL)
            if em:
                result["etymology"] = _clean_wikitext(em.group(1))
            # Noun: {{el-noun|m/f/n|plural}}
            nm = re.search(r"\{\{el-noun\|([mfn])(?:\|([^|}\n]*))?", greek_sec)
            if nm:
                g = nm.group(1)
                result["grammar_gender"]  = {"m": "Masculine", "f": "Feminine", "n": "Neuter"}[g]
                result["grammar_article"] = {"m": "ο", "f": "η", "n": "το"}[g]
                pl = (nm.group(2) or "").strip()
                if pl and not pl.startswith("-"):
                    result["grammar_plural"] = pl
            # Verb: look for past/aorist in headword template {{el-verb|past=...|...}}
            vm = re.search(r"\{\{el-verb[^}]*?\|(?:aor(?:ist)?|past)=([^|}\n]+)", greek_sec)
            if vm:
                result["grammar_aorist"] = vm.group(1).strip()
    except Exception:
        pass

    return result


# ── Flask app ─────────────────────────────────────────────────────────────────
vocab_bp = Blueprint('vocab', __name__)

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Greek Vocab Trainer</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f0f1a">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Λεξιλόγιο">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f1a;font-family:Georgia,serif;color:#fff;min-height:100vh}
.app{padding:24px 14px 60px;display:flex;flex-direction:column;align-items:center}
.header{text-align:center;margin-bottom:20px}
.home-link{position:fixed;top:10px;left:10px;z-index:100;font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;text-decoration:none;letter-spacing:.5px;padding:6px 10px;border-radius:8px;transition:color .15s,background .15s}
.home-link:hover{color:rgba(201,169,110,.8);background:rgba(201,169,110,.08)}
.header-sub{font-size:10px;letter-spacing:4px;color:#c9a96e;text-transform:uppercase;margin-bottom:4px;font-family:sans-serif;opacity:.8}
.header-title{font-size:22px;color:#fff;font-weight:normal;letter-spacing:1px}
.content{width:100%;max-width:480px}
/* Pills */
.pill{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:6px 12px;cursor:pointer;font-size:12px;color:rgba(255,255,255,.45);font-family:sans-serif;transition:all .15s;display:inline-block;margin:3px}
.pill.active{background:rgba(201,169,110,.2);border-color:#c9a96e;color:#c9a96e}
.pill:hover{border-color:rgba(255,255,255,.3)}
/* Sections */
.sec{margin-bottom:18px}
.sec-label{font-size:10px;color:rgba(201,169,110,.7);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;display:flex;justify-content:space-between;align-items:center}
.sec-label a{color:#c9a96e;font-size:10px;cursor:pointer;text-decoration:underline}
.pills{display:flex;flex-wrap:wrap;gap:2px;margin-top:6px}
/* Big button */
.btn-primary{width:100%;padding:14px;border-radius:12px;background:linear-gradient(135deg,#c9a96e,#e8c98a);border:none;color:#1a1a2e;font-size:15px;font-weight:700;font-family:sans-serif;letter-spacing:1px;cursor:pointer;text-transform:uppercase;margin-top:8px}
.btn-primary:disabled{background:rgba(255,255,255,.05);color:rgba(255,255,255,.2);cursor:default}
.btn-secondary{padding:12px;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);color:rgba(255,255,255,.6);font-size:12px;font-weight:700;font-family:sans-serif;cursor:pointer;text-transform:uppercase}
/* Progress bar */
.progress-wrap{height:2px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:20px;overflow:hidden}
.progress-bar{height:100%;background:#c9a96e;border-radius:2px;transition:width .3s}
.progress-label{display:flex;justify-content:space-between;margin-bottom:8px;font-family:sans-serif;font-size:11px;color:rgba(255,255,255,.3)}
/* Prompt card */
.prompt-card{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;padding:36px 24px 28px;text-align:center;margin-bottom:16px;position:relative}
.prompt-greek{font-family:Georgia,serif;color:#e8c98a;margin-bottom:2px;line-height:1.3}
.prompt-decl{font-family:Georgia,serif;font-size:15px;color:rgba(255,255,255,.32);margin-bottom:6px;letter-spacing:.5px}
.prompt-sub{font-family:monospace;font-size:12px;color:rgba(255,255,255,.35)}
.fc-decl{font-family:Georgia,serif;font-size:13px;color:rgba(255,255,255,.3);margin-top:-2px;margin-bottom:6px}
.prompt-scene{font-size:9px;color:rgba(255,255,255,.2);margin-top:8px;font-family:sans-serif}
.mastery-badge{position:absolute;top:12px;right:14px;font-size:10px;font-family:sans-serif;opacity:.7}
.star-badge{position:absolute;top:11px;left:14px;font-size:12px;opacity:.8}
/* Input */
input[type=text]{width:100%;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;font-size:16px;outline:none;margin-bottom:12px}
/* Feedback */
.feedback{border-radius:12px;padding:14px 16px;margin-bottom:14px}
.feedback.correct{background:rgba(122,196,154,.12);border:1px solid #7ac49a}
.feedback.wrong{background:rgba(212,122,143,.12);border:1px solid #d47a8f}
.feedback.close{background:rgba(230,180,80,.10);border:1px solid #e6b450}
.feedback-verdict{font-size:13px;font-weight:700;font-family:sans-serif;margin-bottom:6px}
.feedback.correct .feedback-verdict{color:#7ac49a}
.feedback.wrong .feedback-verdict{color:#d47a8f}
.feedback.close .feedback-verdict{color:#e6b450}
.feedback-answer{font-size:13px;color:#f0ebe0;font-family:sans-serif;margin-bottom:4px}
.feedback-yours{font-size:12px;color:rgba(255,255,255,.35);font-family:sans-serif}
.feedback-note{font-size:11px;color:#c9a96e;margin-top:8px;font-style:italic;font-family:sans-serif}
.window-dots{display:flex;gap:3px;align-items:center;margin-top:8px}
.dot{width:8px;height:8px;border-radius:50%}
/* Results */
.result-row{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.04);border-radius:10px;padding:8px 12px;margin-bottom:5px}
.result-greek{font-family:Georgia,serif;font-size:15px;color:#e8c98a;flex-shrink:0}
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
/* Mode tabs */
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
.fc-wrap{perspective:1000px;width:100%;margin-bottom:20px;cursor:pointer}
.fc-inner{position:relative;width:100%;transform-style:preserve-3d;transition:transform .5s cubic-bezier(.4,0,.2,1)}
.fc-inner.flipped{transform:rotateY(180deg)}
.fc-front,.fc-back{width:100%;border-radius:18px;padding:36px 24px 28px;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.fc-front{background:linear-gradient(145deg,#1a1a2e 0%,#16213e 100%);border:1px solid rgba(255,255,255,.08);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:220px}
.fc-back{background:linear-gradient(145deg,#16213e 0%,#1a2a1e 100%);border:1px solid rgba(201,169,110,.2);position:absolute;top:0;left:0;transform:rotateY(180deg);overflow-y:auto;max-height:420px}
.fc-article{font-size:13px;color:rgba(255,255,255,.35);font-family:sans-serif;margin-bottom:4px}
.fc-greek{font-family:Georgia,serif;font-size:38px;color:#e8c98a;line-height:1.2;margin-bottom:8px}
.fc-pron{font-family:monospace;font-size:13px;color:rgba(255,255,255,.3)}
.fc-type{font-size:10px;color:rgba(255,255,255,.18);margin-top:6px;font-family:sans-serif}
.fc-hint{font-size:10px;color:rgba(255,255,255,.15);margin-top:20px;font-family:sans-serif;letter-spacing:1px}
.fc-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.fc-nav-btn{padding:8px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:rgba(255,255,255,.5);font-size:13px;cursor:pointer;font-family:sans-serif}
.fc-nav-btn:disabled{opacity:.2;cursor:default}
.fc-counter{font-size:12px;color:rgba(255,255,255,.3);font-family:sans-serif}
.bcard{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:12px;margin-bottom:8px;overflow:hidden;cursor:pointer;transition:border-color .15s}
.bcard:hover{border-color:rgba(201,169,110,.3)}
.bcard.open{border-color:rgba(201,169,110,.4)}
.bcard-head{display:flex;align-items:center;gap:10px;padding:10px 14px}
.bcard-greek{font-family:Georgia,serif;font-size:17px;color:#e8c98a;flex-shrink:0}
.bcard-pron{font-family:monospace;font-size:11px;color:rgba(255,255,255,.3);flex-shrink:0}
.bcard-trans{font-size:12px;color:rgba(255,255,255,.5);font-style:italic;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:sans-serif}
.bcard-badges{display:flex;gap:4px;flex-shrink:0;align-items:center}
.bcard-body{padding:0 14px 14px;display:none}
.bcard.open .bcard-body{display:block}
.bcard-section{margin-top:10px}
.bcard-section-label{font-size:9px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;margin-bottom:4px}
.bcard-def{font-size:12px;color:rgba(255,255,255,.6);font-family:sans-serif;line-height:1.5}
.bcard-example{font-size:12px;font-family:sans-serif;line-height:1.6}
.bcard-example .gr{color:#e8c98a;font-family:Georgia,serif}
.bcard-example .en{color:rgba(255,255,255,.35);font-style:italic}
.bcard-grammar{display:flex;flex-direction:column;gap:3px}
.bcard-grammar-row{display:flex;gap:8px;font-size:11px;font-family:sans-serif}
.bcard-grammar-lbl{color:rgba(201,169,110,.5);min-width:80px;flex-shrink:0}
.bcard-grammar-val{color:rgba(255,255,255,.55)}
.bcard-note{font-size:11px;color:#c9a96e;font-style:italic;font-family:sans-serif;line-height:1.5}
.bcard-etym{font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;line-height:1.5}
.mastery-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
/* Add tab */
textarea{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);border-radius:10px;color:#fff;font-family:monospace;font-size:12px;padding:12px 14px;resize:vertical;outline:none;min-height:260px;line-height:1.6}
textarea::placeholder{color:rgba(255,255,255,.2)}
.add-actions{display:flex;gap:8px;margin-top:8px}
.preview-area{margin-top:16px}
.preview-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:10px 14px;margin-bottom:6px}
.preview-card.valid{border-color:rgba(122,196,154,.3)}
.preview-card.invalid{border-color:rgba(212,122,143,.3);background:rgba(212,122,143,.05)}
.preview-greek{font-family:Georgia,serif;font-size:16px;color:#e8c98a}
.preview-trans{font-size:12px;color:rgba(255,255,255,.45);font-family:sans-serif;margin-top:2px}
.preview-meta{font-size:10px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-top:3px}
.preview-error{font-size:11px;color:#d47a8f;font-family:sans-serif;margin-top:4px}
.preview-new-scene{font-size:10px;color:#c9a96e;font-family:sans-serif;margin-top:3px}
.add-success{background:rgba(122,196,154,.12);border:1px solid #7ac49a;border-radius:10px;padding:12px 16px;font-family:sans-serif;font-size:13px;color:#7ac49a;text-align:center;margin-top:12px}
.add-hint{font-size:10px;color:rgba(255,255,255,.18);font-family:monospace;line-height:1.8;background:rgba(255,255,255,.03);border-radius:8px;padding:10px 12px;margin-bottom:12px}
.add-tips{background:rgba(255,255,255,.03);border-radius:8px;padding:10px 12px;margin-bottom:12px;border:1px solid rgba(255,255,255,.06)}
.add-tips-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-bottom:8px}
.add-tip{font-size:11px;font-family:sans-serif;color:rgba(255,255,255,.3);line-height:1.6;margin-bottom:6px;padding-left:14px;position:relative}
.add-tip:last-child{margin-bottom:0}
.tip-bullet{position:absolute;left:0;color:rgba(201,169,110,.5)}
.add-tip strong{color:rgba(255,255,255,.45)}
.add-tip code{font-family:monospace;font-size:10px;color:#c9a96e;background:rgba(201,169,110,.08);padding:1px 4px;border-radius:3px}
/* Edit/delete icon buttons */
.icon-btn{background:none;border:none;cursor:pointer;padding:3px 5px;border-radius:5px;font-size:13px;opacity:.35;transition:opacity .15s;line-height:1;flex-shrink:0}
.icon-btn:hover{opacity:.85}
.icon-btn.del:hover{color:#d47a8f;opacity:1}
.add-mode-label{font-size:10px;color:#c9a96e;font-family:sans-serif;font-weight:700;text-transform:uppercase;letter-spacing:1px;text-align:center;margin-bottom:10px}
.add-mode-toggle{display:flex;gap:4px;margin-bottom:16px}
.lookup-wrap{display:flex;gap:8px;margin-bottom:12px}
.lookup-wrap input{margin-bottom:0;flex:1}
.lookup-result{background:rgba(255,255,255,.03);border:1px solid rgba(201,169,110,.2);border-radius:10px;padding:12px 14px;margin-bottom:12px;font-family:sans-serif;font-size:12px}
.lookup-result-row{display:flex;gap:8px;margin-bottom:4px;line-height:1.5}
.lookup-result-lbl{color:rgba(201,169,110,.55);min-width:72px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;flex-shrink:0;padding-top:1px}
.lookup-result-val{color:rgba(255,255,255,.7)}
.lookup-status{font-size:12px;font-family:sans-serif;text-align:center;padding:10px;color:rgba(255,255,255,.3)}
.lookup-status.error{color:#d47a8f}
/* Mask form */
.mask-form{display:flex;flex-direction:column;gap:10px;margin-bottom:12px}
.mask-row{display:flex;gap:10px}
@media(max-width:480px){.mask-row{flex-direction:column}}
.mask-field{flex:1;display:flex;flex-direction:column;gap:5px;min-width:0}
.mask-label{font-size:10px;color:rgba(255,255,255,.35);font-family:sans-serif;letter-spacing:.3px}
.mask-input,.mask-select{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);border-radius:8px;color:#fff;font-family:sans-serif;font-size:12px;padding:7px 10px;outline:none;-webkit-appearance:none;box-sizing:border-box}
.mask-input:focus,.mask-select:focus{border-color:rgba(201,169,110,.4)}
.mask-select option{background:#1a1a2e;color:#fff}
.mask-radio-group{display:flex;gap:14px;flex-wrap:wrap;padding:4px 0}
.mask-radio-group label{display:flex;align-items:center;gap:5px;font-size:12px;color:rgba(255,255,255,.55);cursor:pointer;font-family:sans-serif}
.mask-section-label{font-size:10px;color:rgba(201,169,110,.5);letter-spacing:1px;text-transform:uppercase;padding-top:10px;border-top:1px solid rgba(255,255,255,.06);font-family:sans-serif;margin-top:4px}
.mask-custom-row{display:flex;gap:8px;align-items:center;margin-bottom:2px}
button,a{-webkit-tap-highlight-color:transparent;touch-action:manipulation}
.app{padding-bottom:calc(60px + env(safe-area-inset-bottom,0px))}
@media(max-width:430px){
  .app{padding-left:10px;padding-right:10px;padding-top:16px}
  .prompt-card{padding:22px 14px 18px}
  .fc-front,.fc-back{padding:22px 14px 18px}
  .fc-greek{font-size:30px}
  .score-big{font-size:40px}
  .tab{padding:10px 4px;font-size:11px}
  .btn-primary{padding:13px}
}
</style>
</head>
<body>
<a class="home-link" href="/">🧿 Home</a>
<div class="app" id="root">
<div class="header">
  <div class="header-sub">Εξάσκηση Λεξιλογίου</div>
  <div class="header-title">Greek Vocab Trainer</div>
</div>
<div class="tabs" id="tabs">
  <button class="tab active" onclick="switchMode('study')">📝 Study</button>
  <button class="tab" onclick="switchMode('browse')">📖 Browse</button>
  <button class="tab" onclick="switchMode('add')">➕ Add</button>
</div>
<div class="content" id="content"></div>
</div>

<script>
// Escape HTML entities — applied to all user/card data inserted into innerHTML
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  mode: 'study',          // study | browse
  phase: 'setup',        // setup | quiz | results
  selectedScenes: new Set({{ scene_ids | tojson }}),
  activeTags: new Set(),
  direction: 'gr-en',
  masteryFilter: new Set(),   // empty = all; specific levels = filter to those
  wordCount: 10,
  quizPickMode: false,        // false = filter by group/tag; true = browse & hand-pick
  manualCards: new Set(),     // card IDs selected in pick mode
  pickSearch: '',
  pickScenes: new Set(),
  pickTags: new Set(),
  session: null,         // { words, direction, idx }
  results: [],
  progressData: {},
  // browse state
  browseScenes: new Set({{ scene_ids | tojson }}),
  browseTags: new Set(),
  browseMastery: 'all',
  browseSearch: '',
  browseOpen: new Set(),
  browseCards: [],
  browseView: 'list',     // list | cards
  browseCardIdx: 0,
  browseFlipped: false,
  studyFlipped: false,
};

function switchMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => {
    if (t.textContent.includes('Study') && mode==='study') t.classList.add('active');
    if (t.textContent.includes('Browse') && mode==='browse') t.classList.add('active');
    if (t.textContent.includes('Add') && mode==='add') t.classList.add('active');
  });
  if (mode === 'study') { state.phase = 'setup'; state.studyFlipped = false; }
  if (mode === 'browse') renderBrowse();
  else if (mode === 'add') renderAdd();
  else render();
}

const SCENES_META = {{ scenes_meta | tojson }};
const ALL_TAGS = [
  { key:'priority',          label:'⭐ Priority' },
  { key:'everyday life',     label:'🏠 Everyday Life' },
  { key:'colloquial',        label:'💬 Colloquial' },
  { key:'formal',            label:'📜 Formal' },
  { key:'literary',          label:'📖 Literary' },
  { key:'figurative',        label:'🔀 Figurative' },
  { key:'emotion',           label:'❤️ Emotion' },
  { key:'body',              label:'🫀 Body' },
  { key:'nature',            label:'🌿 Nature' },
  { key:'nautical',          label:'⚓ Nautical' },
  { key:'mythological',      label:'⚡ Mythological' },
  { key:'religion',          label:'✝️ Religion' },
  { key:'loanword:turkish',  label:'🇹🇷 Turkish' },
  { key:'loanword:italian',  label:'🇮🇹 Italian/Venetian' },
  { key:'loanword:slavic',   label:'🌾 Slavic' },
  { key:'loanword:french',   label:'🇫🇷 French' },
  { key:'loanword:english',  label:'🇬🇧 English' },
];
const MASTERY_COLORS = {
  new:'#7ab3d4', learning:'#c9a96e', struggling:'#d47a8f', mastered:'#7ac49a'
};
const MASTERY_LABELS = {
  new:'🆕 New', learning:'📘 Learning', struggling:'⚠️ Struggling', mastered:'✅ Mastered'
};

// ── API calls ──────────────────────────────────────────────────────────────
const API_BASE = '/vocab';
async function api(path, body) {
  const opts = body
    ? { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) }
    : {};
  const r = await fetch(API_BASE + path, opts);
  return r.json();
}

// ── Render dispatcher ──────────────────────────────────────────────────────
async function render() {
  if (state.phase === 'setup') await renderSetup();
  else if (state.phase === 'study') renderStudyCards();
  else if (state.phase === 'quiz') renderQuiz();
  else renderResults();
}

// ── Setup ──────────────────────────────────────────────────────────────────
async function renderSetup() {
  const progress = await api('/api/progress');
  state.progressData = progress;
  if (!state.browseCards.length) {
    const r = await api('/api/cards', {});
    state.browseCards = r.cards;
  }

  // Count mastery levels
  const counts = {new:0,learning:0,struggling:0,mastered:0};
  for (const [,s] of Object.entries(progress)) {
    const n = s.window.length;
    if (!n) { counts.new++; continue; }
    const acc = s.window.filter(Boolean).length / n;
    if (n>=5 && acc>=0.8) counts.mastered++;
    else if (n>=3 && acc<0.4) counts.struggling++;
    else counts.learning++;
  }
  const totalCards = {{ total_cards }};
  counts.new += totalCards - Object.keys(progress).length;

  const poolResp = await api('/api/pool', {
    scenes: [...state.selectedScenes],
    tags: [...state.activeTags],
    mastery_filter: [...state.masteryFilter],
  });
  const poolSize = poolResp.count;
  const actualCount = Math.min(state.wordCount, poolSize);

  document.getElementById('content').innerHTML = `
    <div class="sec">
      <div class="sec-label">Direction</div>
      <div class="pills">
        ${pill('gr-en','🇬🇷 → 🇬🇧 Greek to English', state.direction==='gr-en', "setDir('gr-en')")}
        ${pill('en-gr','🇬🇧 → 🇬🇷 English to Greek', state.direction==='en-gr', "setDir('en-gr')")}
      </div>
    </div>
    <div class="sec">
      <div class="sec-label">Groups
        <a onclick="toggleAllScenes()">
          ${state.selectedScenes.size===SCENES_META.length?'deselect all':'select all'}
        </a>
      </div>
      <div class="pills">
        ${SCENES_META.map(s => pill(s.id, s.label, state.selectedScenes.has(s.id), `toggleScene('${s.id}')`)).join('')}
      </div>
    </div>
    <div class="sec">
      <div class="sec-label">Tags (optional)</div>
      <div class="pills">
        ${ALL_TAGS.map(t => pill(t.key, t.label, state.activeTags.has(t.key), `toggleTag('${t.key}')`)).join('')}
      </div>
    </div>
    <div class="sec">
      <div class="sec-label">Knowledge level (last {{ window_size }} attempts)</div>
      <div class="pills">
        ${pill('all','🎲 All', state.masteryFilter.size===0, "setMastery('all')")}
        ${['new','learning','struggling','mastered'].map(k =>
          pill(k, MASTERY_LABELS[k], state.masteryFilter.has(k), `setMastery('${k}')`)
        ).join('')}
      </div>
    </div>
    <div class="sec">
      <div class="sec-label">Number of words</div>
      <div class="pills">${[5,10,15,20].map(n => pill(n,n,state.wordCount===n,`setCount(${n})`)).join('')}</div>
    </div>
    <div class="pool-info">
      ${poolSize} word${poolSize!==1?'s':''} match your filters
      ${poolSize>0&&actualCount<state.wordCount?` — quiz will use ${actualCount}`:''}
    </div>
    <div class="sec" style="border-top:1px solid rgba(255,255,255,.06);padding-top:16px;margin-top:4px">
      <div class="sec-label">Selection mode</div>
      <div class="pills">
        ${pill('filter', '🔽 Filter by group', !state.quizPickMode, "setPickMode(false)")}
        ${pill('pick',   '☑ Browse & pick',   state.quizPickMode,  "setPickMode(true)")}
      </div>
    </div>
    ${state.quizPickMode ? renderPickPanel() : ''}
    ${((!state.quizPickMode&&poolSize===0)||(state.quizPickMode&&state.manualCards.size===0))
      ? `<div class="pool-info" style="color:rgba(255,255,255,.25);margin-top:4px">${state.quizPickMode ? 'Select at least one word above' : 'No words match — adjust filters'}</div>`
      : `<div class="row-btns" style="margin-top:0">
          <button class="btn-secondary" style="flex:1" onclick="startStudy()">📖 Study</button>
          <button class="btn-primary" style="margin-top:0;flex:2" onclick="startQuiz()">🎯 Start Quiz →${state.quizPickMode&&state.manualCards.size?` (${state.manualCards.size})`:''}</button>
        </div>`
    }
    <div style="font-size:10px;color:rgba(201,169,110,.6);text-transform:uppercase;letter-spacing:1.5px;font-family:sans-serif;font-weight:700;text-align:center;margin-top:28px;margin-bottom:8px">
      Your Progress
    </div>
    <div class="stats-grid">
      ${['new','learning','struggling','mastered'].map(level => `
        <div class="stat-box" style="border:1px solid ${MASTERY_COLORS[level]}33">
          <div class="stat-num" style="color:${MASTERY_COLORS[level]}">${counts[level]}</div>
          <div class="stat-lbl">${MASTERY_LABELS[level]}</div>
        </div>`).join('')}
    </div>
    <div class="meta">Based on last {{ window_size }} attempts · ${totalCards} words total</div>
  `;
}

function pill(key, label, active, onclick) {
  return `<span class="pill${active?' active':''}" onclick="${onclick}">${esc(label)}</span>`;
}

// Setup actions
function setDir(d) { state.direction=d; render(); }
function toggleScene(id) {
  state.selectedScenes.has(id)?state.selectedScenes.delete(id):state.selectedScenes.add(id);
  render();
}
function toggleAllScenes() {
  state.selectedScenes = state.selectedScenes.size===SCENES_META.length
    ? new Set() : new Set(SCENES_META.map(s=>s.id));
  render();
}
function toggleTag(k) { state.activeTags.has(k)?state.activeTags.delete(k):state.activeTags.add(k); render(); }
function setMastery(m) {
  if (m === 'all') { state.masteryFilter = new Set(); }
  else {
    state.masteryFilter.has(m) ? state.masteryFilter.delete(m) : state.masteryFilter.add(m);
  }
  render();
}
function setCount(n) { state.wordCount=n; render(); }
function setPickMode(on) { state.quizPickMode = on; state.pickSearch = ''; state.pickScenes = new Set(); state.pickTags = new Set(); render(); }

function _pickFiltered() {
  const q = state.pickSearch.toLowerCase();
  return (state.browseCards.length ? state.browseCards : []).filter(c => {
    if (state.pickScenes.size && !state.pickScenes.has(c.scene_id)) return false;
    if (state.pickTags.size) {
      for (const t of state.pickTags) {
        if (t === 'priority' && !c.priority) return false;
        if (t !== 'priority' && !(c.tags||[]).includes(t)) return false;
      }
    }
    if (q && !(c.greek+' '+c.translation).toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderPickPanel() {
  const filtered = _pickFiltered();
  const sceneFilter = SCENES_META.map(s =>
    `<span class="pill${state.pickScenes.has(s.id)?' on':''}" onclick="togglePickScene('${s.id}')">${s.label}</span>`
  ).join('');
  const rows = filtered.slice(0, 100).map(c => {
    const checked = state.manualCards.has(c.id);
    return `<label style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;font-family:sans-serif">
      <input type="checkbox" ${checked?'checked':''} onchange="togglePickCard(${c.id},this.checked)" style="accent-color:#c9a96e;flex-shrink:0">
      <span style="font-family:Georgia,serif;font-size:14px;color:#e8c98a;flex-shrink:0">${esc(c.greek)}</span>
      <span style="font-size:11px;color:rgba(255,255,255,.4);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.translation)}</span>
      <span style="font-size:10px;color:rgba(255,255,255,.2);flex-shrink:0;font-family:sans-serif">${esc(c.scene_label||'')}</span>
    </label>`;
  }).join('');
  const more = filtered.length > 100 ? `<div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;padding:4px 0">${filtered.length-100} more — refine search to see them</div>` : '';
  return `
    <div style="border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px;margin-top:4px">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
        <span style="font-size:14px;color:rgba(255,255,255,.4)">⌕</span>
        <input id="pick-search" type="text" placeholder="Search…" value="${esc(state.pickSearch)}"
          oninput="state.pickSearch=this.value;document.getElementById('pick-list').innerHTML=renderPickPanel()"
          style="flex:1;background:transparent;border:none;outline:none;color:#fff;font-size:13px;font-family:sans-serif">
      </div>
      <div class="pills" style="margin-bottom:8px">${sceneFilter}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-bottom:6px">
        ${filtered.length} word${filtered.length!==1?'s':''} · ${state.manualCards.size} selected
        ${state.manualCards.size?'<a onclick="state.manualCards=new Set();render()" style="color:#c9a96e;cursor:pointer;margin-left:8px">clear</a>':''}
      </div>
      <div id="pick-list" style="max-height:260px;overflow-y:auto">${rows}${more}</div>
    </div>`;
}

function togglePickScene(id) {
  state.pickScenes.has(id) ? state.pickScenes.delete(id) : state.pickScenes.add(id);
  render();
}
function togglePickCard(id, checked) {
  if (checked) state.manualCards.add(id); else state.manualCards.delete(id);
  // re-render just the button label without full re-render
  const btn = document.querySelector('.btn-primary[onclick="startQuiz()"]') || document.querySelector('.btn-primary');
  if (btn) btn.textContent = state.manualCards.size===0 ? 'Select at least one word' : `Start Quiz (${state.manualCards.size} words) →`;
}

async function startQuiz() {
  const body = { direction: state.direction };
  if (state.quizPickMode && state.manualCards.size) {
    body.card_ids = [...state.manualCards];
    body.count = state.manualCards.size;
  } else {
    body.scenes = [...state.selectedScenes];
    body.tags = [...state.activeTags];
    body.mastery_filter = [...state.masteryFilter];
    body.count = state.wordCount;
  }
  const resp = await api('/api/start', body);
  state.session = { words: resp.words, direction: state.direction, idx: 0, retrying: false };
  state.results = [];
  state.phase = 'quiz';
  render();
}

async function startStudy() {
  const body = { direction: state.direction };
  if (state.quizPickMode && state.manualCards.size) {
    body.card_ids = [...state.manualCards];
    body.count = state.manualCards.size;
  } else {
    body.scenes = [...state.selectedScenes];
    body.tags = [...state.activeTags];
    body.mastery_filter = [...state.masteryFilter];
    body.count = state.wordCount;
  }
  const resp = await api('/api/start', body);
  state.session = { words: resp.words, direction: state.direction, idx: 0, retrying: false };
  state.studyFlipped = false;
  state.phase = 'study';
  render();
}
function startQuizFromStudy() {
  state.session = { ...state.session, idx: 0, retrying: false };
  state.results = [];
  state.studyFlipped = false;
  state.phase = 'quiz';
  render();
}
function sNav(dir) {
  const n = state.session.words.length;
  state.session.idx = Math.max(0, Math.min(state.session.idx + dir, n - 1));
  state.studyFlipped = false;
  renderStudyCards();
}
function sFlip() {
  state.studyFlipped = !state.studyFlipped;
  const el = document.getElementById('s-inner');
  if (el) el.classList.toggle('flipped', state.studyFlipped);
}
function renderStudyCards() {
  const { words, idx } = state.session;
  if (!words || !words.length) { state.phase = 'setup'; render(); return; }
  const card = words[idx];
  const article = _cardArticle(card);
  const gc = _genderColor(card);
  const declHint = _declensionHint(card);
  const lvl = card._mastery_level || 'new';
  const mastLbl = lvl !== 'new' ? MASTERY_LABELS[lvl] : null;
  const borderColor = gc || `${MASTERY_COLORS[lvl]}55`;
  const pct = Math.round((idx + 1) / words.length * 100);
  const atEnd = idx >= words.length - 1;

  document.getElementById('content').innerHTML = `
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="fc-nav">
      <button class="fc-nav-btn" onclick="sNav(-1)" ${idx === 0 ? 'disabled' : ''}>← Prev</button>
      <span class="fc-counter">${idx + 1} / ${words.length}</span>
      <button class="fc-nav-btn" onclick="sNav(1)" ${atEnd ? 'disabled' : ''}>Next →</button>
    </div>
    <div class="fc-wrap" onclick="sFlip()" id="fc-wrap">
      <div class="fc-inner${state.studyFlipped ? ' flipped' : ''}" id="s-inner">
        <div class="fc-front" style="border-color:${borderColor};${gc ? 'border-width:2px' : ''}">
          ${card.priority ? '<div style="font-size:14px;margin-bottom:8px">⭐</div>' : ''}
          ${mastLbl ? `<div class="mastery-badge" style="color:${MASTERY_COLORS[lvl]}">${mastLbl}</div>` : ''}
          ${article ? `<div class="fc-article" style="${gc ? `color:${gc};font-size:15px;font-weight:700` : ''}">${article}</div>` : ''}
          <div class="fc-greek" style="${gc ? `color:${gc}` : ''}">${esc(card.greek)}</div>
          ${declHint ? `<div class="fc-decl">${esc(declHint)}</div>` : ''}
          <div class="fc-pron">/${esc(card.pronunciation)}/</div>
          <div class="fc-type">${esc(card.type)}</div>
          <div class="fc-hint">tap to reveal</div>
        </div>
        <div class="fc-back">
          ${_cardBackHTML(card)}
        </div>
      </div>
    </div>
    <div class="row-btns" style="margin-top:0">
      <button class="btn-secondary" style="flex:1" onclick="state.phase='setup';state.studyFlipped=false;render()">← Back</button>
      <button class="btn-primary" style="margin-top:0;flex:2" onclick="startQuizFromStudy()">🎯 Start Quiz →</button>
    </div>
  `;
  requestAnimationFrame(() => {
    const inner = document.getElementById('s-inner');
    if (inner) { const f = inner.querySelector('.fc-front'); if (f) inner.style.height = f.offsetHeight + 'px'; }
  });
}

// ── Quiz ───────────────────────────────────────────────────────────────────
function renderQuiz() {
  const { words, direction, idx } = state.session;
  const card = words[idx];
  const isGrEn = direction === 'gr-en';
  const prompt = isGrEn ? card.greek : card.translation;
  const promptSub = isGrEn ? `/${card.pronunciation}/` : card.type.split('·')[0].trim();
  const pct = ((idx+1)/words.length*100).toFixed(1);
  const mastLbl = card._mastery_label;
  const mastColor = card._mastery_level ? MASTERY_COLORS[card._mastery_level] : null;
  const promptSize = isGrEn ? '40px' : '22px';

  document.getElementById('content').innerHTML = `
    <div class="progress-label">
      <span>${isGrEn?'🇬🇷 → 🇬🇧':'🇬🇧 → 🇬🇷'}</span>
      <span>${idx+1} / ${words.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="prompt-card" style="border:1px solid ${card._gc?card._gc+'33':'rgba(255,255,255,.06)'}">
      ${card.priority?'<div class="star-badge">⭐</div>':''}
      ${mastLbl?`<div class="mastery-badge" style="color:${mastColor}">${mastLbl}</div>`:''}
      <div class="prompt-greek" style="font-size:${promptSize}">${esc(prompt)}</div>
      ${isGrEn && _declensionHint(card) ? `<div class="prompt-decl">${esc(_declensionHint(card))}</div>` : ''}
      <div class="prompt-sub">${esc(promptSub)}</div>
      <div class="prompt-scene">${esc(card.scene_label||'')}</div>
    </div>
    <input type="text" id="answer-input" autofocus
      placeholder="${isGrEn?'Type the English translation...':'Type the Greek word (article optional)...'}"
      style="font-family:${isGrEn?'sans-serif':'Georgia,serif'}"
      onkeydown="if(event.key==='Enter')checkAnswer()">
    <div class="row-btns">
      <button class="btn-primary" style="margin-top:0" onclick="checkAnswer()">Check ↵</button>
      <button class="btn-secondary" onclick="skipWord()">Skip</button>
      <button class="btn-secondary" onclick="dropWord()" title="Remove this word from the session">Drop ✕</button>
    </div>
  `;
  document.getElementById('answer-input').focus();
}

async function checkAnswer() {
  const input = document.getElementById('answer-input');
  if (!input) return;
  const guess = input.value.trim();
  const { words, direction, idx, retrying } = state.session;
  const card = words[idx];
  const resp = await api('/api/check', {
    card_id: card.id, guess, direction, is_retry: retrying,
  });
  const result = resp.result;

  if (result === 'close' && !retrying) {
    state.session.retrying = true;
    showCloseFeedback(card, guess, direction);
    return;
  }

  state.session.retrying = false;
  const score = result === 'correct' ? 1 : retrying ? 0.5 : 0;
  state.results.push({ card, score, userAnswer: guess });
  showFeedback(card, guess, result, resp.stat, direction);
}

async function skipWord() {
  const { words, direction, idx, retrying } = state.session;
  const card = words[idx];
  // If skipping during retry, record 0.5; otherwise 0
  const score = retrying ? 0.5 : 0;
  const stat = await api('/api/check', {
    card_id: card.id, guess: '', direction, is_retry: retrying,
  });
  state.session.retrying = false;
  state.results.push({ card, score, userAnswer: '' });
  showFeedback(card, '', 'wrong', stat.stat, direction);
}

function dropWord() {
  // Remove current word from the session entirely — no score recorded
  const { words, idx } = state.session;
  words.splice(idx, 1);
  state.session.retrying = false;
  if (words.length === 0) { state.phase = 'results'; render(); return; }
  if (idx >= words.length) state.session.idx = words.length - 1;
  renderQuiz();
}

function showCloseFeedback(card, guess, direction) {
  const isGrEn = direction === 'gr-en';
  const pct = ((state.session.idx+1)/state.session.words.length*100).toFixed(1);
  document.getElementById('content').innerHTML = `
    <div class="progress-label">
      <span>${isGrEn?'🇬🇷 → 🇬🇧':'🇬🇧 → 🇬🇷'}</span>
      <span>${state.session.idx+1} / ${state.session.words.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="feedback close">
      <div class="feedback-verdict">～ Almost — check your spelling</div>
      <div class="feedback-yours">You wrote: ${esc(guess)}</div>
    </div>
    <input type="text" id="answer-input" autofocus
      placeholder="${isGrEn?'Try again in English…':'Try again in Greek…'}"
      style="font-family:${isGrEn?'sans-serif':'Georgia,serif'}"
      onkeydown="if(event.key==='Enter')checkAnswer()">
    <div class="row-btns">
      <button class="btn-primary" style="margin-top:0" onclick="checkAnswer()">Try Again ↵</button>
      <button class="btn-secondary" onclick="skipWord()">Give Up</button>
    </div>
  `;
  document.getElementById('answer-input').focus();
}

function showFeedback(card, guess, result, stat, direction) {
  const isGrEn = direction === 'gr-en';
  const correct = result === 'correct';
  const close = result === 'close';
  const correctAnswer = isGrEn ? card.translation : card.greek;
  const window_ = stat?.window || [];
  const dots = window_.map((v, i) => {
    const opacity = 0.3 + 0.7*(i/Math.max(window_.length-1,1));
    const color = v === 1 ? '#7ac49a' : v === 0.5 ? '#e6b450' : '#d47a8f';
    return `<div class="dot" style="background:${color};opacity:${opacity}"></div>`;
  }).join('');
  const answerFont = isGrEn ? 'sans-serif' : 'Georgia,serif';
  const feedbackClass = correct ? 'correct' : close ? 'close' : 'wrong';
  const verdict = correct
    ? (state.session.retrying === false && guess ? '✓ Correct!' : '✓ Correct!')
    : '✗ Not quite';

  document.getElementById('content').innerHTML = `
    <div class="progress-label">
      <span>${isGrEn?'🇬🇷 → 🇬🇧':'🇬🇧 → 🇬🇷'}</span>
      <span>${state.session.idx+1} / ${state.session.words.length}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${((state.session.idx+1)/state.session.words.length*100).toFixed(1)}%"></div></div>
    <div class="feedback ${feedbackClass}">
      <div class="feedback-verdict">${verdict}</div>
      <div class="feedback-answer">
        <span style="color:rgba(255,255,255,.4);font-size:11px">Answer: </span>
        <strong style="font-family:${answerFont}">${esc(correctAnswer)}</strong>
      </div>
      ${!correct&&guess?`<div class="feedback-yours">You wrote: ${esc(guess)}</div>`:''}
      ${window_.length?`<div class="window-dots">
        <span style="font-size:9px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-right:2px">
          last ${window_.length}:
        </span>${dots}</div>`:''}
    </div>
    <div class="bcard-body" style="display:block;padding:0 0 12px">
      ${_cardBackHTML(card)}
    </div>
    <button class="btn-primary" id="next-word-btn" onclick="nextWord()">
      ${state.session.idx+1>=state.session.words.length?'See Results →':'Next Word →'}
    </button>
    <div style="font-size:10px;color:rgba(255,255,255,.2);font-family:sans-serif;text-align:center;margin-top:6px">press Enter to continue</div>
  `;
  document.addEventListener('keydown', _feedbackEnterHandler);
}

function _feedbackEnterHandler(e) {
  if (e.key === 'Enter') { e.preventDefault(); nextWord(); }
}

function nextWord() {
  document.removeEventListener('keydown', _feedbackEnterHandler);
  state.session.idx++;
  if (state.session.idx >= state.session.words.length) {
    state.phase = 'results';
    render();
  } else {
    renderQuiz();
  }
}

// ── Results ────────────────────────────────────────────────────────────────
function renderResults() {
  const results = state.results;
  const totalScore = results.reduce((s,r)=>s+r.score,0);
  const correctCount = results.filter(r=>r.score===1).length;
  const total = results.length;
  const pct = total>0 ? Math.round(totalScore/total*100) : 0;
  let verdict, color;
  if (pct>=90){verdict='Εξαιρετικά! Outstanding!';color='#7ac49a';}
  else if(pct>=70){verdict='Πολύ καλά! Well done!';color='#c9a96e';}
  else if(pct>=50){verdict='Καλή προσπάθεια! Keep going!';color='#e8c98a';}
  else{verdict='Χρειάζεται εξάσκηση. More practice needed.';color='#d47a8f';}

  const rows = results.map(r => {
    const stat = state.progressData[String(r.card.id)];
    const n = stat?.window?.length||0;
    const acc = n ? Math.round(stat.window.filter(Boolean).length/n*100) : null;
    const lbl = acc!==null ? `${acc}% (last ${n})` : null;
    const level = r.card._mastery_level||'new';
    return `<div class="result-row" style="border-color:${r.score===1?'rgba(122,196,154,.3)':r.score===0.5?'rgba(230,180,80,.3)':'rgba(212,122,143,.3)'}">
      <span>${r.score===1?'✅':r.score===0.5?'〰️':'❌'}</span>
      <span class="result-greek">${esc(r.card.greek)}</span>
      <span class="result-trans">${esc(r.card.translation)}</span>
      ${lbl?`<span class="result-acc" style="color:${MASTERY_COLORS[level]}">${lbl}</span>`:''}
    </div>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <div class="score-big" style="color:${color}">${pct}%</div>
    <div class="score-sub">${totalScore.toFixed(1)} / ${total} points</div>
    <div class="score-verdict" style="color:${color}">${verdict}</div>
    ${rows}
    <div class="row-btns" style="margin-top:16px">
      <button class="btn-secondary" style="flex:1" onclick="newSession()">← New Setup</button>
      <button class="btn-secondary" style="flex:1" onclick="flipSession()">${state.session.direction==='gr→en'?'Flip: en→gr ⇄':'Flip: gr→en ⇄'}</button>
      <button class="btn-primary" style="margin-top:0;flex:1" onclick="restartSession()">Again →</button>
    </div>
  `;
}

function newSession() { state.phase='setup'; render(); }
function restartSession() {
  const words = [...state.session.words].sort(()=>Math.random()-.5);
  state.session = { ...state.session, words, idx:0, retrying:false };
  state.results = [];
  state.phase = 'quiz';
  render();
}
function flipSession() {
  const flipped = state.session.direction === 'gr→en' ? 'en→gr' : 'gr→en';
  const words = [...state.session.words].sort(()=>Math.random()-.5);
  state.session = { ...state.session, words, idx:0, direction:flipped, retrying:false };
  state.results = [];
  state.phase = 'quiz';
  render();
}

// ── Browse ────────────────────────────────────────────────────────────────
async function renderBrowse() {
  if (!state.browseCards.length) {
    const resp = await api('/api/cards', {});
    state.browseCards = resp.cards;
  }
  const prog = await api('/api/progress');
  state.progressData = prog;
  _renderBrowseUI();
}

function _browseFilter() {
  const q = state.browseSearch.toLowerCase();
  return state.browseCards.filter(c => {
    if (state.browseScenes.size && !state.browseScenes.has(c.scene_id)) return false;
    if (state.browseTags.size) {
      for (const t of state.browseTags) {
        if (t === 'priority' && !c.priority) return false;
        if (t !== 'priority' && !(c.tags||[]).includes(t)) return false;
      }
    }
    if (state.browseMastery !== 'all') {
      const stat = state.progressData[String(c.id)];
      const n = stat?.window?.length||0;
      const acc = n ? stat.window.filter(Boolean).length/n : 0;
      let lvl = 'new';
      if (n>=5&&acc>=0.8) lvl='mastered';
      else if (n>=3&&acc<0.4) lvl='struggling';
      else if (n>0) lvl='learning';
      if (lvl !== state.browseMastery) return false;
    }
    if (q) {
      const haystack = (c.greek+' '+c.translation+' '+c.pronunciation+(c.definition||'')).toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

function _cardMastery(c) {
  const stat = state.progressData[String(c.id)];
  const n = stat?.window?.length||0;
  const acc = n ? stat.window.filter(Boolean).length/n : 0;
  if (n>=5&&acc>=0.8) return 'mastered';
  if (n>=3&&acc<0.4) return 'struggling';
  if (n>0) return 'learning';
  return 'new';
}

function _cardArticle(c) {
  const g = (c.grammar||[]).find(r => r.label === 'Article');
  return g ? g.value : null;
}

function _genderColor(c) {
  const g = (c.grammar||[]).find(r => r.label === 'Gender');
  if (!g) return null;
  const v = g.value.toLowerCase();
  if (v.includes('feminine'))  return '#d47a8f';
  if (v.includes('masculine')) return '#7ab3d4';
  if (v.includes('neuter'))    return '#7ac49a';
  return null;
}

function _declensionHint(card) {
  // Returns compact suffix string like "-η / -ο" for adjectives, or null.
  const g = card.grammar || [];
  function cleanVal(v) {
    return (v || '').replace(/\s*←.*$/, '').split(/[·,]/)[0].trim();
  }
  function findLabel(labels) {
    const e = g.find(e => labels.some(l => e.label.toLowerCase().includes(l)));
    return e ? cleanVal(e.value) : null;
  }
  const masc = findLabel(['masculine', 'masc']);
  const fem  = findLabel(['feminine',  'fem']);
  const neut = findLabel(['neuter',    'neut']);

  const forms = [masc, fem, neut].filter(Boolean);
  if (forms.length < 2) return null;

  // Longest common prefix across all forms
  let stem = forms[0];
  for (const f of forms.slice(1)) {
    let i = 0;
    while (i < stem.length && i < f.length && stem[i] === f[i]) i++;
    stem = stem.slice(0, i);
  }

  // Collect endings that differ from masculine (or from each other)
  const seen = new Set();
  const parts = [];
  for (const f of [fem, neut]) {
    if (!f || f === masc) continue;
    const suf = '-' + f.slice(stem.length);
    if (!seen.has(suf)) { seen.add(suf); parts.push(suf); }
  }
  return parts.length ? parts.join(' / ') : null;
}

function _cardBackHTML(c) {
  const grammarRows = (c.grammar||[]).map(g =>
    `<div class="bcard-grammar-row"><span class="bcard-grammar-lbl">${esc(g.label)}</span><span class="bcard-grammar-val">${esc(g.value)}</span></div>`
  ).join('');
  return `
    <div style="margin-bottom:6px">
      <div style="font-size:13px;color:rgba(255,255,255,.45);font-family:sans-serif;font-style:italic">${esc(c.translation)}</div>
      <div style="font-size:10px;color:rgba(255,255,255,.2);font-family:sans-serif;margin-top:2px">${esc(c.type)}</div>
    </div>
    ${c.definition?`<div class="bcard-section"><div class="bcard-section-label">Definition</div><div class="bcard-def">${esc(c.definition)}</div></div>`:''}
    ${c.example?`<div class="bcard-section"><div class="bcard-section-label">Example</div><div class="bcard-example"><div class="gr">${esc(c.example.gr)}</div><div class="en">${esc(c.example.en)}</div></div></div>`:''}
    ${grammarRows?`<div class="bcard-section"><div class="bcard-section-label">Grammar</div><div class="bcard-grammar">${grammarRows}</div></div>`:''}
    ${c.note?`<div class="bcard-section"><div class="bcard-section-label">Note</div><div class="bcard-note">💡 ${esc(c.note)}</div></div>`:''}
    ${c.etymology?`<div class="bcard-section"><div class="bcard-section-label">Etymology</div><div class="bcard-etym">${esc(c.etymology)}</div></div>`:''}
  `;
}

function _renderBrowseUI() {
  const filtered = _browseFilter();

  const filters = `
    <div class="sec">
      <div class="sec-label">Groups
        <a onclick="event.stopPropagation();toggleAllBrowseScenes()">${state.browseScenes.size===SCENES_META.length?'deselect all':'select all'}</a>
      </div>
      <div class="pills">${SCENES_META.map(s=>pill(s.id,s.label,state.browseScenes.has(s.id),`toggleBScene('${s.id}')`)).join('')}</div>
    </div>
    <div class="sec">
      <div class="sec-label">Tags</div>
      <div class="pills">${ALL_TAGS.map(t=>pill(t.key,t.label,state.browseTags.has(t.key),`toggleBTag('${t.key}')`)).join('')}</div>
    </div>
    <div class="sec">
      <div class="sec-label">Knowledge level</div>
      <div class="pills">${['all','new','learning','struggling','mastered'].map(k=>pill(k,k==='all'?'🎲 All':MASTERY_LABELS[k],state.browseMastery===k,`setBMastery('${k}')`)).join('')}</div>
    </div>
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input type="text" placeholder="Search words, translations…" value="${esc(state.browseSearch)}"
        oninput="state.browseSearch=this.value;state.browseCardIdx=0;state.browseFlipped=false;_renderBrowseUI()">
    </div>
    <div class="view-toggle">
      <button class="view-btn${state.browseView==='list'?' active':''}" onclick="setBrowseView('list')">☰ List</button>
      <button class="view-btn${state.browseView==='cards'?' active':''}" onclick="setBrowseView('cards')">◻ Cards</button>
    </div>
    <div class="browse-count">${filtered.length} word${filtered.length!==1?'s':''}</div>
  `;

  if (state.browseView === 'cards') {
    const idx = Math.min(state.browseCardIdx, Math.max(filtered.length-1, 0));
    state.browseCardIdx = idx;
    if (!filtered.length) {
      document.getElementById('content').innerHTML = filters + `<div style="text-align:center;color:rgba(255,255,255,.2);font-family:sans-serif;padding:40px 0">No words match</div>`;
      return;
    }
    const c = filtered[idx];
    const article = _cardArticle(c);
    const lvl = _cardMastery(c);
    const gc = _genderColor(c);
    const borderColor = gc || `${MASTERY_COLORS[lvl]}55`;

    document.getElementById('content').innerHTML = filters + `
      <div class="fc-nav">
        <button class="fc-nav-btn" onclick="fcNav(-1)" ${idx===0?'disabled':''}>← Prev</button>
        <span class="fc-counter">${idx+1} / ${filtered.length}</span>
        <button class="icon-btn" title="Edit" onclick="editCard(${c.id})">✏️</button>
        <button class="icon-btn del" title="Delete" onclick="deleteCard(${c.id})">🗑️</button>
        <button class="fc-nav-btn" onclick="fcNav(1)" ${idx>=filtered.length-1?'disabled':''}>Next →</button>
      </div>
      <div class="fc-wrap" onclick="fcFlip()" id="fc-wrap">
        <div class="fc-inner${state.browseFlipped?' flipped':''}" id="fc-inner">
          <div class="fc-front" style="border-color:${borderColor};${gc?`border-width:2px`:''}">
            ${c.priority?'<div style="font-size:14px;margin-bottom:8px">⭐</div>':''}
            ${article?`<div class="fc-article" style="${gc?`color:${gc};font-size:15px;font-weight:700`:''}">${article}</div>`:''}
            <div class="fc-greek" style="${gc?`color:${gc}`:''}">${esc(c.greek)}</div>
            ${_declensionHint(c)?`<div class="fc-decl">${esc(_declensionHint(c))}</div>`:''}
            <div class="fc-pron">/${esc(c.pronunciation)}/</div>
            <div class="fc-type">${esc(c.type)}</div>
            <div class="fc-hint">tap to reveal</div>
          </div>
          <div class="fc-back">
            ${_cardBackHTML(c)}
          </div>
        </div>
      </div>
    `;
    // Fix fc-inner height to match front after render
    requestAnimationFrame(() => {
      const inner = document.getElementById('fc-inner');
      if (inner) inner.style.height = inner.querySelector('.fc-front').offsetHeight + 'px';
    });
  } else {
    const cards = filtered.map(c => {
      const lvl = _cardMastery(c);
      const dotColor = MASTERY_COLORS[lvl];
      const isOpen = state.browseOpen.has(c.id);
      const tagBadges = (c.tags||[]).filter(t=>t!=='neutral').map(t => {
        const found = ALL_TAGS.find(x=>x.key===t);
        return found ? `<span style="font-size:9px;opacity:.6">${found.label.split(' ')[0]}</span>` : '';
      }).join('');
      const gc = _genderColor(c);
      const body = isOpen ? `<div class="bcard-body">${_cardBackHTML(c)}</div>` : '';
      return `<div class="bcard${isOpen?' open':''}" style="${gc?`border-left:3px solid ${gc}`:''}" onclick="toggleBCard(${c.id})">
        <div class="bcard-head">
          <div class="mastery-dot" style="background:${dotColor}" title="${lvl}"></div>
          ${c.priority?'<span style="font-size:11px">⭐</span>':''}
          <span class="bcard-greek" style="${gc?`color:${gc}`:''}">${esc(c.greek)}</span>
          <span class="bcard-pron">/${esc(c.pronunciation)}/</span>
          <span class="bcard-trans">${esc(c.translation)}</span>
          <span class="bcard-badges">${tagBadges}</span>
          <button class="icon-btn" title="Edit" onclick="event.stopPropagation();editCard(${c.id})">✏️</button>
          <button class="icon-btn del" title="Delete" onclick="event.stopPropagation();deleteCard(${c.id})">🗑️</button>
          <span style="font-size:10px;color:rgba(255,255,255,.2)">${isOpen?'▲':'▼'}</span>
        </div>
        ${body}
      </div>`;
    }).join('');
    document.getElementById('content').innerHTML = filters + cards;
  }
}

function setBrowseView(v) { state.browseView=v; state.browseFlipped=false; state.browseCardIdx=0; _renderBrowseUI(); }
function fcFlip() {
  state.browseFlipped = !state.browseFlipped;
  const inner = document.getElementById('fc-inner');
  if (inner) inner.classList.toggle('flipped', state.browseFlipped);
}
function fcNav(dir) {
  const filtered = _browseFilter();
  state.browseCardIdx = Math.max(0, Math.min(state.browseCardIdx + dir, filtered.length - 1));
  state.browseFlipped = false;
  _renderBrowseUI();
}

function toggleBCard(id) {
  state.browseOpen.has(id) ? state.browseOpen.delete(id) : state.browseOpen.add(id);
  _renderBrowseUI();
}
function toggleBScene(id) { state.browseScenes.has(id)?state.browseScenes.delete(id):state.browseScenes.add(id); _renderBrowseUI(); }
function toggleAllBrowseScenes() {
  state.browseScenes = state.browseScenes.size===SCENES_META.length ? new Set() : new Set(SCENES_META.map(s=>s.id));
  _renderBrowseUI();
}
function toggleBTag(k) { state.browseTags.has(k)?state.browseTags.delete(k):state.browseTags.add(k); _renderBrowseUI(); }
function setBMastery(m) { state.browseMastery=m; _renderBrowseUI(); }

// ── Add ───────────────────────────────────────────────────────────────────
const ADD_TEMPLATE = `greek:
pronunciation:
type: Ουσιαστικό · Noun
translation:
group: general
definition:
example.gr:
example.en:
grammar.Article:
grammar.Gender: Feminine
grammar.Plural:
note:
etymology:
tags: neutral
priority: no`;

let addParsed = null;
let addScenesMeta = null;
let addEditingId = null;
let addInputMode = 'lookup';   // 'lookup' | 'manual'

function cardToText(card) {
  const lines = [];
  lines.push(`greek: ${card.greek}`);
  if (card.pronunciation) lines.push(`pronunciation: ${card.pronunciation}`);
  if (card.type)          lines.push(`type: ${card.type}`);
  lines.push(`translation: ${card.translation}`);
  lines.push(`group: ${card.scene_id}`);
  if (card.definition)    lines.push(`definition: ${card.definition}`);
  if (card.example?.gr)   lines.push(`example.gr: ${card.example.gr}`);
  if (card.example?.en)   lines.push(`example.en: ${card.example.en}`);
  for (const g of (card.grammar||[])) lines.push(`grammar.${g.label}: ${g.value}`);
  if (card.note)          lines.push(`note: ${card.note}`);
  if (card.etymology)     lines.push(`etymology: ${card.etymology}`);
  if (card.tags?.length)  lines.push(`tags: ${card.tags.join(', ')}`);
  lines.push(`priority: ${card.priority ? 'yes' : 'no'}`);
  return lines.join('\n');
}

async function renderAdd(prefillCard) {
  if (!addScenesMeta) {
    const r = await api('/api/scenes');
    addScenesMeta = r.scenes;
  }
  const isEditing = prefillCard != null;
  const sceneList = addScenesMeta.map(s => s.id).join(', ');

  const modeToggle = isEditing ? '' : `
    <div class="add-mode-toggle">
      <button class="view-btn${addInputMode==='lookup'?' active':''}" onclick="setAddMode('lookup')">🔍 Wiktionary</button>
      <button class="view-btn${addInputMode==='mask'?' active':''}" onclick="setAddMode('mask')">📋 Mask</button>
      <button class="view-btn${addInputMode==='manual'?' active':''}" onclick="setAddMode('manual')">✏️ Manual</button>
    </div>`;

  const lookupSection = `
    <div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-bottom:10px">
      Enter the dictionary/base form of the word (e.g. <em>κατεβαίνω</em> not <em>κατέβηκε</em>).
      Fetches from English Wiktionary — coverage varies.
    </div>
    <form class="lookup-wrap" onsubmit="event.preventDefault();doLookup()">
      <input type="search" id="lookup-input" placeholder="Greek word…" spellcheck="false"
        autocomplete="off" autocorrect="off" autocapitalize="none" inputmode="search">
      <button type="submit" class="btn-secondary">Look up →</button>
    </form>
    <div id="lookup-status"></div>
    <div id="lookup-result-area"></div>`;

  const sceneOptions = addScenesMeta.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
  const maskSection = (addInputMode !== 'mask' || isEditing) ? '' : `
    <div class="mask-form">
      <div class="mask-row">
        <div class="mask-field">
          <span class="mask-label">Greek word *</span>
          <div style="display:flex;gap:6px;align-items:center">
            <input id="mask-greek" type="text" class="mask-input" style="flex:1;min-width:0" spellcheck="false" autocorrect="off" autocapitalize="none" placeholder="e.g. γράφω, σπίτι, καλός">
            <button type="button" class="view-btn" onclick="maskLookupFill()" style="white-space:nowrap;padding:7px 10px;font-size:11px;flex-shrink:0">🔍 Wiktionary</button>
          </div>
          <span id="mask-lookup-status" style="font-size:10px;font-family:sans-serif;color:rgba(255,255,255,.3);margin-top:2px;display:block"></span>
        </div>
        <div class="mask-field">
          <span class="mask-label">Pronunciation</span>
          <input id="mask-pronunciation" type="text" class="mask-input" placeholder="GRA-fo, spi-TI">
        </div>
      </div>
      <div class="mask-field">
        <span class="mask-label">Translation (English) *</span>
        <input id="mask-translation" type="text" class="mask-input" placeholder="concise English gloss">
      </div>
      <div class="mask-row">
        <div class="mask-field">
          <span class="mask-label">Type</span>
          <select id="mask-type" class="mask-select" onchange="maskTypeChange(this.value)">
            <option value="">— select —</option>
            <option value="Ουσιαστικό · Noun">Noun</option>
            <option value="Ρήμα · Verb">Verb</option>
            <option value="Επίθετο · Adjective">Adjective</option>
            <option value="Επίρρημα · Adverb">Adverb</option>
            <option value="Σύνδεσμος · Conjunction">Conjunction</option>
            <option value="Πρόθεση · Preposition">Preposition</option>
            <option value="Φράση · Phrase">Phrase</option>
          </select>
        </div>
        <div class="mask-field">
          <span class="mask-label">Group *</span>
          <select id="mask-scene" class="mask-select" onchange="maskSceneChange(this.value)">
            ${sceneOptions}
            <option value="__new__">✨ New group…</option>
          </select>
          <input id="mask-scene-new" type="text" class="mask-input" placeholder="New group name"
            style="display:none;margin-top:6px">
        </div>
      </div>

      <div class="mask-section-label">Grammar</div>

      <div id="mask-grammar-noun" style="display:none">
        <div class="mask-row" style="margin-bottom:8px">
          <div class="mask-field">
            <span class="mask-label">Article</span>
            <div class="mask-radio-group">
              <label><input type="radio" name="mask-article" value="ο"> ο (masc.)</label>
              <label><input type="radio" name="mask-article" value="η"> η (fem.)</label>
              <label><input type="radio" name="mask-article" value="το"> το (neut.)</label>
            </div>
          </div>
          <div class="mask-field">
            <span class="mask-label">Gender</span>
            <input id="mask-noun-gender" type="text" class="mask-input" placeholder="Masculine / Feminine / Neuter">
          </div>
        </div>
        <div class="mask-row">
          <div class="mask-field">
            <span class="mask-label">Genitive singular</span>
            <input id="mask-noun-genitive" type="text" class="mask-input" placeholder="e.g. του άνδρα, της γυναίκας">
          </div>
          <div class="mask-field">
            <span class="mask-label">Nominative plural</span>
            <input id="mask-noun-plural" type="text" class="mask-input" placeholder="e.g. οι άνδρες">
          </div>
        </div>
      </div>

      <div id="mask-grammar-verb" style="display:none">
        <div class="mask-row" style="margin-bottom:8px">
          <div class="mask-field">
            <span class="mask-label">Aorist (1sg) *</span>
            <input id="mask-verb-aorist" type="text" class="mask-input" spellcheck="false" autocorrect="off" placeholder="e.g. έγραψα, πήγα">
          </div>
          <div class="mask-field">
            <span class="mask-label">Voice</span>
            <div class="mask-radio-group">
              <label><input type="radio" name="mask-voice" value="Active" checked> Active</label>
              <label><input type="radio" name="mask-voice" value="Passive"> Passive</label>
              <label><input type="radio" name="mask-voice" value="Deponent"> Deponent</label>
            </div>
          </div>
        </div>
        <div class="mask-field">
          <span class="mask-label">Stem (if irregular)</span>
          <input id="mask-verb-stem" type="text" class="mask-input" spellcheck="false" autocorrect="off" placeholder="e.g. γραφ-, πηγ-">
        </div>
      </div>

      <div id="mask-grammar-adj" style="display:none">
        <div class="mask-row" style="margin-bottom:8px">
          <div class="mask-field">
            <span class="mask-label">Masculine</span>
            <input id="mask-adj-masc" type="text" class="mask-input" spellcheck="false" autocorrect="off" placeholder="e.g. καλός">
          </div>
          <div class="mask-field">
            <span class="mask-label">Feminine</span>
            <input id="mask-adj-fem" type="text" class="mask-input" spellcheck="false" autocorrect="off" placeholder="e.g. καλή">
          </div>
          <div class="mask-field">
            <span class="mask-label">Neuter</span>
            <input id="mask-adj-neut" type="text" class="mask-input" spellcheck="false" autocorrect="off" placeholder="e.g. καλό">
          </div>
        </div>
        <div class="mask-field">
          <span class="mask-label">Plural (all genders)</span>
          <input id="mask-adj-plural" type="text" class="mask-input" spellcheck="false" placeholder="e.g. -οι / -ες / -α">
        </div>
      </div>

      <div style="margin-top:6px">
        <div id="mask-custom-rows"></div>
        <button class="btn-secondary" style="font-size:11px;padding:5px 12px;margin-top:6px;height:auto;width:auto" onclick="maskAddCustomRow()">+ Custom grammar field</button>
      </div>

      <div class="mask-section-label">Optional</div>

      <div class="mask-field">
        <span class="mask-label">Definition</span>
        <input id="mask-definition" type="text" class="mask-input" placeholder="extended description if translation is not enough">
      </div>
      <div class="mask-row">
        <div class="mask-field">
          <span class="mask-label">Example (Greek)</span>
          <input id="mask-example-gr" type="text" class="mask-input" spellcheck="false" autocorrect="off">
        </div>
        <div class="mask-field">
          <span class="mask-label">Example (English)</span>
          <input id="mask-example-en" type="text" class="mask-input">
        </div>
      </div>
      <div class="mask-row">
        <div class="mask-field">
          <span class="mask-label">Note</span>
          <input id="mask-note" type="text" class="mask-input" placeholder="usage, register, common idioms">
        </div>
        <div class="mask-field">
          <span class="mask-label">Etymology</span>
          <input id="mask-etymology" type="text" class="mask-input">
        </div>
      </div>
      <div class="mask-row">
        <div class="mask-field">
          <span class="mask-label">Tags</span>
          <input id="mask-tags" type="text" class="mask-input" value="neutral" placeholder="neutral, everyday life, …">
        </div>
        <div class="mask-field" style="flex:0 0 auto;justify-content:flex-end">
          <span class="mask-label">Priority</span>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:8px 0;font-size:12px;color:rgba(255,255,255,.55);font-family:sans-serif">
            <input type="checkbox" id="mask-priority" style="accent-color:#c9a96e"> ⭐ High priority
          </label>
        </div>
      </div>
    </div>`;

  const manualHint = `
    <div class="add-hint">
      <strong style="color:rgba(255,255,255,.4)">Fields${isEditing ? '' : ' — separate cards with <code style="color:#c9a96e">---</code> or <code style="color:#c9a96e">//</code>'}</strong><br><br>
      <span style="color:rgba(255,255,255,.35)">Required</span><br>
      <span style="color:#c9a96e">greek:</span>        base/dictionary form &nbsp;<span style="color:rgba(255,255,255,.2)">verb: present 1sg (-ω/-ομαι) · noun: nominative sg · adjective: masculine sg</span><br>
      <span style="color:#c9a96e">translation:</span>  concise English gloss<br>
      <span style="color:#c9a96e">group:</span>        group name &nbsp;<span style="color:rgba(255,255,255,.2)">existing: ${sceneList} — or any new name</span><br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Optional</span><br>
      <span style="color:rgba(201,169,110,.7)">pronunciation:</span> &nbsp;stressed syllable in CAPS · e.g. <em>ka-te-VAI-no</em>, <em>GRA-fo</em><br>
      <span style="color:rgba(201,169,110,.7)">type:</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<em>Ουσιαστικό · Noun</em> · <em>Ρήμα · Verb</em> · <em>Επίθετο · Adjective</em> · <em>Επίρρημα · Adverb</em> · <em>Σύνδεσμος · Conjunction</em><br>
      <span style="color:rgba(201,169,110,.7)">definition:</span> &nbsp;&nbsp;&nbsp;&nbsp;extended description if translation is not enough<br>
      <span style="color:rgba(201,169,110,.7)">note:</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;usage notes, register, common phrases<br>
      <span style="color:rgba(201,169,110,.7)">etymology:</span> &nbsp;&nbsp;&nbsp;&nbsp;word origin<br>
      <span style="color:rgba(201,169,110,.7)">example.gr:</span> &nbsp;&nbsp;&nbsp;Greek example sentence<br>
      <span style="color:rgba(201,169,110,.7)">example.en:</span> &nbsp;&nbsp;&nbsp;English translation of example<br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Grammar — Nouns</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Article:</span> &nbsp;&nbsp;ο / η / το &nbsp;<span style="color:rgba(255,255,255,.2)">(validated against typed article in quiz)</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Gender:</span> &nbsp;&nbsp;Masculine / Feminine / Neuter<br>
      <span style="color:rgba(201,169,110,.7)">grammar.Plural:</span> &nbsp;&nbsp;plural nominative<br>
      <span style="color:rgba(201,169,110,.7)">grammar.Genitive:</span> &nbsp;genitive singular<br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Grammar — Verbs</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Aorist:</span> &nbsp;&nbsp;aorist 1sg &nbsp;<span style="color:rgba(255,255,255,.2)">e.g. <em>έγραψα</em> · required for past-tense forms</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Voice:</span> &nbsp;&nbsp;&nbsp;Active / Passive<br>
      <span style="color:rgba(201,169,110,.7)">grammar.Stem:</span> &nbsp;&nbsp;&nbsp;&nbsp;verb stem if irregular<br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Grammar — Adjectives</span> &nbsp;<span style="color:rgba(122,196,154,.5)">★ Masculine / Feminine / Neuter all accepted as correct quiz answers</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Masculine:</span> &nbsp;masculine sg &nbsp;<span style="color:rgba(255,255,255,.2)">(same as <em>greek:</em> field)</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Feminine:</span> &nbsp;&nbsp;feminine sg &nbsp;<span style="color:rgba(255,255,255,.2)">not always -η — e.g. -ιος→-ια, -ύς→-ιά</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.Neuter:</span> &nbsp;&nbsp;&nbsp;&nbsp;neuter sg<br>
      <span style="color:rgba(201,169,110,.7)">grammar.Plural:</span> &nbsp;&nbsp;&nbsp;&nbsp;plural forms e.g. <em>-οι / -ες / -α</em><br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Grammar — Custom</span><br>
      <span style="color:rgba(201,169,110,.7)">grammar.[Anything]:</span> &nbsp;any label — displayed on card back only<br>
      <br>
      <span style="color:rgba(255,255,255,.35)">Metadata</span><br>
      <span style="color:rgba(201,169,110,.7)">tags:</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style="color:rgba(255,255,255,.2)">neutral, everyday life, colloquial, formal, literary, figurative, emotion, body, nature, nautical, mythological, religion, loanword:turkish, loanword:italian, loanword:slavic, loanword:french, loanword:english — or any new tag</span><br>
      <span style="color:rgba(201,169,110,.7)">priority:</span> yes / no &nbsp;<span style="color:rgba(255,255,255,.2)">(yes = high-frequency or important — shown first in priority quiz mode)</span><br>
      <span style="color:rgba(201,169,110,.7)">alts:</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;comma-separated accepted spelling variants<br>
    </div>`;

  const tipsSection = `
    <div class="add-tips">
      <div class="add-tips-title">Tips for filling cards</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Always use the base / dictionary form.</strong> Verbs: active present 1sg ending in -ω or -ομαι (γράφω, not έγραψα or γράφοντας). Nouns: nominative singular. Adjectives: masculine singular.</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Verbs: always add the Aorist.</strong> The aorist stem is often irregular and needed for all past tenses. Add <code>grammar.Aorist: έγραψα</code>. For medio-passive verbs, use the passive aorist (e.g. γράφτηκα).</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Adjectives: provide all three genders.</strong> Put the masculine in <code>greek:</code> and add <code>grammar.Masculine</code>, <code>grammar.Feminine</code>, <code>grammar.Neuter</code>. The feminine is <em>not</em> always -η — words ending in -ιος take -ια (ηλίθιος→ηλίθια), -ύς takes -ιά, 3rd-declension types (-ης/-ης/-ες) have no -η feminine. The app auto-shows the alternate endings on the card front and accepts all three in the quiz.</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Nouns: always add <code>grammar.Article</code></strong> (ο / η / το). The app validates the article when the user types it together with the word. For nouns with irregular plurals, add <code>grammar.Plural</code> too.</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Priority: yes for words you encounter regularly or that belong to core vocabulary.</strong> Use <em>no</em> for literary, archaic, or highly domain-specific words you are adding for reference rather than active drilling.</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Pronunciation:</strong> split into syllables with hyphens, stressed syllable fully in CAPS. E.g. γράφω → <code>GRA-fo</code>, κατεβαίνω → <code>ka-te-VAI-no</code>, θάλασσα → <code>THA-la-sa</code>. Omit the article.</div>
      <div class="add-tip"><span class="tip-bullet">▸</span><strong>Tags</strong> help with filtering and discovery. Use <em>colloquial</em> for words that would sound odd in formal writing, <em>figurative</em> for non-literal senses, <em>loanword:X</em> for borrowed words (common in Greek: Turkish, Italian, French). You can also invent new tags freely — they appear in the filter list automatically.</div>
    </div>`;

  const AI_PROMPT = `Generate input for Greek vocabulary flashcards. For each word in my list, produce a card in the text format below. Separate multiple cards with --- or // (both work).

Rules:
- greek: base/dictionary form. Verbs: active present 1sg (e.g. γράφω). Nouns: nominative sg (but if the plural is the standard/citation form — e.g. πληροφορίες — use the plural and note the singular). Adjectives: masculine sg.
- translation: concise English gloss (1–4 words); slash-separate multiple senses
- group: [I will specify the group name]
- pronunciation: syllables split by hyphens, stressed syllable in ALL CAPS (e.g. GRA-fo, ka-te-VAI-no)
- type: one of → Ουσιαστικό · Noun / Ρήμα · Verb / Επίθετο · Adjective / Επίρρημα · Adverb / Σύνδεσμος · Conjunction
- definition: fuller explanation if the translation alone is insufficient
- note: usage notes, register, common set phrases or idioms with this word
- etymology: brief word origin (keep to one sentence)
- example.gr / example.en: a natural Greek sentence and its English translation

Grammar — include all that apply:
  Nouns:      grammar.Article (ο/η/το), grammar.Gender, grammar.Plural (if irregular)
  Verbs:      grammar.Aorist (1sg aorist, e.g. έγραψα — always include this), grammar.Voice (if passive/medio-passive)
  Adjectives: grammar.Masculine, grammar.Feminine, grammar.Neuter — all three required.
              The feminine is NOT always -η: words in -ιος take -ια (ηλίθιος→ηλίθια), words in -ύς take -ιά, 3rd-declension types (-ης/-ης/-ες) have no -η feminine. Check carefully.
              grammar.Plural (e.g. -οι / -ες / -α or the full forms)

tags: comma-separated from the list below — or invent new tags freely, they will appear automatically:
       neutral, everyday life, colloquial, formal, literary, figurative, emotion, body, nature, nautical, mythological, religion, loanword:turkish, loanword:italian, loanword:slavic, loanword:french, loanword:english
priority: yes (very common or core vocabulary word) / no (rare, literary, or domain-specific)

Word list:`;

  const aiPromptSection = `
    <div class="add-tips" style="margin-top:0">
      <div class="add-tips-title" style="display:flex;justify-content:space-between;align-items:center">
        <span>AI prompt template</span>
        <button class="btn-secondary" style="font-size:10px;padding:3px 10px;margin:0;height:auto"
          onclick="navigator.clipboard.writeText(document.getElementById('ai-prompt-text').value).then(()=>{this.textContent='Copied ✓';setTimeout(()=>{this.textContent='Copy'},1600)})">Copy</button>
      </div>
      <textarea id="ai-prompt-text" style="min-height:140px;font-size:10px;margin-top:8px;color:rgba(255,255,255,.35);cursor:text;line-height:1.5" readonly>${AI_PROMPT}</textarea>
    </div>`;

  const isMask = addInputMode === 'mask' && !isEditing;
  document.getElementById('content').innerHTML = `
    ${isEditing ? `<div class="add-mode-label">✏️ Editing: ${esc(prefillCard.greek)}</div>` : modeToggle}
    ${(!isEditing && addInputMode === 'lookup') ? lookupSection : ''}
    ${maskSection}
    <textarea id="add-textarea" spellcheck="false"
      style="${isMask ? 'display:none' : ''}"
      oninput="addParsed=null;document.getElementById('add-preview').innerHTML=''"
    >${isEditing ? cardToText(prefillCard) : ''}</textarea>
    <div class="add-actions">
      <button class="btn-secondary" style="flex:1"
        onclick="${isMask ? 'maskSendToPreview()' : 'parseAndPreview()'}">Preview →</button>
      <button class="btn-primary" style="margin-top:0;flex:1" id="add-save-btn" disabled
        onclick="${isEditing ? 'saveEdit()' : 'saveCards()'}">
        ${isEditing ? 'Update Card' : 'Save to Library'}
      </button>
      ${isEditing ? `<button class="btn-secondary" onclick="addEditingId=null;renderAdd()">Cancel</button>` : ''}
    </div>
    <div id="add-preview" class="preview-area"></div>
    ${isMask ? '' : manualHint}
    ${tipsSection}
    ${isMask ? '' : aiPromptSection}
  `;
  if (!isEditing) {
    document.getElementById('add-textarea').placeholder = ADD_TEMPLATE;
  }
  if (!isEditing && addInputMode === 'lookup') {
    document.getElementById('lookup-input').focus();
  } else if (isMask) {
    document.getElementById('mask-greek').focus();
  }
}

function setAddMode(mode) {
  addInputMode = mode;
  addParsed = null;
  renderAdd();
}

function maskTypeChange(val) {
  ['noun','verb','adj'].forEach(t => {
    const el = document.getElementById('mask-grammar-' + t);
    if (el) el.style.display = 'none';
  });
  const v = val.toLowerCase();
  if (v.includes('noun') || v.includes('ουσιαστ'))        document.getElementById('mask-grammar-noun').style.display = '';
  else if (v.includes('verb') || v.includes('ρήμα'))       document.getElementById('mask-grammar-verb').style.display = '';
  else if (v.includes('adjective') || v.includes('επίθ'))  document.getElementById('mask-grammar-adj').style.display = '';
}

let _maskRowIdx = 0;
function maskAddCustomRow() {
  const container = document.getElementById('mask-custom-rows');
  const div = document.createElement('div');
  div.className = 'mask-custom-row';
  div.innerHTML =
    '<input type="text" class="mask-input mask-glbl" style="flex:0 0 130px;min-width:0" placeholder="Label (e.g. Aorist)">' +
    '<input type="text" class="mask-input mask-gval" style="flex:1;min-width:0" placeholder="Value">' +
    '<button class="icon-btn del" style="opacity:.5;flex-shrink:0" onclick="this.parentElement.remove()">✕</button>';
  container.appendChild(div);
  div.querySelector('.mask-glbl').focus();
}

function maskSendToPreview() {
  const get  = id   => (document.getElementById(id) || {}).value?.trim() || '';
  const radio = name => (document.querySelector(`input[name="${name}"]:checked`) || {}).value || '';
  const greek = get('mask-greek');
  const trans = get('mask-translation');
  if (!greek) { document.getElementById('mask-greek').focus(); return; }
  if (!trans)  { document.getElementById('mask-translation').focus(); return; }

  const lines = [];
  lines.push('greek: ' + greek);
  const pron = get('mask-pronunciation'); if (pron) lines.push('pronunciation: ' + pron);
  const type = get('mask-type');          if (type) lines.push('type: ' + type);
  lines.push('translation: ' + trans);
  let maskGroup = get('mask-scene');
  if (maskGroup === '__new__') maskGroup = get('mask-scene-new') || '';
  lines.push('group: ' + (maskGroup || 'general'));

  const def  = get('mask-definition');   if (def)  lines.push('definition: ' + def);
  const exGr = get('mask-example-gr');   if (exGr) lines.push('example.gr: ' + exGr);
  const exEn = get('mask-example-en');   if (exEn) lines.push('example.en: ' + exEn);

  const tv = type.toLowerCase();
  if (tv.includes('noun') || tv.includes('ουσιαστ')) {
    const art = radio('mask-article');     if (art) lines.push('grammar.Article: ' + art);
    const gen = get('mask-noun-gender');   if (gen) lines.push('grammar.Gender: ' + gen);
    const gt  = get('mask-noun-genitive'); if (gt)  lines.push('grammar.Genitive: ' + gt);
    const pl  = get('mask-noun-plural');   if (pl)  lines.push('grammar.Plural: ' + pl);
  } else if (tv.includes('verb') || tv.includes('ρήμα')) {
    const ao  = get('mask-verb-aorist');   if (ao)  lines.push('grammar.Aorist: ' + ao);
    const vo  = radio('mask-voice');       if (vo)  lines.push('grammar.Voice: ' + vo);
    const st  = get('mask-verb-stem');     if (st)  lines.push('grammar.Stem: ' + st);
  } else if (tv.includes('adjective') || tv.includes('επίθ')) {
    const m   = get('mask-adj-masc');      if (m)   lines.push('grammar.Masculine: ' + m);
    const f   = get('mask-adj-fem');       if (f)   lines.push('grammar.Feminine: ' + f);
    const n   = get('mask-adj-neut');      if (n)   lines.push('grammar.Neuter: ' + n);
    const pl  = get('mask-adj-plural');    if (pl)  lines.push('grammar.Plural: ' + pl);
  }

  document.querySelectorAll('.mask-custom-row').forEach(row => {
    const lbl = (row.querySelector('.mask-glbl') || {}).value?.trim();
    const val = (row.querySelector('.mask-gval') || {}).value?.trim();
    if (lbl && val) lines.push('grammar.' + lbl + ': ' + val);
  });

  const note = get('mask-note');      if (note) lines.push('note: ' + note);
  const etym = get('mask-etymology'); if (etym) lines.push('etymology: ' + etym);
  lines.push('tags: ' + (get('mask-tags') || 'neutral'));
  lines.push('priority: ' + (document.getElementById('mask-priority')?.checked ? 'yes' : 'no'));

  document.getElementById('add-textarea').value = lines.join('\n');
  addParsed = null;
  document.getElementById('add-preview').innerHTML = '';
  parseAndPreview();
  document.getElementById('add-preview').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function maskSceneChange(val) {
  const inp = document.getElementById('mask-scene-new');
  if (inp) { inp.style.display = val === '__new__' ? '' : 'none'; if (val === '__new__') inp.focus(); }
}

async function maskLookupFill() {
  const greek = (document.getElementById('mask-greek') || {}).value?.trim();
  if (!greek) { document.getElementById('mask-greek').focus(); return; }
  const st = document.getElementById('mask-lookup-status');
  if (st) { st.textContent = 'Looking up…'; st.style.color = 'rgba(255,255,255,.3)'; }

  const set = (id, val) => { if (val) { const el = document.getElementById(id); if (el) el.value = val; } };

  let data;
  try {
    const res = await fetch(`/vocab/api/lookup?word=${encodeURIComponent(greek)}`);
    data = await res.json();
    if (!res.ok || data.error) {
      if (st) { st.textContent = data.error || 'Lookup failed'; st.style.color = '#d47a8f'; }
      return;
    }
  } catch(e) {
    if (st) { st.textContent = 'Network error'; st.style.color = '#d47a8f'; }
    return;
  }

  if (st) { st.textContent = '✓ Prefilled from Wiktionary'; st.style.color = 'rgba(122,196,154,.7)'; }

  if (data.type) {
    const sel = document.getElementById('mask-type');
    if (sel) { sel.value = data.type; maskTypeChange(data.type); }
  }
  set('mask-translation', data.translation);
  set('mask-definition',  data.definition);
  set('mask-example-gr',  data.example_gr);
  set('mask-example-en',  data.example_en);
  if (data.grammar_article) {
    const r = document.querySelector(`input[name="mask-article"][value="${data.grammar_article}"]`);
    if (r) r.checked = true;
  }
  set('mask-noun-gender',   data.grammar_gender);
  set('mask-noun-genitive', data.grammar_genitive);
  set('mask-noun-plural',   data.grammar_plural);
  set('mask-verb-aorist',   data.grammar_aorist);
  if (data.grammar_voice) {
    const r = document.querySelector(`input[name="mask-voice"][value="${data.grammar_voice}"]`);
    if (r) r.checked = true;
  }
  set('mask-verb-stem', data.grammar_stem);
  const tv = (data.type || '').toLowerCase();
  if (tv.includes('adj') || tv.includes('επίθ')) {
    set('mask-adj-masc',   data.grammar_masculine);
    set('mask-adj-fem',    data.grammar_feminine);
    set('mask-adj-neut',   data.grammar_neuter);
    set('mask-adj-plural', data.grammar_plural);
  }
}

async function doLookup() {
  const input = document.getElementById('lookup-input');
  if (!input) return;
  const word = input.value.trim();
  if (!word) return;
  const statusEl = document.getElementById('lookup-status');
  const resultEl = document.getElementById('lookup-result-area');
  statusEl.innerHTML = `<div class="lookup-status">Looking up <em>${esc(word)}</em>…</div>`;
  resultEl.innerHTML = '';

  let data;
  try {
    const r = await fetch(API_BASE + `/api/lookup?word=${encodeURIComponent(word)}`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      statusEl.innerHTML = `<div class="lookup-status error">${esc(err.error || 'Lookup failed')}</div>`;
      return;
    }
    data = await r.json();
  } catch(e) {
    statusEl.innerHTML = `<div class="lookup-status error">Network error — check connection</div>`;
    return;
  }
  statusEl.innerHTML = '';

  // Show what was found
  const rows = [
    data.type             && ['Type',       data.type],
    data.translation      && ['Translation', data.translation],
    data.definition  && data.definition !== data.translation && ['Definition', data.definition],
    data.grammar_gender   && ['Gender',     data.grammar_gender],
    data.grammar_plural   && ['Plural',     data.grammar_plural],
    data.grammar_aorist   && ['Aorist',     data.grammar_aorist],
    data.example_gr       && ['Example (gr)', data.example_gr],
    data.example_en       && ['Example (en)', data.example_en],
    data.etymology        && ['Etymology',  data.etymology],
  ].filter(Boolean);

  resultEl.innerHTML = `
    <div class="lookup-result">
      <div style="font-family:Georgia,serif;font-size:18px;color:#e8c98a;margin-bottom:10px">${esc(word)}</div>
      ${rows.map(([lbl,val]) => `
        <div class="lookup-result-row">
          <span class="lookup-result-lbl">${esc(lbl)}</span>
          <span class="lookup-result-val">${esc(String(val))}</span>
        </div>`).join('')}
      ${rows.length === 0 ? '<div style="color:rgba(255,255,255,.3);font-size:12px">No details found — you can still fill in manually below.</div>' : ''}
    </div>
    <div style="font-size:11px;color:rgba(255,255,255,.25);font-family:sans-serif;margin-bottom:8px">
      ↓ Pre-filled below — add scene, pronunciation &amp; any missing fields, then Preview → Save
    </div>`;

  // Build pre-filled text and populate textarea
  const sceneList = addScenesMeta.map(s => s.id).join(', ');
  const lines = [];
  lines.push(`greek: ${word}`);
  lines.push(`pronunciation: `);
  if (data.type)        lines.push(`type: ${data.type}`);
  if (data.translation) lines.push(`translation: ${data.translation}`);
  lines.push(`group: `);
  if (data.definition && data.definition !== data.translation)
                        lines.push(`definition: ${data.definition}`);
  if (data.example_gr)  lines.push(`example.gr: ${data.example_gr}`);
  if (data.example_en)  lines.push(`example.en: ${data.example_en}`);
  // Grammar rows — auto-filled from Wiktionary where available, else blank placeholders
  const isNoun = (data.type||'').toLowerCase().includes('noun') || data.grammar_gender;
  const isVerb = (data.type||'').toLowerCase().includes('verb');
  if (isNoun) {
    lines.push(`grammar.Article: ${data.grammar_article||''}`);
    lines.push(`grammar.Gender: ${data.grammar_gender||''}`);
    lines.push(`grammar.Plural: ${data.grammar_plural||''}`);
  } else if (isVerb) {
    lines.push(`grammar.Aorist: ${data.grammar_aorist||''}`);
  }
  if (data.etymology)   lines.push(`etymology: ${data.etymology}`);
  if (data.auto_note)   lines.push(`note: ${data.auto_note}`);
  lines.push(`tags: neutral`);
  lines.push(`priority: no`);

  const ta = document.getElementById('add-textarea');
  ta.value = lines.join('\n');
  ta.focus();
  // Move cursor to pronunciation line so user fills it in first
  const pronPos = ta.value.indexOf('pronunciation: ') + 'pronunciation: '.length;
  ta.setSelectionRange(pronPos, pronPos);
  addParsed = null;
  document.getElementById('add-preview').innerHTML = '';
}

function parseVocabText(text) {
  const blocks = text.trim().split(/\n[ \t]*(?:---|\/{2})[ \t]*(?:\n|$)/).filter(b => b.trim());
  const knownSceneIds = new Set(addScenesMeta.map(s => s.id));
  const newScenes = {};
  const cards = [], errors = [];

  blocks.forEach((block, bi) => {
    const card = { grammar: [], tags: ['neutral'], priority: false,
                   definition: null, note: null, etymology: null,
                   example: null, conjugation: null };
    const alts = [];
    let sceneRaw = '', sceneEmoji = '📚';

    block.trim().split('\n').forEach(line => {
      const m = line.match(/^([^:]+):\s*(.*)/);
      if (!m) return;
      const key = m[1].trim();
      const val = m[2].trim();
      const kl = key.toLowerCase();
      if (kl === 'greek')          card.greek = val;
      else if (kl === 'pronunciation') card.pronunciation = val;
      else if (kl === 'type')      card.type = val;
      else if (kl === 'translation') card.translation = val;
      else if (kl === 'definition') card.definition = val || null;
      else if (kl === 'note')      card.note = val || null;
      else if (kl === 'etymology') card.etymology = val || null;
      else if (kl === 'group' || kl === 'scene')           sceneRaw = val;
      else if (kl === 'group_emoji' || kl === 'scene_emoji') sceneEmoji = val;
      else if (kl === 'priority')  card.priority = /^yes|true|1$/i.test(val);
      else if (kl === 'tags')      card.tags = val.split(',').map(t=>t.trim()).filter(Boolean);
      else if (kl === 'alts')      alts.push(...val.split(',').map(t=>t.trim()).filter(Boolean));
      else if (kl === 'example.gr') card.example = {...(card.example||{}), gr: val};
      else if (kl === 'example.en') card.example = {...(card.example||{}), en: val};
      else if (kl.startsWith('grammar.') && val) card.grammar.push({label: key.slice(8), value: val});
    });

    const errs = [];
    if (!card.greek)       errs.push('missing: greek');
    if (!card.translation) errs.push('missing: translation');
    if (!sceneRaw)         errs.push('missing: group');

    // Resolve scene
    let sceneId = '', sceneLabel = '', isNewScene = false;
    if (sceneRaw) {
      const existing = addScenesMeta.find(s =>
        s.id === sceneRaw || s.label.toLowerCase().includes(sceneRaw.toLowerCase())
      );
      if (existing) {
        sceneId = existing.id; sceneLabel = existing.label;
      } else {
        sceneId = sceneRaw.toLowerCase().replace(/\s+/g,'_').replace(/[^\w]/g,'');
        sceneLabel = `${sceneEmoji} ${sceneRaw}`;
        isNewScene = !knownSceneIds.has(sceneId) && !newScenes[sceneId];
        newScenes[sceneId] = {id: sceneId, label: sceneLabel};
      }
    }

    card.scene_id = sceneId;
    card.scene_label = sceneLabel;
    if (alts.length) card._alts = alts;

    errors.push({block: bi+1, errs, card, isNewScene, sceneLabel: sceneLabel||sceneRaw});
  });

  return {cards: errors.filter(e=>!e.errs.length).map(e=>e.card),
          newScenes: Object.values(newScenes),
          parsed: errors};
}

function parseAndPreview() {
  const text = document.getElementById('add-textarea').value;
  if (!text.trim()) return;
  const result = parseVocabText(text);
  addParsed = result;
  const validCount = result.cards.length;
  const total = result.parsed.length;

  const rows = result.parsed.map(({block, errs, card, isNewScene, sceneLabel}) => {
    if (errs.length) return `
      <div class="preview-card invalid">
        <div style="font-size:11px;color:#d47a8f;font-family:sans-serif">Card ${block} — ${errs.join(' · ')}</div>
      </div>`;
    const grammarRows = (card.grammar||[]).map(g =>
      `<div style="display:flex;gap:8px;font-size:11px;font-family:sans-serif;margin-top:2px">
        <span style="color:rgba(201,169,110,.5);min-width:80px;flex-shrink:0">${esc(g.label)}</span>
        <span style="color:rgba(255,255,255,.55)">${esc(g.value)}</span>
      </div>`).join('');
    return `
      <div class="preview-card valid">
        <div class="preview-greek">${esc(card.greek)}${card.pronunciation?` <span style="font-family:monospace;font-size:11px;color:rgba(255,255,255,.3)">/${esc(card.pronunciation)}/</span>`:''}</div>
        <div class="preview-trans">${esc(card.translation)}</div>
        <div class="preview-meta">${esc(card.type||'')}${sceneLabel?' · '+esc(sceneLabel):''}</div>
        ${isNewScene?`<div class="preview-new-scene">✨ New group: ${esc(sceneLabel)}</div>`:''}
        ${card._alts?`<div class="preview-meta">alts: ${esc(card._alts.join(', '))}</div>`:''}
        ${card.definition?`<div style="font-size:12px;color:rgba(255,255,255,.55);font-family:sans-serif;margin-top:8px;line-height:1.5;border-top:1px solid rgba(255,255,255,.06);padding-top:8px">${esc(card.definition)}</div>`:''}
        ${grammarRows?`<div style="margin-top:6px">${grammarRows}</div>`:''}
        ${card.example&&card.example.gr?`<div style="margin-top:8px;font-size:12px;font-family:sans-serif;line-height:1.6"><span style="color:#e8c98a;font-family:Georgia,serif">${esc(card.example.gr)}</span>${card.example.en?` <span style="color:rgba(255,255,255,.35);font-style:italic">— ${esc(card.example.en)}</span>`:''}</div>`:''}
        ${card.note?`<div style="font-size:11px;color:#c9a96e;font-style:italic;font-family:sans-serif;margin-top:6px;line-height:1.5">${esc(card.note)}</div>`:''}
        ${card.etymology?`<div style="font-size:11px;color:rgba(255,255,255,.3);font-family:sans-serif;margin-top:4px;line-height:1.5">${esc(card.etymology)}</div>`:''}
      </div>`;
  }).join('');

  document.getElementById('add-preview').innerHTML = `
    <div style="font-size:11px;font-family:sans-serif;color:rgba(255,255,255,.3);margin-bottom:10px">
      ${validCount} of ${total} card${total!==1?'s':''} valid
      ${result.newScenes.length ? ` · ${result.newScenes.length} new group${result.newScenes.length>1?'s':''}` : ''}
    </div>
    ${rows}
  `;
  document.getElementById('add-save-btn').disabled = validCount === 0;
}

async function saveCards() {
  if (!addParsed || !addParsed.cards.length) return;
  const btn = document.getElementById('add-save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  const resp = await api('/api/add_cards', {
    cards: addParsed.cards,
    scenes: addParsed.newScenes,
  });
  if (resp.error) {
    btn.textContent = 'Error — try again';
    btn.disabled = false;
    return;
  }
  // Update local scene list so new scenes are immediately available
  for (const s of addParsed.newScenes) {
    if (!addScenesMeta.find(x => x.id === s.id)) addScenesMeta.push(s);
  }
  // Reset browse cache so new cards appear
  state.browseCards = [];
  addParsed = null;
  const skipped = resp.skipped || [];
  const allSkipped = resp.saved === 0 && skipped.length > 0;
  let msg = '';
  if (allSkipped) {
    msg = `<div class="add-error" style="background:rgba(212,122,143,.12);border-color:rgba(212,122,143,.4);color:#d47a8f">
      Already exists: <strong>${esc(skipped.join(', '))}</strong>
    </div>`;
  } else {
    const savedLine = resp.saved
      ? `✓ Saved ${resp.saved} card${resp.saved!==1?'s':''}${resp.new_scenes ? ` and ${resp.new_scenes} new group${resp.new_scenes!==1?'s':''}` : ''} — available in Browse &amp; Study now`
      : '';
    const skipLine = skipped.length
      ? `Skipped ${skipped.length} duplicate${skipped.length!==1?'s':''}: <em>${esc(skipped.join(', '))}</em>`
      : '';
    msg = `<div class="add-success">${[savedLine, skipLine].filter(Boolean).join('<br>')}</div>`;
  }
  document.getElementById('add-preview').innerHTML = msg;
  if (!allSkipped) document.getElementById('add-textarea').value = '';
  btn.textContent = 'Save to Library';
  btn.disabled = false;
}

async function saveEdit() {
  if (!addParsed || !addParsed.cards.length) return;
  const btn = document.getElementById('add-save-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  const card = addParsed.cards[0];
  const resp = await api('/api/edit_card', { card_id: addEditingId, card });
  if (resp.error) { btn.textContent = 'Error'; btn.disabled = false; return; }
  // Update local browse cache
  const idx = state.browseCards.findIndex(c => c.id === addEditingId);
  if (idx !== -1) Object.assign(state.browseCards[idx], card, { id: addEditingId });
  addEditingId = null;
  addParsed = null;
  document.getElementById('add-preview').innerHTML =
    `<div class="add-success">✓ Card updated — changes are live in Browse &amp; Study</div>`;
  document.getElementById('add-textarea').value = '';
  btn.textContent = 'Update Card';
}

function editCard(cardId) {
  const card = state.browseCards.find(c => c.id === cardId);
  if (!card) return;
  addEditingId = cardId;
  addParsed = null;
  switchMode('add');
  renderAdd(card);
}

async function deleteCard(cardId) {
  const card = state.browseCards.find(c => c.id === cardId);
  if (!card) return;
  if (!confirm(`Delete "${card.greek}"?\nThis cannot be undone.`)) return;
  const resp = await api('/api/delete_card', { card_id: cardId });
  if (resp.error) { alert('Error deleting card'); return; }
  state.browseCards = state.browseCards.filter(c => c.id !== cardId);
  state.browseOpen.delete(cardId);
  _renderBrowseUI();
}

// ── Boot ──────────────────────────────────────────────────────────────────
render();
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@vocab_bp.route("/")
def index():
    scene_ids = [s["id"] for s in SCENES_META]
    return render_template_string(
        HTML,
        scenes_meta=SCENES_META,
        scene_ids=scene_ids,
        total_cards=len(CARDS),
        window_size=WINDOW_SIZE,
    )


def filter_cards(scenes, tags, mastery_filter):
    result = []
    for card in CARDS:
        if scenes and card["scene_id"] not in scenes:
            continue
        if tags:
            card_tags = set(card.get("tags", []))
            ok = True
            for t in tags:
                if t == "priority" and not card.get("priority"):
                    ok = False; break
                if t != "priority" and t not in card_tags:
                    ok = False; break
            if not ok:
                continue
        if mastery_filter and mastery_level(card["id"]) not in mastery_filter:
            continue
        result.append(card)
    return result


def enrich_card(card):
    """Add mastery info to card for the frontend."""
    level = mastery_level(card["id"])
    lbl = mastery_label(card["id"])
    # Gender colour hint
    gc = None
    for g in card.get("grammar", []):
        if g["label"] == "Gender":
            v = g["value"].lower()
            if "masculine" in v:
                gc = "#7ab3d4"
            elif "feminine" in v:
                gc = "#d47a8f"
            elif "neuter" in v:
                gc = "#7ac49a"
            break
    return {
        **card,
        "_mastery_level": level,
        "_mastery_label": lbl,
        "_gc": gc,
    }


@vocab_bp.route("/api/progress")
def api_progress():
    return jsonify(progress)


@vocab_bp.route("/api/pool", methods=["POST"])
def api_pool():
    body = request.json
    cards = filter_cards(
        set(body.get("scenes", [])),
        set(body.get("tags", [])),
        set(body.get("mastery_filter", [])),
    )
    return jsonify({"count": len(cards)})


@vocab_bp.route("/api/start", methods=["POST"])
def api_start():
    body = request.json
    card_ids = body.get("card_ids")
    if card_ids is not None:
        id_set = set(card_ids)
        cards = [c for c in CARDS if c["id"] in id_set]
    else:
        cards = filter_cards(
            set(body.get("scenes", [])),
            set(body.get("tags", [])),
            set(body.get("mastery_filter", [])),
        )
    # Sort: struggling first, then new, then rest
    def sort_key(c):
        lvl = mastery_level(c["id"])
        return {"struggling": 0, "new": 1, "learning": 2, "mastered": 3}.get(lvl, 2)

    cards.sort(key=sort_key)
    # Shuffle within each bucket
    for bucket in [0, 1, 2, 3]:
        group = [c for c in cards if sort_key(c) == bucket]
        random.shuffle(group)
        idx = next((i for i, c in enumerate(cards) if sort_key(c) == bucket), None)
        if idx is not None:
            cards[idx:idx+len(group)] = group

    count = min(body.get("count", 10), len(cards))
    words = [enrich_card(c) for c in cards[:count]]
    return jsonify({"words": words})


@vocab_bp.route("/api/cards", methods=["POST"])
def api_cards():
    return jsonify({"cards": CARDS})


@vocab_bp.route("/api/lookup")
def api_lookup():
    word = request.args.get("word", "").strip()
    if not word:
        return jsonify({"error": "no word provided"}), 400
    result = _fetch_wiktionary(word)
    if result.get("not_found"):
        return jsonify({"error": f'"{word}" not found on English Wiktionary. Try the dictionary/base form, or use Manual mode.'}), 404
    return jsonify(result)


@vocab_bp.route("/api/scenes")
def api_scenes():
    return jsonify({"scenes": SCENES_META})


@vocab_bp.route("/api/add_cards", methods=["POST"])
def api_add_cards():
    body = request.json
    new_cards = body.get("cards", [])
    new_scenes = body.get("scenes", [])
    new_alts = body.get("alts", {})
    if not new_cards:
        return jsonify({"error": "no cards provided"}), 400

    data = _load_user_data()

    # Build set of existing greek forms for duplicate detection
    existing_greek = {
        unicodedata.normalize("NFC", c["greek"].strip().lower())
        for c in CARDS
    }

    # Assign IDs: start after the current max
    next_id = max((c["id"] for c in CARDS), default=0) + 1
    existing_scene_ids = {s["id"] for s in SCENES_META}

    saved_scenes, saved_cards, skipped = [], [], []
    for s in new_scenes:
        if s["id"] not in existing_scene_ids:
            SCENES_META.append(s)
            data["scenes"].append(s)
            existing_scene_ids.add(s["id"])
            saved_scenes.append(s)

    for card in new_cards:
        norm = unicodedata.normalize("NFC", card.get("greek", "").strip().lower())
        if norm in existing_greek:
            skipped.append(card.get("greek", "?"))
            continue
        card["id"] = next_id
        next_id += 1
        CARDS.append(card)
        CARDS_BY_ID[card["id"]] = card
        data["cards"].append(card)
        saved_cards.append(card)
        existing_greek.add(norm)
        alts = card.pop("_alts", [])
        if alts:
            ACCEPTED_ALTS.setdefault(card["id"], []).extend(alts)
            data["alts"][str(card["id"])] = alts

    data["alts"].update(new_alts)
    _save_user_data(data)

    return jsonify({"saved": len(saved_cards), "new_scenes": len(saved_scenes), "skipped": skipped})


@vocab_bp.route("/api/delete_card", methods=["POST"])
def api_delete_card():
    card_id = request.json.get("card_id")
    card = CARDS_BY_ID.get(card_id)
    if not card:
        return jsonify({"error": "not found"}), 404
    data = _load_user_data()
    # Remove from user cards list if present, else add to deleted_ids
    data["cards"] = [c for c in data["cards"] if c["id"] != card_id]
    if card_id not in data["deleted_ids"]:
        data["deleted_ids"].append(card_id)
    data["overrides"].pop(str(card_id), None)
    _save_user_data(data)
    # Update in-memory
    CARDS.remove(card)
    CARDS_BY_ID.pop(card_id)
    return jsonify({"ok": True})


@vocab_bp.route("/api/edit_card", methods=["POST"])
def api_edit_card():
    body = request.json
    card_id = body.get("card_id")
    updated = body.get("card")
    if not card_id or not updated:
        return jsonify({"error": "missing fields"}), 400
    card = CARDS_BY_ID.get(card_id)
    if not card:
        return jsonify({"error": "not found"}), 404
    data = _load_user_data()
    updated["id"] = card_id
    # Update in user cards list if present, else store as override
    user_card = next((c for c in data["cards"] if c["id"] == card_id), None)
    if user_card:
        idx = data["cards"].index(user_card)
        data["cards"][idx] = updated
    else:
        data["overrides"][str(card_id)] = updated
    _save_user_data(data)
    # Update in-memory
    card.update(updated)
    return jsonify({"ok": True})


@vocab_bp.route("/api/check", methods=["POST"])
def api_check():
    body = request.json
    card_id = body["card_id"]
    guess = body.get("guess", "")
    direction = body.get("direction", "gr-en")
    is_retry = body.get("is_retry", False)
    card = CARDS_BY_ID.get(card_id)
    if not card:
        return jsonify({"result": "wrong", "stat": None})
    result = check_answer(guess, card, direction)
    # On first attempt: record immediately unless it's 'close' (defer to retry)
    # On retry: always record (1 if correct, 0.5 otherwise)
    stat = None
    if is_retry:
        score = 1 if result == "correct" else 0.5
        stat = add_attempt(card_id, score)
    elif result != "close":
        score = 1 if result == "correct" else 0
        stat = add_attempt(card_id, score)
    return jsonify({"result": result, "stat": stat})


if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    print("🇬🇷 Greek Vocab Trainer")
    print(f"   {len(CARDS)} words · {len(SCENES_META)} scenes")
    print("   Open http://localhost:5001")
    print("   Press Ctrl+C to stop\n")
    app.run(debug=False, port=5001)
