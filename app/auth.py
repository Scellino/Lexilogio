"""
auth.py — Authentication blueprint.

Routes:
    GET  /auth/login            login page
    POST /auth/login            submit credentials
    GET  /auth/signup           signup page
    POST /auth/signup           create account
    GET  /auth/logout           log out
    GET  /auth/google           start Google OAuth flow
    GET  /auth/google/callback  Google OAuth callback
"""
import os
import json
import urllib.request
import urllib.parse
import bcrypt
from flask import (Blueprint, render_template_string, request,
                   redirect, url_for, flash, session)
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from models import db, User

auth_bp = Blueprint("auth", __name__)
oauth    = OAuth()

# Google client registered at module level; init_app() wires it to the Flask app.
_GOOGLE_ENABLED = bool(os.environ.get("GOOGLE_CLIENT_ID"))

# Cloudflare Turnstile — disabled when env vars are blank (local dev)
_TURNSTILE_SITE_KEY   = os.environ.get("TURNSTILE_SITE_KEY", "")
_TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
_TURNSTILE_ENABLED    = bool(_TURNSTILE_SITE_KEY and _TURNSTILE_SECRET_KEY)


def _verify_turnstile(token: str) -> bool:
    if not _TURNSTILE_ENABLED:
        return True
    data = urllib.parse.urlencode({
        "secret": _TURNSTILE_SECRET_KEY,
        "response": token,
    }).encode()
    try:
        with urllib.request.urlopen(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=data, timeout=5
        ) as resp:
            return json.loads(resp.read()).get("success", False)
    except Exception:
        return False


def _turnstile_widget() -> str:
    if not _TURNSTILE_ENABLED:
        return ""
    return (f'<div class="cf-turnstile" data-sitekey="{_TURNSTILE_SITE_KEY}"'
            f' style="margin:12px 0"></div>')

if _GOOGLE_ENABLED:
    oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
    )


# ── Shared HTML chrome ────────────────────────────────────────────────────────

_BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f1a;font-family:system-ui,sans-serif;color:#fff;
     min-height:100dvh;display:flex;flex-direction:column;align-items:center;
     justify-content:center;padding:24px}
a{color:#c9a96e;text-decoration:none}
a:hover{text-decoration:underline}
.logo{font-family:Georgia,serif;font-size:32px;color:#c9a96e;letter-spacing:2px;
      margin-bottom:4px;text-align:center}
.tagline{font-size:12px;color:rgba(255,255,255,.3);letter-spacing:1px;
         margin-bottom:36px;text-align:center}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
      border-radius:20px;padding:36px 32px;width:100%;max-width:400px}
.card h2{font-size:18px;font-weight:600;margin-bottom:24px;color:#fff;
         font-family:system-ui,sans-serif}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;color:rgba(255,255,255,.45);
             letter-spacing:.5px;margin-bottom:6px;font-family:system-ui,sans-serif}
.field input{width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
             border-radius:10px;padding:11px 14px;color:#fff;font-size:14px;
             font-family:system-ui,sans-serif;outline:none;transition:border-color .15s}
.field input:focus{border-color:rgba(201,169,110,.5)}
.btn-primary{width:100%;background:#c9a96e;color:#0f0f1a;border:none;border-radius:10px;
             padding:13px;font-size:14px;font-weight:700;cursor:pointer;
             font-family:system-ui,sans-serif;letter-spacing:.3px;margin-top:8px;
             transition:opacity .15s}
.btn-primary:hover{opacity:.85}
.divider{display:flex;align-items:center;gap:12px;margin:20px 0;
         color:rgba(255,255,255,.2);font-size:12px}
.divider::before,.divider::after{content:'';flex:1;height:1px;
                                  background:rgba(255,255,255,.1)}
.btn-google{width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
            border-radius:10px;padding:12px;font-size:14px;color:#fff;cursor:pointer;
            font-family:system-ui,sans-serif;display:flex;align-items:center;
            justify-content:center;gap:10px;transition:background .15s}
.btn-google:hover{background:rgba(255,255,255,.1)}
.footer-link{margin-top:20px;text-align:center;font-size:13px;
             color:rgba(255,255,255,.35)}
.flash{background:rgba(220,60,60,.15);border:1px solid rgba(220,60,60,.3);
       border-radius:10px;padding:10px 14px;font-size:13px;color:#ff8a8a;
       margin-bottom:16px;font-family:system-ui,sans-serif}
"""

_GOOGLE_ICON = """<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
<path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
<path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
<path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
<path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
</svg>"""


def _page(title, body, flash_msg=""):
    flash_html = f'<div class="flash">{flash_msg}</div>' if flash_msg else ""
    google_btn = ""
    if _GOOGLE_ENABLED:
        google_btn = f"""
        <div class="divider">or</div>
        <form action="/auth/google" method="get">
          <button type="submit" class="btn-google">{_GOOGLE_ICON} Continue with Google</button>
        </form>"""
    turnstile_script = (
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"'
        ' async defer></script>'
    ) if _TURNSTILE_ENABLED else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Λεξιλόγιο</title>
<style>{_BASE_CSS}</style>
{turnstile_script}
</head><body>
<div class="logo">Λεξιλόγιο</div>
<div class="tagline">Language Trainer</div>
<div class="card">
  <h2>{title}</h2>
  {flash_html}
  {body}
  {google_btn}
</div>
</body></html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/")

    error = ""
    if request.method == "POST":
        if not _verify_turnstile(request.form.get("cf-turnstile-response", "")):
            error = "CAPTCHA verification failed. Please try again."
        else:
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.password_hash and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
                login_user(user, remember=True)
                return redirect(request.args.get("next") or "/")
            error = "Incorrect email or password."

    body = f"""
    <form method="post">
      <div class="field"><label>Email</label>
        <input type="email" name="email" required autofocus placeholder="you@example.com"></div>
      <div class="field"><label>Password</label>
        <input type="password" name="password" required placeholder="••••••••"></div>
      {_turnstile_widget()}
      <button type="submit" class="btn-primary">Sign in</button>
    </form>"""
    return _page("Sign in", body, error), (400 if error else 200)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect("/")

    error = ""
    if request.method == "POST":
        if not _verify_turnstile(request.form.get("cf-turnstile-response", "")):
            error = "CAPTCHA verification failed. Please try again."
        else:
            name     = request.form.get("name", "").strip()
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if len(password) < 8:
                error = "Password must be at least 8 characters."
            elif User.query.filter_by(email=email).first():
                error = "An account with that email already exists."
            else:
                pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                user = User(email=email, name=name or None, password_hash=pw_hash)
                db.session.add(user)
                db.session.commit()
                login_user(user, remember=True)
                return redirect("/")

    body = f"""
    <form method="post">
      <div class="field"><label>Name (optional)</label>
        <input type="text" name="name" placeholder="Your name"></div>
      <div class="field"><label>Email</label>
        <input type="email" name="email" required placeholder="you@example.com"></div>
      <div class="field"><label>Password</label>
        <input type="password" name="password" required placeholder="At least 8 characters"></div>
      {_turnstile_widget()}
      <button type="submit" class="btn-primary">Create account</button>
    </form>
    <div class="footer-link">Already have an account? <a href="/auth/login">Sign in</a></div>"""
    return _page("Create account", body, error), (400 if error else 200)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


# ── Google OAuth ──────────────────────────────────────────────────────────────

@auth_bp.route("/google")
def google_login():
    if not _GOOGLE_ENABLED:
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    if not _GOOGLE_ENABLED:
        return redirect(url_for("auth.login"))
    token = oauth.google.authorize_access_token()
    info  = token.get("userinfo") or oauth.google.userinfo()
    google_id = info["sub"]
    email     = info.get("email", "").lower()
    name      = info.get("name", "")

    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id  # link Google to existing email account
        else:
            user = User(email=email, name=name, google_id=google_id)
            db.session.add(user)
        db.session.commit()

    login_user(user, remember=True)
    return redirect(session.pop("next_url", "/"))
