"""
el_vocab_app.py — Greek vocabulary trainer.
Registered in app.py at /vocab (replacing the old vocab_app.py Blueprint).
"""
import re
import unicodedata
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint

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
    return None


def _el_grammar_forms(card):
    forms = []
    for entry in (card.get("grammar") or []):
        lbl = entry.get("label", "").lower()
        if any(x in lbl for x in ("masculine", "feminine", "neuter", "masc", "fem", "neut")):
            val = entry.get("value", "")
            val = val.split("←")[0].split("·")[0].split(",")[0].strip()
            if val:
                forms.append(_el_normalize(val))
    return forms


def _make_greek_check_fn(accepted_alts):
    def _greek_check_fn(guess, correct, direction, card):
        norm_guess = _el_normalize(guess)
        if not norm_guess:
            return "wrong"

        if direction == "word→en":
            # Greek → English: spelling tolerance → auto-correct
            options = [_el_normalize(o) for o in re.split(r"[/,]", card.get("translation", "")) if o.strip()]
            for opt in options:
                if norm_guess == opt:
                    return "correct"
                opt_w   = [w for w in opt.split()       if len(w) > 2]
                guess_w = [w for w in norm_guess.split() if len(w) > 2]
                if opt_w and guess_w and opt_w[0] == guess_w[0]:
                    return "correct"
            for opt in options:
                if _el_is_close(norm_guess, opt):
                    return "correct"
                opt_w   = [w for w in opt.split()       if len(w) > 2]
                guess_w = [w for w in norm_guess.split() if len(w) > 2]
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

            guess_art, guess_word = _el_strip_article(norm_guess)

            def _word_matches(w):
                if w == target or w in alts:
                    return True
                cw = [x for x in target.split() if len(x) > 3]
                gw = [x for x in w.split()      if len(x) > 3]
                return bool(cw and gw and cw[0] == gw[0] and len(target) < 20)

            if _word_matches(norm_guess) or _word_matches(guess_word):
                if guess_art is not None:
                    exp_art = _el_card_article(card)
                    if exp_art and guess_art != exp_art:
                        return "close"  # right word, wrong article
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
