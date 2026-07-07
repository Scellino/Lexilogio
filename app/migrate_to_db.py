"""
migrate_to_db.py — One-time migration of existing flat-file data into the DB.

Run once:
    python migrate_to_db.py --email your@email.com [--name "Your Name"] [--admin]

What it does:
1. Creates an admin user account for you
2. Imports your Greek progress (progress.json) → Progress table
3. Imports your Greek user cards (user_cards.json) → UserCard table
4. Imports Dutch progress (progress.json) → Progress table
"""
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description="Migrate flat-file data to the Lexilogio DB")
parser.add_argument("--email",    required=True, help="Your email address (admin account)")
parser.add_argument("--name",     default="",    help="Your display name")
parser.add_argument("--password", default="",    help="Password (prompted if blank)")
parser.add_argument("--admin",    action="store_true", help="Set this user as admin")
args = parser.parse_args()

if not args.password:
    import getpass
    args.password = getpass.getpass("Password: ")
if len(args.password) < 8:
    print("Password must be at least 8 characters.")
    sys.exit(1)

# ── Bootstrap Flask app ───────────────────────────────────────────────────────
from app import app, db
import bcrypt
from models import User, Progress, UserCard

with app.app_context():

    # ── 1. Create admin user ──────────────────────────────────────────────────
    user = User.query.filter_by(email=args.email.lower()).first()
    if user:
        print(f"User {args.email} already exists (id={user.id}), using existing account.")
    else:
        pw_hash = bcrypt.hashpw(args.password.encode(), bcrypt.gensalt()).decode()
        user = User(
            email=args.email.lower(),
            name=args.name or None,
            password_hash=pw_hash,
            is_admin=args.admin,
        )
        db.session.add(user)
        db.session.commit()
        print(f"Created user {args.email} (id={user.id}, admin={args.admin})")

    # ── 2. Migrate Greek progress ─────────────────────────────────────────────
    greek_progress = Path(__file__).parent.parent / "Vocab App" / "progress.json"
    if greek_progress.exists():
        with open(greek_progress, encoding="utf-8") as f:
            prog = json.load(f)
        inserted = 0
        for card_id, entry in prog.items():
            if not isinstance(entry, dict):
                continue
            exists = Progress.query.filter_by(
                user_id=user.id, lang_code="el", card_id=card_id
            ).first()
            if exists:
                continue
            row = Progress(
                user_id=user.id,
                lang_code="el",
                card_id=card_id,
                window=json.dumps(entry.get("window", [])),
                last_day=entry.get("last_day"),
                spaced_days=entry.get("spaced_days", 0),
                dirs=json.dumps(entry.get("dirs", [])),
            )
            db.session.add(row)
            inserted += 1
        db.session.commit()
        print(f"Greek progress: {inserted} entries imported ({len(prog) - inserted} already present)")
    else:
        print("Greek progress.json not found, skipping.")

    # ── 3. Migrate Greek user cards ───────────────────────────────────────────
    greek_cards = Path(__file__).parent.parent / "Vocab App" / "user_cards.json"
    if greek_cards.exists():
        with open(greek_cards, encoding="utf-8") as f:
            data = json.load(f)
        cards = data["cards"] if isinstance(data, dict) else data
        inserted = 0
        for card in cards:
            cid = str(card.get("id", ""))
            if not cid:
                continue
            exists = UserCard.query.filter_by(
                user_id=user.id, lang_code="el", card_id=cid
            ).first()
            if exists:
                continue
            row = UserCard(
                user_id=user.id,
                lang_code="el",
                card_id=cid,
                card_data=json.dumps(card, ensure_ascii=False),
            )
            db.session.add(row)
            inserted += 1
        db.session.commit()
        print(f"Greek user cards: {inserted} imported ({len(cards) - inserted} already present)")
    else:
        print("Greek user_cards.json not found, skipping.")

    # ── 4. Migrate Dutch progress ─────────────────────────────────────────────
    dutch_progress = Path(__file__).parent.parent / "Dutch Vocab" / "progress.json"
    if dutch_progress.exists():
        with open(dutch_progress, encoding="utf-8") as f:
            prog = json.load(f)
        inserted = 0
        for card_id, entry in prog.items():
            if not isinstance(entry, dict):
                continue
            exists = Progress.query.filter_by(
                user_id=user.id, lang_code="nl", card_id=card_id
            ).first()
            if exists:
                continue
            row = Progress(
                user_id=user.id,
                lang_code="nl",
                card_id=card_id,
                window=json.dumps(entry.get("window", [])),
                last_day=entry.get("last_day"),
                spaced_days=entry.get("spaced_days", 0),
                dirs=json.dumps(entry.get("dirs", [])),
            )
            db.session.add(row)
            inserted += 1
        db.session.commit()
        print(f"Dutch progress: {inserted} entries imported ({len(prog) - inserted} already present)")
    else:
        print("Dutch progress.json not found, skipping.")

    print("\nDone. Run the app and log in with your email.")
