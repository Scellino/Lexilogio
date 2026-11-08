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


def test_first_start_creates_the_schema_under_the_startup_lock(tmp_path):
    """gunicorn's workers import the app at once; on an empty DB two
    create_all() calls race ('table users already exists', worker exit 3).
    While another worker holds the startup lock, a new one must not touch
    the schema."""
    import fcntl
    import os
    import sqlite3
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    db_file = tmp_path / "fresh.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}", "SECRET_KEY": "test-secret"}
    app_dir = Path(__file__).parent.parent
    lock_path = Path(tempfile.gettempdir()) / "lexilogio-preset-load.lock"

    def tables():
        if not db_file.exists():
            return set()
        with sqlite3.connect(db_file) as con:
            return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}

    with open(lock_path, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = subprocess.Popen([sys.executable, "-c", "import app"], cwd=app_dir, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(3)
            assert worker.poll() is None, worker.stdout.read().decode()[-2000:]
            assert "users" not in tables()
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
    out, _ = worker.communicate(timeout=120)
    assert worker.returncode == 0, out.decode()[-2000:]
    assert "users" in tables()
