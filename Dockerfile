# Production image: gunicorn serving the Flask app on :8080.
# The container runs with a read-only root: state lives in LEXILOGIO_DATA_DIR
# (SQLite via DATABASE_URL, verb_presets.json), temp files in /tmp.
FROM python:3.12-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEXILOGIO_DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/lexilogio.db

WORKDIR /srv/lexilogio
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r app/requirements.txt
COPY . .
RUN python -m compileall -q app \
 && useradd --uid 1000 --user-group --no-create-home --home-dir /nonexistent lexilogio \
 && mkdir /data && chown lexilogio:lexilogio /data

USER lexilogio
EXPOSE 8080
CMD ["gunicorn", "--chdir", "app", "--workers", "2", "--bind", "0.0.0.0:8080", "--no-control-socket", "app:app"]
