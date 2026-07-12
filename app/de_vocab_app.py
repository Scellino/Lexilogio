"""
de_vocab_app.py — German vocabulary trainer.
Registered in app.py at /de/vocab.
"""
import re
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint, resolve_expected_article

# ── Check function ─────────────────────────────────────────────────────────────

_DE_ARTICLES = frozenset([
    "der", "die", "das", "den", "dem", "des",   # definite
    "ein", "eine", "einen", "einem", "eines", "einer",  # indefinite
])


def _de_edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], prev if a[i-1]==b[j-1] else 1+min(prev, dp[j], dp[j-1])
    return dp[n]


def _de_normalize(s):
    """Normalize German text: lowercase, expand umlauts, strip punctuation."""
    s = s.lower().strip()
    for a, b in [("ä","ae"),("ö","oe"),("ü","ue"),("ß","ss")]:
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.strip()


def _de_strip_article(s):
    for art in sorted(_DE_ARTICLES, key=len, reverse=True):
        art_norm = _de_normalize(art)
        if s.startswith(art_norm + " "):
            return s[len(art_norm):].lstrip()
    return s


def _de_is_close(guess, target):
    max_len = max(len(guess), len(target), 1)
    d = _de_edit_distance(guess, target)
    if max_len <= 4:  return d == 1
    if max_len <= 9:  return d <= 2
    return d <= 3


def _strip_the(s):
    return s[4:] if s.startswith("the ") else s


def _de_check_fn(guess, correct, direction, card):
    g_norm = _strip_the(_de_normalize(guess))
    c_norm = _strip_the(_de_normalize(correct))

    if direction == "word→en":
        if g_norm == c_norm:
            return "correct"
        if _de_is_close(g_norm, c_norm):
            return "correct"
        return "wrong"

    # en→word: the article is part of the answer whenever the card's gender
    # is known — a bare noun with no article, or the wrong one, is "close"
    # (retry), not silently accepted or hard-failed.
    g_bare = _de_strip_article(g_norm)
    if g_bare == c_norm:
        expected = _de_normalize(resolve_expected_article(DE_LANG, card))
        if not expected:
            return "correct"   # no known gender — can't require an article
        return "correct" if g_norm == f"{expected} {c_norm}" else "close"
    if _de_is_close(g_bare, c_norm):
        return "close"
    if _de_is_close(g_norm, c_norm):
        return "close"
    return "wrong"


# ── Language config ────────────────────────────────────────────────────────────

DE_LANG = {
    "code":         "de",
    "name":         "German",
    "header_sub":   "DEUTSCH · WORTSCHATZ",
    "header_title": "German Vocab Trainer",
    "data_dir":     Path(__file__).parent.parent / "german",
    "data_module":  "de_vocab_data",
    "word_field":   "german",
    "group_field":  "scene_id",
    "example_native_code": "de",
    "gender_grammar_label": "Gender",
    "word_types":   ["noun", "verb", "adjective", "adverb", "phrase"],

    "tag_labels": {
        "common":    "⭐ Common",
        "daily":     "🏡 Daily life",
        "food":      "🍽️ Food",
        "travel":    "✈️ Travel",
        "work":      "💼 Work",
        "noun":      "📦 Noun",
        "verb":      "⚡ Verb",
        "adjective": "🎨 Adjective",
        "adverb":    "💨 Adverb",
        "formal":    "📜 Formal",
        "colloquial":"💬 Colloquial",
        "emotion":   "❤️ Emotion",
        "nature":    "🌿 Nature",
        "body":      "🫀 Body",
    },
    "group_labels": {
        "daily":    "🏡 Daily life",
        "food":     "🍽️ Food & Drink",
        "travel":   "✈️ Travel",
        "work":     "💼 Work & Study",
        "nature":   "🌿 Nature",
        "body":     "🫀 Body & Health",
        "emotions": "❤️ Emotions",
        "culture":  "🎭 Culture",
        "verbs":    "⚡ Verbs",
    },

    # Three genders — same colours as Greek (m=blue, f=rose, n=green)
    "article_rule": {
        "based_on": "gender",
        "rules": {
            "m": {"otherwise": "der"},
            "f": {"otherwise": "die"},
            "n": {"otherwise": "das"},
        },
    },
    "article_colors": {
        "m": "#7ab3d4",
        "f": "#d47a8f",
        "n": "#7ac49a",
    },

    "grammar_fields": {
        "noun": [
            {
                "name":      "gender",
                "label":     "Gender",
                "widget":    "radio",
                "top_level": True,
                "options": [
                    {"value": "m", "label": "Masculine — der"},
                    {"value": "f", "label": "Feminine — die"},
                    {"value": "n", "label": "Neuter — das"},
                ],
            },
            {
                "name":        "plural",
                "label":       "Plural (with article)",
                "widget":      "text",
                "placeholder": "e.g. die Bücher, die Männer, die Autos",
                "hint":        "Plural is unpredictable — always include it. Plural article is always 'die'.",
            },
            {
                "name":        "genitive",
                "label":       "Genitive singular",
                "widget":      "text",
                "placeholder": "e.g. des Buches, der Frau, des Kindes",
                "hint":        "Masc./neuter: usually -s or -es. Fem.: unchanged (same as nominative).",
            },
        ],
        "verb": [
            {
                "name":   "verb_type",
                "label":  "Verb type",
                "widget": "select",
                "options": [
                    {"value": "weak",    "label": "Weak / regular (machen→machte→gemacht)"},
                    {"value": "strong",  "label": "Strong / irregular (fahren→fuhr→gefahren)"},
                    {"value": "mixed",   "label": "Mixed (denken→dachte→gedacht)"},
                    {"value": "modal",   "label": "Modal (können, müssen, dürfen…)"},
                ],
            },
            {
                "name":   "separable",
                "label":  "Separable verb?",
                "widget": "radio",
                "options": [
                    {"value": "no",  "label": "No"},
                    {"value": "yes", "label": "Yes (aufmachen, anrufen, einladen…)"},
                ],
            },
            {
                "name":   "auxiliary",
                "label":  "Auxiliary (Perfekt)",
                "widget": "radio",
                "options": [
                    {"value": "haben", "label": "haben"},
                    {"value": "sein",  "label": "sein"},
                ],
            },
            {
                "name":        "partizip2",
                "label":       "Partizip II",
                "widget":      "text",
                "placeholder": "e.g. gemacht, gefahren, gedacht",
            },
            {
                "name":        "praeteritum",
                "label":       "Präteritum (ich)",
                "widget":      "text",
                "placeholder": "e.g. machte, fuhr, dachte",
                "hint":        "First person singular simple past.",
            },
            {
                "name":        "present",
                "label":       "Präsens (all 6 forms)",
                "widget":      "text",
                "placeholder": "mache / machst / macht / machen / macht / machen",
                "hint":        'Order: ich / du / er / wir / ihr / sie — separate with " / "',
            },
        ],
        "adjective": [
            {
                "name":        "comparative",
                "label":       "Comparative",
                "widget":      "text",
                "placeholder": "e.g. größer, schneller, besser",
            },
            {
                "name":        "superlative",
                "label":       "Superlative",
                "widget":      "text",
                "placeholder": "e.g. am größten, am schnellsten, am besten",
            },
        ],
        "phrase": [
            {
                "name":   "register",
                "label":  "Register",
                "widget": "select",
                "options": [
                    {"value": "neutral",    "label": "Neutral"},
                    {"value": "formal",     "label": "Formal"},
                    {"value": "colloquial", "label": "Colloquial / idiomatic"},
                ],
            },
            {
                "name":        "notes",
                "label":       "Usage notes",
                "widget":      "text",
                "placeholder": "e.g. used with accusative, always plural, regional",
                "hint":        "Use 'phrase' for multi-word expressions and idioms, even if they contain a noun.",
            },
        ],
        "adverb": [],
    },
}

de_vocab_bp = make_vocab_blueprint(DE_LANG, check_fn=_de_check_fn)
