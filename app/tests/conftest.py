"""Shared fixtures. A throwaway SQLite file stands in for the real DB;
env vars are pinned before the app module is imported (load_dotenv does
not override pre-set values)."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lexilogio-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("RESEND_API_KEY", None)
os.environ.pop("TURNSTILE_SITE_KEY", None)
os.environ.pop("TURNSTILE_SECRET_KEY", None)

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import bcrypt

import app as mainapp
import auth as auth_mod
from models import db, User


@pytest.fixture()
def app():
    return mainapp.app


@pytest.fixture()
def client(app):
    # Rate-limit buckets are module-global; isolate tests from each other
    auth_mod._RATE_BUCKETS.clear()
    return app.test_client()


def make_user(app, email, password="hunter2secret", verified=True):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            user = User(email=email, password_hash=pw, is_verified=verified)
            db.session.add(user)
            db.session.commit()
        return user.id


def login(client, user_id):
    with client.session_transaction() as s:
        s["_user_id"] = str(user_id)
