# Lexilogio — Conventions & Architecture Notes

## Languages

### Target language vs departure language
- **Target language** — the language being learned (Greek, French, Dutch, etc.)
- **Departure language** — the language the user already speaks (English, German, etc.)

These are always kept separate. A card for Dutch taught from English and a card for Dutch taught from German are two entirely independent cards — different translations, different notes, different examples. There is no linking or translation between them.

### Departure language as a user account setting
- Stored on the `User` model as `departure_lang` (ISO 639-1 code, e.g. `'en'`, `'de'`)
- Default: `'en'` (English)
- Displayed as a "I speak: ..." banner on the home screen with a change link
- All card creation, AI prompts, and card filtering respect this setting automatically

---

## Card IDs

Card IDs encode both target language and departure language:

```
{target_lang}-{departure_lang}-{slug}
```

Examples:
- `nl-en-hallo` — Dutch word "hallo", taught from English
- `nl-de-hallo` — Dutch word "hallo", taught from German
- `el-en-agapi` — Greek word "αγάπη", taught from English

The slug is derived from the word itself (lowercased, accents stripped, spaces → hyphens).

Existing cards (created before departure language was introduced) were migrated to `departure_lang = 'en'`.

---

## Preset Files

Preset cards live in the language folder for their target language and follow this naming convention:

```
{target_lang}_{departure_lang}_presets.txt
```

Examples:
- `dutch/nl_en_presets.txt` — Dutch presets for English speakers
- `dutch/nl_de_presets.txt` — Dutch presets for German speakers
- `greek/el_en_presets.txt` — Greek presets for English speakers

The preset loader reads both codes from the filename automatically.

### Preset file format

Fields are separated by `//`. All fields for one card can be on one line or spread across multiple lines.

```
word: hallo // translation: hello // group: Basics // type: interjection
//
word: de hond // translation: the dog // type: noun // group: Animals // pronunciation: də hɔnt // note: Common pet. // example.nl: De hond rent. // example.en: The dog runs.
```

Supported fields:
- `word` — the word in the target language (required)
- `translation` — meaning in the departure language (required)
- `type` — grammatical type (noun, verb, adjective, etc.)
- `group` — thematic group shown in filters (e.g. "Travelling", "Animals")
- `pronunciation` — IPA or phonetic hint
- `note` — free-text note shown on card back (in departure language)
- `etymology` — word origin (in departure language)
- `tags` — comma-separated tags
- `priority` — integer, 1 = starred card
- `example.{lang}` — example sentence in target language (e.g. `example.nl`)
- `example.{departure}` — translation of example (e.g. `example.en`, `example.de`)
- `grammar.{Label}` — grammar table row, e.g. `grammar.Plural: honden`

No `id` field — IDs are auto-generated as `{target}-{departure}-{slug}`.

---

## AI Prompts (Card Filling)

When a user bulk-adds or AI-fills a card, the prompt must include the departure language so all generated fields (translation, note, etymology, examples) come back in the correct language.

The departure language is read from `current_user.departure_lang` and injected into the prompt automatically. Users never need to set it per-card.

---

## Quiz Spell-checking

- The **departure language** side (the user's native language) gets **lenient** checking (edit distance tolerance + auto-correct on close matches)
- The **target language** side (what they're learning) gets **strict** checking with a "close — try again" prompt on near-misses

Verb answers: `"to "` prefix is always stripped before comparison on both sides.

---

## Progress Carry-over

When a user switches departure language and encounters a card for the first time in the new departure language, the app checks whether they have existing progress on any card with the same `word` + `lang_code` combination in a different departure language. If found, that progress window is copied over lazily (on first encounter, not on switch).

---

## Folder Structure

```
Lexilogio/
├── app/                  Flask app
│   ├── app.py
│   ├── models.py
│   ├── auth.py
│   ├── generic_vocab_bp.py
│   ├── community_bp.py
│   └── preset_loader.py
├── greek/
│   ├── el_en_presets.txt
│   └── el_vocab_data.py
├── french/
│   ├── fr_en_presets.txt
│   └── fr_vocab_data.py
├── dutch/
│   ├── nl_en_presets.txt
│   └── nl_vocab_data.py
├── spanish/
├── italian/
├── german/
└── CONVENTIONS.md        ← this file
```
