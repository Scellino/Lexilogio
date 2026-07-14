"""
el_vocab_app.py — Greek vocabulary trainer.
Registered in app.py at /vocab (replacing the old vocab_app.py Blueprint).
"""
import re
import unicodedata
import html as html_mod
import urllib.parse
import urllib.request
from pathlib import Path
from flask import request, jsonify
from generic_vocab_bp import make_vocab_blueprint, resolve_expected_article

# ── Data ─────────────────────────────────────────────────────────────────────
import json
import sys
_VOCAB_DIR = Path(__file__).parent.parent / 'greek'
if str(_VOCAB_DIR) not in sys.path:
    sys.path.insert(0, str(_VOCAB_DIR))
from vocab_data import SCENES_META, ACCEPTED_ALTS

def _load_greek_user_extras():
    """Load user-defined scenes and alts from user_cards.json (old dict format)."""
    path = _VOCAB_DIR / "user_cards.json"
    if not path.exists():
        return {}, {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}, {}
    scenes = {s["id"]: s["label"] for s in data.get("scenes", [])}
    alts   = {int(k): v for k, v in data.get("alts", {}).items()}
    return scenes, alts

_user_scenes, _user_alts = _load_greek_user_extras()

# Merge user alts into ACCEPTED_ALTS so the check function sees them
for _cid, _alt_list in _user_alts.items():
    ACCEPTED_ALTS.setdefault(_cid, []).extend(_alt_list)

# ── Wiktionary lookup ────────────────────────────────────────────────────────

_POS_MAP = {
    "Noun": "Ουσιαστικό", "Verb": "Ρήμα", "Adjective": "Επίθετο",
    "Adverb": "Επιρρήματα", "Conjunction": "Φράση", "Preposition": "Φράση",
    "Particle": "Φράση", "Interjection": "Επιφώνημα",
}
_GRAM_FORM_RE = re.compile(
    r"\b(?:variant|form|spelling|synonym|diminutive|augmentative|feminine|masculine|"
    r"plural|singular|genitive|dative|accusative|nominative|vocative|participle|"
    r"imperative)\s+of\b", re.IGNORECASE,
)
_QUOTED = re.compile(r'[“”‘’"]([^“”‘’"]+)[“”‘’"]')


def _strip_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    return html_mod.unescape(text).strip()


def _clean_wikitext(text):
    def sub_template(m):
        parts = [p.strip() for p in m.group(1).split("|")]
        meaningful = [p for p in parts[1:] if " " in p or not re.match(r"^[a-z\-]{2,8}$", p) and "=" not in p]
        return meaningful[-1] if meaningful else ""
    text = re.sub(r"\{\{([^{}]+)\}\}", sub_template, text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"'''?([^']+)'''?", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip() or None


def _clean_definition(text):
    text = text.strip().rstrip(".")
    if _GRAM_FORM_RE.search(text):
        quotes = _QUOTED.findall(text)
        english = [q for q in quotes if not re.search(r"[Ͱ-Ͽ]", q) and len(q) < 100]
        if english:
            meanings = [m.strip() for m in re.split(r"[,/]", english[0]) if m.strip()]
            return " / ".join(meanings[:4]), None
        return None, None
    def strip_paren(m):
        content = m.group(1)
        english = [q for q in _QUOTED.findall(content) if not re.search(r"[Ͱ-Ͽ]", q)]
        if english:
            return " / ".join(p.strip() for p in re.split(r"[,/]", english[0]) if p.strip())
        if re.match(r"^[A-Za-zÀ-ÿ\s,;.\-'']+$", content):
            return ""
        return m.group(0)
    cleaned = re.sub(r"\s*\(([^)]{3,150})\)", strip_paren, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".,;")
    if not cleaned:
        return None, None
    parts = [p.strip() for p in re.split(r"[,/]", cleaned) if p.strip()]
    if all(len(p) < 35 for p in parts):
        return " / ".join(parts[:5]), None
    return cleaned, None


def _fetch_wiktionary(word):
    encoded = urllib.parse.quote(word)
    headers = {"User-Agent": "LexilogioVocabTrainer/1.0 (personal learning app)"}
    result = {}
    try:
        url = f"https://en.wiktionary.org/api/rest_v1/page/definition/{encoded}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=7) as r:
            data = __import__("json").loads(r.read())
        entries = data.get("el", [])
        if not entries:
            return {"not_found": True}
        entry = entries[0]
        result["type"] = _POS_MAP.get(entry.get("partOfSpeech", ""), "")
        defs = entry.get("definitions", [])
        if defs:
            translations = []
            for d in defs[:5]:
                raw = _strip_html(d.get("definition", ""))
                if not raw:
                    continue
                trans, _ = _clean_definition(raw)
                if trans:
                    translations.append(trans)
            if translations:
                all_parts = []
                seen = set()
                for t in translations:
                    for part in t.split(" / "):
                        part = part.strip()
                        if part and part.lower() not in seen:
                            seen.add(part.lower())
                            all_parts.append(part)
                result["translation"] = " / ".join(all_parts[:3])
            for d in defs:
                for ex in d.get("examples", []):
                    gr = _strip_html(ex.get("example", ""))
                    en = _strip_html(ex.get("translation", ""))
                    if gr:
                        result["example_native"] = gr
                    if en:
                        result["example_en"] = en
                    if gr:
                        break
                if result.get("example_native"):
                    break
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"not_found": True}
    except Exception:
        pass
    try:
        url = (f"https://en.wiktionary.org/w/api.php?action=parse&page={encoded}"
               f"&prop=wikitext&format=json")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=7) as r:
            data = __import__("json").loads(r.read())
        wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
        gm = re.search(r"==Greek==(.+?)(?:\n==[^=]|\Z)", wikitext, re.DOTALL)
        if gm:
            greek_sec = gm.group(1)
            em = re.search(r"===Etymology(?:\s*\d*)?===\s*\n(.+?)(?:\n===|\Z)", greek_sec, re.DOTALL)
            if em:
                result["etymology"] = _clean_wikitext(em.group(1))
            nm = re.search(r"\{\{el-noun\|([mfn])(?:\|([^|}\n]*))?", greek_sec)
            if nm:
                result["grammar_gender"] = nm.group(1)  # "m"/"f"/"n"
                pl = (nm.group(2) or "").strip()
                if pl and not pl.startswith("-"):
                    result["plural"] = pl
            vm = re.search(r"\{\{el-verb[^}]*?\|(?:aor(?:ist)?|past)=([^|}\n]+)", greek_sec)
            if vm:
                result["past"] = vm.group(1).strip()
    except Exception:
        pass
    return result


# ── Answer-checking helpers ───────────────────────────────────────────────────

def _el_edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1])
            prev = temp
    return dp[n]


def _el_is_close(guess, target):
    if not guess or not target:
        return False
    d = _el_edit_distance(guess, target)
    mx = max(len(guess), len(target))
    if mx <= 4: return d == 1
    if mx <= 9: return d <= 2
    return d <= 3


def _el_normalize(s):
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/]", "", s, flags=re.UNICODE)
    return s.strip()


_GREEK_ARTICLES = frozenset(["ο", "η", "το", "οι", "τα", "ενας", "μια", "ενα"])

def _el_strip_article(s):
    parts = s.split(None, 1)
    if len(parts) == 2 and parts[0] in _GREEK_ARTICLES:
        return parts[0], parts[1]
    return None, s


def _el_card_article(card):
    for g in (card.get("grammar") or []):
        if g.get("label") == "Article":
            parts = _el_normalize(g["value"]).split()
            return parts[0] if parts else None
    # No explicit "Article" grammar entry (e.g. cards that only list
    # Gender, or Masculine/Feminine forms) — fall back to deriving the
    # article from the card's gender via the same article_rule the other
    # five languages use, so the requirement never silently no-ops.
    fallback = resolve_expected_article(EL_LANG, card)
    return _el_normalize(fallback) if fallback else None


_EL_GENDER_LABELS = {
    "masculine": "m", "masc": "m",
    "feminine":  "f", "fem":  "f",
    "neuter":    "n", "neut": "n",
}


def _el_grammar_forms(card):
    """Bare alternate forms accepted as a correct answer, with any article
    the data happened to include stripped off (some cards write "η κολλητή",
    others just "χαρούμενη" — both must match a bare guess the same way)."""
    forms = []
    for entry in (card.get("grammar") or []):
        lbl = entry.get("label", "").lower()
        if any(x in lbl for x in ("masculine", "feminine", "neuter", "masc", "fem", "neut")):
            val = entry.get("value", "")
            val = val.split("←")[0].split("·")[0].split(",")[0].strip()
            if val:
                _, bare = _el_strip_article(_el_normalize(val))
                forms.append(bare)
        elif any(x in lbl for x in ("alt", "also written", "alternative")):
            # Extract the Greek word before any parenthetical description
            val = entry.get("value", "").split("(")[0].strip()
            if val:
                forms.append(_el_normalize(val))
    return forms


def _el_grammar_gendered_forms(card):
    """Masculine/Feminine/Neuter alt forms paired with their gender, so the
    checker can require the article matching whichever gendered form the
    guess actually used — typing "η κολλητή" must be checked against "η",
    not against the card's default (masculine) article."""
    out = []
    for entry in (card.get("grammar") or []):
        lbl = entry.get("label", "").lower()
        gender = next((g for key, g in _EL_GENDER_LABELS.items() if key in lbl), None)
        if not gender:
            continue
        val = entry.get("value", "")
        val = val.split("←")[0].split("·")[0].split(",")[0].strip()
        if not val:
            continue
        _, bare = _el_strip_article(_el_normalize(val))
        if bare:
            out.append((bare, gender))
    return out


def _make_greek_check_fn(accepted_alts):
    def _greek_check_fn(guess, correct, direction, card):
        norm_guess = _el_normalize(guess)
        if not norm_guess:
            return "wrong"

        if direction == "word→en":
            # Greek → English: spelling tolerance → auto-correct.
            # Strip a leading "the " on both sides so "house" and "the house"
            # are treated as the same answer.
            def _strip_the(s):
                return s[4:] if s.startswith("the ") else s
            en_guess = _strip_the(norm_guess)
            options = [_strip_the(_el_normalize(o))
                       for o in re.split(r"[/,]", card.get("translation", "")) if o.strip()]
            for opt in options:
                if en_guess == opt:
                    return "correct"
                opt_w   = [w for w in opt.split()      if len(w) > 2]
                guess_w = [w for w in en_guess.split() if len(w) > 2]
                if opt_w and guess_w and opt_w[0] == guess_w[0]:
                    return "correct"
            for opt in options:
                if _el_is_close(en_guess, opt):
                    return "correct"
                opt_w   = [w for w in opt.split()      if len(w) > 2]
                guess_w = [w for w in en_guess.split() if len(w) > 2]
                if opt_w and guess_w and _el_is_close(guess_w[0], opt_w[0]):
                    return "correct"
            return "wrong"
        else:
            # English → Greek: recall matters → close = retry
            greek_field = card.get("greek") or card.get("word") or ""
            target = _el_normalize(greek_field)
            alts   = [_el_normalize(a) for a in accepted_alts.get(card.get("id"), [])]
            alts  += _el_grammar_forms(card)
            alts   = list(dict.fromkeys(alts))
            gendered_forms = _el_grammar_gendered_forms(card)

            guess_art, guess_word = _el_strip_article(norm_guess)

            def _word_matches(w):
                if w == target or w in alts:
                    return True
                cw = [x for x in target.split() if len(x) > 3]
                gw = [x for x in w.split()      if len(x) > 3]
                return bool(cw and gw and cw[0] == gw[0] and len(target) < 20)

            if _word_matches(norm_guess) or _word_matches(guess_word):
                # The article is part of the answer whenever the card's
                # article is known — omitting it, or getting it wrong, is
                # "close" (retry), not silently accepted. Cards with no
                # known article (e.g. plain adjectives with no Gender/
                # Article field) stay exempt, regardless of which
                # Masculine/Feminine/Neuter form the guess happened to
                # match — that's not an article requirement, just an
                # accepted alternate spelling.
                exp_art = _el_card_article(card)
                if exp_art:
                    # If the guess matched a specific gendered alt form
                    # rather than the card's primary word, require THAT
                    # gender's article instead of the card's default —
                    # typing "η κολλητή" must not be told it needs "ο".
                    for form, gender in gendered_forms:
                        if form == guess_word or form == norm_guess:
                            matched = _el_normalize(
                                resolve_expected_article(EL_LANG, {**card, "gender": gender}))
                            if matched:
                                exp_art = matched
                            break
                    if guess_art == exp_art:
                        return "correct"
                    return ("close", "missing_article") if not guess_art else ("close", "wrong_article")
                return "correct"

            for w in {norm_guess, guess_word}:
                if _el_is_close(w, target):
                    return "close"
                for alt in alts:
                    if _el_is_close(w, alt):
                        return "close"
            return "wrong"
    return _greek_check_fn


# ── LANG config ───────────────────────────────────────────────────────────────

EL_LANG = {
    "code":         "el",
    "name":         "Greek",
    "header_sub":   "ΕΛΛΗΝΙΚΑ · ΛΕΞΙΛΟΓΙΟ",
    "header_title": "Greek Vocab Trainer",
    "data_dir":     _VOCAB_DIR,
    "data_module":  "vocab_data",

    # Field mapping: Greek cards use different field names than the generic schema
    "has_lookup":            True,
    "word_field":            "greek",        # card.greek → card.word
    "group_field":           "scene_label",   # card.scene_label → card.group (already has emoji)
    "example_native_code":   "gr",           # card.example.gr → card.example.el
    "gender_grammar_label":  "Gender",       # extract card.gender from grammar[label=Gender].value

    "word_types": [
        "Ουσιαστικό", "Ρήμα", "Επίθετο", "Επιρρήματα", "Φράση", "Επιφώνημα",
    ],

    "group_labels": {**{s["id"]: s["label"] for s in SCENES_META}, **_user_scenes},

    "tag_labels": {
        "neutral":          "◽ neutral",
        "everyday life":    "🏠 everyday life",
        "everyday":         "🏠 everyday",
        "figurative":       "🎭 figurative",
        "formal":           "📚 formal",
        "colloquial":       "💬 colloquial",
        "nature":           "🌿 nature",
        "mythological":     "⚡ mythological",
        "literary":         "✍️ literary",
        "emotion":          "❤️ emotion",
        "nautical":         "⚓ nautical",
        "body":             "🫀 body",
        "religion":         "🕍 religion",
        "common":           "⭐ common",
        "deponent":         "📝 deponent",
        "loanword:italian": "🇮🇹 loanword",
        "loanword:turkish": "🇹🇷 loanword",
        "loanword:slavic":  "🇷🇺 loanword",
        "emphatic":         "❗ emphatic",
        "emotional":        "❤️ emotional",
        "café":             "☕ café",
        "character":        "👤 character",
    },

    "article_rule": {
        "based_on": "gender",
        "rules": {
            "m":  {"vowel_start": "ο",  "otherwise": "ο"},
            "f":  {"vowel_start": "η",  "otherwise": "η"},
            "n":  {"vowel_start": "το", "otherwise": "το"},
        },
    },
    "article_colors": {
        "m": "#7ab3d4",
        "f": "#d47a8f",
        "n": "#7ac49a",
    },

    "grammar_fields": {
        "Ουσιαστικό": [
            {
                "name":    "gender",
                "label":   "Gender",
                "widget":  "radio",
                "top_level": True,
                "options": [
                    {"value": "m", "label": "Masculine (ο)"},
                    {"value": "f", "label": "Feminine (η)"},
                    {"value": "n", "label": "Neuter (το)"},
                ],
            },
            {
                "name":        "plural",
                "label":       "Plural",
                "widget":      "text",
                "placeholder": "e.g. τα βιβλία",
            },
        ],
        "Ρήμα": [
            {
                "name":        "present",
                "label":       "Present (all persons)",
                "widget":      "text",
                "placeholder": "e.g. πηγαίνω / πηγαίνεις / πηγαίνει ...",
                "hint":        'Separate with " / "',
            },
            {
                "name":        "past",
                "label":       "Simple past",
                "widget":      "text",
                "placeholder": "e.g. πήγα",
            },
        ],
        "Επίθετο": [
            {
                "name":        "masculine",
                "label":       "Masculine",
                "widget":      "text",
                "placeholder": "e.g. καλός",
            },
            {
                "name":        "feminine",
                "label":       "Feminine",
                "widget":      "text",
                "placeholder": "e.g. καλή",
            },
            {
                "name":        "neuter",
                "label":       "Neuter",
                "widget":      "text",
                "placeholder": "e.g. καλό",
            },
        ],
    },
}

el_vocab_bp = make_vocab_blueprint(EL_LANG, check_fn=_make_greek_check_fn(ACCEPTED_ALTS))


@el_vocab_bp.route("/api/lookup")
def api_lookup():
    word = request.args.get("word", "").strip()
    if not word:
        return jsonify({"error": "no word provided"}), 400
    result = _fetch_wiktionary(word)
    if result.get("not_found"):
        return jsonify({"error": f'"{word}" not found on Wiktionary. Try the base/dictionary form.'}), 404
    return jsonify(result)
