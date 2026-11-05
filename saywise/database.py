"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g

from .sentences import SENTENCES


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    db = get_db()
    db.executescript(schema)
    sentence_id = next(
        (column for column in db.execute("PRAGMA table_info(attempts)") if column["name"] == "sentence_id"),
        None,
    )
    if sentence_id is not None and sentence_id["notnull"]:
        db.execute("BEGIN")
        try:
            db.execute(
                """
                CREATE TABLE attempts_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    sentence_id INTEGER REFERENCES sentences(id),
                    sentence_text TEXT NOT NULL,
                    recognized_text TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    audio_path TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                INSERT INTO attempts_new
                    (id, user_id, sentence_id, sentence_text, recognized_text,
                     analysis_json, audio_path, created_at)
                SELECT id, user_id, sentence_id, sentence_text, recognized_text,
                       analysis_json, audio_path, created_at
                FROM attempts
                """
            )
            db.execute("DROP TABLE attempts")
            db.execute("ALTER TABLE attempts_new RENAME TO attempts")
            db.execute(
                "CREATE INDEX IF NOT EXISTS attempts_user_created "
                "ON attempts(user_id, created_at DESC)"
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    db.executemany(
        "INSERT OR IGNORE INTO sentences (id, text, category, level) VALUES (?, ?, ?, ?)",
        [(item["id"], item["text"], item["category"], item["level"]) for item in SENTENCES],
    )
    db.commit()


def init_app(app: object) -> None:
    app.teardown_appcontext(close_db)