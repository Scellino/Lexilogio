# Λεξιλόγιο (Lexilogio)

Free, ad-free vocabulary and verb trainer for Greek, Italian, Spanish, German,
French, and Dutch — live at [lexilogio.org](https://lexilogio.org).

Users study flashcards (curated, preset packs, community-submitted, or their
own), take quizzes with accent-tolerant answer checking, and track per-card
mastery. No gamification, no ads, no tracking.

## Architecture

Single Flask app (`app/app.py`) serving self-contained HTML pages (inline
CSS/JS, no build step) from blueprints:

| Module | Role |
|---|---|
| `app.py` | App setup, home page, profile, legal pages, error pages |
| `auth.py` | Email/password + Google OAuth, verification, reset, rate limits |
| `models.py` | SQLAlchemy models: User, Progress, UserCard, PresetCard, CardSubmission |
| `generic_vocab_bp.py` | Vocab trainer factory — one blueprint per language |
| `el_vocab_app.py` etc. | Thin per-language wrappers (Greek adds alt-spelling checks) |
| `generic_verb_bp.py` | Verb trainer factory (fr/de/it/es/nl) |
| `verb_app.py` | Greek verb trainer (600+ verbs from cached JSON conjugations) |
| `community_bp.py` | Community hub: browse presets/community cards, copy to deck |
| `admin.py` | Admin review queue for community submissions |
| `preset_loader.py` | Upserts `<lang>/**_presets.txt` card packs into the DB at startup |

Language content lives in sibling folders (`greek/`, `french/`, …): static
curated cards as Python data modules, preset packs as `*_presets.txt`
(single-line card format, `//`-separated), verb conjugation JSON caches.

Storage: SQLite (WAL mode) via SQLAlchemy — swap `DATABASE_URL` for Postgres
if it ever outgrows that. Progress rows are per user per card; Greek verb
progress uses `lang_code="el-verb"`.

## Local development

```bash
cd app
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in SECRET_KEY at minimum
venv/bin/python app.py        # → http://localhost:5003
```

Optional env: `GOOGLE_CLIENT_ID/SECRET` (OAuth), `RESEND_API_KEY` (email),
`TURNSTILE_SITE_KEY/SECRET_KEY` (CAPTCHA), `COOKIE_SECURE=1` (HTTPS-only
cookies — production only).

## Tests

```bash
cd app && venv/bin/pip install pytest && venv/bin/python -m pytest tests/ -q
```

CI runs the suite on every push (`.github/workflows/ci.yml`).

## Deployment

Production: OVH VPS (Frankfurt), nginx → gunicorn (2 workers) → Flask,
systemd unit `lexilogio`, SQLite in `app/lexilogio.db`.

```bash
git push
ssh ubuntu@57.129.125.68 "cd ~/Lexilogio && git pull && sudo systemctl restart lexilogio"
```

Schema changes: `db.create_all()` only creates missing tables — new *columns*
on existing tables need a manual `ALTER TABLE` (see `migrate_*.py` scripts).

## Backups

- Server: nightly cron (03:15) → gzipped SQLite `.backup` + verb-trainer JSON,
  7-day weekday rotation in `~/backups/`
- Offsite: weekly launchd job on the maintainer's Mac rsyncs `~/backups/` down
  (`~/Backups/lexilogio/pull_backups.sh`)

Restore: `gunzip lexilogio-DAY.db.gz`, replace `app/lexilogio.db`, restart.
