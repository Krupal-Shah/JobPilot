import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta

import db
from services.users import SqliteUserService
from services.applications import SqliteApplicationService
from services.dto import JobPosting


class SortingExtraTests(unittest.TestCase):
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

    def test_deadline_numeric_days(self):
        # Create three jobs with "30 days", "17 days", "21 days" phrases
        self._create('N1', 'Job30', 'https://n30.example', 'apply by 30 days')
        self._create('N2', 'Job17', 'https://n17.example', 'apply by 17 days')
        self._create('N3', 'Job21', 'https://n21.example', 'apply by 21 days')

        resp = self.client.get('/api/applications', query_string={'stage': 'bookmarked', 'sort': 'deadline'})
        urls = [r['url'] for r in resp.json]
        # Expect soonest first
        self.assertEqual(urls[:3], ['https://n17.example', 'https://n21.example', 'https://n30.example'])


if __name__ == '__main__':
    unittest.main()
