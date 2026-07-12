"""
it_vocab_app.py — Italian vocabulary trainer.
Registered in app.py at /it/vocab.
"""
import re
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint

# ── Check function ─────────────────────────────────────────────────────────────

_IT_VOWELS = "aeiouàèéìòù"

_IT_ARTICLES = frozenset([
    "il", "lo", "l'", "l’", "i", "gli",
    "la", "le",
    "un", "uno", "una", "un'", "un’",
])


def _it_edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], prev if a[i-1]==b[j-1] else 1+min(prev, dp[j], dp[j-1])
    return dp[n]


def _it_normalize(s):
    s = s.lower().strip()
    for a, b in [("à","a"),("è","e"),("é","e"),("ì","i"),("ò","o"),("ù","u")]:
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.strip()


def _it_strip_article(s):
    for art in sorted(_IT_ARTICLES, key=len, reverse=True):
        norm_art = re.sub(r"[^\w]", "", art)  # strip punctuation from article
        if s.startswith(norm_art + " "):
            return s[len(norm_art):].lstrip()
    return s


def _it_is_close(guess, target):
    max_len = max(len(guess), len(target), 1)
    d = _it_edit_distance(guess, target)
    if max_len <= 4:  return d == 1
    if max_len <= 9:  return d <= 2
    return d <= 3


def _strip_the(s):
    return s[4:] if s.startswith("the ") else s


def _it_check_fn(guess, correct, direction, card):
    g_norm = _strip_the(_it_normalize(guess))
    c_norm = _strip_the(_it_normalize(correct))
    if g_norm == c_norm:
        return "correct"

    g_bare = _it_strip_article(g_norm)
    c_bare = _it_strip_article(c_norm)

    if direction == "word→en":
        if _it_is_close(g_norm, c_norm):
            return "correct"
    else:
        # The English prompt gives no article, so omitting one is fully
        # correct. Only flag "close" (retry) when the learner supplied an
        # article that doesn't match the expected one.
        g_had_article = g_bare != g_norm
        c_had_article = c_bare != c_norm
        if g_bare == c_bare:
            if g_had_article and c_had_article:
                return "close"  # right word, wrong article
            return "correct"
        if _it_is_close(g_bare, c_bare):
            return "close"
        if _it_is_close(g_norm, c_norm):
            return "close"

    return "wrong"


# ── Language config ────────────────────────────────────────────────────────────

IT_LANG = {
    "code":         "it",
    "name":         "Italian",
    "header_sub":   "ITALIANO · VOCABOLARIO",
    "header_title": "Italian Vocab Trainer",
    "data_dir":     Path(__file__).parent.parent / "italian",
    "data_module":  "it_vocab_data",
    "word_field":   "italian",
    "group_field":  "scene_id",
    "example_native_code": "it",
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

    # Italian masculine article: "l'" before vowels, "lo" before special
    # consonant clusters (z, s+cons, gn, pn, ps, x, y), "il" otherwise.
    "article_rule": {
        "based_on": "gender",
        "vowels":   _IT_VOWELS,
        "rules": {
            "m": {
                "vowel_start": "l'",
                "prefix_overrides": {
                    # Special clusters → lo
                    "gn": "lo", "pn": "lo", "ps": "lo",
                    "z":  "lo", "x":  "lo", "y":  "lo",
                    # s + any consonant → lo
                    "sc": "lo", "sd": "lo", "sf": "lo", "sg": "lo",
                    "sk": "lo", "sl": "lo", "sm": "lo", "sn": "lo",
                    "sp": "lo", "sq": "lo", "sr": "lo", "st": "lo",
                    "sv": "lo", "sw": "lo", "sz": "lo",
                },
                "otherwise": "il",
            },
            "f": {"vowel_start": "l'", "otherwise": "la"},
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
                    {"value": "m", "label": "Masculine (il / un)"},
                    {"value": "f", "label": "Feminine (la / una)"},
                ],
            },
            {
                "name":        "article",
                "label":       "Article override",
                "widget":      "text",
                "top_level":   True,
                "placeholder": "e.g. lo, l', gli",
                "hint":        "Leave blank — the rule computes il/lo/l' automatically. Override only for unusual edge cases.",
            },
            {
                "name":        "plural",
                "label":       "Plural",
                "widget":      "text",
                "placeholder": "e.g. gatti, case, uomini",
                "hint":        "Masc -o→-i, fem -a→-e. Note irregular plurals (l'uomo→gli uomini).",
            },
        ],
        "verb": [
            {
                "name":   "group_it",
                "label":  "Verb group",
                "widget": "select",
                "options": [
                    {"value": "-are",      "label": "-are  (mangiare, parlare)"},
                    {"value": "-ere",      "label": "-ere  (vedere, credere)"},
                    {"value": "-ire",      "label": "-ire  (dormire, partire)"},
                    {"value": "irregular", "label": "Irregular (essere, avere, fare…)"},
                ],
            },
            {
                "name":   "auxiliary",
                "label":  "Auxiliary (passato prossimo)",
                "widget": "radio",
                "options": [
                    {"value": "avere",  "label": "avere"},
                    {"value": "essere", "label": "essere"},
                ],
            },
            {
                "name":        "participle",
                "label":       "Participio passato",
                "widget":      "text",
                "placeholder": "e.g. mangiato, visto, fatto",
            },
            {
                "name":        "passato_remoto",
                "label":       "Passato remoto (io)",
                "widget":      "text",
                "placeholder": "e.g. mangiai, vidi, feci",
                "hint":        "First person singular simple past.",
            },
            {
                "name":        "gerundio",
                "label":       "Gerundio",
                "widget":      "text",
                "placeholder": "e.g. mangiando, vedendo, dormendo",
            },
            {
                "name":        "present",
                "label":       "Presente (all 6 forms)",
                "widget":      "text",
                "placeholder": "mangio / mangi / mangia / mangiamo / mangiate / mangiano",
                "hint":        'Order: io / tu / lui / noi / voi / loro — separate with " / "',
            },
        ],
        "adjective": [
            {
                "name":        "feminine",
                "label":       "Feminine form",
                "widget":      "text",
                "placeholder": "e.g. bella, grande",
                "hint":        "Masculine singular is the base. -o→-a for most; invariable (grande) unchanged.",
            },
            {
                "name":        "plural_m",
                "label":       "Plural (masc.)",
                "widget":      "text",
                "placeholder": "e.g. belli, grandi",
            },
            {
                "name":        "plural_f",
                "label":       "Plural (fem.)",
                "widget":      "text",
                "placeholder": "e.g. belle, grandi",
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

it_vocab_bp = make_vocab_blueprint(IT_LANG, check_fn=_it_check_fn)
