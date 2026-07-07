"""
models.py — SQLAlchemy models for Lexilogio.

SQLite by default; switch to PostgreSQL by changing DATABASE_URL.
"""
import json
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id           = db.Column(db.Integer, primary_key=True)
    email        = db.Column(db.String(256), unique=True, nullable=False)
    name         = db.Column(db.String(256))
    password_hash= db.Column(db.String(256))   # None for Google-only accounts
    google_id    = db.Column(db.String(256), unique=True)
    is_admin     = db.Column(db.Boolean, default=False)
    is_verified  = db.Column(db.Boolean, default=True)  # False for new email signups until verified
    created_at   = db.Column(db.DateTime, server_default=db.func.now())

    def get_id(self):
        return str(self.id)

    @property
    def display_name(self):
        return self.name or self.email.split("@")[0]


class Progress(db.Model):
    __tablename__ = "progress"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    lang_code   = db.Column(db.String(10), nullable=False)
    card_id     = db.Column(db.String(64), nullable=False)
    window      = db.Column(db.Text, default="[]")   # JSON bool array
    last_day    = db.Column(db.String(10))            # ISO date "YYYY-MM-DD"
    spaced_days = db.Column(db.Integer, default=0)
    dirs        = db.Column(db.Text, default="[]")   # JSON string array

    __table_args__ = (
        db.UniqueConstraint("user_id", "lang_code", "card_id", name="uq_progress"),
    )

    def to_dict(self):
        return {
            "window":      json.loads(self.window or "[]"),
            "last_day":    self.last_day,
            "spaced_days": self.spaced_days or 0,
            "dirs":        json.loads(self.dirs or "[]"),
        }


class UserCard(db.Model):
    __tablename__ = "user_cards"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    lang_code = db.Column(db.String(10), nullable=False)
    card_id   = db.Column(db.String(64), nullable=False)   # matches card["id"] in JSON
    card_data = db.Column(db.Text, nullable=False)          # full card JSON

    __table_args__ = (
        db.UniqueConstraint("user_id", "lang_code", "card_id", name="uq_user_card"),
    )

    def card(self):
        return json.loads(self.card_data)


class PresetCard(db.Model):
    """Preset cards loaded from *_presets.txt files in language folders."""
    __tablename__ = "preset_cards"
    id          = db.Column(db.String(128), primary_key=True)  # e.g. "fr-voyager"
    lang        = db.Column(db.String(10),  nullable=False, index=True)
    word        = db.Column(db.String(256), nullable=False)
    translation = db.Column(db.Text,        nullable=False)
    type        = db.Column(db.String(64))
    group       = db.Column(db.String(128))
    pronunciation = db.Column(db.String(256))
    etymology   = db.Column(db.Text)
    note        = db.Column(db.Text)
    tags        = db.Column(db.Text, default="[]")   # JSON array
    grammar     = db.Column(db.Text, default="[]")   # JSON array of {label, value}
    example     = db.Column(db.Text)                  # JSON string or object
    priority    = db.Column(db.Integer, default=0)
    imported_at = db.Column(db.DateTime, server_default=db.func.now())

    def card(self):
        ex = self.example
        try:
            ex = json.loads(ex) if ex else ""
        except (ValueError, TypeError):
            pass
        return {
            "id":           self.id,
            "word":         self.word,
            "translation":  self.translation,
            "type":         self.type or "",
            "group":        self.group or "",
            "pronunciation":self.pronunciation or "",
            "etymology":    self.etymology or "",
            "note":         self.note or "",
            "tags":         json.loads(self.tags or "[]"),
            "grammar":      json.loads(self.grammar or "[]"),
            "example":      ex,
            "priority":     self.priority or 0,
            "language":     self.lang,
        }


class CardSubmission(db.Model):
    """Community card submissions awaiting admin review."""
    __tablename__  = "card_submissions"
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    lang_code      = db.Column(db.String(10), nullable=False)
    card_data      = db.Column(db.Text, nullable=False)
    status         = db.Column(db.String(20), default="pending")  # pending/approved/rejected
    submitted_at   = db.Column(db.DateTime, server_default=db.func.now())
    reviewed_at    = db.Column(db.DateTime)
    reviewer_notes = db.Column(db.Text)

    user = db.relationship("User", backref="submissions")

    def card(self):
        return json.loads(self.card_data)
