"""
app.py — Lexilogio (multi-language trainer)
===========================================
Serves vocab trainers, the verb trainer, auth, and admin under one Flask app.

    /          → landing page
    /vocab/... → Greek vocab trainer
    /fr/vocab/ → French vocab trainer
    /nl/vocab/ → Dutch vocab trainer
    /verb/...  → Greek verb conjugation trainer
    /auth/...  → login / signup / Google OAuth
    /admin/... → admin review queue (is_admin only)

Config via environment variables (see .env.example):
    SECRET_KEY       — Flask session key (required in production)
    DATABASE_URL     — SQLAlchemy URL (default: sqlite:///lexilogio.db)
    GOOGLE_CLIENT_ID     — enable Google OAuth
    GOOGLE_CLIENT_SECRET

Usage:
    python app.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, redirect, request, send_from_directory, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import LoginManager, current_user
from models import db, User
from auth import auth_bp, oauth
from admin import admin_bp
from el_vocab_app import el_vocab_bp
from verb_app import verb_bp
from fr_vocab_app import fr_vocab_bp
from nl_vocab_app import nl_vocab_bp
from it_vocab_app import it_vocab_bp
from es_vocab_app import es_vocab_bp
from de_vocab_app import de_vocab_bp
from community_bp import community_bp
from preset_loader import load_presets
from generic_verb_bp import make_verb_blueprint
fr_verb_bp = make_verb_blueprint("fr")
de_verb_bp = make_verb_blueprint("de")
it_verb_bp = make_verb_blueprint("it")
es_verb_bp = make_verb_blueprint("es")
nl_verb_bp = make_verb_blueprint("nl")

_DIR = Path(__file__).parent

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SECRET_KEY"]             = os.environ.get("SECRET_KEY", "dev-secret-CHANGE-IN-PROD")
app.config["SQLALCHEMY_DATABASE_URI"]= os.environ.get("DATABASE_URL", f"sqlite:///{_DIR / 'lexilogio.db'}")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

oauth.init_app(app)

app.register_blueprint(auth_bp,     url_prefix="/auth")
app.register_blueprint(admin_bp,    url_prefix="/admin")
app.register_blueprint(el_vocab_bp, url_prefix="/vocab")
app.register_blueprint(verb_bp,     url_prefix="/verb")
app.register_blueprint(fr_vocab_bp, url_prefix="/fr/vocab")
app.register_blueprint(nl_vocab_bp, url_prefix="/nl/vocab")
app.register_blueprint(it_vocab_bp, url_prefix="/it/vocab")
app.register_blueprint(es_vocab_bp, url_prefix="/es/vocab")
app.register_blueprint(de_vocab_bp,   url_prefix="/de/vocab")
app.register_blueprint(community_bp,  url_prefix="/community")
app.register_blueprint(fr_verb_bp,    url_prefix="/fr/verb")
app.register_blueprint(de_verb_bp,    url_prefix="/de/verb")
app.register_blueprint(it_verb_bp,    url_prefix="/it/verb")
app.register_blueprint(es_verb_bp,    url_prefix="/es/verb")
app.register_blueprint(nl_verb_bp,    url_prefix="/nl/verb")

with app.app_context():
    db.create_all()
    load_presets(app)

HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Λεξιλόγιο</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f0f1a">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Λεξιλόγιο">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100dvh;
    background: #0f0f1a;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: system-ui, sans-serif;
    color: #fff;
    padding: 24px;
    padding-bottom: calc(24px + env(safe-area-inset-bottom, 0px));
  }
  .logo {
    font-family: Georgia, serif;
    font-size: 36px;
    color: #c9a96e;
    letter-spacing: 2px;
    margin-bottom: 6px;
    text-align: center;
  }
  .tagline {
    font-size: 13px;
    color: rgba(255,255,255,.35);
    letter-spacing: 1px;
    margin-bottom: 24px;
    text-align: center;
  }
  .dep-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 32px;
    flex-wrap: wrap;
    justify-content: center;
  }
  .dep-label {
    font-size: 12px;
    color: rgba(255,255,255,.3);
    font-family: system-ui, sans-serif;
    letter-spacing: .5px;
  }
  .dep-flag {
    background: none;
    border: 1px solid rgba(255,255,255,.1);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: rgba(255,255,255,.45);
    cursor: pointer;
    transition: all .15s;
    font-family: system-ui, sans-serif;
  }
  .dep-flag:hover { background: rgba(255,255,255,.07); color: #fff; }
  .dep-flag.active {
    background: rgba(201,169,110,.15);
    border-color: rgba(201,169,110,.5);
    color: #c9a96e;
  }
  .cards {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    justify-content: center;
  }
  .app-card {
    background: rgba(255,255,255,.04);
    border: 1px solid rgba(255,255,255,.09);
    border-radius: 16px;
    padding: 32px 28px;
    width: 220px;
    text-align: center;
    text-decoration: none;
    color: inherit;
    transition: background .15s, border-color .15s, transform .15s;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .app-card:hover {
    background: rgba(255,255,255,.08);
    border-color: rgba(201,169,110,.4);
    transform: translateY(-2px);
  }
  .app-card .icon { font-size: 40px; margin-bottom: 14px; display: block; }
  .app-card .name {
    font-size: 15px;
    font-weight: 700;
    color: #c9a96e;
    margin-bottom: 8px;
    letter-spacing: .5px;
  }
  .app-card .desc {
    font-size: 12px;
    color: rgba(255,255,255,.35);
    line-height: 1.5;
  }
  /* Language step: larger flag cards */
  .lang-card {
    background: rgba(255,255,255,.04);
    border: 1px solid rgba(255,255,255,.09);
    border-radius: 20px;
    padding: 36px 32px;
    width: 180px;
    text-align: center;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background .15s, border-color .15s, transform .15s;
    user-select: none;
  }
  .lang-card:hover {
    background: rgba(255,255,255,.08);
    border-color: rgba(201,169,110,.4);
    transform: translateY(-3px);
  }
  .lang-card .flag { font-size: 52px; display: block; margin-bottom: 12px; }
  .lang-card .lang-name {
    font-size: 14px;
    font-weight: 700;
    color: #c9a96e;
    letter-spacing: .5px;
    margin-bottom: 4px;
  }
  .lang-card .lang-sub {
    font-size: 11px;
    color: rgba(255,255,255,.25);
    letter-spacing: .3px;
  }
  /* Back button */
  .back-btn {
    background: transparent;
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 10px;
    color: rgba(255,255,255,.4);
    font-size: 12px;
    font-family: system-ui, sans-serif;
    padding: 7px 16px;
    cursor: pointer;
    margin-bottom: 36px;
    letter-spacing: .5px;
    -webkit-tap-highlight-color: transparent;
    transition: color .15s, border-color .15s;
  }
  .back-btn:hover { color: rgba(201,169,110,.8); border-color: rgba(201,169,110,.3); }
  /* Step label */
  .step-label {
    font-size: 10px;
    color: rgba(255,255,255,.2);
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 28px;
    text-align: center;
  }
  .welcome {
    max-width: 480px;
    text-align: center;
    font-size: 13px;
    color: rgba(255,255,255,.28);
    line-height: 1.65;
    margin-bottom: 36px;
    letter-spacing: .1px;
  }
  .footer {
    margin-top: 56px;
    font-size: 11px;
    color: rgba(255,255,255,.15);
    letter-spacing: .5px;
  }
  a { -webkit-tap-highlight-color: transparent; }
  /* ── Hamburger menu ── */
  .menu-btn{position:fixed;top:8px;right:8px;z-index:200;background:transparent;border:1px solid rgba(255,255,255,.12);border-radius:9px;color:rgba(255,255,255,.45);font-size:18px;line-height:1;padding:5px 10px;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:color .15s,border-color .15s;font-family:system-ui,sans-serif}
  .menu-btn:hover{color:#c9a96e;border-color:rgba(201,169,110,.35)}
  .menu-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:300;backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px)}
  .menu-overlay.open{display:block}
  .menu-drawer{position:fixed;top:0;right:-300px;width:270px;height:100dvh;background:#111128;border-left:1px solid rgba(255,255,255,.08);z-index:310;display:flex;flex-direction:column;transition:right .25s cubic-bezier(.4,0,.2,1);overflow-y:auto;-webkit-overflow-scrolling:touch}
  .menu-drawer.open{right:0}
  .menu-spacer{height:52px;flex-shrink:0}
  .menu-divider{height:1px;background:rgba(255,255,255,.06);margin:4px 0;flex-shrink:0}
  .menu-item{display:flex;align-items:center;gap:10px;padding:13px 20px;font-size:13px;font-family:system-ui,sans-serif;color:rgba(255,255,255,.6);text-decoration:none;transition:color .15s,background .15s;-webkit-tap-highlight-color:transparent}
  .menu-item:hover,.menu-item:active{color:#c9a96e;background:rgba(201,169,110,.06)}
  .menu-item.cta{color:#c9a96e;font-weight:600}
  .menu-item-icon{font-size:15px;width:22px;text-align:center;flex-shrink:0;line-height:1}
  @media(max-width:430px) {
    .logo { font-size: 28px; }
    .cards { gap: 14px; }
    .app-card { width: 100%; max-width: 340px; padding: 24px 20px; }
    .lang-card { width: 140px; padding: 28px 18px; }
    .lang-card .flag { font-size: 44px; }
  }
</style>
</head>
<body>
  <button class="menu-btn" onclick="toggleMenu()" aria-label="Menu">&#9776;</button>
  <div class="menu-overlay" id="menu-overlay" onclick="toggleMenu()"></div>
  <nav class="menu-drawer" id="menu-drawer" aria-label="Navigation">
    <div class="menu-spacer"></div>
    <div id="menu-account"></div>
    <div class="menu-divider"></div>
    <a href="/about" class="menu-item"><span class="menu-item-icon">&#127760;</span>About Lexilogio</a>
    <a href="/tutorial" class="menu-item"><span class="menu-item-icon">&#128218;</span>Tutorial</a>
    <a href="/donate" class="menu-item"><span class="menu-item-icon">&#9749;</span>Donate</a>
    <a href="/impressum" class="menu-item"><span class="menu-item-icon">&#128196;</span>Legal Notice</a>
  </nav>
  <div class="logo">Λεξιλόγιο</div>
  <div class="tagline" id="tagline">Language Trainer</div>

  <p class="welcome">Welcome to Lexilogio, the vocabulary trainer built to help you improve your language skills. No ads, no paywall, no notifications. Just a clean way to drill the words you want to study. What distinguishes Lexilogio is its simple but effective quiz interface and an easy way to create custom flashcards. If you want to add many words at once, go to the bulk add section, copy the Lexilogio prompt into any chatbot, add your word list, and copy the output back. You will have a set of beautiful flashcards ready to study in no time. Additionally, I am currently working on adding curated card presets you can use to get going right away.</p>

  <!-- Departure language selector (logged-in users only) -->
  <div class="dep-bar" id="dep-bar" style="display:none">
    <span class="dep-label">I speak:</span>
    <button class="dep-flag" data-dep="en"  onclick="setDep('en')">🇬🇧 English</button>
    <button class="dep-flag" data-dep="de"  onclick="setDep('de')">🇩🇪 Deutsch</button>
    <button class="dep-flag" data-dep="el"  onclick="setDep('el')">🇬🇷 Ελληνικά</button>
  </div>

  <!-- Step 1: language picker -->
  <div class="cards" id="step-lang">
    <div class="lang-card" id="lang-card-el" onclick="pickLang('el')">
      <span class="flag">🇬🇷</span>
      <div class="lang-name">Greek</div>
      <div class="lang-sub">Ελληνικά</div>
    </div>
    <div class="lang-card" onclick="pickLang('it')">
      <span class="flag">🇮🇹</span>
      <div class="lang-name">Italian</div>
      <div class="lang-sub">Italiano</div>
    </div>
    <div class="lang-card" onclick="pickLang('es')">
      <span class="flag">🇪🇸</span>
      <div class="lang-name">Spanish</div>
      <div class="lang-sub">Español</div>
    </div>
    <div class="lang-card" id="lang-card-de" onclick="pickLang('de')">
      <span class="flag">🇩🇪</span>
      <div class="lang-name">German</div>
      <div class="lang-sub">Deutsch</div>
    </div>
    <div class="lang-card" onclick="pickLang('fr')">
      <span class="flag">🇫🇷</span>
      <div class="lang-name">French</div>
      <div class="lang-sub">Français</div>
    </div>
    <div class="lang-card" onclick="pickLang('nl')">
      <span class="flag">🇳🇱</span>
      <div class="lang-name">Dutch</div>
      <div class="lang-sub">Nederlands</div>
    </div>
  </div>

  <!-- Step 2: app picker (hidden until language chosen) -->
  <div id="step-apps" style="display:none;flex-direction:column;align-items:center;width:100%">
    <button class="back-btn" onclick="goBack()">← Back</button>
    <div class="step-label" id="apps-label"></div>
    <div class="cards" id="apps-cards"></div>
  </div>

  <div class="footer">lexilogio.org</div>

<script>
/* __HOME_USER__ */
const LANGS = {
  el: {
    label: 'Greek · Ελληνικά',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/vocab/',
        desc: 'Flashcards &amp; quizzes for Greek vocabulary, phrases, and grammar' },
      { icon: '🔤', name: 'Verb Trainer',  href: '/verb/',
        desc: 'Conjugation practice across all tenses for 600+ Greek verbs' },
    ]
  },
  fr: {
    label: 'French · Français',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/fr/vocab/',
        desc: 'Flashcards &amp; quizzes for French vocabulary and grammar' },
      { icon: '🔤', name: 'Verb Trainer',  href: '/fr/verb/',
        desc: 'Conjugation practice across all tenses for 495 French verbs' },
    ]
  },
  nl: {
    label: 'Dutch · Nederlands',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/nl/vocab/',
        desc: 'Flashcards &amp; quizzes for Dutch vocabulary and grammar' },
    ]
  },
  it: {
    label: 'Italian · Italiano',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/it/vocab/',
        desc: 'Flashcards &amp; quizzes for Italian vocabulary and grammar' },
    ]
  },
  es: {
    label: 'Spanish · Español',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/es/vocab/',
        desc: 'Flashcards &amp; quizzes for Spanish vocabulary and grammar' },
    ]
  },
  de: {
    label: 'German · Deutsch',
    apps: [
      { icon: '📖', name: 'Vocab Trainer', href: '/de/vocab/',
        desc: 'Flashcards &amp; quizzes for German vocabulary and grammar' },
    ]
  },
};

function pickLang(code) {
  const lang = LANGS[code];
  document.getElementById('step-lang').style.display = 'none';
  document.getElementById('tagline').textContent = lang.label;

  const appsLabel = document.getElementById('apps-label');
  appsLabel.textContent = 'Choose a trainer';

  const appsCards = document.getElementById('apps-cards');
  appsCards.innerHTML = '';
  lang.apps.forEach(a => {
    const el = document.createElement('a');
    el.className = 'app-card';
    el.href = a.href;
    el.innerHTML = '<span class="icon">' + a.icon + '</span>' +
                   '<div class="name">' + a.name + '</div>' +
                   '<div class="desc">' + a.desc + '</div>';
    appsCards.appendChild(el);
  });

  const stepApps = document.getElementById('step-apps');
  stepApps.style.display = 'flex';
}

function goBack() {
  document.getElementById('step-apps').style.display = 'none';
  document.getElementById('step-lang').style.display = 'flex';
  document.getElementById('tagline').textContent = 'Language Trainer';
}

function toggleMenu() {
  document.getElementById('menu-drawer').classList.toggle('open');
  document.getElementById('menu-overlay').classList.toggle('open');
}

(function _initHomeMenu(){
  const mac = document.getElementById('menu-account');
  if (!mac) return;
  if (HOME_USER.guest) {
    mac.innerHTML =
      '<a href="/auth/login" class="menu-item cta"><span class="menu-item-icon">&#128100;</span>Sign in</a>' +
      '<a href="/auth/signup" class="menu-item"><span class="menu-item-icon">&#10133;</span>Create account</a>';
  } else {
    const name = HOME_USER.name || HOME_USER.email || '';
    mac.innerHTML =
      `<div style="padding:12px 20px 4px;font-size:13px;font-family:sans-serif;color:rgba(255,255,255,.4)">&#128100; ${name}</div>` +
      '<a href="/profile" class="menu-item"><span class="menu-item-icon">&#128202;</span>My Stats</a>' +
      '<a href="/auth/logout" class="menu-item"><span class="menu-item-icon">&#128682;</span>Sign out</a>';
  }
})();

function _syncDepUI(code) {
  document.querySelectorAll('.dep-flag').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`.dep-flag[data-dep="${code}"]`);
  if (btn) btn.classList.add('active');
  const deCard = document.getElementById('lang-card-de');
  if (deCard) deCard.style.display = code === 'de' ? 'none' : '';
  const elCard = document.getElementById('lang-card-el');
  if (elCard) elCard.style.display = code === 'el' ? 'none' : '';
}

// Departure language bar
(function _initDepBar(){
  if (HOME_USER.guest) return;
  const bar = document.getElementById('dep-bar');
  if (!bar) return;
  bar.style.display = 'flex';
  _syncDepUI(HOME_USER.departure_lang || 'en');
})();

function setDep(code) {
  fetch('/auth/departure', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({departure_lang: code})
  }).then(r => r.json()).then(() => {
    HOME_USER.departure_lang = code;
    _syncDepUI(code);
  });
}
</script>
</body>
</html>"""


@app.before_request
def touch_last_seen():
    if current_user.is_authenticated:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        if not current_user.last_seen or now - current_user.last_seen > timedelta(minutes=5):
            current_user.last_seen = now
            from models import db
            db.session.commit()

@app.after_request
def set_cache(resp):
    # Static assets can be cached; HTML pages must not (prevents iOS PWA stale views)
    if request.path.startswith("/icons/") or request.path == "/manifest.json":
        resp.headers["Cache-Control"] = "public, max-age=3600"
    else:
        resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/")
def home():
    import json as _json
    if current_user.is_authenticated:
        user_js = _json.dumps({
            "guest": False,
            "name": current_user.name,
            "email": current_user.email,
            "departure_lang": current_user.departure_lang or 'en',
        })
    else:
        user_js = '{"guest":true}'
    return HOME_HTML.replace("/* __HOME_USER__ */", f"const HOME_USER={user_js};")


@app.route("/profile")
def profile():
    import json as _json
    from flask_login import current_user as cu
    if not cu.is_authenticated:
        return redirect("/auth/login")
    from models import Progress
    LANG_NAMES = {
        'el': ('Greek',   '🇬🇷'),
        'fr': ('French',  '🇫🇷'),
        'nl': ('Dutch',   '🇳🇱'),
        'it': ('Italian', '🇮🇹'),
        'es': ('Spanish', '🇪🇸'),
        'de': ('German',  '🇩🇪'),
    }
    rows = Progress.query.filter_by(user_id=cu.id).all()
    stats = {}
    for r in rows:
        lang = r.lang_code
        if lang not in LANG_NAMES:
            continue
        if lang not in stats:
            stats[lang] = {'seen': 0, 'mastered': 0}
        stats[lang]['seen'] += 1
        rw = _json.loads(r.rev_window or '[]')
        rn = len(rw)
        racc = sum(rw) / rn if rn else 0
        sd = r.spaced_days or 0
        if rn >= 5 and racc >= 0.8 and sd >= 3:
            stats[lang]['mastered'] += 1
    name = cu.name or cu.email or ''
    rows_html = ''
    for lang, (lname, flag) in LANG_NAMES.items():
        s = stats.get(lang, {'seen': 0, 'mastered': 0})
        rows_html += f'''<tr>
          <td class="lang-cell">{flag} {lname}</td>
          <td class="num-cell">{s["seen"]}</td>
          <td class="num-cell mastered">{s["mastered"]}</td>
        </tr>'''
    total_seen     = sum(s['seen']     for s in stats.values())
    total_mastered = sum(s['mastered'] for s in stats.values())
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Stats · Λεξιλόγιο</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f0f1a;font-family:system-ui,sans-serif;color:#fff;
     min-height:100dvh;display:flex;flex-direction:column;align-items:center;
     justify-content:flex-start;padding:48px 24px 40px}}
.logo{{font-family:Georgia,serif;font-size:28px;color:#c9a96e;margin-bottom:8px;text-align:center}}
.sub{{font-size:13px;color:rgba(255,255,255,.3);margin-bottom:36px;text-align:center}}
.card{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
       border-radius:20px;padding:32px;width:100%;max-width:440px}}
h2{{font-size:15px;font-weight:600;color:rgba(255,255,255,.5);
    text-transform:uppercase;letter-spacing:1.5px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;font-size:11px;color:rgba(255,255,255,.25);
    text-transform:uppercase;letter-spacing:1px;padding:0 0 12px;font-weight:400}}
th.num-cell{{text-align:right}}
td{{padding:10px 0;border-top:1px solid rgba(255,255,255,.06);font-size:14px}}
td.lang-cell{{color:rgba(255,255,255,.8)}}
td.num-cell{{text-align:right;color:rgba(255,255,255,.35);font-variant-numeric:tabular-nums}}
td.num-cell.mastered{{color:#7ac49a;font-weight:600}}
.totals{{margin-top:24px;padding-top:20px;border-top:1px solid rgba(255,255,255,.1);
         display:flex;gap:32px;justify-content:flex-end}}
.tot-item{{text-align:right}}
.tot-num{{font-size:26px;font-weight:700;color:#c9a96e;line-height:1}}
.tot-lbl{{font-size:11px;color:rgba(255,255,255,.3);margin-top:4px;letter-spacing:.5px}}
.tot-num.mastered{{color:#7ac49a}}
.back{{margin-top:28px;font-size:13px;color:rgba(201,169,110,.55);text-decoration:none;
       display:inline-block}}
.back:hover{{color:#c9a96e}}
.danger-zone{{margin-top:16px;width:100%;max-width:440px;text-align:right}}
.btn-delete{{background:none;border:none;font-size:12px;color:rgba(212,122,143,.45);
             cursor:pointer;padding:4px 0;letter-spacing:.3px}}
.btn-delete:hover{{color:#d47a8f}}
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
                z-index:100;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal{{background:#1a1a2e;border:1px solid rgba(255,255,255,.12);border-radius:16px;
        padding:32px;max-width:360px;width:90%;text-align:center}}
.modal h3{{font-size:16px;color:#fff;margin-bottom:10px}}
.modal p{{font-size:13px;color:rgba(255,255,255,.45);line-height:1.6;margin-bottom:24px}}
.modal-btns{{display:flex;gap:12px;justify-content:center}}
.btn-cancel{{padding:10px 22px;border-radius:8px;border:1px solid rgba(255,255,255,.15);
             background:none;color:rgba(255,255,255,.6);font-size:14px;cursor:pointer}}
.btn-confirm-delete{{padding:10px 22px;border-radius:8px;border:none;
                     background:#d47a8f;color:#fff;font-size:14px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="logo">Λεξιλόγιο</div>
<div class="sub">&#128100; {name}</div>
<div class="card">
  <h2>Vocabulary Progress</h2>
  <table>
    <thead><tr>
      <th>Language</th>
      <th class="num-cell">Seen</th>
      <th class="num-cell">Mastered</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <div class="totals">
    <div class="tot-item">
      <div class="tot-num">{total_seen}</div>
      <div class="tot-lbl">Words seen</div>
    </div>
    <div class="tot-item">
      <div class="tot-num mastered">{total_mastered}</div>
      <div class="tot-lbl">Mastered</div>
    </div>
  </div>
</div>
<a href="/" class="back">← Back to home</a>
<div class="danger-zone">
  <button class="btn-delete" onclick="document.getElementById('del-modal').classList.add('open')">
    Delete my account
  </button>
</div>
<div class="modal-overlay" id="del-modal">
  <div class="modal">
    <h3>Delete account?</h3>
    <p>This will permanently delete your account and all your progress data. There's no undo.</p>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="document.getElementById('del-modal').classList.remove('open')">Cancel</button>
      <form method="POST" action="/auth/delete-account" style="display:inline">
        <button type="submit" class="btn-confirm-delete">Yes, delete</button>
      </form>
    </div>
  </div>
</div>
</body></html>"""


@app.route("/manifest.json")
def manifest():
    return send_from_directory(_DIR, "manifest.json", mimetype="application/manifest+json")


@app.route("/icons/<path:filename>")
def icons(filename):
    return send_from_directory(_DIR / "icons", filename)


@app.route("/vocab")
def vocab_redirect():
    return redirect("/vocab/")


@app.route("/verb")
def verb_redirect():
    return redirect("/verb/")


@app.route("/fr/vocab")
def fr_vocab_redirect():
    return redirect("/fr/vocab/")


@app.route("/nl/vocab")
def nl_vocab_redirect():
    return redirect("/nl/vocab/")


@app.route("/it/vocab")
def it_vocab_redirect():
    return redirect("/it/vocab/")


@app.route("/es/vocab")
def es_vocab_redirect():
    return redirect("/es/vocab/")


@app.route("/de/vocab")
def de_vocab_redirect():
    return redirect("/de/vocab/")


@app.route("/settings")
def settings():
    from flask_login import current_user as cu
    dep = (cu.departure_lang or 'en') if cu.is_authenticated else 'en'
    dep_names = {'en':'English','de':'German','el':'Greek'}
    options = [
        ('en','🇬🇧 English'), ('de','🇩🇪 Deutsch'), ('el','🇬🇷 Ελληνικά'),
    ]
    btns = ''.join(
        f'<button class="dep-btn{" active" if code==dep else ""}" onclick="setDep(\'{code}\')">{label}</button>'
        for code, label in options
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings · Λεξιλόγιο</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f0f1a;font-family:system-ui,sans-serif;color:#fff;
     min-height:100dvh;display:flex;flex-direction:column;align-items:center;
     justify-content:center;padding:24px}}
.logo{{font-family:Georgia,serif;font-size:28px;color:#c9a96e;margin-bottom:32px}}
.card{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
       border-radius:16px;padding:32px;width:100%;max-width:400px}}
h2{{font-size:16px;font-weight:600;margin-bottom:6px}}
p{{font-size:13px;color:rgba(255,255,255,.4);margin-bottom:20px;line-height:1.5}}
.dep-btn{{display:block;width:100%;text-align:left;background:rgba(255,255,255,.04);
          border:1px solid rgba(255,255,255,.1);border-radius:10px;
          padding:12px 16px;font-size:14px;color:rgba(255,255,255,.6);
          cursor:pointer;margin-bottom:8px;transition:all .15s;font-family:inherit}}
.dep-btn:hover{{background:rgba(255,255,255,.08);color:#fff}}
.dep-btn.active{{background:rgba(201,169,110,.12);border-color:rgba(201,169,110,.4);color:#c9a96e;font-weight:600}}
.back{{margin-top:20px;font-size:13px;color:rgba(201,169,110,.6);text-decoration:none}}
.back:hover{{text-decoration:underline}}
.toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
        background:#2a2a3a;border:1px solid rgba(255,255,255,.12);border-radius:10px;
        padding:10px 20px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none}}
</style></head><body>
<div class="logo">🧿 Λεξιλόγιο</div>
<div class="card">
  <h2>Language I speak</h2>
  <p>Cards and quizzes will be in the language you choose.</p>
  <div id="dep-btns">{btns}</div>
  <a href="/" class="back">← Back to home</a>
</div>
<div class="toast" id="toast">Saved ✓</div>
<script>
function setDep(code){{
  fetch('/auth/departure',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{departure_lang:code}})}})
    .then(()=>{{
      document.querySelectorAll('.dep-btn').forEach(b=>b.classList.remove('active'));
      const b=document.querySelector('.dep-btn[onclick*="\\'' +code+ '\\'"]');
      if(b)b.classList.add('active');
      const t=document.getElementById('toast');
      t.style.opacity='1';setTimeout(()=>t.style.opacity='0',1500);
    }});
}}
</script>
</body></html>"""


@app.route("/sitemap.xml")
def sitemap():
    base = "https://lexilogio.org"
    urls = [
        (base + "/",           "weekly", "1.0"),
        (base + "/vocab/",     "weekly", "0.9"),
        (base + "/fr/vocab/",  "weekly", "0.9"),
        (base + "/nl/vocab/",  "weekly", "0.9"),
        (base + "/it/vocab/",  "weekly", "0.9"),
        (base + "/es/vocab/",  "weekly", "0.9"),
        (base + "/de/vocab/",  "weekly", "0.9"),
        (base + "/verb/",      "weekly", "0.9"),
        (base + "/community",   "weekly",  "0.8"),
        (base + "/auth/login",  "monthly", "0.5"),
        (base + "/auth/signup", "monthly", "0.5"),
        (base + "/about",       "monthly", "0.4"),
        (base + "/tutorial",    "monthly", "0.4"),
        (base + "/donate",      "yearly",  "0.3"),
        (base + "/impressum",   "yearly",  "0.3"),
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, pri in urls:
        lines.append(f"  <url><loc>{loc}</loc>"
                     f"<changefreq>{freq}</changefreq>"
                     f"<priority>{pri}</priority></url>")
    lines.append("</urlset>")
    return Response("\n".join(lines), mimetype="application/xml")


_IMPRESSUM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Impressum · Λεξιλόγιο</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100dvh;
    background: #0f0f1a;
    font-family: system-ui, sans-serif;
    color: #fff;
    padding: 48px 24px;
    max-width: 600px;
    margin: 0 auto;
  }
  h1 { font-family: Georgia, serif; color: #c9a96e; font-size: 28px;
       letter-spacing: 1px; margin-bottom: 32px; }
  h2 { font-size: 13px; color: rgba(255,255,255,.4); letter-spacing: 2px;
       text-transform: uppercase; margin: 28px 0 10px; }
  p, address { font-size: 15px; color: rgba(255,255,255,.75); line-height: 1.7;
               font-style: normal; }
  a { color: #c9a96e; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .back { display: inline-block; margin-bottom: 36px; font-size: 13px;
          color: rgba(255,255,255,.35); }
  .back:hover { color: #c9a96e; }
  .note { font-size: 13px; color: rgba(255,255,255,.35); margin-top: 40px;
          border-top: 1px solid rgba(255,255,255,.07); padding-top: 24px; line-height: 1.6; }
</style>
</head>
<body>
  <a class="back" href="/">🧿 Λεξιλόγιο</a>
  <h1>Impressum</h1>

  <h2>Responsible for this website</h2>
  <address>
    Christoph Schilling<br>
    Gorderlweg 47B<br>
    3037 AD Rotterdam<br>
    Netherlands
  </address>

  <h2>Contact</h2>
  <p><a href="mailto:info@lexilogio.org">info@lexilogio.org</a></p>

  <h2>Purpose</h2>
  <p>Λεξιλόγιο is a non-commercial language learning platform offered free of charge.
     Voluntary donations help cover server and maintenance costs.</p>

  <p class="note">
    This website does not pursue commercial purposes. No goods or services are sold.
    Donations are voluntary and grant no rights to specific services or features.
  </p>
</body>
</html>"""


@app.route("/impressum")
def impressum():
    return _IMPRESSUM_HTML


def _stub_page(title, icon, heading, body_html):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Λεξιλόγιο</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ min-height: 100dvh; background: #0f0f1a; font-family: system-ui, sans-serif;
          color: #fff; padding: 48px 24px; max-width: 600px; margin: 0 auto; }}
  h1 {{ font-family: Georgia, serif; color: #c9a96e; font-size: 28px;
        letter-spacing: 1px; margin-bottom: 32px; }}
  p {{ font-size: 15px; color: rgba(255,255,255,.65); line-height: 1.8; margin-bottom: 22px; }}
  .back {{ display: inline-block; margin-bottom: 36px; font-size: 13px;
           color: rgba(255,255,255,.35); text-decoration: none; }}
  .back:hover {{ color: #c9a96e; }}
  .coming {{ display: inline-block; margin-top: 8px; padding: 6px 14px;
             border: 1px solid rgba(201,169,110,.25); border-radius: 8px;
             font-size: 12px; color: rgba(201,169,110,.6); font-family: sans-serif;
             letter-spacing: .5px; }}
</style>
</head>
<body>
  <a class="back" href="/">🧿 Λεξιλόγιο</a>
  <h1>{icon} {heading}</h1>
  {body_html}
</body>
</html>"""


@app.route("/about")
def about():
    return _stub_page("About", "🌐", "About Lexilogio",
        """<p>Hi there! I&#8217;m so glad you&#8217;re using this app, and I hope you&#8217;re getting a lot out of it.
        This is a passion project for me because I love learning languages, and I wanted to make
        &#8220;the boring parts&#8221; easier and more fun.</p>

        <p>When it comes to learning languages, I&#8217;m more of an intuitive learner; I don&#8217;t spend much time
        in courses. I try to absorb media like music and texts. But of course, some study of grammar and vocabulary
        is necessary. I think there are many vocab apps out there, and I&#8217;m sure many can do what I built here,
        but I just wanted a pretty, minimalist and functional app that puts users in charge of their learning
        experience. Here you can collect words you come across and repeat them until they feel they&#8217;ve mastered
        them. All of this without gamification, reminders or goals beyond the ones you set yourselves. I believe the
        feeling of agency that comes from going back to doing something like learning a language without being nudged
        is valuable and I&#8217;d like to foster it if I can.</p>

        <p>This app is completely free; I won&#8217;t put ads anywhere. The only thing I hope is that, if you find it
        useful, you can donate to help cover server costs. And if this app grows, I&#8217;d love to build on
        top of it. For me, this is very much a hobby project. This app was built with the assistance of AI, and as
        such I am obviously relying on the work of many others. Even though I find the ethics of AI a difficult
        topic, I believe that if you can use a technology to create something non-commercial that empowers people
        (in its own small way), then it is worth it.</p>

        <p>I put a lot of love into it, and hope it brings you joy, like it did for me.</p>""")


@app.route("/tutorial")
def tutorial():
    return _stub_page("Tutorial", "📖", "Tutorial",
        """<p>A step-by-step guide to getting the most out of Lexilogio is on its way.</p>
        <p>In the meantime: pick a language on the home page, choose Vocab Trainer, and start with the Study tab.</p>
        <span class="coming">Full tutorial coming soon</span>""")


@app.route("/donate")
def donate():
    return _stub_page("Donate", "☕", "Support Lexilogio",
        """<p>Lexilogio is free and non-commercial. If it helps you learn, a small voluntary donation helps cover server costs.</p>
        <p>Suggested: €2 / year — completely optional, no commitment, no perks withheld.</p>
        <p>Donation links coming soon.</p>
        <span class="coming">Donation page coming soon</span>""")


if __name__ == "__main__":
    print()
    print("  🇬🇷  Λεξιλόγιο — Greek Trainer")
    print("  Open http://localhost:5003")
    print()
    app.run(debug=False, port=5003, host="0.0.0.0")
