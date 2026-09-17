import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta

import db
from services.users import SqliteUserService
from services.applications import SqliteApplicationService
from services.dto import JobPosting


class SortingISOTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)

        from app import app
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        self.client = app.test_client()

        self.users = SqliteUserService()
        self.user = self.users.create_user('Demo', 'demo@example.com', 'password')
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.userID

    def _create(self, company, title, url, description, location=None):
        service = SqliteApplicationService()
        service.create_application(JobPosting(company, title, url, description, location), self.user.userID)

    def test_iso_deadline_sorting(self):
        # Provided example dates
        self._create('X1', 'One', 'https://x1.example', '2026-09-21')
        self._create('X2', 'Two', 'https://x2.example', '2026-09-17')
        self._create('X3', 'Three', 'https://x3.example', '2026-09-30')

        resp = self.client.get('/api/applications', query_string={'stage': 'bookmarked', 'sort': 'deadline'})
        urls = [r['url'] for r in resp.json]
        # Expect 17,21,30 order
        self.assertEqual(urls[:3], ['https://x2.example', 'https://x1.example', 'https://x3.example'])


if __name__ == '__main__':
    unittest.main()
