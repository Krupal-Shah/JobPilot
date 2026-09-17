import sqlite3
import tempfile
from pathlib import Path

import db
from services.users import SqliteUserService
from services.profiles import SqliteProfileService


def test_db_migrations_add_columns_and_profile_seed_and_save():
    td = tempfile.TemporaryDirectory()
    orig = db.DB_PATH
    try:
        db.DB_PATH = Path(td.name) / "legacy.db"
        # and profile without desired_titles/skills.
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute(
            "CREATE TABLE users (userID INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password TEXT)"
        )
        conn.execute("CREATE TABLE profile (userID INTEGER PRIMARY KEY, first_name TEXT)")
        conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                     ("Legacy", "legacy@example.com", "plain"))
        conn.commit()
        conn.close()

        # Trigger connect() which should run profile migrations.
        with db.connect() as migrated_conn:
            # Check profile table now has desired_titles and skills
            profile_cols = {r[1] for r in migrated_conn.execute("PRAGMA table_info(profile)").fetchall()}
            assert 'desired_titles' in profile_cols
            assert 'skills' in profile_cols

        # Ensure the users service can read the existing user without raising
        svc = SqliteUserService()
        user = svc.get_user_by_email("legacy@example.com")
        assert user is not None
        assert user.email == "legacy@example.com"

        # Ensure profile service seeds defaults and can save/reload desired_titles and skills
        psvc = SqliteProfileService()
        profile = psvc.get_profile(user.userID)
        assert isinstance(profile.desired_titles, list)
        assert isinstance(profile.skills, list)

        # Modify and persist (dataclass instances may be immutable in some
        # configurations; use dataclasses.replace to create a modified copy).
        from dataclasses import replace

        new_profile = replace(profile, desired_titles=["Engineer", "Developer"], skills=["Python", "SQL"])
        psvc.save_profile(user.userID, new_profile)

        reloaded = psvc.get_profile(user.userID)
        assert reloaded.desired_titles == ["Engineer", "Developer"]
        assert reloaded.skills == ["Python", "SQL"]

    finally:
        db.DB_PATH = orig
        td.cleanup()
