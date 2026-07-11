"""Auth flows: signup → verify → login, plus the abuse guards."""
import auth as auth_mod
from models import db, User
from conftest import make_user, login


def test_signup_creates_unverified_user(app, client):
    r = client.post("/auth/signup", data={
        "name": "Test", "email": "signup-test@local.test", "password": "longenough1"})
    assert r.status_code == 200
    with app.app_context():
        u = User.query.filter_by(email="signup-test@local.test").first()
        assert u is not None and not u.is_verified


def test_unverified_login_blocked(app, client):
    make_user(app, "unverified@local.test", verified=False)
    r = client.post("/auth/login", data={
        "email": "unverified@local.test", "password": "hunter2secret"})
    assert r.status_code == 400
    assert "verify your email" in r.get_data(as_text=True)


def test_verify_token_flow(app, client):
    make_user(app, "verifyme@local.test", verified=False)
    with app.test_request_context():
        token = auth_mod._make_token("verifyme@local.test", salt="email-verify")
    r = client.get(f"/auth/verify/{token}")
    assert r.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email="verifyme@local.test").first().is_verified


def test_login_wrong_password(app, client):
    make_user(app, "gooduser@local.test")
    r = client.post("/auth/login", data={
        "email": "gooduser@local.test", "password": "wrong-password"})
    assert r.status_code == 400
    assert "Incorrect email or password" in r.get_data(as_text=True)


def test_login_correct(app, client):
    make_user(app, "loginok@local.test")
    r = client.post("/auth/login", data={
        "email": "loginok@local.test", "password": "hunter2secret"})
    assert r.status_code == 302


def test_short_password_rejected(client):
    r = client.post("/auth/signup", data={
        "email": "shortpw@local.test", "password": "short"})
    assert "at least 8 characters" in r.get_data(as_text=True)


def test_login_rate_limit(app, client):
    make_user(app, "ratelimit@local.test")
    for _ in range(10):
        client.post("/auth/login", data={
            "email": "ratelimit@local.test", "password": "wrong"})
    r = client.post("/auth/login", data={
        "email": "ratelimit@local.test", "password": "hunter2secret"})
    assert "Too many attempts" in r.get_data(as_text=True)


def test_delete_account_requires_csrf_header(app, client):
    uid = make_user(app, "deleteme@local.test")
    login(client, uid)
    # plain POST (what a cross-site form could send) → refused
    r = client.post("/auth/delete-account")
    assert r.status_code == 400
    with app.app_context():
        assert User.query.get(uid) is not None
    # with the header → account gone
    r = client.post("/auth/delete-account",
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 302
    with app.app_context():
        assert User.query.get(uid) is None
