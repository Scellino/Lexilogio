"""
nl_vocab_app.py — Dutch vocabulary trainer.
Registered in app.py at /nl/vocab.

To add another language, copy this file, fill in a new LANG dict,
call make_vocab_blueprint(), and register the blueprint in app.py.
"""
import re
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint, resolve_expected_article

# ── Check function ─────────────────────────────────────────────────────────────

_NL_ARTICLES = frozenset(["de", "het", "een"])


def _nl_edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], prev if a[i-1]==b[j-1] else 1+min(prev, dp[j], dp[j-1])
    return dp[n]


def _nl_normalize(s):
    s = s.lower().strip()
    for a, b in [("é","e"),("è","e"),("ë","e"),("ï","i"),("ö","o"),("ü","u")]:
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.strip()


def _nl_strip_article(s):
    for art in sorted(_NL_ARTICLES, key=len, reverse=True):
        if s.startswith(art + " "):
            return s[len(art):].lstrip()
    return s


def _nl_is_close(guess, target):
    max_len = max(len(guess), len(target), 1)
    d = _nl_edit_distance(guess, target)
    if max_len <= 4:  return d == 1
    if max_len <= 9:  return d <= 2
    return d <= 3


def _strip_the(s):
    return s[4:] if s.startswith("the ") else s


def _nl_check_fn(guess, correct, direction, card):
    g_norm = _strip_the(_nl_normalize(guess))
    c_norm = _strip_the(_nl_normalize(correct))

    if direction == "word→en":
        options = [_strip_the(_nl_normalize(o)) for o in re.split(r"[/,]", correct) if o.strip()]
        if not options:
            options = [c_norm]
        for opt in options:
            if g_norm == opt:
                return "correct"
            opt_w   = [w for w in opt.split()    if len(w) > 2]
            guess_w = [w for w in g_norm.split() if len(w) > 2]
            if opt_w and guess_w and opt_w[0] == guess_w[0]:
                return "correct"
        for opt in options:
            if _nl_is_close(g_norm, opt):
                return "correct"
            opt_w   = [w for w in opt.split()    if len(w) > 2]
            guess_w = [w for w in g_norm.split() if len(w) > 2]
            if opt_w and guess_w and _nl_is_close(guess_w[0], opt_w[0]):
                return "correct"
        return "wrong"

    # en→word: the article is part of the answer whenever the card's gender
    # is known — a bare noun with no article, or the wrong one, is "close"
    # (retry), not silently accepted or hard-failed.
    g_bare = _nl_strip_article(g_norm)
    if g_bare == c_norm:
        expected = _nl_normalize(resolve_expected_article(NL_LANG, card))
        if not expected:
            return "correct"   # no known gender — can't require an article
        if g_norm == f"{expected} {c_norm}":
            return "correct"
        return ("close", "missing_article") if g_norm == c_norm else ("close", "wrong_article")
    if _nl_is_close(g_bare, c_norm):
        return "close"
    if _nl_is_close(g_norm, c_norm):
        return "close"
    return "wrong"


NL_LANG = {
    "code":         "nl",
    "name":         "Dutch",
    "header_sub":   "NEDERLANDS · WOORDENSCHAT",
    "header_title": "Dutch Vocab Trainer",
    "data_dir":     Path(__file__).parent.parent / "dutch",
    "data_module":  "nl_vocab_data",
    "word_types":   ["noun", "verb", "adjective", "adverb", "preposition", "conjunction", "particle", "interjection", "phrase"],

    # Emoji labels for tags and groups (shown in filter pills)
    "tag_labels": {
        "common":        "⭐ Common",
        "daily":         "🏡 Daily life",
        "food":          "🍽️ Food",
        "home":          "🏠 Home",
        "movement":      "🚶 Movement",
        "time":          "⏰ Time",
        "communication": "💬 Communication",
        "greeting":      "👋 Greeting",
        "noun":          "📦 Noun",
        "verb":          "⚡ Verb",
        "adjective":     "🎨 Adjective",
        "adverb":        "💨 Adverb",
    },
    "group_labels": {
        "daily life":    "🏡 Daily life",
        "food":          "🍽️ Food",
        "travel":        "✈️ Travel",
        "work":          "💼 Work",
        "nature":        "🌿 Nature",
        "body":          "🫀 Body",
        "emotions":      "❤️ Emotions",
        "culture":       "🎭 Culture",
    },

    # Lets preset cards set their article class via grammar.Article / Gender: de|het
    "gender_grammar_label": "Article / Gender",

    # Dutch has two article classes: "de" (common) and "het" (neuter).
    # No vowel elision, so vowel_start == otherwise for both genders.
    "article_rule": {
        "based_on": "gender",
        "rules": {
            "de":  {"vowel_start": "de",  "otherwise": "de"},
            "het": {"vowel_start": "het", "otherwise": "het"},
        },
    },
    "article_colors": {
        "de":  "#9b6dcc",   # purple — de-woorden (common gender)
        "het": "#7ac49a",   # green  — het-woorden (same as Greek το)
    },

    # Grammar fields shown in the Add tab, keyed by word type.
    "grammar_fields": {
        "noun": [
            {
                "name":      "gender",
                "label":     "Article / Gender",
                "widget":    "radio",
                "top_level": True,
                "options": [
                    {"value": "de",  "label": "de (common — masc. & fem.)"},
                    {"value": "het", "label": "het (neuter)"},
                ],
            },
            {
                "name":        "plural",
                "label":       "Plural",
                "widget":      "text",
                "placeholder": "e.g. huizen, dagen, auto's",
                "hint":        "Most -en; some -s or -eren; watch double letters.",
            },
            {
                "name":        "diminutive",
                "label":       "Diminutive",
                "widget":      "text",
                "placeholder": "e.g. het huisje",
                "hint":        "Always het. Common suffixes: -je, -tje, -pje, -etje.",
            },
        ],
        "verb": [
            {
                "name":        "past_tense",
                "label":       "Past tense (sg.)",
                "widget":      "text",
                "placeholder": "e.g. werkte, ging, zag",
                "hint":        "Weak: -te/-de. Strong: vowel change.",
            },
            {
                "name":        "participle",
                "label":       "Past participle",
                "widget":      "text",
                "placeholder": "e.g. gewerkt, gegaan, gezien",
            },
            {
                "name":   "auxiliary",
                "label":  "Auxiliary",
                "widget": "radio",
                "options": [
                    {"value": "hebben", "label": "hebben"},
                    {"value": "zijn",   "label": "zijn"},
                ],
            },
            {
                "name":        "present",
                "label":       "Present (ik / jij / hij / wij / jullie / zij)",
                "widget":      "text",
                "placeholder": "werk / werkt / werkt / werken / werken / werken",
                "hint":        'Separate with " / "',
            },
        ],
        "adjective": [
            {
                "name":        "inflected",
                "label":       "Inflected form",
                "widget":      "text",
                "placeholder": "e.g. grote, mooie, nieuwe",
                "hint":        "Add -e in most contexts (attributive). Predicative = base form.",
            },
            {
                "name":        "comparative",
                "label":       "Comparative",
                "widget":      "text",
                "placeholder": "e.g. groter, mooier",
            },
            {
                "name":        "superlative",
                "label":       "Superlative",
                "widget":      "text",
                "placeholder": "e.g. grootst, mooist",
            },
        ],
        "preposition": [
            {
                "name":        "pronominal_adverb",
                "label":       "Pronominal adverb",
                "widget":      "text",
                "placeholder": "e.g. van → ervan, op → erop, voor → ervoor",
                "hint":        "Replaces preposition + pronoun in Dutch (ik denk eraan).",
            },
            {
                "name":        "fixed_combos",
                "label":       "Fixed combinations",
                "widget":      "text",
                "placeholder": "e.g. van plan zijn, op zoek naar",
                "hint":        "Common verb + preposition or fixed expression combos.",
            },
        ],
        "conjunction": [
            {
                "name":   "conj_type",
                "label":  "Type",
                "widget": "radio",
                "options": [
                    {"value": "coordinating",   "label": "Coordinating (en, maar, of, want, dus)"},
                    {"value": "subordinating",  "label": "Subordinating (omdat, dat, als, toen…)"},
                    {"value": "correlative",    "label": "Correlative (zowel…als, niet…maar)"},
                ],
            },
            {
                "name":        "word_order",
                "label":       "Word-order effect",
                "widget":      "text",
                "placeholder": "e.g. verb to end of clause (SOV), or no change",
                "hint":        "Subordinating conjunctions move the verb to the end.",
            },
        ],
        "particle": [
            {
                "name":        "function",
                "label":       "Function / meaning nuance",
                "widget":      "text",
                "placeholder": "e.g. softener, emphasis, surprise, impatience",
                "hint":        "Dutch modal particles (hoor, toch, maar, eens, even…) change tone.",
            },
            {
                "name":        "example_usage",
                "label":       "Typical usage example",
                "widget":      "text",
                "placeholder": "e.g. Doe maar! · Kom toch binnen.",
            },
        ],
        # interjection has no grammar fields — the word and translation say it all.
        "interjection": [],
    },
}

nl_vocab_bp = make_vocab_blueprint(NL_LANG, check_fn=_nl_check_fn)
