#!/usr/bin/env python3
"""Thin SQLite access layer shared by the service layer.

SQLite (not Postgres), so a teammate can `git clone` and run the app
without a database server. Parameterized queries only; never build SQL with f-strings.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the original schema was created.

    `CREATE TABLE IF NOT EXISTS` does not alter an existing table, so a
    database created before a column was added would otherwise keep the
    old shape. This runs idempotently on every connection.
    """
    # CREATE TABLE IF NOT EXISTS leaves older per-user profile tables unchanged.
    # Add every editable field so a successful save cannot silently omit it.
    profile_cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(profile)").fetchall()}
    user_cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(users)").fetchall()}
    if 'master_resume' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN master_resume TEXT")
    profile_fields = (
        'first_name', 'last_name', 'full_name', 'email', 'phone', 'school',
        'linkedin', 'github', 'address_line_1', 'city', 'province',
        'postal_code', 'degree_program', 'expected_graduation',
    )
    for field in profile_fields:
        if field not in profile_cols:
            conn.execute(
                f"ALTER TABLE profile ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    for field in ('field_map', 'screening_answers', 'desired_titles', 'skills'):
        if field not in profile_cols:
            conn.execute(
                f"ALTER TABLE profile ADD COLUMN {field} TEXT NOT NULL DEFAULT '[]'")
    application_cols = {row[1] for row in conn.execute(
        'PRAGMA table_info(applications)').fetchall()}
    for field in ('resume_base', 'resume_state', 'tailoring_error', 'cover_letter'):
        if field not in application_cols:
            conn.execute(f'ALTER TABLE applications ADD COLUMN {field} TEXT')


def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call on every startup."""
    with connect():
        pass


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One connection per call. Fine for a single-process hackathon app.

    Runs the schema script on every connection, not just once at process
    startup. schema.sql uses idempotent table/index creation and obsolete cache
    removal, and it means the database self-heals if app.db is
    ever deleted or recreated while a server process is still running
    (which init_db()-once-at-startup does not protect against).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Serialize startup across the website and extension processes. Legacy
        # rows have no owner: retain them intact for explicit recovery later.
        conn.execute('BEGIN IMMEDIATE')
        for table in ('applications', 'profile'):
            columns = conn.execute(f'PRAGMA table_info({table})').fetchall()
            if columns and 'userID' not in {row['name'] for row in columns}:
                conn.execute(f'ALTER TABLE {table} RENAME TO legacy_{table}')
        # execute individually: executescript would commit the migration early.
        statement = ''
        for line in SCHEMA_PATH.read_text().splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ''
        # Ensure any later-added columns exist on older databases.
        _ensure_schema_columns(conn)
        conn.commit()
        yield conn
        conn.commit()
    finally:
        conn.close()
