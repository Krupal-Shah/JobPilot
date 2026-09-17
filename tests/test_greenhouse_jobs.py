"""Greenhouse board filtering, ranking, and signed-in page behavior."""
import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import db
from services import greenhouse_jobs as gh
from services.profiles import SqliteProfileService
from services.users import SqliteUserService


class GreenhouseJobsTests(unittest.TestCase):
    def setUp(self):
        gh._cache.clear()

    def test_only_junior_software_roles_are_selected(self):
        yes = (
            'Software Engineer Intern', 'Software Engineering Internship',
            'Software Engineer - New Grad', 'Software Engineer I, Backend',
            'Data Engineer Intern', 'Machine Learning Engineer, Early Career',
            'Junior Frontend Developer', 'Data Science Intern',
            'Software Development Engineer Intern', 'SRE Intern',
            'Software Intern', 'Database Administrator Intern',
            'Data Analyst Intern', 'QA Tester Intern',
            'Computer Vision Engineer Co-op',
        )
        no = (
            'Senior Software Engineer, Early Career', 'Staff Data Engineer',
            'Software Engineer II', 'Product Manager Intern',
            'Associate Sales Engineer', 'Software Engineer',
        )
        for title in yes:
            self.assertTrue(gh.is_junior_software_title(title), title)
        for title in no:
            self.assertFalse(gh.is_junior_software_title(title), title)

    def test_canada_location_requires_clear_signal(self):
        yes = ('Remote Canada', 'Toronto', 'Vancouver, BC', 'Montréal, Québec',
               'Hybrid - Ottawa, ON', 'New York; Toronto', 'Canada / United States')
        no = ('Remote', 'In-Office', 'San Francisco, CA', 'London - UK2',
              'Ontario, CA', 'Ontario, California', 'Vancouver, WA',
              'Ottawa, Illinois', 'Remote US', '')
        for location in yes:
            self.assertTrue(gh.is_canadian_location(location), location)
        for location in no:
            self.assertFalse(gh.is_canadian_location(location), location)

    def test_board_fetch_filters_invalid_urls_and_unrelated_roles(self):
        payload = {'jobs': [
            {'id': 1, 'title': 'Data Engineer Intern', 'absolute_url': 'https://example.com/1',
             'location': {'name': 'Remote'}, 'updated_at': '2026-09-12T10:00:00Z'},
            {'id': 2, 'title': 'Senior Software Engineer', 'absolute_url': 'https://example.com/2'},
            {'id': 3, 'title': 'Software Engineer Intern', 'absolute_url': 'javascript:alert(1)'},
        ]}
        data = json.dumps(payload).encode()
        response = BytesIO(data)
        with patch.object(gh, 'urlopen', return_value=response) as opened:
            jobs, error = gh._fetch_board(('Example', 'example'))
        self.assertIsNone(error)
        self.assertEqual([job['title'] for job in jobs], ['Data Engineer Intern'])
        self.assertEqual(jobs[0]['company'], 'Example')
        self.assertEqual(jobs[0]['location'], 'Remote')
        self.assertIn('/v1/boards/example/jobs', opened.call_args.args[0].full_url)

    def test_configurable_boards_cache_and_partial_failure(self):
        with patch.dict('os.environ', {'GREENHOUSE_BOARDS': 'figma,cloudflare,figma'}):
            self.assertEqual(gh.configured_boards(), (('Figma', 'figma'), ('Cloudflare', 'cloudflare')))
        boards = (('A', 'a'), ('B', 'b'))
        job = {'id': 1, 'company': 'A', 'title': 'Software Engineer Intern',
               'location': '', 'url': 'https://example.com/1', 'updated_at': ''}
        with patch.object(gh, '_fetch_board', side_effect=[([job], None), ([], 'B')]) as fetch:
            first = gh.list_jobs(boards)
            second = gh.list_jobs(boards)
        self.assertEqual(first, ([job], ['B']))
        self.assertEqual(second, first)
        self.assertEqual(fetch.call_count, 2)

    def test_desired_titles_come_first_without_mutating_cached_jobs(self):
        jobs = [
            {'company': 'A', 'title': 'Software Engineer Intern', 'updated_at': '2026-09-12T00:00:00Z'},
            {'company': 'B', 'title': 'Data Engineer Intern', 'updated_at': '2026-09-11T00:00:00Z'},
        ]
        ranked = gh.prioritize_jobs(jobs, ['Data Engineer'])
        self.assertEqual(ranked[0]['title'], 'Data Engineer Intern')
        self.assertGreater(ranked[0]['title_match'], 0)
        self.assertNotIn('title_match', jobs[0])


class JobsPageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path_patch = patch.object(db, 'DB_PATH', Path(temp.name) / 'jobs.db')
        path_patch.start()
        self.addCleanup(path_patch.stop)
        from app import app
        app.config.update(TESTING=True, SECRET_KEY='jobs-test')
        self.client = app.test_client()
        self.user = SqliteUserService().create_user('Job Seeker', 'jobs@example.com', 'password')
        profiles = SqliteProfileService()
        profile = profiles.get_profile(self.user.userID)
        profiles.save_profile(self.user.userID, replace(profile, desired_titles=['Data Engineer']))

    def test_page_requires_login_and_uses_saved_titles(self):
        self.assertEqual(self.client.get('/jobs').status_code, 302)
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.userID
        jobs = [
            {'id': 1, 'company': 'A', 'title': 'Software Engineer Intern',
             'location': 'Toronto', 'url': 'https://example.com/software', 'updated_at': ''},
            {'id': 2, 'company': 'B', 'title': 'Data Engineer Intern',
             'location': 'Remote Canada', 'url': 'https://example.com/data', 'updated_at': ''},
            {'id': 3, 'company': 'B', 'title': 'Software Engineer Intern - US',
             'location': 'San Francisco, CA', 'url': 'https://example.com/us', 'updated_at': ''},
            {'id': 4, 'company': 'A', 'title': 'Database Administrator Intern',
             'location': 'Toronto', 'url': 'https://example.com/database', 'updated_at': ''},
        ]
        with patch('routes.jobs.configured_boards', return_value=(('A', 'a'), ('B', 'b'))), \
                patch('routes.jobs.list_jobs', return_value=(jobs, ['B'])):
            response = self.client.get('/jobs')
            filtered = self.client.get('/jobs?q=toronto')
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b'Data Engineer Intern'),
                        response.data.index(b'Software Engineer Intern'))
        self.assertIn(b'Database Administrator Intern', response.data)
        self.assertNotIn(b'Desired-title match', response.data)
        self.assertNotIn(b'Your desired titles', response.data)
        self.assertNotIn(b'Matching titles appear first', response.data)
        self.assertNotIn(b'Some boards could not be loaded', response.data)
        self.assertIn(b'Jobs</a>', response.data)
        self.assertNotIn(b'Software Engineer Intern - US', response.data)
        self.assertIn(b'Software Engineer Intern', filtered.data)
        self.assertNotIn(b'Data Engineer Intern', filtered.data)


if __name__ == '__main__':
    unittest.main()
