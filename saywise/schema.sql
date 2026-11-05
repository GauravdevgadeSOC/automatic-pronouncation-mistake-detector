CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'beginner'
        CHECK (level IN ('beginner', 'intermediate', 'advanced')),
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sentences (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('beginner', 'intermediate', 'advanced'))
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sentence_id INTEGER REFERENCES sentences(id),
    sentence_text TEXT NOT NULL,
    recognized_text TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    audio_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS attempts_user_created ON attempts(user_id, created_at DESC);