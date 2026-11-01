"""What hosting relies on: the health endpoint and the production key guard."""
import pytest

import app as mainapp


def test_healthz_answers_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "ok\n"


@pytest.mark.parametrize("key", [None, "", "dev-secret-CHANGE-IN-PROD"])
def test_production_refuses_missing_or_dev_secret_key(key):
    env = {"COOKIE_SECURE": "1"}
    if key is not None:
        env["SECRET_KEY"] = key
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        mainapp.secret_key_from_env(env)


def test_production_uses_given_secret_key():
    assert mainapp.secret_key_from_env({"COOKIE_SECURE": "1", "SECRET_KEY": "s3cret"}) == "s3cret"


def test_local_dev_falls_back_to_dev_key():
    assert mainapp.secret_key_from_env({}) == "dev-secret-CHANGE-IN-PROD"
