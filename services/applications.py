"""Concrete ApplicationService: reads/writes the `applications` table.

Every write goes through parameterized SQL and every read is mapped back
into `Application`/`JobPosting` DTOs. Callers never see a raw
sqlite3.Row. Every method takes a `user_id` so each user only ever sees
and modifies their own applications.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import db
from .dto import Application, ApplicationStage, JobPosting
from .interfaces import ApplicationService


def _row_to_application(row: sqlite3.Row) -> Application:
    job = JobPosting(
        company=row["company"],
        title=row["title"],
        url=row["url"],
        description=row["description"],
        location=row["location"],
    )
    date_applied = date.fromisoformat(
        row["date_applied"]) if row["date_applied"] else None
    return Application(
        id=row["id"],
        job=job,
        stage=ApplicationStage(row["stage"]),
        date_applied=date_applied,
        resume_variant=row["resume_variant"],
        resume_base=row["resume_base"],
        resume_state=row["resume_state"],
        tailoring_error=row["tailoring_error"],
        cover_letter=row["cover_letter"],
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class SqliteApplicationService(ApplicationService):
    """Default ApplicationService, backed by the local SQLite database."""

    def list_applications(
        self, user_id: int, stage: ApplicationStage | None = None
    ) -> list[Application]:
        query = "SELECT * FROM applications WHERE userID = ?"
        params: tuple = (user_id,)
        if stage is not None:
            query += " AND stage = ?"
            params = (user_id, stage.value)
        # For bookmarked stage, server-side scoring may reorder results later.
        query += " ORDER BY updated_at DESC"

        with db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            apps = [_row_to_application(row) for row in rows]

        return apps

    def get_application(self, application_id: int, user_id: int) -> Application | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND userID = ?",
                (application_id, user_id),
            ).fetchone()
        return _row_to_application(row) if row else None

    def get_application_by_url(self, url: str, user_id: int) -> Application | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE url = ? AND userID = ?",
                (url, user_id),
            ).fetchone()
        return _row_to_application(row) if row else None

    def update_resume_variant(
        self, application_id: int, user_id: int, resume_variant: str,
        *, only_if_missing: bool = False,
    ) -> Application:
        """Persist compressed LaTeX; compiled PDFs are never stored."""
        with db.connect() as conn:
            condition = ' AND resume_variant IS NULL' if only_if_missing else ''
            cursor = conn.execute(
                """UPDATE applications SET resume_variant = ?, updated_at = datetime('now')
                   WHERE id = ? AND userID = ?""" + condition,
                (resume_variant, application_id, user_id))
            if cursor.rowcount == 0 and not only_if_missing:
                raise ValueError(f"No application with id {application_id}")
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND userID = ?",
                (application_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"No application with id {application_id}")
        return _row_to_application(row)

    def save_resume_draft(self, application_id: int, user_id: int, variant: str,
                          base: str, state: str, *, expected: tuple | None = None) -> Application:
        with db.connect() as conn:
            query = """UPDATE applications SET resume_variant = ?, resume_base = ?,
                   resume_state = ?, tailoring_error = NULL, updated_at = datetime('now')
                   WHERE id = ? AND userID = ?"""
            params = (variant, base, state, application_id, user_id)
            if expected is not None:
                query += ' AND resume_variant IS ? AND resume_state IS ?'
                params += expected
            cursor = conn.execute(
                query, params,
            )
            if cursor.rowcount == 0:
                raise ValueError('The resume changed in another tab. Reload before saving.'
                                 if expected is not None else 'Application not found.')
            row = conn.execute('SELECT * FROM applications WHERE id = ? AND userID = ?',
                               (application_id, user_id)).fetchone()
        return _row_to_application(row)

    def set_tailoring_error(self, application_id: int, user_id: int, message: str) -> None:
        with db.connect() as conn:
            conn.execute('UPDATE applications SET tailoring_error = ? WHERE id = ? AND userID = ?',
                         (message, application_id, user_id))

    def save_resume_metadata(self, application_id: int, user_id: int,
                             base: str, state: str) -> Application:
        """Make a legacy saved variant editable without replacing its PDF source."""
        with db.connect() as conn:
            cursor = conn.execute(
                """UPDATE applications SET resume_base = ?, resume_state = ?
                   WHERE id = ? AND userID = ? AND resume_variant IS NOT NULL""",
                (base, state, application_id, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError('Saved resume not found.')
            row = conn.execute('SELECT * FROM applications WHERE id = ? AND userID = ?',
                               (application_id, user_id)).fetchone()
        return _row_to_application(row)

    def save_cover_letter(self, application_id: int, user_id: int, text: str) -> Application:
        with db.connect() as conn:
            cursor = conn.execute(
                """UPDATE applications SET cover_letter = ?, updated_at = datetime('now')
                   WHERE id = ? AND userID = ?""", (text, application_id, user_id))
            if cursor.rowcount == 0:
                raise ValueError('Application not found.')
            row = conn.execute('SELECT * FROM applications WHERE id = ? AND userID = ?',
                               (application_id, user_id)).fetchone()
        return _row_to_application(row)

    def create_application(
        self,
        job: JobPosting,
        user_id: int,
        stage: ApplicationStage = ApplicationStage.BOOKMARKED,
    ) -> Application:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO applications (userID, company, title, url, description, location, stage, date_applied)
                VALUES (?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'applied' THEN date('now') END)
                ON CONFLICT(userID, url) DO UPDATE SET
                    description = CASE WHEN excluded.description != '' THEN excluded.description
                                       ELSE applications.description END,
                    updated_at = datetime('now')
                """,
                (user_id, job.company, job.title, job.url,
                 job.description, job.location, stage.value, stage.value),
            )
            row = conn.execute(
                "SELECT * FROM applications WHERE userID = ? AND url = ?",
                (user_id, job.url),
            ).fetchone()
        return _row_to_application(row)

    def update_stage(
        self, application_id: int, user_id: int, stage: ApplicationStage
    ) -> Application:
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE applications
                SET stage = ?,
                    date_applied = CASE WHEN ? = 'applied' THEN COALESCE(date_applied, date('now')) ELSE date_applied END,
                    updated_at = datetime('now')
                WHERE id = ? AND userID = ?
                """,
                (stage.value, stage.value, application_id, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No application with id {application_id}")
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND userID = ?",
                (application_id, user_id),
            ).fetchone()
        return _row_to_application(row)

    def update_application(
        self,
        application_id: int,
        user_id: int,
        company: str | None = None,
        title: str | None = None,
        url: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> Application:
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE applications
                SET company = COALESCE(?, company), title = COALESCE(?, title),
                    url = COALESCE(?, url), description = COALESCE(?, description),
                    notes = COALESCE(?, notes), updated_at = datetime('now')
                WHERE id = ? AND userID = ?
                """,
                (company, title, url, description, notes, application_id, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No application with id {application_id}")
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND userID = ?",
                (application_id, user_id),
            ).fetchone()
        return _row_to_application(row)

    def delete_application(self, application_id: int, user_id: int) -> None:
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM applications WHERE id = ? AND userID = ?",
                (application_id, user_id),
            )
