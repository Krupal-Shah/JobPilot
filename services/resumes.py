"""Per-account master LaTeX source stored in SQLite."""
from pathlib import Path

import db

from .resume_tailoring import TailoringError, latex_to_pdf

MASTER_RESUME_PATH = Path(__file__).resolve(
).parent.parent / 'assets' / 'resumeTemplate.tex'


def master_file_path(user_id: int) -> Path:
    """Return the private per-user asset path (also isolated by temporary test DBs)."""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError('A valid user ID is required.')
    return Path(db.DB_PATH).parent / 'assets' / 'master_resumes' / f'{user_id}.tex'


def load_master_resume(user_id=None):
    if user_id is not None:
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError('A valid user ID is required.')
        with db.connect() as conn:
            row = conn.execute(
                'SELECT master_resume FROM users WHERE userID = ?', (user_id,)
            ).fetchone()
            if row is not None and row['master_resume']:
                source = row['master_resume']
            else:
                legacy = conn.execute(
                    'SELECT latex FROM master_resumes WHERE userID = ?', (
                        user_id,)
                ).fetchone()
                source = legacy['latex'] if legacy is not None else None
                if source:
                    conn.execute('UPDATE users SET master_resume = ? WHERE userID = ?',
                                 (source, user_id))
        if source:
            if not source.strip():
                raise TailoringError(
                    'Your saved master resume is empty. Upload a valid .tex file.')
            return source

        # Read-only migration for installations that still have file-backed masters.
        path = master_file_path(user_id)
        if path.exists():
            try:
                source = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                raise TailoringError(
                    'Your saved master resume could not be read.') from None
            if not source.strip():
                raise TailoringError(
                    'Your saved master resume is empty. Upload a valid .tex file.')
            with db.connect() as conn:
                conn.execute('UPDATE users SET master_resume = ? WHERE userID = ?',
                             (source, user_id))
            return source
    try:
        source = MASTER_RESUME_PATH.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise TailoringError(
            'The master resume could not be read. Check assets/resumeTemplate.tex.') from None
    if not source.strip():
        raise TailoringError(
            'The master resume is empty. Update assets/resumeTemplate.tex.')
    return source


def save_master_resume(user_id, latex):
    if not isinstance(latex, str) or not latex.strip():
        raise TailoringError('The master resume cannot be empty.')
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError('A valid user ID is required.')
    with db.connect() as conn:
        cursor = conn.execute('UPDATE users SET master_resume = ? WHERE userID = ?',
                              (latex, user_id))
        if cursor.rowcount == 0:
            raise TailoringError(
                'The account for this master resume does not exist.')


def get_master_pdf(user_id=None):
    """Compile the current master for viewing without storing a PDF copy."""
    return latex_to_pdf(load_master_resume(user_id))
