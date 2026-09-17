"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import tempfile
import unittest
from pathlib import Path

import db
from services.users import SqliteUserService


class UserServiceTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._temp_dir.name) / "test.db"
        db.init_db()
        self.addCleanup(self._restore_db_path)
        self.service = SqliteUserService()

    def _restore_db_path(self):
        db.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def test_create_user_round_trips(self):
        created = self.service.create_user("Jordan Rivera", "jordan@example.com", "password123")

        self.assertEqual(created.name, "Jordan Rivera")
        self.assertEqual(created.email, "jordan@example.com")
        self.assertNotEqual(created.password, "password123")
        self.assertIsNotNone(self.service.verify_credentials(created.email, "password123"))
        self.assertIsNotNone(created.userID)

    def test_create_user_duplicate_email_raises(self):
        self.service.create_user("Jordan Rivera", "jordan@example.com", "password123")
        with self.assertRaises(ValueError):
            self.service.create_user("Someone Else", "jordan@example.com", "another")

    def test_get_user_returns_none_for_missing(self):
        self.assertIsNone(self.service.get_user(999))

    def test_get_user_by_email(self):
        created = self.service.create_user("Jordan Rivera", "jordan@example.com", "password123")
        found = self.service.get_user_by_email("jordan@example.com")

        self.assertEqual(found.userID, created.userID)
        self.assertEqual(found.name, created.name)

    def test_verify_credentials_success(self):
        self.service.create_user("Jordan Rivera", "jordan@example.com", "password123")
        user = self.service.verify_credentials("jordan@example.com", "password123")

        self.assertIsNotNone(user)
        self.assertEqual(user.email, "jordan@example.com")

    def test_verify_credentials_wrong_password(self):
        self.service.create_user("Jordan Rivera", "jordan@example.com", "password123")
        self.assertIsNone(self.service.verify_credentials("jordan@example.com", "wrong"))

    def test_verify_credentials_unknown_email(self):
        self.assertIsNone(self.service.verify_credentials("nobody@example.com", "password123"))

    def test_account_without_local_password_cannot_sign_in(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, NULL)",
                         ("Legacy", "legacy@example.com"))
        self.assertIsNone(self.service.verify_credentials("legacy@example.com", "password123"))
