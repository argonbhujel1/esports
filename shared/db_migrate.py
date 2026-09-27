"""Lightweight schema patches for existing Neon/Postgres DBs (no full drop)."""
from sqlalchemy import text

def run_migrations(db):
    """Add missing columns/tables safely. Idempotent."""
    statements = [
        # rooms result columns
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS winner_user_id INTEGER REFERENCES users(id)",
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS result_status VARCHAR(30) DEFAULT 'NONE'",
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS result_notes TEXT",
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS prize_paid BOOLEAN DEFAULT FALSE",
        # room_players game_uid
        "ALTER TABLE room_players ADD COLUMN IF NOT EXISTS game_uid VARCHAR(100)",
        # game_profiles table
        """
        CREATE TABLE IF NOT EXISTS game_profiles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            game_id INTEGER NOT NULL REFERENCES games(id),
            game_uid VARCHAR(100) NOT NULL,
            display_name VARCHAR(100),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (user_id, game_id)
        )
        """,
        # users avatar if missing
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar VARCHAR(500)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_hash VARCHAR(255)",
        # load_requests receipt
        "ALTER TABLE load_requests ADD COLUMN IF NOT EXISTS receipt_url VARCHAR(500)",
    ]
    conn = db.engine.connect()
    try:
        for sql in statements:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()
    finally:
        conn.close()
