"""
es_vocab_app.py — Spanish vocabulary trainer.
Registered in app.py at /es/vocab.
"""
import re
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint

# ── Check function ─────────────────────────────────────────────────────────────

_ES_ARTICLES = frozenset([
    "el", "la", "los", "las",
    "un", "una", "unos", "unas",
])


def _es_edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], prev if a[i-1]==b[j-1] else 1+min(prev, dp[j], dp[j-1])
    return dp[n]


def _es_normalize(s):
    s = s.lower().strip()
    # Strip vowel accents — but preserve ñ (año ≠ ano)
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u")]:
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.strip()


def _es_strip_article(s):
    for art in sorted(_ES_ARTICLES, key=len, reverse=True):
        if s.startswith(art + " "):
            return s[len(art):].lstrip()
    return s


def _es_is_close(guess, target):
    max_len = max(len(guess), len(target), 1)
    d = _es_edit_distance(guess, target)
    if max_len <= 4:  return d == 1
    if max_len <= 9:  return d <= 2
    return d <= 3


def _strip_the(s):
    return s[4:] if s.startswith("the ") else s


def _es_check_fn(guess, correct, direction, card):
    g_norm = _strip_the(_es_normalize(guess))
    c_norm = _strip_the(_es_normalize(correct))
    if g_norm == c_norm:
        return "correct"

    g_bare = _es_strip_article(g_norm)
    c_bare = _es_strip_article(c_norm)

    if direction == "word→en":
        if _es_is_close(g_norm, c_norm):
            return "correct"
    else:
        if g_bare == c_bare and g_bare != g_norm:
            return "close"
        if _es_is_close(g_bare, c_bare):
            return "close"
        if _es_is_close(g_norm, c_norm):
            return "close"

    return "wrong"


# ── Language config ────────────────────────────────────────────────────────────

ES_LANG = {
    "code":         "es",
    "name":         "Spanish",
    "header_sub":   "ESPAÑOL · VOCABULARIO",
    "header_title": "Spanish Vocab Trainer",
    "data_dir":     Path(__file__).parent.parent / "spanish",
    "data_module":  "es_vocab_data",
    "word_field":   "spanish",
    "group_field":  "scene_id",
    "example_native_code": "es",
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

    "article_rule": {
        "based_on": "gender",
        "rules": {
            "m": {"otherwise": "el"},
            "f": {"otherwise": "la"},
        },
    },
    "article_colors": {
        "m": "#7ab3d4",
        "f": "#d47a8f",
    },

    "grammar_fields": {
        "noun": [
            {
                "name":      "gender",
                "label":     "Gender",
                "widget":    "radio",
                "top_level": True,
                "options": [
                    {"value": "m", "label": "Masculine (el / un)"},
                    {"value": "f", "label": "Feminine (la / una)"},
                ],
            },
            {
                "name":        "plural",
                "label":       "Plural",
                "widget":      "text",
                "placeholder": "e.g. gatos, casas, ciudades",
                "hint":        "Most add -s or -es. Note: el agua → las aguas (fem, takes el in sg).",
            },
        ],
        "verb": [
            {
                "name":   "type_es",
                "label":  "Verb type",
                "widget": "select",
                "options": [
                    {"value": "-ar",      "label": "-ar  (hablar, caminar)"},
                    {"value": "-er",      "label": "-er  (comer, beber)"},
                    {"value": "-ir",      "label": "-ir  (vivir, salir)"},
                    {"value": "irregular","label": "Irregular (ser, ir, tener…)"},
                ],
            },
            {
                "name":   "reflexive",
                "label":  "Reflexive",
                "widget": "radio",
                "options": [
                    {"value": "no",  "label": "No"},
                    {"value": "yes", "label": "Yes (lavarse, llamarse…)"},
                ],
            },
            {
                "name":        "participle",
                "label":       "Participio pasado",
                "widget":      "text",
                "placeholder": "e.g. hablado, comido, ido",
            },
            {
                "name":        "indefinido",
                "label":       "Pretérito indefinido (yo)",
                "widget":      "text",
                "placeholder": "e.g. hablé, comí, fui",
                "hint":        "First person singular simple past.",
            },
            {
                "name":        "imperfecto",
                "label":       "Pretérito imperfecto (yo)",
                "widget":      "text",
                "placeholder": "e.g. hablaba, comía, iba",
            },
            {
                "name":        "gerundio",
                "label":       "Gerundio",
                "widget":      "text",
                "placeholder": "e.g. hablando, comiendo, yendo",
            },
            {
                "name":        "present",
                "label":       "Presente (all 6 forms)",
                "widget":      "text",
                "placeholder": "hablo / hablas / habla / hablamos / habláis / hablan",
                "hint":        'Order: yo / tú / él / nosotros / vosotros / ellos — separate with " / "',
            },
        ],
        "adjective": [
            {
                "name":        "feminine",
                "label":       "Feminine form",
                "widget":      "text",
                "placeholder": "e.g. bella, grande",
                "hint":        "Masculine singular is the base. -o→-a; invariable adjectives unchanged.",
            },
            {
                "name":        "plural_m",
                "label":       "Plural (masc.)",
                "widget":      "text",
                "placeholder": "e.g. bellos, grandes",
            },
            {
                "name":        "plural_f",
                "label":       "Plural (fem.)",
                "widget":      "text",
                "placeholder": "e.g. bellas, grandes",
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
        ],
        "adverb": [],
    },
}

es_vocab_bp = make_vocab_blueprint(ES_LANG, check_fn=_es_check_fn)
