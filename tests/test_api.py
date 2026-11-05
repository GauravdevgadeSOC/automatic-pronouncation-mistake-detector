import re
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from saywise import create_app
from saywise.database import get_db


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(root / "test.sqlite3"),
            "UPLOAD_FOLDER": str(root / "recordings"),
        })
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as browser_session:
            self.csrf_token = browser_session["csrf_token"]

    def tearDown(self):
        self.temporary_directory.cleanup()

    def headers(self):
        return {"X-CSRF-Token": self.csrf_token}

    def register(self):
        return self.client.post(
            "/api/register",
            json={
                "display_name": "Test Student",
                "email": "student@example.test",
                "password": "a-long-test-password",
                "level": "beginner",
            },
            headers=self.headers(),
        )

    def test_homepage_renders(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Saywise", response.data)
        self.assertIn(b"There are no length limits.", response.data)

    def test_mutation_requires_csrf(self):
        response = self.client.post("/api/register", json={})

        self.assertEqual(response.status_code, 403)

    def test_register_hashes_password_and_lists_sentences(self):
        response = self.register()

        self.assertEqual(response.status_code, 201)
        self.assertIn("csrf_token", response.json)
        with self.app.app_context():
            user = get_db().execute("SELECT password_hash FROM users").fetchone()
        self.assertNotEqual(user["password_hash"], "a-long-test-password")
        sentences = self.client.get("/api/sentences")
        self.assertEqual(sentences.status_code, 200)
        self.assertGreater(len(sentences.json["sentences"]), 0)

    def test_login_rejects_wrong_password_and_accepts_correct_password(self):
        registration = self.register()
        self.csrf_token = registration.json["csrf_token"]
        credentials = {"email": "student@example.test", "password": "wrong-password"}

        rejected = self.client.post("/api/login", json=credentials, headers=self.headers())
        self.assertEqual(rejected.status_code, 401)

        credentials["password"] = "a-long-test-password"
        accepted = self.client.post("/api/login", json=credentials, headers=self.headers())
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json["user"]["email"], "student@example.test")

    def test_private_routes_require_login(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 401)

    def test_daily_challenge_matches_user_level(self):
        self.register()

        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["daily_challenge"]["level"], "beginner")

    def test_registration_rejects_short_password(self):
        response = self.client.post(
            "/api/register",
            json={"display_name": "Test", "email": "test@example.test", "password": "short"},
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 400)

    def test_custom_paragraph_is_analyzed_and_saved_without_sentence_id(self):
        registration = self.register()
        self.csrf_token = registration.json["csrf_token"]
        custom_text = "A paragraph with several sentences. It should be saved exactly as entered."
        analysis = {"transcript": "recognized words", "word_comparison": {"edits": []}, "feedback": []}

        with patch("saywise.decode_audio", return_value=([], 16_000, 2.0)), patch(
            "saywise.analyze_recording", return_value=analysis
        ) as analyze_recording:
            response = self.client.post(
                "/api/analyze",
                data={
                    "audio": (BytesIO(b"audio"), "practice.webm"),
                    "custom_text": custom_text,
                },
                headers=self.headers(),
            )

        self.assertEqual(response.status_code, 201)
        analyze_recording.assert_called_once()
        self.assertEqual(analyze_recording.call_args.args[-1], custom_text)
        with self.app.app_context():
            attempt = get_db().execute(
                "SELECT sentence_id, sentence_text FROM attempts"
            ).fetchone()
        self.assertIsNone(attempt["sentence_id"])
        self.assertEqual(attempt["sentence_text"], custom_text)

    def test_custom_prompt_must_not_be_empty(self):
        registration = self.register()
        self.csrf_token = registration.json["csrf_token"]

        response = self.client.post(
            "/api/analyze",
            data={
                "audio": (BytesIO(b"audio"), "practice.webm"),
                "custom_text": "   ",
            },
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "Enter a sentence or paragraph to practice.")

    def test_existing_database_migrates_attempts_for_custom_prompts(self):
        root = Path(self.temporary_directory.name)
        database_path = root / "legacy.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'beginner',
                    role TEXT NOT NULL DEFAULT 'student',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE sentences (
                    id INTEGER PRIMARY KEY,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    level TEXT NOT NULL
                );
                CREATE TABLE attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    sentence_id INTEGER NOT NULL REFERENCES sentences(id),
                    sentence_text TEXT NOT NULL,
                    recognized_text TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    audio_path TEXT,
                    created_at TEXT NOT NULL
                );
                INSERT INTO users (email, display_name, password_hash)
                    VALUES ('legacy@example.test', 'Legacy User', 'hash');
                INSERT INTO sentences (id, text, category, level)
                    VALUES (1, 'Old prompt', 'Everyday', 'beginner');
                INSERT INTO attempts
                    (user_id, sentence_id, sentence_text, recognized_text,
                     analysis_json, created_at)
                    VALUES (1, 1, 'Old prompt', 'Old transcript', '{}', '2025-01-01');
                CREATE INDEX attempts_user_created ON attempts(user_id, created_at DESC);
                """
            )
        finally:
            connection.close()

        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(database_path),
            "UPLOAD_FOLDER": str(root / "legacy-recordings"),
        })
        with app.app_context():
            db = get_db()
            old_attempt = db.execute(
                "SELECT sentence_id, sentence_text FROM attempts WHERE id = 1"
            ).fetchone()
            db.execute(
                "INSERT INTO attempts (user_id, sentence_text, recognized_text, "
                "analysis_json, created_at) VALUES (1, ?, ?, ?, ?)",
                ("A custom paragraph.", "Recognized.", "{}", "2025-01-02"),
            )
            db.commit()
            new_attempt = db.execute(
                "SELECT sentence_id, sentence_text FROM attempts WHERE id = 2"
            ).fetchone()

        self.assertEqual(old_attempt["sentence_id"], 1)
        self.assertEqual(old_attempt["sentence_text"], "Old prompt")
        self.assertIsNone(new_attempt["sentence_id"])
        self.assertEqual(new_attempt["sentence_text"], "A custom paragraph.")


if __name__ == "__main__":
    unittest.main()