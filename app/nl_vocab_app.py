"""
nl_vocab_app.py — Dutch vocabulary trainer.
Registered in app.py at /nl/vocab.

To add another language, copy this file, fill in a new LANG dict,
call make_vocab_blueprint(), and register the blueprint in app.py.
"""
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint

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

nl_vocab_bp = make_vocab_blueprint(NL_LANG)
