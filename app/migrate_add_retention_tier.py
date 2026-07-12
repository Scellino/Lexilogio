"""
migrate_add_retention_tier.py — One-time migration adding the retention_tier
column used by the "Review due" long-interval retention checks.

Run once with: python migrate_add_retention_tier.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "lexilogio.db"


def run():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    try:
        cur.execute("ALTER TABLE progress ADD COLUMN retention_tier INTEGER DEFAULT 0")
        print("Added retention_tier column.")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("retention_tier column already exists — nothing to do.")
        else:
            raise
    con.commit()
    con.close()


if __name__ == "__main__":
    run()
