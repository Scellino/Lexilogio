"""
fr_vocab_app.py — French vocabulary trainer.
Registered in app.py at /fr/vocab.

To add another language, copy this file, fill in a new LANG dict,
call make_vocab_blueprint(), and register the blueprint in app.py.
"""
from pathlib import Path
from generic_vocab_bp import make_vocab_blueprint

FR_LANG = {
    "code":         "fr",
    "name":         "French",
    "header_sub":   "FRANÇAIS · VOCABULAIRE",
    "header_title": "French Vocab Trainer",
    "data_dir":     Path(__file__).parent.parent / "french",
    "data_module":  "fr_vocab_data",
    "word_types":   ["noun", "verb", "adjective", "adverb", "phrase"],

    # Lets preset cards set their article colour via grammar.Gender: m|f
    "gender_grammar_label": "Gender",

    # Emoji labels for tags and groups (shown in filter pills)
    "tag_labels": {
        "common":    "⭐ Common",
        "daily":     "🏡 Daily life",
        "food":      "🍽️ Food",
        "home":      "🏠 Home",
        "travel":    "✈️ Travel",
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
        "Emotions & Character":   "❤️ Emotions & Character",
        "Nature & Senses":        "🌿 Nature & Senses",
        "Language & Expression":  "💬 Language & Expression",
        "Daily life & Culture":   "🏡 Daily life & Culture",
    },

    # Drives article display on the flashcard front (e.g. "le", "la", "l'").
    # Remove this key entirely for languages without grammatical gender articles.
    "article_rule": {
        "based_on": "gender",               # top-level card field to inspect
        "vowels":   "aeiouéèêëàâùûüîïô",   # triggers vowel_start form
        "rules": {
            "m": {"vowel_start": "l'", "otherwise": "le"},
            "f": {"vowel_start": "l'", "otherwise": "la"},
        },
    },
    "article_colors": {
        "m":  "#7ab3d4",   # blue — masculine (gender field)
        "f":  "#d47a8f",   # rose — feminine  (gender field)
        "le": "#7ab3d4",   # blue — masculine (article prefix "le ")
        "la": "#d47a8f",   # rose — feminine  (article prefix "la ")
        # "l'" is ambiguous (both genders) — no colour assigned
    },

    # Grammar fields shown in the Add tab, keyed by word type.
    # Supported widgets: "text", "radio", "select".
    # top_level:True  → value also stored as a top-level card property (e.g. card.gender).
    # in_grammar:False → value NOT added to the grammar[] array shown on card back.
    "grammar_fields": {
        "noun": [
            {
                "name":      "gender",
                "label":     "Gender",
                "widget":    "radio",
                "top_level": True,
                "options": [
                    {"value": "m", "label": "Masculine (le / un)"},
                    {"value": "f", "label": "Feminine (la / une)"},
                ],
            },
            {
                "name":        "plural",
                "label":       "Plural",
                "widget":      "text",
                "placeholder": "e.g. maisons",
                "hint":        "Most French nouns add -s. Note irregulars.",
            },
        ],
        "verb": [
            {
                "name":   "group",
                "label":  "Verb group",
                "widget": "select",
                "options": [
                    {"value": "1er", "label": "1st group (-er)"},
                    {"value": "2e",  "label": "2nd group (-ir)"},
                    {"value": "3e",  "label": "3rd group (irregular)"},
                ],
            },
            {
                "name":   "auxiliary",
                "label":  "Auxiliary",
                "widget": "radio",
                "options": [
                    {"value": "avoir", "label": "avoir"},
                    {"value": "être",  "label": "être"},
                ],
            },
            {
                "name":        "participle",
                "label":       "Past participle",
                "widget":      "text",
                "placeholder": "e.g. mangé, fini, fait",
            },
            {
                "name":        "present",
                "label":       "Présent (all 6 forms)",
                "widget":      "text",
                "placeholder": "mange / manges / mange / mangeons / mangez / mangent",
                "hint":        'Order: je / tu / il / nous / vous / ils — separate with " / "',
            },
        ],
        "adjective": [
            {
                "name":        "feminine",
                "label":       "Feminine form",
                "widget":      "text",
                "placeholder": "e.g. grande, belle",
                "hint":        "Masculine singular is the base form entered above.",
            },
            {
                "name":        "plural_m",
                "label":       "Plural (masc.)",
                "widget":      "text",
                "placeholder": "e.g. grands",
            },
            {
                "name":        "plural_f",
                "label":       "Plural (fem.)",
                "widget":      "text",
                "placeholder": "e.g. grandes",
            },
        ],
    },
}

fr_vocab_bp = make_vocab_blueprint(FR_LANG)
