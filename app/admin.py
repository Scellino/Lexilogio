"""
admin.py — Admin blueprint for reviewing community card submissions.

Routes (all require is_admin=True):
    GET  /admin/submissions               list pending submissions
    POST /admin/submissions/<id>/approve  approve → card goes live for everyone
    POST /admin/submissions/<id>/reject   reject with optional notes
"""
import json
from datetime import datetime
from functools import wraps
from flask import Blueprint, jsonify, request, abort, render_template_string
from flask_login import login_required, current_user
from models import db, CardSubmission, UserCard

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Submission review UI ──────────────────────────────────────────────────────

_ADMIN_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f1a;font-family:system-ui,sans-serif;color:#fff;padding:32px 20px}
h1{font-size:20px;color:#c9a96e;margin-bottom:24px;letter-spacing:.5px}
.sub{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
     border-radius:16px;padding:20px;margin-bottom:16px}
.sub-meta{font-size:11px;color:rgba(255,255,255,.3);margin-bottom:10px}
.sub-word{font-size:18px;font-weight:600;margin-bottom:4px}
.sub-trans{font-size:14px;color:rgba(255,255,255,.6);margin-bottom:12px}
.sub-json{font-size:11px;color:rgba(255,255,255,.3);font-family:monospace;
          white-space:pre-wrap;margin-bottom:14px;max-height:160px;overflow:auto}
.row{display:flex;gap:10px}
button{border:none;border-radius:8px;padding:8px 18px;font-size:13px;
       font-family:system-ui,sans-serif;cursor:pointer;font-weight:600}
.approve{background:#2a6a3a;color:#7ac49a}
.approve:hover{background:#35854a}
.reject{background:rgba(220,60,60,.2);color:#ff8a8a}
.reject:hover{background:rgba(220,60,60,.35)}
.notes{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
       border-radius:8px;padding:8px 12px;color:#fff;font-size:13px;font-family:system-ui,sans-serif}
.empty{color:rgba(255,255,255,.3);font-size:14px;margin-top:40px;text-align:center}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;
       font-weight:700;letter-spacing:.5px;text-transform:uppercase;margin-left:8px}
.badge-pending{background:rgba(201,169,110,.2);color:#c9a96e}
.badge-approved{background:rgba(122,196,154,.2);color:#7ac49a}
.badge-rejected{background:rgba(220,60,60,.15);color:#ff8a8a}
"""

@admin_bp.route("/submissions")
@admin_required
def submissions():
    status_filter = request.args.get("status", "pending")
    subs = CardSubmission.query.filter_by(status=status_filter)\
                               .order_by(CardSubmission.submitted_at.desc()).all()

    def badge(s):
        cls = {"pending": "badge-pending", "approved": "badge-approved",
               "rejected": "badge-rejected"}.get(s, "")
        return f'<span class="badge {cls}">{s}</span>'

    tabs = ""
    for s in ("pending", "approved", "rejected"):
        active = "color:#c9a96e;border-bottom:2px solid #c9a96e;" if s == status_filter else ""
        count = CardSubmission.query.filter_by(status=s).count()
        tabs += f'<a href="?status={s}" style="padding:8px 16px;font-size:13px;color:rgba(255,255,255,.5);text-decoration:none;{active}">{s.capitalize()} ({count})</a>'

    items = ""
    for sub in subs:
        card = sub.card()
        word  = card.get("word") or card.get("greek") or "?"
        trans = card.get("translation") or ""
        raw   = json.dumps(card, ensure_ascii=False, indent=2)
        items += f"""
        <div class="sub" id="sub-{sub.id}">
          <div class="sub-meta">
            #{sub.id} · {sub.lang_code} · by {sub.user.display_name}
            · {sub.submitted_at.strftime('%Y-%m-%d %H:%M') if sub.submitted_at else '?'}
            {badge(sub.status)}
          </div>
          <div class="sub-word">{word}</div>
          <div class="sub-trans">{trans}</div>
          <details><summary style="font-size:11px;color:rgba(255,255,255,.3);cursor:pointer;margin-bottom:8px">Raw JSON</summary>
            <div class="sub-json">{raw}</div>
          </details>
          <div class="row">
            <button class="approve" onclick="act({sub.id},'approve','')">✓ Approve</button>
            <input class="notes" id="notes-{sub.id}" placeholder="Rejection reason (optional)">
            <button class="reject" onclick="act({sub.id},'reject',document.getElementById('notes-{sub.id}').value)">✗ Reject</button>
          </div>
        </div>"""

    if not subs:
        items = '<div class="empty">No submissions.</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Admin · Submissions</title>
<style>{_ADMIN_CSS}</style>
</head><body>
<h1>Card Submissions</h1>
<div style="display:flex;gap:0;margin-bottom:24px;border-bottom:1px solid rgba(255,255,255,.08)">{tabs}</div>
{items}
<script>
async function act(id, action, notes) {{
  const res = await fetch('/admin/submissions/' + id + '/' + action, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{notes}})
  }});
  if (res.ok) document.getElementById('sub-' + id).remove();
  else alert('Error: ' + await res.text());
}}
</script>
</body></html>"""


@admin_bp.route("/submissions/<int:sub_id>/approve", methods=["POST"])
@admin_required
def approve(sub_id):
    sub = CardSubmission.query.get_or_404(sub_id)
    card = sub.card()

    # Add to community pool: stored as a UserCard with user_id=0 (sentinel for "community")
    existing = UserCard.query.filter_by(
        user_id=0, lang_code=sub.lang_code, card_id=str(card.get("id", sub_id))
    ).first()
    if not existing:
        row = UserCard(
            user_id=0,
            lang_code=sub.lang_code,
            card_id=str(card.get("id", sub_id)),
            card_data=sub.card_data,
        )
        db.session.add(row)

    sub.status      = "approved"
    sub.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/submissions/<int:sub_id>/reject", methods=["POST"])
@admin_required
def reject(sub_id):
    sub = CardSubmission.query.get_or_404(sub_id)
    notes = (request.get_json() or {}).get("notes", "")
    sub.status         = "rejected"
    sub.reviewed_at    = datetime.utcnow()
    sub.reviewer_notes = notes
    db.session.commit()
    return jsonify({"ok": True})
