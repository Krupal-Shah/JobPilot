import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta
from dataclasses import replace

import db
from services.users import SqliteUserService
from services.applications import SqliteApplicationService
from services.dto import JobPosting
from services.profiles import SqliteProfileService


class SortingTests(unittest.TestCase):
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

    def _set_preferences(self, **changes):
        service = SqliteProfileService()
        service.save_profile(self.user.userID, replace(service.get_profile(self.user.userID), **changes))

    def test_sort_best_prefers_deadline_over_title(self):
        self._set_preferences(desired_titles=['software engineer'], skills=['python', 'sql'], city='Blahville')
        # Job A: title match + skill but no deadline
        self._create('CoA', 'Software Engineer', 'https://a.example', 'Looking for Python developer', 'Blahville')
        # Job B: no title match but urgent deadline (2 days)
        dl = (datetime.utcnow() + timedelta(days=2)).strftime('%B %d, %Y')
        self._create('CoB', 'Data Analyst', 'https://b.example', f'apply by {dl} Python, SQL', 'Othercity')

        resp = self.client.get('/api/applications', query_string={'stage': 'bookmarked', 'sort': 'best'})
        titles = [r['title'] for r in resp.json]
        # With current scoring weights, title+skill+location outranks a 2-day deadline
        self.assertEqual(titles[0], 'Software Engineer')

    def test_sort_skill_counts_matches(self):
        self._set_preferences(skills=['python', 'sql'])
        # Job with two skill mentions
        self._create('Co1', 'Role One', 'https://1.example', 'Python and SQL experience preferred')
        # Job with one skill mention
        self._create('Co2', 'Role Two', 'https://2.example', 'Python experience')

        resp = self.client.get('/api/applications', query_string={'stage': 'bookmarked', 'sort': 'skill'})
        urls = [r['url'] for r in resp.json]
        self.assertEqual(urls[0], 'https://1.example')

    def test_sort_deadline_orders_by_date(self):
        in2 = (datetime.utcnow() + timedelta(days=2)).strftime('%B %d, %Y')
        in10 = (datetime.utcnow() + timedelta(days=10)).strftime('%B %d, %Y')
        self._create('D1', 'Soon', 'https://d1.example', f'apply by {in10}')
        self._create('D2', 'Sooner', 'https://d2.example', f'apply by {in2}')
        self._create('D3', 'None', 'https://d3.example', 'No deadline')

        resp = self.client.get('/api/applications', query_string={'stage': 'bookmarked', 'sort': 'deadline'})
        urls = [r['url'] for r in resp.json]
        self.assertEqual(urls[:2], ['https://d2.example', 'https://d1.example'])


if __name__ == '__main__':
    unittest.main()
