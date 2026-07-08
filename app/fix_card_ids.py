"""
fix_card_ids.py — Fix migration bug where numeric card IDs like '1001'
were incorrectly renamed to '1001-en-' instead of being left alone.

Run once: python fix_card_ids.py
"""
import re
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "lexilogio.db"


def is_broken(card_id: str) -> bool:
    """Detect IDs like '1001-en-' that should be '1001'."""
    return bool(re.match(r'^\w+-[a-z]{2}-$', card_id))


def original_id(card_id: str) -> str:
    """'1001-en-' → '1001'"""
    return card_id.rsplit('-', 2)[0]


def run():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    print("── Fix broken card IDs ──")

    # user_cards
    cur.execute("SELECT id, card_id, card_data FROM user_cards")
    rows = cur.fetchall()
    fixed = 0
    for row_id, card_id, card_data in rows:
        if not is_broken(card_id):
            continue
        orig = original_id(card_id)
        try:
            data = json.loads(card_data)
            data["id"] = orig
            new_data = json.dumps(data, ensure_ascii=False)
        except Exception:
            new_data = card_data
        cur.execute("UPDATE user_cards SET card_id=?, card_data=? WHERE id=?",
                    (orig, new_data, row_id))
        fixed += 1
    print(f"  user_cards: {fixed} IDs reverted")

    # progress
    cur.execute("SELECT id, card_id FROM progress")
    rows = cur.fetchall()
    fixed = 0
    for row_id, card_id in rows:
        if not is_broken(card_id):
            continue
        cur.execute("UPDATE progress SET card_id=? WHERE id=?",
                    (original_id(card_id), row_id))
        fixed += 1
    print(f"  progress:   {fixed} IDs reverted")

    # preset_cards (primary key — insert+delete)
    cur.execute("SELECT id, lang, departure_lang, word, translation, type, \"group\", pronunciation, etymology, note, tags, grammar, example, priority, imported_at FROM preset_cards")
    rows = cur.fetchall()
    fixed = 0
    for row in rows:
        old_id = row[0]
        if not is_broken(old_id):
            continue
        orig = original_id(old_id)
        cur.execute("""
            INSERT OR IGNORE INTO preset_cards
              (id, lang, departure_lang, word, translation, type, "group", pronunciation,
               etymology, note, tags, grammar, example, priority, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (orig,) + row[1:])
        cur.execute("DELETE FROM preset_cards WHERE id=?", (old_id,))
        fixed += 1
    print(f"  preset_cards: {fixed} IDs reverted")

    con.commit()
    con.close()
    print("\n✓ Done.")


if __name__ == "__main__":
    run()
