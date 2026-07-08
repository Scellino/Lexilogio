"""
migrate_departure_lang.py — One-time migration to add departure_lang support.

Run once with: python migrate_departure_lang.py

What it does:
  1. Adds departure_lang column to users, user_cards, preset_cards tables
  2. Sets departure_lang = 'en' on all existing rows
  3. Renames card IDs from '{lang}-{slug}' to '{lang}-en-{slug}'
     in user_cards, progress, and preset_cards tables
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "lexilogio.db"


def rename_id(card_id: str) -> str:
    """'nl-hallo' → 'nl-en-hallo'  (idempotent if already in new format)"""
    parts = card_id.split("-", 2)
    # Already new format: first segment = lang (2 chars), second = departure (2 chars)
    if len(parts) >= 2 and len(parts[1]) == 2 and parts[1].isalpha():
        return card_id
    lang = parts[0]
    rest = "-".join(parts[1:])
    return f"{lang}-en-{rest}"


def run():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    print("── Step 1: add departure_lang columns ──")
    for stmt in [
        "ALTER TABLE users       ADD COLUMN departure_lang TEXT DEFAULT 'en'",
        "ALTER TABLE user_cards  ADD COLUMN departure_lang TEXT DEFAULT 'en'",
        "ALTER TABLE preset_cards ADD COLUMN departure_lang TEXT DEFAULT 'en'",
    ]:
        try:
            cur.execute(stmt)
            print(f"  OK: {stmt[:60]}")
        except sqlite3.OperationalError as e:
            print(f"  skip (already exists): {e}")

    con.commit()

    print("\n── Step 2: set departure_lang = 'en' on existing rows ──")
    for table in ("users", "user_cards", "preset_cards"):
        cur.execute(f"UPDATE {table} SET departure_lang = 'en' WHERE departure_lang IS NULL")
        print(f"  {table}: {cur.rowcount} rows updated")
    con.commit()

    print("\n── Step 3: rename card IDs to include departure lang ──")

    # user_cards: update card_id and card_data['id']
    cur.execute("SELECT id, card_id, card_data FROM user_cards")
    rows = cur.fetchall()
    updated = 0
    for row_id, card_id, card_data in rows:
        new_id = rename_id(card_id)
        if new_id == card_id:
            continue
        import json
        try:
            data = json.loads(card_data)
            data["id"] = new_id
            new_data = json.dumps(data, ensure_ascii=False)
        except Exception:
            new_data = card_data
        cur.execute(
            "UPDATE user_cards SET card_id=?, card_data=? WHERE id=?",
            (new_id, new_data, row_id)
        )
        updated += 1
    print(f"  user_cards: {updated} card IDs renamed")

    # progress: update card_id
    cur.execute("SELECT id, card_id FROM progress")
    rows = cur.fetchall()
    updated = 0
    for row_id, card_id in rows:
        new_id = rename_id(card_id)
        if new_id == card_id:
            continue
        cur.execute("UPDATE progress SET card_id=? WHERE id=?", (new_id, row_id))
        updated += 1
    print(f"  progress:   {updated} card IDs renamed")

    # preset_cards: primary key rename (insert new + delete old)
    cur.execute("SELECT id, lang, word, translation, type, \"group\", pronunciation, etymology, note, tags, grammar, example, priority, imported_at FROM preset_cards")
    rows = cur.fetchall()
    updated = 0
    for row in rows:
        old_id = row[0]
        new_id = rename_id(old_id)
        if new_id == old_id:
            continue
        cur.execute("""
            INSERT OR IGNORE INTO preset_cards
              (id, lang, departure_lang, word, translation, type, "group", pronunciation,
               etymology, note, tags, grammar, example, priority, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (new_id, row[1], 'en', row[2], row[3], row[4], row[5], row[6],
              row[7], row[8], row[9], row[10], row[11], row[12], row[13]))
        cur.execute("DELETE FROM preset_cards WHERE id=?", (old_id,))
        updated += 1
    print(f"  preset_cards: {updated} card IDs renamed")

    con.commit()
    con.close()
    print("\n✓ Migration complete.")


if __name__ == "__main__":
    run()
