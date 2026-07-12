"""Card API contracts: auth requirements, payload validation, ownership."""
import json
from models import db, UserCard
from conftest import make_user, login


def test_save_requires_login(client):
    r = client.post("/vocab/api/save", json={"word": "λέξη", "translation": "word"})
    assert r.status_code == 401


def test_save_rejects_non_dict(app, client):
    uid = make_user(app, "cards-a@local.test")
    login(client, uid)
    r = client.post("/vocab/api/save", data='"just a string"',
                    content_type="application/json")
    assert r.status_code == 400


def test_save_rejects_oversized_card(app, client):
    uid = make_user(app, "cards-a@local.test")
    login(client, uid)
    r = client.post("/vocab/api/save",
                    json={"word": "x", "note": "y" * 25_000})
    assert r.status_code == 400


def test_save_edit_delete_own_card(app, client):
    uid = make_user(app, "cards-a@local.test")
    login(client, uid)

    r = client.post("/vocab/api/save", json={"word": "λέξη", "translation": "word"})
    assert r.status_code == 200
    cid = r.get_json()["id"]

    r = client.post("/vocab/api/edit",
                    json={"id": cid, "word": "λέξη", "translation": "word, term"})
    assert r.status_code == 200
    with app.app_context():
        row = UserCard.query.filter_by(user_id=uid, card_id=cid).first()
        assert json.loads(row.card_data)["translation"] == "word, term"

    r = client.post("/vocab/api/delete", json={"id": cid})
    assert r.status_code == 200
    with app.app_context():
        assert UserCard.query.filter_by(user_id=uid, card_id=cid).first() is None


def test_edit_cannot_touch_other_users_card(app, client):
    uid_a = make_user(app, "cards-a@local.test")
    uid_b = make_user(app, "cards-b@local.test")

    login(client, uid_a)
    r = client.post("/vocab/api/save", json={"word": "δικό μου", "translation": "mine"})
    cid = r.get_json()["id"]

    login(client, uid_b)
    client.post("/vocab/api/edit", json={"id": cid, "word": "hijacked", "translation": "x"})
    client.post("/vocab/api/delete", json={"id": cid})

    with app.app_context():
        row = UserCard.query.filter_by(user_id=uid_a, card_id=cid).first()
        assert row is not None
        assert json.loads(row.card_data)["word"] == "δικό μου"


def test_check_handles_malformed_json(client):
    r = client.post("/vocab/api/check", data="not json at all",
                    content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["result"] == "wrong"


def test_submit_requires_login(client):
    r = client.post("/vocab/api/submit", json={"word": "x", "translation": "y"})
    assert r.status_code == 401


def test_retention_check_increments_tier_on_correct(app, client):
    from models import Progress
    uid = make_user(app, "retention-a@local.test")
    login(client, uid)
    cid = "el-en-test-word"

    for _ in range(3):
        r = client.post("/fr/vocab/api/check", json={
            "id": cid, "guess": "correct-answer", "correct": "correct-answer",
            "direction": "en→word", "retention_check": True,
        })
        assert r.status_code == 200
    with app.app_context():
        row = Progress.query.filter_by(user_id=uid, card_id=cid).first()
        assert row.retention_tier == 3


def test_retention_check_resets_tier_on_wrong(app, client):
    from models import Progress
    uid = make_user(app, "retention-b@local.test")
    login(client, uid)
    cid = "el-en-test-word-2"

    client.post("/fr/vocab/api/check", json={
        "id": cid, "guess": "right", "correct": "right",
        "direction": "en→word", "retention_check": True,
    })
    with app.app_context():
        assert Progress.query.filter_by(user_id=uid, card_id=cid).first().retention_tier == 1

    client.post("/fr/vocab/api/check", json={
        "id": cid, "guess": "totally-wrong", "correct": "right",
        "direction": "en→word", "retention_check": True,
    })
    with app.app_context():
        assert Progress.query.filter_by(user_id=uid, card_id=cid).first().retention_tier == 0


def test_retention_check_ignored_without_flag(app, client):
    from models import Progress
    uid = make_user(app, "retention-c@local.test")
    login(client, uid)
    cid = "el-en-test-word-3"

    client.post("/fr/vocab/api/check", json={
        "id": cid, "guess": "right", "correct": "right", "direction": "en→word",
    })
    with app.app_context():
        row = Progress.query.filter_by(user_id=uid, card_id=cid).first()
        assert (row.retention_tier or 0) == 0
