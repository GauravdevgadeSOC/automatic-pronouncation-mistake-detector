"""Saywise Flask application."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from flask import Flask, abort, g, jsonify, render_template, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import database
from .analysis import AnalysisUnavailable, analyze_recording, decode_audio
from .feedback import repeated_word_suggestion
from .sentences import SENTENCES


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SAYWISE_SECRET_KEY") or secrets.token_urlsafe(32),
        DATABASE=str(Path(app.instance_path) / "saywise.sqlite3"),
        UPLOAD_FOLDER=str(Path(app.instance_path) / "recordings"),
        MAX_CONTENT_LENGTH=15 * 1024 * 1024,
        AUDIO_MAX_SECONDS=60,
        DEBUG=os.environ.get("SAYWISE_DEBUG", "0") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SAYWISE_COOKIE_SECURE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    database.init_app(app)
    with app.app_context():
        database.init_db()

    @app.context_processor
    def inject_csrf_token() -> dict[str, Callable[[], str]]:
        def csrf_token() -> str:
            token = session.get("csrf_token")
            if token is None:
                token = secrets.token_urlsafe(32)
                session["csrf_token"] = token
            return token

        return {"csrf_token": csrf_token}

    @app.before_request
    def protect_mutations() -> Any:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith("/api/"):
            expected = session.get("csrf_token", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                return jsonify({"error": "Your session token expired. Refresh the page and try again."}), 403
        return None

    def current_user() -> sqlite3.Row | None:
        user_id = session.get("user_id")
        if user_id is None:
            return None
        return database.get_db().execute(
            "SELECT id, email, display_name, level, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if current_user() is None:
                return jsonify({"error": "Sign in to continue."}), 401
            return view(*args, **kwargs)

        return wrapped

    def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = current_user()
            if user is None or user["role"] != "admin":
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    @app.errorhandler(413)
    def request_too_large(_: Exception) -> tuple[Any, int]:
        return jsonify({"error": "Recording exceeds the 15 MB upload limit."}), 413

    @app.errorhandler(403)
    def forbidden(_: Exception) -> tuple[Any, int]:
        return jsonify({"error": "You do not have permission to access this resource."}), 403

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.get("/api/me")
    def me() -> Any:
        user = current_user()
        if user is None:
            return jsonify({"user": None})
        return jsonify({"user": dict(user)})

    @app.post("/api/register")
    def register() -> Any:
        payload = request.get_json(silent=True) or {}
        display_name = str(payload.get("display_name", "")).strip()[:80]
        email = str(payload.get("email", "")).strip().casefold()[:254]
        password = str(payload.get("password", ""))
        level = str(payload.get("level", "beginner"))
        if not display_name or "@" not in email or len(password) < 10:
            return jsonify({"error": "Enter a name, valid email, and password with at least 10 characters."}), 400
        if level not in {"beginner", "intermediate", "advanced"}:
            return jsonify({"error": "Choose a valid learning level."}), 400
        db = database.get_db()
        try:
            cursor = db.execute(
                "INSERT INTO users (email, display_name, password_hash, level, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    email,
                    display_name,
                    generate_password_hash(password),
                    level,
                    "admin" if email == os.environ.get("SAYWISE_ADMIN_EMAIL", "").strip().casefold() else "student",
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "An account with that email already exists."}), 409
        session.clear()
        session["user_id"] = cursor.lastrowid
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"user": dict(current_user()), "csrf_token": session["csrf_token"]}), 201

    @app.post("/api/login")
    def login() -> Any:
        payload = request.get_json(silent=True) or {}
        email = str(payload.get("email", "")).strip().casefold()
        password = str(payload.get("password", ""))
        user = database.get_db().execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Email or password is incorrect."}), 401
        session.clear()
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"user": dict(current_user()), "csrf_token": session["csrf_token"]})

    @app.post("/api/logout")
    @login_required
    def logout() -> Any:
        session.clear()
        return jsonify({"ok": True})

    @app.patch("/api/profile")
    @login_required
    def update_profile() -> Any:
        payload = request.get_json(silent=True) or {}
        display_name = str(payload.get("display_name", "")).strip()[:80]
        level = str(payload.get("level", ""))
        if not display_name or level not in {"beginner", "intermediate", "advanced"}:
            return jsonify({"error": "Enter a name and a valid learning level."}), 400
        database.get_db().execute(
            "UPDATE users SET display_name = ?, level = ? WHERE id = ?",
            (display_name, level, session["user_id"]),
        )
        database.get_db().commit()
        return jsonify({"user": dict(current_user())})

    @app.delete("/api/account")
    @login_required
    def delete_account() -> Any:
        user_id = session["user_id"]
        db = database.get_db()
        rows = db.execute("SELECT audio_path FROM attempts WHERE user_id = ?", (user_id,)).fetchall()
        for row in rows:
            if row["audio_path"]:
                Path(row["audio_path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM attempts WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/sentences")
    @login_required
    def sentences() -> Any:
        level = request.args.get("level") or current_user()["level"]
        if level not in {"beginner", "intermediate", "advanced"}:
            return jsonify({"error": "Choose a valid learning level."}), 400
        rows = database.get_db().execute(
            "SELECT id, text, category, level FROM sentences WHERE level = ? ORDER BY category, id",
            (level,),
        ).fetchall()
        return jsonify({"sentences": [dict(row) for row in rows]})

    @app.get("/api/dashboard")
    @login_required
    def dashboard() -> Any:
        db = database.get_db()
        user_id = session["user_id"]
        level = current_user()["level"]
        level_sentences = [item for item in SENTENCES if item["level"] == level]
        attempts = db.execute(
            "SELECT id, sentence_text, recognized_text, analysis_json, created_at "
            "FROM attempts WHERE user_id = ? ORDER BY id DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) AS count FROM attempts WHERE user_id = ?", (user_id,)
        ).fetchone()["count"]
        recent = [serialize_attempt(row) for row in reversed(attempts)]
        return jsonify({
            "total_attempts": total,
            "recent_attempts": recent,
            "daily_challenge": level_sentences[
                datetime.now(timezone.utc).timetuple().tm_yday % len(level_sentences)
            ],
        })

    @app.get("/api/attempts")
    @login_required
    def attempts() -> Any:
        rows = database.get_db().execute(
            "SELECT id, sentence_text, recognized_text, analysis_json, created_at "
            "FROM attempts WHERE user_id = ? ORDER BY id DESC LIMIT 100",
            (session["user_id"],),
        ).fetchall()
        return jsonify({"attempts": [serialize_attempt(row) for row in rows]})

    @app.post("/api/analyze")
    @login_required
    def analyze() -> Any:
        upload = request.files.get("audio")
        if upload is None or not upload.filename:
            return jsonify({"error": "Record or select an audio file first."}), 400
        custom_text = request.form.get("custom_text")
        if custom_text is not None:
            expected_text = custom_text.strip()
            if not expected_text:
                return jsonify({"error": "Enter a sentence or paragraph to practice."}), 400
            sentence_id = None
        else:
            sentence_id = request.form.get("sentence_id", type=int)
            sentence = database.get_db().execute(
                "SELECT id, text FROM sentences WHERE id = ?", (sentence_id,)
            ).fetchone()
            if sentence is None:
                return jsonify({"error": "Select a valid practice sentence."}), 400
            expected_text = sentence["text"]

        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}:
            suffix = ".audio"
        temporary_path: Path | None = None
        retained_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=app.config["UPLOAD_FOLDER"], suffix=suffix, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                upload.save(temporary)
            samples, sample_rate, duration = decode_audio(
                temporary_path, max_duration=app.config["AUDIO_MAX_SECONDS"]
            )
            result = analyze_recording(
                temporary_path,
                samples,
                sample_rate,
                duration,
                expected_text,
            )
            previous_rows = database.get_db().execute(
                "SELECT analysis_json FROM attempts WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                (session["user_id"],),
            ).fetchall()
            suggestion = repeated_word_suggestion(
                result["word_comparison"],
                [json.loads(row["analysis_json"]).get("word_comparison", {}) for row in previous_rows],
            )
            if suggestion:
                result["feedback"].append(suggestion)
        except AnalysisUnavailable as exc:
            return jsonify({"error": str(exc)}), 503
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        store_audio = request.form.get("store_audio") == "true"
        if store_audio:
            retained_path = Path(app.config["UPLOAD_FOLDER"]) / f"{uuid4().hex}{suffix}"
            upload.stream.seek(0)
            retained_path.write_bytes(upload.stream.read())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db = database.get_db()
        cursor = db.execute(
            "INSERT INTO attempts (user_id, sentence_id, sentence_text, recognized_text, "
            "analysis_json, audio_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session["user_id"], sentence_id, expected_text, result["transcript"],
                json.dumps(result), str(retained_path) if retained_path else None, now,
            ),
        )
        db.commit()
        result["attempt_id"] = cursor.lastrowid
        result["created_at"] = now
        return jsonify(result), 201

    @app.delete("/api/attempts/<int:attempt_id>")
    @login_required
    def delete_attempt(attempt_id: int) -> Any:
        db = database.get_db()
        row = db.execute(
            "SELECT audio_path FROM attempts WHERE id = ? AND user_id = ?",
            (attempt_id, session["user_id"]),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Practice attempt not found."}), 404
        if row["audio_path"]:
            Path(row["audio_path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))
        db.commit()
        return jsonify({"ok": True})

    @app.delete("/api/attempts")
    @login_required
    def delete_all_attempts() -> Any:
        db = database.get_db()
        rows = db.execute(
            "SELECT audio_path FROM attempts WHERE user_id = ?", (session["user_id"],)
        ).fetchall()
        for row in rows:
            if row["audio_path"]:
                Path(row["audio_path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM attempts WHERE user_id = ?", (session["user_id"],))
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/attempts/<int:attempt_id>/audio")
    @login_required
    def play_attempt(attempt_id: int) -> Any:
        row = database.get_db().execute(
            "SELECT audio_path FROM attempts WHERE id = ? AND user_id = ?",
            (attempt_id, session["user_id"]),
        ).fetchone()
        if row is None or not row["audio_path"] or not Path(row["audio_path"]).is_file():
            return jsonify({"error": "No retained recording is available."}), 404
        return send_file(row["audio_path"], as_attachment=False)

    @app.get("/api/attempts/<int:attempt_id>/report")
    @login_required
    def report(attempt_id: int) -> Any:
        row = database.get_db().execute(
            "SELECT sentence_text, recognized_text, analysis_json, created_at "
            "FROM attempts WHERE id = ? AND user_id = ?",
            (attempt_id, session["user_id"]),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Practice attempt not found."}), 404
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
        except ImportError:
            return jsonify({"error": "PDF export requires the reportlab package."}), 503
        from io import BytesIO

        analysis = json.loads(row["analysis_json"])
        stream = BytesIO()
        pdf = canvas.Canvas(stream, pagesize=letter)
        pdf.setTitle("Saywise practice report")
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(48, 744, "Saywise practice report")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(48, 716, f"Date: {row['created_at']}")
        pdf.drawString(48, 692, _pdf_text(f"Expected: {row['sentence_text'][:90]}"))
        pdf.drawString(48, 670, _pdf_text(f"Recognized: {row['recognized_text'][:90]}"))
        edits = analysis.get("word_comparison", {})
        pdf.drawString(
            48, 640,
            "Word edits: " + ", ".join(
                f"{name} {edits.get(name, 0)}" for name in ("substitutions", "deletions", "insertions")
            ),
        )
        fluency = analysis.get("fluency", {})
        pdf.drawString(48, 618, f"Speaking rate: {fluency.get('speaking_rate_wpm', 'N/A')} words/min")
        pdf.drawString(48, 596, f"Pauses: {fluency.get('pause_count', 0)}")
        pdf.drawString(48, 566, "Pronunciation analysis is experimental and is not a validated score.")
        pdf.save()
        stream.seek(0)
        return send_file(stream, mimetype="application/pdf", as_attachment=True, download_name="saywise-report.pdf")

    @app.get("/api/admin/summary")
    @admin_required
    def admin_summary() -> Any:
        db = database.get_db()
        return jsonify({
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "attempts": db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
            "retained_recordings": db.execute(
                "SELECT COUNT(*) FROM attempts WHERE audio_path IS NOT NULL"
            ).fetchone()[0],
        })

    return app


def serialize_attempt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sentence_text": row["sentence_text"],
        "recognized_text": row["recognized_text"],
        "analysis": json.loads(row["analysis_json"]),
        "created_at": row["created_at"],
    }


def _pdf_text(value: str) -> str:
    return value.encode("latin-1", errors="replace").decode("latin-1")