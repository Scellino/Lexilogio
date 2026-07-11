"""
migrate_verb_progress.py — one-time import of the legacy shared Greek verb
progress file into per-user Progress rows.

The verb trainer historically stored all progress in greek/verb_progress.json,
shared by every visitor. That data belonged to a single user in practice; this
script assigns it to their account.

Usage:
    python migrate_verb_progress.py user@example.com
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import app as mainapp
from models import db, User, Progress
from verb_app import PROGRESS_FILE, VERB_PROGRESS_LANG


def main():
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <user-email>")
    email = sys.argv[1].strip().lower()

    if not PROGRESS_FILE.exists():
        sys.exit(f"No legacy progress file at {PROGRESS_FILE} — nothing to migrate")
    data = json.loads(PROGRESS_FILE.read_text())

    with mainapp.app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            sys.exit(f"No user with email {email}")

        created = updated = 0
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            row = Progress.query.filter_by(
                user_id=user.id, lang_code=VERB_PROGRESS_LANG, card_id=key).first()
            if row:
                updated += 1
            else:
                row = Progress(user_id=user.id, lang_code=VERB_PROGRESS_LANG, card_id=key)
                db.session.add(row)
                created += 1
            row.window = json.dumps(val, ensure_ascii=False)
        db.session.commit()
        print(f"Migrated {created + updated} keys for {email} "
              f"({created} created, {updated} updated)")


if __name__ == "__main__":
    main()
