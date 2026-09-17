"""Per-user profile storage for application autofill."""
from __future__ import annotations

import json
import sqlite3

import db
from .dto import CandidateProfile, FieldMapRule, ScreeningAnswerRule
from .interfaces import ProfileService

DEFAULT_FIELD_MAP = (
    FieldMapRule("first_name", "first_name", [r"\bfirst name\b"]),
    FieldMapRule("last_name", "last_name", [r"\blast name\b"]),
    FieldMapRule("full_name", "full_name", [r"\bfull name\b", r"\blegal name\b"]),
    FieldMapRule("email", "email", [r"\bemail\b"]),
    FieldMapRule("phone", "phone", [r"\bphone\b", r"\btelephone\b"]),
    FieldMapRule("school", "school", [r"\bschool\b", r"\buniversity\b"]),
    FieldMapRule("linkedin", "linkedin", [r"\blinkedin\b"]),
    FieldMapRule("github", "github", [r"\bgithub\b"]),
    FieldMapRule("address_line_1", "address_line_1", [r"\bstreet address\b", r"^address(?: line 1)?$"]),
    FieldMapRule("city", "city", [r"\bcity\b"]),
    FieldMapRule("province", "province", [r"\bprovince\b", r"\bstate\b"]),
    FieldMapRule("postal_code", "postal_code", [r"\bpostal code\b", r"\bzip code\b"]),
    FieldMapRule("degree_program", "degree_program", [r"\bdegree program\b", r"\bmajor\b"]),
    FieldMapRule("expected_graduation", "expected_graduation", [r"\bexpected graduation\b", r"\bgraduation date\b"]),
)


def _seed_if_empty(conn: sqlite3.Connection, user_id: int) -> None:
    # Serialize first-read creation across the app and extension processes.
    conn.execute("BEGIN IMMEDIATE")
    already_seeded = conn.execute(
        "SELECT 1 FROM profile WHERE userID = ?", (user_id,)
    ).fetchone()
    if already_seeded is not None:
        return

    conn.execute(
        """INSERT INTO profile (userID, first_name, last_name, email, phone, school)
           VALUES (?, '', '', '', '', '')""",
        (user_id,),
    )


def _row_to_profile(row: sqlite3.Row) -> CandidateProfile:
    # Defensive read: older databases may lack some columns. Use defaults when absent.
    cols = set(row.keys()) if row is not None else set()

    def _safe(col, default=""):
        return row[col] if (col in cols and row[col] is not None) else default

    def _safe_json(col):
        if col in cols and row[col]:
            try:
                return json.loads(row[col])
            except Exception:
                return []
        return []

    return CandidateProfile(
        first_name=_safe("first_name"),
        last_name=_safe("last_name"),
        full_name=_safe("full_name"),
        email=_safe("email"),
        phone=_safe("phone"),
        school=_safe("school"),
        linkedin=_safe("linkedin"),
        github=_safe("github"),
        address_line_1=_safe("address_line_1"),
        city=_safe("city"),
        province=_safe("province"),
        postal_code=_safe("postal_code"),
        degree_program=_safe("degree_program"),
        expected_graduation=_safe("expected_graduation"),
        field_map=[FieldMapRule(**rule) for rule in _safe_json("field_map")] or list(DEFAULT_FIELD_MAP),
        screening_answers=[
            ScreeningAnswerRule(**rule) for rule in _safe_json("screening_answers")
        ],
        desired_titles=_safe_json("desired_titles"),
        skills=_safe_json("skills"),
    )


class SqliteProfileService(ProfileService):
    """Default ProfileService, backed by the local SQLite database."""

    def get_profile(self, user_id: int) -> CandidateProfile:
        with db.connect() as conn:
            _seed_if_empty(conn, user_id)
            row = conn.execute(
                "SELECT * FROM profile WHERE userID = ?", (user_id,)
            ).fetchone()
        return _row_to_profile(row)

    def save_profile(self, user_id: int, profile: CandidateProfile) -> CandidateProfile:
        with db.connect() as conn:
            _seed_if_empty(conn, user_id)
            conn.execute(
                """
                UPDATE profile
                SET first_name = ?, last_name = ?, full_name = ?, email = ?, phone = ?, school = ?,
                    linkedin = ?, github = ?, address_line_1 = ?, city = ?, province = ?,
                    postal_code = ?, degree_program = ?, expected_graduation = ?,
                    field_map = ?, screening_answers = ?, desired_titles = ?, skills = ?
                WHERE userID = ?
                """,
                (
                    profile.first_name, profile.last_name, profile.full_name,
                    profile.email, profile.phone, profile.school, profile.linkedin,
                    profile.github, profile.address_line_1, profile.city,
                    profile.province, profile.postal_code, profile.degree_program,
                    profile.expected_graduation,
                    json.dumps([rule.__dict__ for rule in profile.field_map]),
                    json.dumps([rule.__dict__ for rule in profile.screening_answers]),
                    json.dumps(profile.desired_titles or []),
                    json.dumps(profile.skills or []),
                    user_id,
                ),
            )
        return profile
