"""
preset_loader.py — Import preset cards from *_presets.txt files into the DB.

File naming convention: {target_lang}_{departure_lang}_presets.txt
  e.g. nl_en_presets.txt  → Dutch words, taught from English
       nl_de_presets.txt  → Dutch words, taught from German

Format: same as the bulk-add textarea in the vocab trainer (key: value lines,
cards separated by //). On app startup, only new cards (by ID) are imported.

ID format: {target_lang}-{departure_lang}-{slug}
  e.g. nl-en-hallo, nl-de-hallo
"""
import re
import json
import unicodedata
from pathlib import Path

BASE = Path(__file__).parent.parent

LANG_FOLDERS = {
    "el": BASE / "greek",
    "fr": BASE / "french",
    "nl": BASE / "dutch",
    "es": BASE / "spanish",
    "it": BASE / "italian",
    "de": BASE / "german",
}


def _slug(lang, departure, word):
    normalized = unicodedata.normalize("NFKD", word.lower())
    no_combining = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^\w]+", "-", no_combining, flags=re.UNICODE).strip("-").replace("_", "-")
    return f"{lang}-{departure}-{slug}"


def _expand_inline(block):
    """If all fields are on one line, insert newlines before each key."""
    if '\n' in block:
        return block
    SIMPLE = r'(?:word|translation|type|group|pronunciation|note|etymology|priority|tags|example(?:\.[a-z]{2,3})?)'
    result = re.sub(r'\s+(?=' + SIMPLE + r'\s*:)', '\n', block)
    result = re.sub(r'\s+(?=grammar\.)', '\n', result)
    return result


def _parse_preset_text(text, lang, departure):
    """Parse // -separated card blocks into a list of card dicts."""
    blocks = re.split(r'\n[ \t]*(?:---|//)[ \t]*(?:\n|$)', text.strip())
    cards = []
    for block in blocks:
        block = _expand_inline(block.strip())
        if not block:
            continue
        card = {"grammar": [], "tags": [], "type": ""}
        for line in block.split("\n"):
            m = re.match(r'^([^:]+):\s*(.*)', line)
            if not m:
                continue
            raw_key = m.group(1).strip()
            key = raw_key.lower()
            val = m.group(2).strip()
            if   key == "word":           card["word"] = val
            elif key == "translation":    card["translation"] = val
            elif key == "type":           card["type"] = val
            elif key == "group":          card["group"] = val
            elif key == "pronunciation":  card["pronunciation"] = val
            elif key in ("example", f"example.{lang}"):
                if isinstance(card.get("example"), dict):
                    card["example"][lang] = val
                else:
                    card["example"] = val
            elif key == f"example.{departure}":
                prev = card.get("example", "")
                if isinstance(prev, dict):
                    prev[departure] = val
                else:
                    card["example"] = {lang: prev, departure: val}
            elif key == "note":           card["note"] = val
            elif key == "etymology":      card["etymology"] = val
            elif key == "priority":       card["priority"] = 1 if re.search(r'yes|true|1', val, re.I) else 0
            elif key == "tags":           card["tags"] = [t.strip() for t in val.split(",") if t.strip()]
            elif key.startswith("grammar.") and val:
                label = raw_key[8:].strip()
                card["grammar"].append({"label": label, "value": val})
        if card.get("word") and card.get("translation"):
            card["id"] = _slug(lang, departure, card["word"])
            card["departure_lang"] = departure
            cards.append(card)

    # Guard against duplicate slugs within one file. Two cases:
    #  1. The same word appears twice (a generation mistake) — keep the first,
    #     drop the rest.
    #  2. Two different words collide once accents are stripped (e.g. French
    #     "sucre" vs "sucré") — both are real content, so instead of losing
    #     one, give the later id a numeric suffix so it stays unique.
    # Without this, importing the file raises UNIQUE constraint failed and
    # the *whole file's* cards are lost.
    seen_words = {}   # id -> word already using it
    deduped = []
    for card in cards:
        base_id = card["id"]
        prior_word = seen_words.get(base_id)
        if prior_word == card["word"]:
            print(f"[presets] {lang}/{departure}: skipping duplicate card "
                  f"'{card['word']}' (id '{base_id}')")
            continue
        if prior_word is not None:
            n = 2
            while f"{base_id}-{n}" in seen_words:
                n += 1
            card["id"] = f"{base_id}-{n}"
            print(f"[presets] {lang}/{departure}: '{card['word']}' collides "
                  f"with '{prior_word}' after accent-stripping — "
                  f"using id '{card['id']}'")
        seen_words[card["id"]] = card["word"]
        deduped.append(card)
    return deduped


def _parse_filename(txt_file):
    """Extract (target_lang, departure_lang) from filename.
    Supports nl_en_presets.txt (new) and nl_presets.txt (legacy → departure='en').
    """
    stem = txt_file.stem  # e.g. "nl_en_presets" or "nl_presets"
    parts = stem.split("_")
    if len(parts) >= 3 and len(parts[1]) == 2:
        return parts[0], parts[1]   # nl_en_presets → ('nl', 'en')
    return parts[0], 'en'           # nl_presets → ('nl', 'en')


def load_presets(app):
    """Upsert all preset cards from txt files at startup.

    Runs under an exclusive file lock so the multiple gunicorn workers that
    each import this module can't race: without it, two workers try to INSERT
    the same new card ids concurrently and the loser crash-fails with
    'UNIQUE constraint failed: preset_cards.id'. With the lock, the first
    worker inserts and the rest see the rows already present (pure updates).
    """
    import fcntl
    lock_path = BASE / ".preset_load.lock"
    with open(lock_path, "w") as _lock:
        fcntl.flock(_lock, fcntl.LOCK_EX)
        _load_presets_locked(app)


def _load_presets_locked(app):
    from models import db, PresetCard

    with app.app_context():
        for lang, folder in LANG_FOLDERS.items():
            if not folder.exists():
                continue
            for txt_file in sorted(folder.glob("*_presets.txt")):
                file_lang, departure = _parse_filename(txt_file)
                if file_lang != lang:
                    continue
                try:
                    text = txt_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                cards = _parse_preset_text(text, lang, departure)
                if not cards:
                    continue
                existing_ids = {
                    row[0] for row in
                    db.session.query(PresetCard.id)
                    .filter_by(lang=lang, departure_lang=departure).all()
                }
                new_count = sum(1 for c in cards if c["id"] not in existing_ids)
                for c in cards:
                    ex = c.get("example")
                    db.session.merge(PresetCard(
                        id=c["id"],
                        lang=lang,
                        departure_lang=departure,
                        word=c.get("word", ""),
                        translation=c.get("translation", ""),
                        type=c.get("type", ""),
                        group=c.get("group", ""),
                        pronunciation=c.get("pronunciation", ""),
                        etymology=c.get("etymology", ""),
                        note=c.get("note", ""),
                        tags=json.dumps(c.get("tags", [])),
                        grammar=json.dumps(c.get("grammar", [])),
                        example=json.dumps(ex) if ex else None,
                        priority=c.get("priority", 0),
                    ))
                try:
                    db.session.commit()
                    updated = len(cards) - new_count
                    print(f"[presets] {lang}/{departure}: {new_count} new, {updated} updated from {txt_file.name}")
                except Exception as e:
                    db.session.rollback()
                    print(f"[presets] {lang}/{departure}: error importing {txt_file.name}: {e}")
