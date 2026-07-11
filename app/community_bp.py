"""
community_bp.py — Community & Preset card hub.

Routes:
    GET  /community/          — browse page
    GET  /community/api/cards — JSON list of preset + community cards
    POST /community/api/copy  — copy a card to the current user's deck
"""
import json
from flask import Blueprint, jsonify, request, session
from flask_login import current_user
from models import db, PresetCard, CardSubmission, UserCard

community_bp = Blueprint("community", __name__)

LANG_NAMES = {
    "el": "Greek",
    "fr": "French",
    "nl": "Dutch",
    "es": "Spanish",
    "it": "Italian",
    "de": "German",
}

DEP_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "nl": "Dutch",
    "es": "Spanish",
    "it": "Italian",
    "el": "Greek",
}

# ── API ────────────────────────────────────────────────────────────────────────

@community_bp.route("/api/cards")
def api_cards():
    lang   = request.args.get("lang", "")        # "" = all
    source = request.args.get("source", "all")   # "preset" | "community" | "all"

    cards = []

    if source in ("preset", "all"):
        q = PresetCard.query
        if lang:
            q = q.filter_by(lang=lang)
        for row in q.order_by(PresetCard.lang, PresetCard.group, PresetCard.word).all():
            c = row.card()
            c["_source"] = "preset"
            cards.append(c)

    if source in ("community", "all"):
        q = CardSubmission.query.filter_by(status="approved")
        if lang:
            q = q.filter_by(lang_code=lang)
        for row in q.order_by(CardSubmission.submitted_at.desc()).all():
            c = row.card()
            c["_source"] = "community"
            c["language"] = row.lang_code
            c.setdefault("group", "🌍 Community")
            cards.append(c)

    return jsonify({"cards": cards})


@community_bp.route("/api/copy", methods=["POST"])
def api_copy():
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "login_required"}), 401

    data = request.get_json(force=True)
    card = data.get("card")
    if not card or not card.get("language"):
        return jsonify({"ok": False, "error": "invalid_card"}), 400

    lang    = card["language"]
    card_id = str(card.get("id", ""))
    if not card_id:
        return jsonify({"ok": False, "error": "missing_id"}), 400

    existing = UserCard.query.filter_by(
        user_id=current_user.id, lang_code=lang, card_id=card_id
    ).first()
    if existing:
        return jsonify({"ok": True, "already_saved": True})

    dep = card.get("departure_lang", "en")
    db.session.add(UserCard(
        user_id=current_user.id,
        lang_code=lang,
        card_id=card_id,
        card_data=json.dumps(card),
        departure_lang=dep,
    ))
    db.session.commit()
    return jsonify({"ok": True, "already_saved": False})


@community_bp.route("/api/copy-batch", methods=["POST"])
def api_copy_batch():
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "login_required"}), 401

    data         = request.get_json(force=True)
    cards        = data.get("cards", [])
    group_override = data.get("group", "").strip() or None

    # Build existing-card index for this user (by card_id and by word+lang)
    user_cards   = UserCard.query.filter_by(user_id=current_user.id).all()
    existing_ids = {uc.card_id for uc in user_cards}
    existing_words = {}
    for uc in user_cards:
        try:
            cd   = json.loads(uc.card_data)
            word = (cd.get("word") or cd.get("spanish") or "").lower().strip()
            if word:
                existing_words.setdefault(uc.lang_code, set()).add(word)
        except Exception:
            pass

    added, skipped = [], []
    for card in cards:
        lang    = card.get("language", "")
        card_id = str(card.get("id", ""))
        word    = (card.get("word") or card.get("spanish") or "").lower().strip()

        if card_id in existing_ids or word in existing_words.get(lang, set()):
            skipped.append(card.get("word") or card.get("spanish") or card_id)
            continue

        save_card = dict(card)
        if group_override:
            save_card["group"] = group_override

        dep = card.get("departure_lang", "en")
        db.session.add(UserCard(
            user_id=current_user.id,
            lang_code=lang,
            card_id=card_id,
            card_data=json.dumps(save_card),
            departure_lang=dep,
        ))
        added.append(card.get("word") or card.get("spanish") or card_id)
        existing_ids.add(card_id)
        existing_words.setdefault(lang, set()).add(word)

    db.session.commit()
    return jsonify({"ok": True, "added": added, "skipped": skipped})


# ── Page ───────────────────────────────────────────────────────────────────────

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Community Cards · Λεξιλόγιο</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  min-height: 100dvh;
  background: #0f0f1a;
  font-family: system-ui, sans-serif;
  color: #fff;
  padding-bottom: 100px;
}
a { color: inherit; text-decoration: none; }

/* ── Top bar ── */
.topbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  background: rgba(15,15,26,.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(255,255,255,.07);
  padding: 10px 16px;
  display: flex; align-items: center; gap: 12px;
}
.home-link {
  font-size: 11px; color: rgba(255,255,255,.3);
  letter-spacing: .5px; padding: 5px 8px; border-radius: 8px;
  transition: color .15s, background .15s;
  white-space: nowrap;
}
.home-link:hover { color: rgba(201,169,110,.8); background: rgba(201,169,110,.08); }
.topbar-title {
  font-family: Georgia, serif; color: #c9a96e;
  font-size: 16px; letter-spacing: .5px; flex: 1;
}
.topbar-sub {
  font-size: 10px; color: rgba(255,255,255,.25);
  letter-spacing: 1px; text-transform: uppercase;
}

/* ── Filters ── */
.filters {
  max-width: 540px; margin: 80px auto 0; padding: 0 14px;
}
.filter-label {
  font-size: 10px; color: rgba(255,255,255,.25);
  letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px;
}
.pills {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;
}
.pill {
  padding: 5px 12px; border-radius: 20px; font-size: 12px; cursor: pointer;
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(255,255,255,.04);
  color: rgba(255,255,255,.45);
  transition: all .15s;
}
.pill.active {
  background: rgba(201,169,110,.15);
  border-color: rgba(201,169,110,.4);
  color: #c9a96e;
}
.search-wrap { position: relative; margin-bottom: 14px; }
.search-input {
  width: 100%; padding: 9px 12px 9px 34px;
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.1);
  border-radius: 10px; color: #fff; font-size: 13px; outline: none;
}
.search-input::placeholder { color: rgba(255,255,255,.2); }
.search-icon {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  font-size: 14px; color: rgba(255,255,255,.25);
}

/* ── Source tabs ── */
.source-tabs {
  max-width: 540px; margin: 0 auto; padding: 0 14px 12px;
  display: flex; gap: 6px;
}
.src-tab {
  padding: 6px 14px; border-radius: 8px; font-size: 12px; cursor: pointer;
  border: 1px solid rgba(255,255,255,.1);
  background: transparent; color: rgba(255,255,255,.35);
  transition: all .15s;
}
.src-tab.active {
  background: rgba(201,169,110,.12);
  border-color: rgba(201,169,110,.3);
  color: #c9a96e;
}

/* ── Card list ── */
.card-list {
  max-width: 540px; margin: 0 auto; padding: 0 14px;
}
.empty-msg {
  text-align: center; color: rgba(255,255,255,.2);
  font-size: 13px; padding: 40px 0;
}
.ccard {
  background: rgba(255,255,255,.04);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 12px; margin-bottom: 8px;
  overflow: hidden; cursor: pointer;
  transition: border-color .15s, background .15s;
}
.ccard:hover { border-color: rgba(201,169,110,.25); background: rgba(255,255,255,.06); }
.ccard.open { border-color: rgba(201,169,110,.3); }
.ccard-head {
  padding: 11px 14px;
  display: flex; align-items: center; gap: 10px;
}
.ccard-word {
  font-family: Georgia, serif; font-size: 15px; color: #e8c98a; flex: 1;
}
.ccard-tr {
  font-size: 12px; color: rgba(255,255,255,.45); flex: 2;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ccard-meta {
  font-size: 10px; color: rgba(255,255,255,.2);
  white-space: nowrap;
}
.ccard-body {
  display: none; padding: 0 14px 14px; border-top: 1px solid rgba(255,255,255,.06);
}
.ccard.open .ccard-body { display: block; }

/* card body details */
.detail-row {
  font-size: 12px; color: rgba(255,255,255,.45); margin-top: 8px;
  display: flex; gap: 8px;
}
.detail-label { color: rgba(255,255,255,.2); min-width: 90px; }
.grammar-table { margin-top: 8px; width: 100%; border-collapse: collapse; }
.grammar-table td { font-size: 11px; padding: 3px 0; color: rgba(255,255,255,.45); }
.grammar-table td:first-child { color: rgba(255,255,255,.2); width: 120px; }
.example-block {
  margin-top: 8px; padding: 8px 10px;
  background: rgba(255,255,255,.04); border-radius: 8px;
  font-size: 12px;
}
.example-lang { color: #c9a96e; margin-bottom: 2px; }
.example-en { color: rgba(255,255,255,.3); margin-top: 2px; }
.tag-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.tag { padding: 2px 8px; border-radius: 10px; font-size: 10px;
       background: rgba(255,255,255,.06); color: rgba(255,255,255,.3); }
.source-badge {
  display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 10px;
  margin-top: 8px;
}
.source-badge.preset    { background: rgba(100,160,255,.1); color: rgba(100,160,255,.7); }
.source-badge.community { background: rgba(100,220,160,.1); color: rgba(100,220,160,.7); }

.add-btn {
  display: block; width: 100%; margin-top: 12px;
  padding: 9px; border-radius: 9px; font-size: 13px;
  background: rgba(201,169,110,.12); border: 1px solid rgba(201,169,110,.3);
  color: #c9a96e; cursor: pointer; text-align: center;
  transition: background .15s;
}
.add-btn:hover { background: rgba(201,169,110,.2); }
.add-btn:disabled { opacity: .4; cursor: default; }
.add-btn.saved { background: rgba(122,196,154,.1); border-color: rgba(122,196,154,.3); color: #7ac49a; }

.count-label {
  font-size: 11px; color: rgba(255,255,255,.2);
  margin-bottom: 10px; font-family: sans-serif;
}

/* ── Group / tag filters ── */
.group-filter { max-width: 540px; margin: 0 auto; padding: 0 14px 4px; }
.tag-filter   { max-width: 540px; margin: 0 auto; padding: 0 14px 4px; }
.pill.tag-active {
  background: rgba(120,180,255,.15);
  border-color: rgba(120,180,255,.4);
  color: rgba(140,190,255,.9);
}

/* ── Count row ── */
.count-row {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 10px;
}
.count-label { font-size: 11px; color: rgba(255,255,255,.2); font-family: sans-serif; flex: 1; }
.sel-all-btn {
  font-size: 11px; color: rgba(201,169,110,.6); background: none;
  border: none; cursor: pointer; padding: 0; text-decoration: underline;
  text-underline-offset: 2px;
}
.sel-all-btn:hover { color: #c9a96e; }

/* ── Multi-select ── */
.ccard-check {
  width: 18px; height: 18px; border-radius: 5px; flex-shrink: 0;
  border: 1.5px solid rgba(255,255,255,.18);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: transparent;
  transition: all .15s;
}
.ccard-check:hover { border-color: rgba(201,169,110,.5); }
.ccard.selected { border-color: rgba(201,169,110,.4); background: rgba(201,169,110,.05); }
.ccard.selected .ccard-check {
  background: rgba(201,169,110,.25); border-color: #c9a96e; color: #c9a96e;
}

/* ── Select bar ── */
.select-bar {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
  background: #1a1624; border: 1px solid rgba(201,169,110,.35);
  border-radius: 14px; padding: 10px 14px;
  display: none; align-items: center; gap: 10px;
  box-shadow: 0 4px 24px rgba(0,0,0,.6); z-index: 200;
  font-size: 12px; color: rgba(255,255,255,.55); white-space: nowrap;
}
.select-bar-btn {
  padding: 7px 14px; border-radius: 9px; font-size: 12px; cursor: pointer;
  background: rgba(201,169,110,.15); border: 1px solid rgba(201,169,110,.3);
  color: #c9a96e; transition: background .15s; white-space: nowrap;
}
.select-bar-btn:hover { background: rgba(201,169,110,.25); }
.select-bar-btn:disabled { opacity: .5; cursor: default; }
.select-bar-clear {
  background: none; border: none; color: rgba(255,255,255,.25);
  cursor: pointer; font-size: 18px; line-height: 1; padding: 0 2px;
}

/* ── Add modal ── */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  z-index: 300; display: flex; align-items: center; justify-content: center;
}
.modal-box {
  background: #1a1624; border: 1px solid rgba(255,255,255,.1);
  border-radius: 16px; padding: 22px 20px; width: 320px; max-width: 92vw;
}
.modal-title { font-family: Georgia,serif; font-size: 16px; color: #e8c98a; margin-bottom: 4px; }
.modal-sub   { font-size: 12px; color: rgba(255,255,255,.3); margin-bottom: 16px; }
.modal-label { font-size: 11px; color: rgba(255,255,255,.3); letter-spacing: .5px;
               text-transform: uppercase; display: block; margin-bottom: 6px; }
.modal-input {
  width: 100%; padding: 9px 11px;
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12);
  border-radius: 9px; color: #fff; font-size: 13px; outline: none;
}
.modal-input:focus { border-color: rgba(201,169,110,.4); }
.modal-actions { display: flex; gap: 8px; margin-top: 16px; }
.modal-cancel {
  flex: 1; padding: 9px; border-radius: 9px; font-size: 13px; cursor: pointer;
  background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1);
  color: rgba(255,255,255,.4);
}
.modal-confirm {
  flex: 2; padding: 9px; border-radius: 9px; font-size: 13px; cursor: pointer;
  background: rgba(201,169,110,.15); border: 1px solid rgba(201,169,110,.35);
  color: #c9a96e; transition: background .15s;
}
.modal-confirm:hover { background: rgba(201,169,110,.25); }
.modal-confirm:disabled { opacity: .5; cursor: default; }

/* ── Toast ── */
.toast {
  position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
  background: #1a1624; border: 1px solid rgba(255,255,255,.12);
  border-radius: 12px; padding: 12px 16px; z-index: 400;
  font-size: 12px; color: rgba(255,255,255,.7); max-width: 320px;
  box-shadow: 0 4px 20px rgba(0,0,0,.5); line-height: 1.5;
  display: none;
}
.toast-added  { color: #7ac49a; }
.toast-skipped { color: rgba(255,200,100,.7); margin-top: 4px; }
</style>
</head>
<body>

<div class="topbar">
  <a class="home-link" href="/">🧿 Λεξιλόγιο</a>
  <div>
    <div class="topbar-sub">COMMUNITY</div>
    <div class="topbar-title">Cards &amp; Presets</div>
  </div>
</div>

<div class="filters" id="filters">
  <div class="filter-label">Language</div>
  <div class="pills" id="lang-pills"></div>
  <div class="filter-label" id="dep-filter-label" style="margin-top:10px">Taught from</div>
  <div class="pills" id="dep-pills"></div>
  <div class="search-wrap">
    <span class="search-icon">&#128269;</span>
    <input class="search-input" id="search" type="text" placeholder="Search word or translation…" oninput="render()">
  </div>
</div>

<div class="source-tabs" id="source-tabs"></div>

<div class="group-filter" id="group-filter">
  <div class="filter-label" id="group-filter-label" style="display:none">GROUP</div>
  <div class="pills" id="group-pills"></div>
</div>

<div class="tag-filter" id="tag-filter">
  <div class="filter-label" id="tag-filter-label" style="display:none">TAGS</div>
  <div class="pills" id="tag-pills"></div>
</div>

<div class="card-list" id="card-list"></div>

<div class="select-bar" id="select-bar">
  <span id="select-count"></span>
  <button class="select-bar-btn" id="select-add-btn" onclick="openAddModal()">Add to my deck</button>
  <button class="select-bar-clear" onclick="clearSelection()" title="Clear selection">×</button>
</div>

<div class="modal-overlay" id="add-modal" style="display:none" onclick="closeModal()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <div class="modal-title">Add to your deck</div>
    <div class="modal-sub" id="modal-sub"></div>
    <label class="modal-label" for="modal-group">Group <span style="color:rgba(255,255,255,.2);font-size:10px;text-transform:none">(optional — leave blank to keep original)</span></label>
    <input class="modal-input" id="modal-group" type="text" placeholder="e.g. 🏡 My words">
    <div class="modal-actions">
      <button class="modal-cancel" onclick="closeModal()">Cancel</button>
      <button class="modal-confirm" id="modal-confirm-btn" onclick="confirmAdd()">Add</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const LANG_NAMES = __LANG_NAMES_JSON__;
const DEP_NAMES  = __DEP_NAMES_JSON__;
const IS_LOGGED_IN = __IS_LOGGED_IN__;
const USER_DEP_LANG = __USER_DEP_LANG__;

let allCards = [];
const _urlLang = new URLSearchParams(window.location.search).get('lang') || '';
const _urlDep  = new URLSearchParams(window.location.search).get('dep') || '';
let activeLang   = Object.keys(LANG_NAMES).includes(_urlLang) ? _urlLang : '';
let activeDep    = Object.keys(DEP_NAMES).includes(_urlDep) ? _urlDep : USER_DEP_LANG;
let activeSource = 'all';
let activeGroup  = '';
let activeTags   = new Set();
let openId       = null;
let savedIds     = new Set();
let selectedIds  = new Set();
let _pendingCards = [];
let _toastTimer   = null;

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  buildLangPills();
  buildSourceTabs();
  const res = await fetch('/community/api/cards');
  const data = await res.json();
  allCards = data.cards || [];
  buildDepPills();
  render();
}

// ── Filters ───────────────────────────────────────────────────────────────────
function buildLangPills() {
  const wrap = document.getElementById('lang-pills');
  wrap.innerHTML = '';
  const langs = ['', ...Object.keys(LANG_NAMES)];
  langs.forEach(code => {
    const p = document.createElement('button');
    p.className = 'pill' + (code === activeLang ? ' active' : '');
    p.textContent = code ? LANG_NAMES[code] : 'All languages';
    p.onclick = () => { activeLang = code; activeGroup = ''; activeTags.clear(); buildLangPills(); render(); };
    wrap.appendChild(p);
  });
}

function buildDepPills() {
  const wrap  = document.getElementById('dep-pills');
  const label = document.getElementById('dep-filter-label');
  wrap.innerHTML = '';
  // Collect dep langs actually present in loaded cards (preset cards have departure_lang)
  const available = [...new Set(allCards.map(c => c.departure_lang).filter(Boolean))].sort();
  if (available.length <= 1) {
    label.style.display = available.length === 1 ? '' : 'none';
    wrap.style.display  = available.length === 1 ? '' : 'none';
    if (available.length === 1 && activeDep !== available[0]) {
      activeDep = available[0];
    }
  } else {
    label.style.display = '';
    wrap.style.display  = '';
  }
  available.forEach(code => {
    const p = document.createElement('button');
    p.className = 'pill' + (code === activeDep ? ' active' : '');
    p.textContent = DEP_NAMES[code] || code;
    p.onclick = () => { activeDep = code; buildDepPills(); render(); };
    wrap.appendChild(p);
  });
}

function buildSourceTabs() {
  const wrap = document.getElementById('source-tabs');
  wrap.innerHTML = '';
  [['all','All'],['preset','Presets'],['community','Community']].forEach(([val,label]) => {
    const b = document.createElement('button');
    b.className = 'src-tab' + (activeSource === val ? ' active' : '');
    b.textContent = label;
    b.onclick = () => { activeSource = val; activeGroup = ''; activeTags.clear(); buildSourceTabs(); render(); };
    wrap.appendChild(b);
  });
}

function buildGroupPills(cards) {
  const groups = [...new Set(cards.map(c => c.group).filter(Boolean))].sort();
  const wrap  = document.getElementById('group-pills');
  const label = document.getElementById('group-filter-label');
  wrap.innerHTML = '';
  if (groups.length <= 1) {
    label.style.display = 'none';
    if (activeGroup && !groups.includes(activeGroup)) activeGroup = '';
    return;
  }
  label.style.display = '';
  ['', ...groups].forEach(g => {
    const p = document.createElement('button');
    p.className = 'pill' + (g === activeGroup ? ' active' : '');
    p.textContent = g || 'All groups';
    p.onclick = () => { activeGroup = g; render(); };
    wrap.appendChild(p);
  });
}

function buildTagPills(cards) {
  const tagSet = new Set();
  cards.forEach(c => (c.tags || []).forEach(t => t && tagSet.add(t)));
  const tags  = [...tagSet].sort();
  const wrap  = document.getElementById('tag-pills');
  const label = document.getElementById('tag-filter-label');
  wrap.innerHTML = '';
  if (!tags.length) { label.style.display = 'none'; return; }
  label.style.display = '';
  tags.forEach(t => {
    const p = document.createElement('button');
    const on = activeTags.has(t);
    p.className = 'pill' + (on ? ' tag-active' : '');
    p.textContent = t;
    p.onclick = () => {
      if (activeTags.has(t)) activeTags.delete(t); else activeTags.add(t);
      render();
    };
    wrap.appendChild(p);
  });
}

// ── Render ────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function render() {
  const q = (document.getElementById('search')?.value || '').toLowerCase();
  let cards = allCards;
  if (activeLang)         cards = cards.filter(c => c.language === activeLang);
  if (activeDep)          cards = cards.filter(c => !c.departure_lang || c.departure_lang === activeDep);
  if (activeSource !== 'all') cards = cards.filter(c => c._source === activeSource);
  if (q) cards = cards.filter(c =>
    (c.word||'').toLowerCase().includes(q) ||
    (c.translation||'').toLowerCase().includes(q)
  );

  buildGroupPills(cards);
  if (activeGroup) cards = cards.filter(c => c.group === activeGroup);

  buildTagPills(cards);
  if (activeTags.size) cards = cards.filter(c => (c.tags||[]).some(t => activeTags.has(t)));

  const list = document.getElementById('card-list');
  if (!cards.length) {
    list.innerHTML = '<div class="empty-msg">No cards found.</div>';
    return;
  }

  const visibleIds = cards.map(c => String(c.id));
  const allSelected = visibleIds.length > 0 && visibleIds.every(id => selectedIds.has(id));
  const selBtn = allSelected
    ? `<button class="sel-all-btn" onclick="deselectAll()">Deselect all</button>`
    : `<button class="sel-all-btn" onclick='selectAll(${JSON.stringify(visibleIds)})'>Select all</button>`;

  list.innerHTML =
    `<div class="count-row"><span class="count-label">${cards.length} card${cards.length!==1?'s':''}</span>${selBtn}</div>` +
    cards.map(c => cardHTML(c)).join('');
}

function cardHTML(c) {
  const id        = esc(String(c.id));
  const isOpen    = String(c.id) === String(openId);
  const isSaved   = savedIds.has(String(c.id));
  const isSelected = selectedIds.has(String(c.id));
  const langName  = LANG_NAMES[c.language] || c.language || '';

  let body = '';
  if (isOpen) {
    if (c.pronunciation)
      body += `<div class="detail-row"><span class="detail-label">Pronunciation</span><span>${esc(c.pronunciation)}</span></div>`;
    if (c.etymology)
      body += `<div class="detail-row"><span class="detail-label">Etymology</span><span>${esc(c.etymology)}</span></div>`;
    if (c.note)
      body += `<div class="detail-row"><span class="detail-label">Note</span><span>${esc(c.note)}</span></div>`;
    if (c.grammar && c.grammar.length) {
      body += '<table class="grammar-table">' +
        c.grammar.map(g => `<tr><td>${esc(g.label)}</td><td>${esc(g.value)}</td></tr>`).join('') +
        '</table>';
    }
    const ex = c.example;
    if (ex && typeof ex === 'object' && (ex[c.language] || ex.en)) {
      const langSentence = ex[c.language] || '';
      const enSentence   = ex.en || '';
      body += `<div class="example-block">`;
      if (langSentence) body += `<div class="example-lang">${esc(langSentence)}</div>`;
      if (enSentence)   body += `<div class="example-en">${esc(enSentence)}</div>`;
      body += `</div>`;
    } else if (ex && typeof ex === 'string') {
      body += `<div class="example-block"><div class="example-lang">${esc(ex)}</div></div>`;
    }
    if (c.tags && c.tags.length)
      body += '<div class="tag-row">' + c.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('') + '</div>';

    body += `<span class="source-badge ${esc(c._source)}">${c._source === 'preset' ? '📚 Preset' : '🌍 Community'}</span>`;

    const btnLabel   = isSaved ? '✓ Added to your deck' : '+ Add to my deck';
    const btnClass   = 'add-btn' + (isSaved ? ' saved' : '');
    const btnDisabled = isSaved ? 'disabled' : '';
    if (IS_LOGGED_IN) {
      body += `<button class="${btnClass}" ${btnDisabled} onclick="copyCard(event,'${id}')">${btnLabel}</button>`;
    } else {
      body += `<a href="/auth/login" class="add-btn" style="display:block;text-align:center">Log in to add this card</a>`;
    }
  }

  return `<div class="ccard${isOpen?' open':''}${isSelected?' selected':''}" id="cc-${id}" onclick="toggleCard('${id}')">
    <div class="ccard-head">
      <span class="ccard-check" onclick="toggleSelect(event,'${id}')">${isSelected?'✓':''}</span>
      <span class="ccard-word">${esc(c.word)}</span>
      <span class="ccard-tr">${esc(c.translation)}</span>
      <span class="ccard-meta">${esc(langName)}${c.group?' · '+esc(c.group):''}</span>
    </div>
    ${isOpen ? `<div class="ccard-body">${body}</div>` : ''}
  </div>`;
}

function toggleCard(id) {
  openId = (openId === id) ? null : id;
  render();
  if (openId) {
    const el = document.getElementById('cc-' + openId);
    if (el) el.scrollIntoView({behavior:'smooth', block:'nearest'});
  }
}

// ── Single-card copy ──────────────────────────────────────────────────────────
async function copyCard(evt, id) {
  evt.stopPropagation();
  const card = allCards.find(c => String(c.id) === String(id));
  if (!card) return;
  const btn = evt.target;
  btn.disabled = true;
  btn.textContent = 'Saving…';
  const res = await fetch('/community/api/copy', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({card}),
  });
  const data = await res.json();
  if (data.ok) {
    savedIds.add(String(id));
    btn.textContent = '✓ Added to your deck';
    btn.classList.add('saved');
  } else if (data.error === 'login_required') {
    window.location = '/auth/login';
  } else {
    btn.disabled = false;
    btn.textContent = '+ Add to my deck';
  }
}

// ── Multi-select ──────────────────────────────────────────────────────────────
function toggleSelect(evt, id) {
  evt.stopPropagation();
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  // update just the checkbox without full re-render
  const el = document.getElementById('cc-' + id);
  if (el) {
    el.classList.toggle('selected', selectedIds.has(id));
    const check = el.querySelector('.ccard-check');
    if (check) check.textContent = selectedIds.has(id) ? '✓' : '';
  }
  updateSelectBar();
}

function updateSelectBar() {
  const bar   = document.getElementById('select-bar');
  const count = document.getElementById('select-count');
  const n = selectedIds.size;
  if (n > 0) {
    bar.style.display = 'flex';
    count.textContent = `${n} card${n!==1?'s':''} selected`;
  } else {
    bar.style.display = 'none';
  }
}

function clearSelection() {
  selectedIds.clear();
  updateSelectBar();
  render();
}

function selectAll(ids) {
  ids.forEach(id => selectedIds.add(id));
  updateSelectBar();
  render();
}

function deselectAll() {
  selectedIds.clear();
  updateSelectBar();
  render();
}

// ── Add modal ─────────────────────────────────────────────────────────────────
function openAddModal() {
  if (!IS_LOGGED_IN) { window.location = '/auth/login'; return; }
  _pendingCards = allCards.filter(c => selectedIds.has(String(c.id)));
  if (!_pendingCards.length) return;
  const n = _pendingCards.length;
  document.getElementById('modal-sub').textContent = `${n} card${n!==1?'s':''} selected`;
  document.getElementById('modal-confirm-btn').textContent = `Add ${n} card${n!==1?'s':''}`;
  // Pre-fill with first card's group as a suggestion
  const firstGroup = _pendingCards[0].group || '';
  const groupInput = document.getElementById('modal-group');
  groupInput.value = '';
  groupInput.placeholder = firstGroup ? `Leave blank to keep original (e.g. ${firstGroup})` : 'e.g. 🏡 My words';
  document.getElementById('add-modal').style.display = 'flex';
}

function closeModal() {
  document.getElementById('add-modal').style.display = 'none';
  _pendingCards = [];
}

async function confirmAdd() {
  const btn   = document.getElementById('modal-confirm-btn');
  const group = document.getElementById('modal-group').value.trim();
  btn.disabled = true;
  btn.textContent = 'Adding…';

  const res  = await fetch('/community/api/copy-batch', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({cards: _pendingCards, group}),
  });
  const data = await res.json();

  if (!data.ok && data.error === 'login_required') {
    window.location = '/auth/login'; return;
  }

  (data.added || []).forEach(word => {
    const card = _pendingCards.find(c => (c.word || c.spanish) === word);
    if (card) savedIds.add(String(card.id));
  });

  closeModal();
  selectedIds.clear();
  updateSelectBar();
  render();
  showToast(data.added || [], data.skipped || []);
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(added, skipped) {
  const el = document.getElementById('toast');
  let html = '';
  if (added.length)
    html += `<div class="toast-added">✓ Added ${added.length} card${added.length!==1?'s':''}</div>`;
  if (skipped.length)
    html += `<div class="toast-skipped">Skipped ${skipped.length} duplicate${skipped.length!==1?'s':''}: ${skipped.join(', ')}</div>`;
  if (!html) return;
  el.innerHTML = html;
  el.style.display = 'block';
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);
}

init();
</script>
</body>
</html>"""


@community_bp.route("/")
@community_bp.route("")
def community_page():
    is_logged_in = "true" if current_user.is_authenticated else "false"
    dep_lang = current_user.departure_lang if current_user.is_authenticated else "en"
    html = _PAGE.replace("__LANG_NAMES_JSON__", json.dumps(LANG_NAMES))
    html = html.replace("__DEP_NAMES_JSON__", json.dumps(DEP_NAMES))
    html = html.replace("__IS_LOGGED_IN__", is_logged_in)
    html = html.replace("__USER_DEP_LANG__", json.dumps(dep_lang or "en"))
    return html
