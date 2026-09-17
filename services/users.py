"""Concrete UserService: reads/writes the `users` table.

Passwords are stored as hashed values using Werkzeug's password hashing.
Every read/write goes through parameterized SQL and maps to `User` DTOs.
"""
from __future__ import annotations

import sqlite3
import hmac

from werkzeug.security import check_password_hash, generate_password_hash

import db
from .dto import User
from .interfaces import UserService


def _row_to_user(row: sqlite3.Row) -> User:
    if row is None:
        return None  # type: ignore[return-value]
    return User(
        userID=row["userID"],
        name=row["name"],
        email=row["email"],
        password=row["password"],
    )


class SqliteUserService(UserService):
    """Default UserService, backed by the local SQLite database."""

    def create_user(self, name: str, email: str, password: str) -> User:
        with db.connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                    (name, email, generate_password_hash(password)),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"An account with email {email} already exists.")
            row = conn.execute(
                "SELECT * FROM users WHERE userID = ?", (cursor.lastrowid,)
            ).fetchone()
        return _row_to_user(row)

    def get_user(self, user_id: int) -> User | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE userID = ?", (user_id,)
            ).fetchone()
        return _row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
        return _row_to_user(row) if row else None

    def verify_credentials(self, email: str, password: str) -> User | None:
        user = self.get_user_by_email(email)
        if user is None or user.password is None:
            return None
        if user.password.startswith(("scrypt:", "pbkdf2:")):
            return user if check_password_hash(user.password, password) else None
        if not hmac.compare_digest(user.password.encode(), password.encode()):
            return None
        with db.connect() as conn:
            conn.execute("UPDATE users SET password = ? WHERE userID = ? AND password = ?",
                         (generate_password_hash(password), user.userID, user.password))
        return self.get_user(user.userID)
