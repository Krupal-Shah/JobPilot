"""Route and persistence regressions; isolated DB, no real provider requests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import db
from services.users import SqliteUserService
from services.applications import SqliteApplicationService
from services.dto import JobPosting, ApplicationStage
from services.resume_tailoring import compress_latex


class ApiRegressionTests(unittest.TestCase):
    def setUp(self):
        config_patch = patch("routes.extension.configuration", return_value=("test/model", "test-key"))
        config_patch.start()
        self.addCleanup(config_patch.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        config_patch = patch('services.llm.configuration', return_value=('', ''))
        config_patch.start()
        self.addCleanup(config_patch.stop)
        from app import app
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        self.client = app.test_client()
        self.users = SqliteUserService()
        self.user = self.users.create_user('Demo', 'demo@example.com', 'password')
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.userID

    def test_extension_launcher_has_working_auth_templates(self):
        from extension_server import app
        client = app.test_client()
        for route in ('/', '/auth/login', '/auth/register'):
            self.assertEqual(client.get(route).status_code, 302 if route == '/' else 200)

    def test_entry_redirect_and_retired_routes(self):
        self.assertEqual(self.client.get('/').location, '/dashboard')
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/').location, '/auth/login')
        for route in ('/auth/google', '/auth/google/callback', '/api/emails',
                      '/api/applications/email-status', '/betting/'):
            self.assertEqual(self.client.get(route).status_code, 404)

    def test_invalid_json_and_fields_are_client_errors(self):
        for route in ('/api/applications', '/api/extension/scrape',
                      '/api/extension/autofill-plan', '/api/extension/tailor-resume', '/api/extension/log'):
            for payload in ([], [1], 'text', 1):
                with self.subTest(route=route, payload=payload):
                    self.assertEqual(self.client.post(route, json=payload).status_code, 400)
        for route in ('/api/extension/scrape', '/api/extension/tailor-resume'):
            for url in ('https://[', 'javascript:alert(1)'):
                self.assertEqual(self.client.post(route, json={'html': '<p>Job</p>', 'url': url}).status_code, 400)
        for payload in ({'field_labels': [1]}, {'field_labels': ['Essay'], 'field_options': []},
                        {'field_labels': ['Essay'], 'field_options': {'Essay': [1]}}):
            self.assertEqual(self.client.post('/api/extension/autofill-plan', json=payload).status_code, 400)
        for company, url in (([], 'https://example.com'), (' ', 'https://example.com'),
                             ('Demo', 'javascript:alert(1)')):
            self.assertEqual(self.client.post('/api/applications', json={
                'company': company, 'title': 'Role', 'url': url}).status_code, 400)

    def test_scraping_and_deterministic_autofill(self):
        response = self.client.post('/api/extension/scrape', json={
            'html': '<h1>Role</h1>', 'url': 'https://example.com/job', 'text': 'Python developer'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['description'], 'Python developer')
        response = self.client.post('/api/extension/autofill-plan', json={'field_labels': ['First Name']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['filled'], [])
        self.assertEqual(response.json['unmapped'], ['First Name'])

    def test_new_profile_starts_blank_without_fixed_screening_answers(self):
        from services.profiles import SqliteProfileService
        profile = SqliteProfileService().get_profile(self.user.userID)
        for field in ('first_name', 'last_name', 'full_name', 'email', 'phone',
                      'school', 'city', 'province', 'degree_program'):
            self.assertEqual(getattr(profile, field), '', field)
        self.assertEqual(profile.screening_answers, [])
        self.assertEqual(profile.desired_titles, [])
        self.assertEqual(profile.skills, [])
        page = self.client.get('/profile')
        self.assertIn(b'name="first_name" value=""', page.data)
        self.assertIn(b'name="email" value=""', page.data)
        with patch('routes.extension._ai_answer_service') as ai:
            ai.is_available.return_value = True
            response = self.client.post('/api/extension/autofill-plan', json={
                'field_labels': ['Email address', 'Are you at least 18 years of age?']})
            ai.suggest_answer.assert_not_called()
        self.assertEqual(response.json['filled'], [])
        self.assertEqual(response.json['ai_suggested'], [])
        self.assertEqual(response.json['unmapped'],
                         ['Email address', 'Are you at least 18 years of age?'])

    def test_user_isolation_and_stale_session(self):
        service = SqliteApplicationService()
        other = self.users.create_user('Other', 'other@example.com', 'password')
        item = service.create_application(JobPosting('Co', 'Role', 'https://example.com'), other.userID)
        self.assertEqual(self.client.get('/api/applications').json, [])
        self.assertEqual(self.client.patch(f'/api/applications/{item.id}', json={'notes': 'bad'}).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/applications/{item.id}').status_code, 404)
        with self.client.session_transaction() as session:
            session['user_id'] = 99999
        self.assertEqual(self.client.get('/api/applications').status_code, 401)

    def test_applied_date_survives_repeated_transition(self):
        service = SqliteApplicationService()
        item = service.create_application(JobPosting('Co', 'Role', 'https://example.com'),
                                          self.user.userID, ApplicationStage.APPLIED)
        self.assertIsNotNone(item.date_applied)
        with db.connect() as conn:
            conn.execute("UPDATE applications SET date_applied = '2020-01-01' WHERE id = ?", (item.id,))
        item = service.update_stage(item.id, self.user.userID, ApplicationStage.APPLIED)
        self.assertEqual(item.date_applied.isoformat(), '2020-01-01')

    def test_tailoring_uses_captured_description_and_stores_variant(self):
        result = Mock(compressed_latex=compress_latex('example'), pdf=b'%PDF-tailored')
        with patch('routes.extension._resume_tailoring_service.tailor_resume', return_value=result) as tailor, \
                patch('routes.extension.load_master_resume', return_value='master'):
            response = self.client.post('/api/extension/tailor-resume', json={
                'html': '<p>navigation</p>', 'text': 'Actual job', 'url': 'https://example.com'})
        self.assertEqual(response.status_code, 200)
        tailor.assert_called_once_with('Actual job', 'master')
        item = SqliteApplicationService().get_application(response.json['application_id'], self.user.userID)
        self.assertEqual(item.resume_variant, result.compressed_latex)

    def test_corrupt_compressed_resume_returns_useful_error(self):
        service = SqliteApplicationService()
        item = service.create_application(JobPosting('Co', 'Role', 'https://example.com'), self.user.userID)
        service.update_resume_variant(item.id, self.user.userID, 'bm90LXpsaWI=')
        self.assertEqual(self.client.get('/api/extension/resume?url=https://example.com').status_code, 422)

    def test_login_rejects_external_redirects_and_upgrades_legacy_password(self):
        with db.connect() as conn:
            conn.execute('UPDATE users SET password = ? WHERE userID = ?', ('password', self.user.userID))
        with self.client.session_transaction() as session:
            session.clear()
        for target in ('https://evil.example', '//evil.example', '/\\evil.example'):
            response = self.client.post('/auth/login', query_string={'next': target},
                                        data={'email': self.user.email, 'password': 'password'})
            self.assertEqual(response.location, '/dashboard')
            with self.client.session_transaction() as session:
                session.clear()
        self.assertNotEqual(self.users.get_user(self.user.userID).password, 'password')
        self.assertIsNone(self.users.verify_credentials(self.user.email, 'wrong'))


    def test_resume_requires_login_and_never_falls_back_to_demo(self):
        response = self.client.get('/api/extension/resume?url=https://example.com/missing')
        self.assertEqual(response.status_code, 404)
        self.assertIn('No tailored resume', response.json['error'])
        self.assertEqual(self.client.get('/api/extension/resume').status_code, 400)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/api/extension/resume?url=https://example.com').status_code, 401)

    def test_resume_download_is_scoped_to_current_account(self):
        service = SqliteApplicationService()
        other = self.users.create_user('Other', 'other@example.com', 'password')
        job = JobPosting('Co', 'Role', 'https://example.com')
        other_item = service.create_application(job, other.userID)
        service.update_resume_variant(other_item.id, other.userID, compress_latex('other resume'))
        self.assertEqual(self.client.get('/api/extension/resume?url=https://example.com').status_code, 404)
        item = service.create_application(job, self.user.userID)
        service.update_resume_variant(item.id, self.user.userID, compress_latex('my tailored resume'))
        with patch('routes.extension.latex_to_pdf', return_value=b'%PDF-tailored') as compile_pdf:
            response = self.client.get('/api/extension/resume?url=https://example.com')
        compile_pdf.assert_called_once_with('my tailored resume')
        self.assertEqual(response.data, b'%PDF-tailored')
        self.assertIn('tailored_resume.pdf', response.headers['Content-Disposition'])
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_failed_tailoring_does_not_create_a_resume_or_application(self):
        from services.resume_tailoring import TailoringError
        with patch('routes.extension.load_master_resume', side_effect=TailoringError('Master unavailable')):
            response = self.client.post('/api/extension/tailor-resume', json={
                'url': 'https://example.com', 'html': '<p>Job</p>'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(SqliteApplicationService().list_applications(self.user.userID), [])

    def test_profile_displays_account_and_pdf_unavailable_without_latex_source(self):
        with patch('routes.profile.load_master_resume', return_value='<script>example</script>'), \
                patch('routes.profile.shutil.which', return_value=None):
            response = self.client.get('/profile')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'demo@example.com', response.data)
        self.assertNotIn(b'<script>example</script>', response.data)
        self.assertIn(b'PDF preview is unavailable', response.data)
        self.assertNotIn(b'View master resume source', response.data)
        self.assertIn(b'Save autofill details', response.data)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn(self.user.password.encode(), response.data)

    def test_profile_and_master_downloads_require_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        for url in ('/profile', '/profile/master-resume.tex', '/profile/master-resume.pdf'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/auth/login', response.location)

    def test_profile_master_downloads_use_same_source(self):
        with patch('services.resumes.MASTER_RESUME_PATH') as master, \
                patch('services.resumes.latex_to_pdf', return_value=b'%PDF-master') as compile_pdf:
            master.read_text.return_value = 'shared master'
            source = self.client.get('/profile/master-resume.tex')
            pdf = self.client.get('/profile/master-resume.pdf')
        self.assertEqual(source.data, b'shared master')
        self.assertEqual(pdf.data, b'%PDF-master')
        compile_pdf.assert_called_once_with('shared master')

    def test_profile_missing_master_shows_useful_error(self):
        from services.resume_tailoring import TailoringError
        with patch('routes.profile.load_master_resume', side_effect=TailoringError('Master unavailable')):
            response = self.client.get('/profile')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Master unavailable', response.data)
            with patch('routes.profile.get_master_pdf', side_effect=TailoringError('Master unavailable')):
                self.assertEqual(self.client.get('/profile/master-resume.pdf').status_code, 422)


    def _profile_form(self):
        from routes.profile import _DETAIL_FIELDS
        from services.profiles import SqliteProfileService
        self.client.get('/profile')
        with self.client.session_transaction() as session:
            token = session['profile_csrf_token']
        profile = SqliteProfileService().get_profile(self.user.userID)
        return {**{key: getattr(profile, key) for _, key in _DETAIL_FIELDS}, 'csrf_token': token}

    def test_edit_profile_persists_and_is_used_by_autofill(self):
        from services.profiles import SqliteProfileService
        service = SqliteProfileService()
        data = self._profile_form()
        before = service.get_profile(self.user.userID)
        other = self.users.create_user('Other', 'other@example.com', 'password')
        other_before = service.get_profile(other.userID)
        data.update(first_name='Taylor', last_name='Example', full_name='Ignored', phone='', user_id=str(other.userID))
        response = self.client.post('/profile', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Autofill details saved', response.data)
        saved = service.get_profile(self.user.userID)
        self.assertEqual(saved.first_name, 'Taylor')
        self.assertEqual(saved.full_name, 'Taylor Example')
        self.assertNotIn(b'name="full_name"', response.data)
        self.assertEqual(saved.phone, '')
        self.assertEqual(saved.field_map, before.field_map)
        self.assertEqual(saved.screening_answers, before.screening_answers)
        self.assertEqual(service.get_profile(other.userID), other_before)
        plan = self.client.post('/api/extension/autofill-plan', json={'field_labels': ['First Name']})
        self.assertEqual(plan.json['filled'][0]['value'], 'Taylor')
        self.assertEqual(self.users.get_user(self.user.userID).email, self.user.email)

    def test_profile_form_saves_all_fields_and_lists_to_database(self):
        from services.profiles import SqliteProfileService
        data = self._profile_form()
        data.update(
            first_name='Taylor', last_name='Example', email='taylor@example.com',
            phone='555-0107', school='Example University', degree_program='Biology',
            expected_graduation='2027', address_line_1='12 Example Street',
            city='Edmonton', province='Alberta', postal_code='T1T 1T1',
            linkedin='https://example.com/taylor', github='https://example.com/code',
            desired_titles='Analyst, Developer', skills='Python, SQL',
        )

        response = self.client.post('/profile', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.history[0].status_code, 302)
        self.assertIn(b'Autofill details saved', response.data)
        self.assertIn(b'profile-save-confirmation', response.data)
        self.assertIn(b'role="tooltip"', response.data)
        self.assertIn(b'value="Taylor"', response.data)
        self.assertIn(b'Analyst, Developer', response.data)
        self.assertNotIn(b'profile-save-confirmation', self.client.get('/profile').data)

        saved = SqliteProfileService().get_profile(self.user.userID)
        for key in ('first_name', 'last_name', 'email', 'phone', 'school',
                    'degree_program', 'expected_graduation', 'address_line_1',
                    'city', 'province', 'postal_code', 'linkedin', 'github'):
            self.assertEqual(getattr(saved, key), data[key], key)
        self.assertEqual(saved.full_name, 'Taylor Example')
        self.assertEqual(saved.desired_titles, ['Analyst', 'Developer'])
        self.assertEqual(saved.skills, ['Python', 'SQL'])
        with db.connect() as conn:
            row = conn.execute('SELECT phone, city, desired_titles, skills FROM profile WHERE userID = ?',
                               (self.user.userID,)).fetchone()
        self.assertEqual(row['phone'], '555-0107')
        self.assertEqual(row['city'], 'Edmonton')
        self.assertEqual(row['desired_titles'], '["Analyst", "Developer"]')
        self.assertEqual(row['skills'], '["Python", "SQL"]')

    def test_profile_list_length_matches_form_limit(self):
        from services.profiles import SqliteProfileService
        data = self._profile_form()
        data['skills'] = 'S' * 600
        self.assertEqual(self.client.post('/profile', data=data).status_code, 302)
        self.assertEqual(SqliteProfileService().get_profile(self.user.userID).skills, ['S' * 600])

        data['skills'] = 'S' * 1001
        response = self.client.post('/profile', data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Skills (comma-separated) must be 1000 characters or fewer', response.data)
        self.assertEqual(SqliteProfileService().get_profile(self.user.userID).skills, ['S' * 600])

    def test_invalid_profile_is_not_partially_saved(self):
        from services.profiles import SqliteProfileService
        data = self._profile_form()
        service = SqliteProfileService()
        before = service.get_profile(self.user.userID)
        for invalid in ({'email': 'bad'}, {'github': 'javascript:alert(1)'}, {'school': 'a' * 501}):
            response = self.client.post('/profile', data={**data, 'first_name': 'Changed', **invalid})
            self.assertEqual(response.status_code, 400)
            self.assertIn(b'Your changes have not been saved', response.data)
            self.assertEqual(service.get_profile(self.user.userID), before)

    def test_profile_save_requires_valid_csrf(self):
        data = self._profile_form()
        for token in ('', 'wrong', 'é'):
            self.assertEqual(self.client.post('/profile', data={**data, 'csrf_token': token}).status_code, 400)

    def test_profile_embeds_pdf_without_exposing_latex(self):
        with patch('routes.profile.load_master_resume', return_value='LATEX_SOURCE_SENTINEL'), \
                patch('routes.profile.shutil.which', return_value='/usr/bin/pdflatex'):
            response = self.client.get('/profile')
        self.assertIn(b'title="Master resume PDF preview"', response.data)
        self.assertIn(b'/profile/master-resume.pdf', response.data)
        self.assertNotIn(b'LATEX_SOURCE_SENTINEL', response.data)
        self.assertNotIn(b'master-resume.tex', response.data)


    def test_tailoring_returns_pdf_for_upload_but_persists_only_compressed_source(self):
        from services.resume_tailoring import decompress_latex
        result = Mock(compressed_latex=compress_latex('saved source'), pdf=b'%PDF-upload')
        with patch('routes.extension.load_master_resume', return_value='master'), \
                patch('routes.extension._resume_tailoring_service.tailor_resume', return_value=result):
            response = self.client.post('/api/extension/tailor-resume',
                headers={'Accept': 'application/pdf'},
                json={'url': 'https://example.com/job', 'html': 'Job description'})
        self.assertEqual(response.data, b'%PDF-upload')
        service = SqliteApplicationService()
        item = service.get_application_by_url('https://example.com/job', self.user.userID)
        self.assertEqual(decompress_latex(item.resume_variant), 'saved source')
        with db.connect() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('application_resumes', tables)
        self.assertNotIn('master_resume_pdf', tables)
        with patch('routes.extension.latex_to_pdf', return_value=b'%PDF-view') as compiler:
            view = self.client.get('/api/extension/resume?url=https://example.com/job')
        compiler.assert_called_once_with('saved source')
        self.assertEqual(view.data, b'%PDF-view')

    def test_missing_key_uploads_master_and_saves_compressed_snapshot(self):
        source = 'untailored master source'
        with patch('routes.extension.configuration', return_value=('', '')), \
                patch('routes.extension.load_master_resume', return_value=source), \
                patch('routes.extension.latex_to_pdf', return_value=b'%PDF-master') as compiler, \
                patch('routes.extension._resume_tailoring_service.tailor_resume') as tailor:
            response = self.client.post('/api/extension/tailor-resume',
                headers={'Accept': 'application/pdf'},
                json={'url': 'https://example.com/master', 'html': 'Job description'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'%PDF-master')
        self.assertEqual(response.headers['X-Resume-Type'], 'master')
        self.assertIn('master_resume.pdf', response.headers['Content-Disposition'])
        tailor.assert_not_called()
        compiler.assert_called_once_with(source)
        listed = self.client.get('/api/applications').json[0]
        self.assertEqual(listed['resume_variant'], compress_latex(source))
        with patch('routes.extension.latex_to_pdf', return_value=b'%PDF-view') as compiler:
            self.assertEqual(self.client.get(listed['resume_pdf_url']).data, b'%PDF-view')
        compiler.assert_called_once_with(source)

    def test_missing_key_compilation_failure_does_not_save_resume(self):
        from services.resume_tailoring import TailoringError
        with patch('routes.extension.configuration', return_value=('', '')), \
                patch('routes.extension.load_master_resume', return_value='master'), \
                patch('routes.extension.latex_to_pdf', side_effect=TailoringError('Compiler unavailable')):
            response = self.client.post('/api/extension/tailor-resume',
                json={'url': 'https://example.com/job', 'html': 'Job description'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/applications').json, [])

    def test_master_pdf_is_compiled_on_demand(self):
        from services.resumes import get_master_pdf
        with patch('services.resumes.load_master_resume', return_value='master') as source, \
                patch('services.resumes.latex_to_pdf', return_value=b'%PDF-master') as compiler:
            self.assertEqual(get_master_pdf(), b'%PDF-master')
            self.assertEqual(get_master_pdf(), b'%PDF-master')
            self.assertEqual(compiler.call_count, 2)

    def test_old_pdf_tables_removed_without_losing_compressed_source(self):
        service = SqliteApplicationService()
        item = service.create_application(JobPosting('Co', 'Job', 'https://example.com'), self.user.userID)
        service.update_resume_variant(item.id, self.user.userID, compress_latex('preserved'))
        with db.connect() as conn:
            conn.execute('CREATE TABLE application_resumes (pdf BLOB)')
            conn.execute("INSERT INTO application_resumes VALUES (?)", (b'%PDF-old',))
            conn.execute('CREATE TABLE master_resume_pdf (pdf BLOB)')
        db.init_db()
        self.assertEqual(service.get_application(item.id, self.user.userID).resume_variant, compress_latex('preserved'))
        with db.connect() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('application_resumes', tables)
        self.assertNotIn('master_resume_pdf', tables)

    def test_kanban_pdf_link_available_for_compressed_resume_only(self):
        service = SqliteApplicationService()
        item = service.create_application(JobPosting('Co', 'Job', 'https://example.com'), self.user.userID)
        self.assertIsNone(self.client.get('/api/applications').json[0]['resume_pdf_url'])
        service.update_resume_variant(item.id, self.user.userID, compress_latex('saved source'))
        listed = self.client.get('/api/applications').json[0]
        self.assertIsNotNone(listed['resume_pdf_url'])
        with patch('routes.extension.latex_to_pdf', return_value=b'%PDF-view') as compiler:
            self.assertEqual(self.client.get(listed['resume_pdf_url']).data, b'%PDF-view')
        compiler.assert_called_once_with('saved source')

    def test_rebookmark_and_manual_applied_transition_preserve_resume(self):
        service = SqliteApplicationService()
        job = JobPosting('Co', 'Role', 'https://example.com/recovery', 'Job description')
        item = service.create_application(job, self.user.userID)
        service.update_resume_variant(item.id, self.user.userID, compress_latex('resume'))
        moved = self.client.patch(f'/api/applications/{item.id}/stage', json={'stage': 'applied'})
        self.assertEqual(moved.status_code, 200)
        saved = self.client.post('/api/applications', json={
            'company': job.company, 'title': job.title, 'url': job.url, 'description': job.description})
        self.assertEqual(saved.json['id'], item.id)
        self.assertEqual(saved.json['stage'], 'applied')
        self.assertEqual(saved.json['date_applied'], moved.json['date_applied'])
        self.assertEqual(len(service.list_applications(self.user.userID)), 1)
        self.assertEqual(service.get_application(item.id, self.user.userID).resume_variant, compress_latex('resume'))
