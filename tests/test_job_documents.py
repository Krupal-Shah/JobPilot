"""Job-specific drafts, account boundaries, and autofill handoff."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from services.applications import SqliteApplicationService
from services.resume_tailoring import decompress_latex
from services.resume_tailoring import compress_latex
from services.resumes import load_master_resume, save_master_resume
from services.dto import JobPosting
from services.job_documents import ai_proposal
from services.resume_tailoring import latex_to_pdf, parse_blocks
from services.users import SqliteUserService


class JobDocumentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        model_patch = patch(
            'services.job_documents.configuration', return_value=('', ''))
        model_patch.start()
        self.addCleanup(model_patch.stop)
        from app import app
        app.config.update(TESTING=True, SECRET_KEY='documents-test')
        self.client = app.test_client()
        users = SqliteUserService()
        self.user = users.create_user(
            'Author', 'author@example.com', 'password')
        self.other = users.create_user(
            'Other', 'other@example.com', 'password')
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.userID
        self.client.get('/dashboard')
        with self.client.session_transaction() as session:
            self.csrf = session['documents_csrf']
        self.service = SqliteApplicationService()

    def _create(self):
        response = self.client.post('/api/applications', json={
            'company': 'Example', 'title': 'Python Engineer',
            'url': 'https://example.com/jobs/1',
            'description': 'Build Python APIs using Docker and SQL. Own testing and documentation.',
        })
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def _path(self, app_id, kind='resume'):
        return f'/api/applications/{app_id}/documents/{kind}'

    def _put(self, app_id, kind, payload, csrf=None):
        return self.client.put(self._path(app_id, kind), json=payload,
                               headers={'X-CSRF-Token': self.csrf if csrf is None else csrf})

    def test_bookmark_creates_editable_snapshot_and_saved_edits_survive_autofill(self):
        app_id = self._create()
        application = self.service.get_application(app_id, self.user.userID)
        self.assertTrue(application.resume_variant)
        self.assertTrue(application.resume_base)
        draft = self.client.get(self._path(app_id)).json
        self.assertEqual(len([item for item in draft['entries']
                         if item['visible'] and item['category'] == 'experience']), 2)
        self.assertIsInstance(draft['score']['percent'], int)
        hidden = next(
            item for item in draft['entries'] if not item['visible'] and item['category'] == 'experience')
        draft['state']['visible']['experience'].append(hidden['id'])
        point = hidden['bullets'][0]
        draft['state']['values'][point['id']
                                 ] = 'Built a truthful Python API for internal use.'
        saved = self._put(app_id, 'resume', {
                          'state': draft['state'], 'version': draft['version']})
        self.assertEqual(saved.status_code, 200, saved.json)
        source = decompress_latex(self.service.get_application(
            app_id, self.user.userID).resume_variant)
        self.assertIn('Built a truthful Python API for internal use.', source)
        self.assertEqual(self._put(app_id, 'resume', {
                         'state': draft['state'], 'version': draft['version']}).status_code, 409)
        with patch('routes.extension.latex_to_pdf', return_value=b'%PDF-saved') as compiler:
            response = self.client.post('/api/extension/tailor-resume', json={
                'url': 'https://example.com/jobs/1', 'html': '<p>Python job</p>'},
                headers={'Accept': 'application/pdf'})
        self.assertEqual(response.data, b'%PDF-saved')
        compiler.assert_called_once_with(source)

    def test_bookmark_hides_optional_sections_and_ranks_limited_points(self):
        source = load_master_resume(self.user.userID)
        additions = (r'\section{Profile Summary}' + '\n'
                     r'\resumeSubHeadingListStart\item Fictional profile.\resumeSubHeadingListEnd' + '\n'
                     r'\section{Publications}' + '\n'
                     r'\resumeSubHeadingListStart'
                     r'\resumePublicationItem{Sample}{Venue}{https://example.com}{2026}'
                     r'\resumeSubHeadingListEnd' + '\n'
                     r'\section{Certifications}' + '\n'
                     r'\resumeSubHeadingListStart'
                     r'\resumeProjectHeading{Sample certificate}{2026}'
                     r'\resumeItemListStart\resumeItem{Completed training.}'
                     r'\resumeItemListEnd\resumeSubHeadingListEnd' + '\n')
        save_master_resume(self.user.userID,
                           source.replace(r'\section{Education}', additions + r'\section{Education}', 1))
        app_id = self._create()
        draft = self.client.get(self._path(app_id)).json
        self.assertEqual(len([entry for entry in draft['entries']
                              if entry['category'] == 'experience' and entry['visible']]), 2)
        self.assertEqual(len([entry for entry in draft['entries']
                              if entry['category'] == 'projects' and entry['visible']]), 1)
        self.assertEqual(len([entry for entry in draft['entries']
                              if entry['category'] == 'leadership' and entry['visible']]), 1)
        self.assertEqual({section['id'] for section in draft['sections']},
                         {'profile summary', 'publications', 'certifications'})
        self.assertTrue(all(not section['visible']
                        for section in draft['sections']))
        for entry in draft['entries']:
            count = sum(point['visible'] for point in entry['bullets'])
            self.assertLessEqual(count, {'experience': 4, 'projects': 3, 'leadership': 3}[
                                 entry['category']])
        saved = decompress_latex(self.service.get_application(
            app_id, self.user.userID).resume_variant)
        for title in ('Profile Summary', 'Publications', 'Certifications'):
            self.assertNotIn('\n\\section{' + title + '}', saved)
        draft['state']['visible_sections']['profile summary'] = True
        saved_response = self._put(app_id, 'resume', {
            'state': draft['state'], 'version': draft['version']})
        self.assertEqual(saved_response.status_code, 200, saved_response.json)
        restored = decompress_latex(self.service.get_application(
            app_id, self.user.userID).resume_variant)
        self.assertIn('\n\\section{Profile Summary}', restored)
        self.assertNotIn('\n\\section{Publications}', restored)

    def test_live_preview_uses_unsaved_state_without_overwriting_saved_resume(self):
        app_id = self._create()
        before = self.service.get_application(
            app_id, self.user.userID).resume_variant
        draft = self.client.get(self._path(app_id)).json
        entry = next(
            item for item in draft['entries'] if item['visible'] and item['category'] == 'experience')
        point = next(item for item in entry['bullets'] if item['visible'])
        draft['state']['values'][point['id']] = 'Built a tested Python API.'
        with patch('routes.documents.latex_to_pdf', return_value=b'%PDF-preview') as compiler:
            response = self.client.post(self._path(app_id) + '/preview', json={
                'state': draft['state'], 'version': draft['version']},
                headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Built a tested Python API.', compiler.call_args.args[0])
        self.assertEqual(self.service.get_application(
            app_id, self.user.userID).resume_variant, before)
        self.assertEqual(self.client.post(self._path(app_id) + '/preview', json={
            'state': draft['state'], 'version': draft['version']}).status_code, 400)

    def test_bookmark_uses_ai_ranking_when_configured(self):
        blocks = parse_blocks(load_master_resume(self.user.userID))
        rankings = {category: [block.id for block in reversed(items)]
                    for category, items in blocks.items()}
        with patch('services.job_documents.configuration', return_value=('test/model', 'key')), \
                patch('services.job_documents.rank_resume', return_value=rankings) as ranker:
            app_id = self._create()
        ranker.assert_called_once()
        draft = self.client.get(self._path(app_id)).json
        self.assertEqual(draft['selection_method'], 'AI')
        self.assertEqual(draft['state']['visible']
                         ['experience'], rankings['experience'][:2])

    def test_ai_ranking_failure_keeps_bookmark_and_uses_keyword_fallback(self):
        from services.resume_tailoring import TailoringError
        with patch('services.job_documents.configuration', return_value=('test/model', 'key')), \
                patch('services.job_documents.rank_resume', side_effect=TailoringError('provider failed')):
            app_id = self._create()
        draft = self.client.get(self._path(app_id)).json
        self.assertEqual(draft['selection_method'], 'keyword')
        self.assertTrue(self.service.get_application(
            app_id, self.user.userID).resume_variant)

    def test_add_remove_points_and_reject_invalid_visibility(self):
        app_id = self._create()
        draft = self.client.get(self._path(app_id)).json
        entry = next(
            item for item in draft['entries'] if item['category'] == 'projects' and item['visible'])
        structure = draft['state']['structure']['projects'][int(
            entry['id'].split(':')[1])]
        new_id = f"{structure['id']}_bullet_new_1"
        structure['bullets'].append(new_id)
        draft['state']['visible_points'][structure['id']].append(new_id)
        draft['state']['values'][new_id] = 'Designed a small internal tool.'
        response = self._put(app_id, 'resume', {
                             'state': draft['state'], 'version': draft['version']})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn('Designed a small internal tool.', decompress_latex(
            self.service.get_application(app_id, self.user.userID).resume_variant))
        current = self.client.get(self._path(app_id)).json
        visible = next(
            item for item in current['entries'] if item['category'] == 'experience' and item['visible'])
        removed = visible['bullets'][0]
        structure_entry = current['state']['structure']['experience'][int(
            visible['id'].split(':')[1])]
        structure_entry['bullets'].remove(removed['id'])
        current['state']['visible_points'][structure_entry['id']].remove(
            removed['id'])
        current['state']['values'].pop(removed['id'])
        response = self._put(app_id, 'resume', {
            'state': current['state'], 'version': current['version']})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotIn(removed['text'], decompress_latex(
            self.service.get_application(app_id, self.user.userID).resume_variant))
        current = self.client.get(self._path(app_id)).json
        current['state']['visible']['projects'] = []
        self.assertEqual(self._put(app_id, 'resume', {
            'state': current['state'], 'version': current['version']}).status_code, 422)

    def test_cover_letter_saved_for_pdf_and_cold_email_not_persisted(self):
        app_id = self._create()
        cover = self.client.get(self._path(app_id, 'cover-letter')).json
        self.assertFalse(cover['saved'])
        self.assertIn('Python Engineer', cover['text'])
        self.assertEqual(self.client.get(self._path(
            app_id, 'cover-letter.pdf')).status_code, 404)
        text = 'Dear Hiring Manager,\nI built Python APIs & used 50% less memory.\nNo \\input{secret}'
        self.assertEqual(self._put(app_id, 'cover-letter',
                         {'text': text}).status_code, 200)
        self.assertEqual(self.client.get(self._path(
            app_id, 'cover-letter')).json['text'], text)
        with patch('routes.documents.latex_to_pdf', wraps=latex_to_pdf) as compiler:
            pdf = self.client.get(self._path(app_id, 'cover-letter.pdf'))
        self.assertTrue(pdf.data.startswith(b'%PDF'))
        self.assertIn('Jake Ryan', compiler.call_args.args[0])
        self.assertIn('https://example.com/portfolio',
                      compiler.call_args.args[0])
        self.assertIn(r'Python APIs \& used 50\% less memory',
                      compiler.call_args.args[0])
        self.assertIn(r'\textbackslash{}input\{secret\}',
                      compiler.call_args.args[0])
        self.assertIn('Dear [Hiring Manager Name]', self.client.get(
            self._path(app_id, 'cold-email')).json['text'])
        self.assertEqual(self.service.get_application(
            app_id, self.user.userID).cover_letter, text)

    def test_cover_letter_preview_renders_unsaved_text(self):
        app_id = self._create()
        response = self.client.post(self._path(app_id, 'cover-letter') + '/preview', json={
            'text': 'Dear Hiring Manager,\n\nI built Python APIs for internal users.'},
            headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200,
                         response.json if response.is_json else response.data[:100])
        self.assertIn('application/pdf', response.content_type)
        self.assertTrue(response.data.startswith(b'%PDF'))
        self.assertIsNone(self.service.get_application(
            app_id, self.user.userID).cover_letter)

    def test_cover_letter_uses_uploaded_header_and_enforces_word_limit(self):
        source = load_master_resume(self.user.userID).replace(
            'Jake Ryan', 'Taylor Example')
        save_master_resume(self.user.userID, source)
        app_id = self._create()
        too_long = 'Dear Hiring Manager,\n\n' + 'word ' * 351
        self.assertEqual(self._put(app_id, 'cover-letter',
                         {'text': too_long}).status_code, 400)
        self.assertEqual(self._put(app_id, 'cover-letter', {
            'text': 'Dear Hiring Manager,\n\nI am applying for the role.\n\nSincerely,\nTaylor Example'
        }).status_code, 200)
        with patch('routes.documents.latex_to_pdf', wraps=latex_to_pdf) as compiler:
            response = self.client.get(self._path(app_id, 'cover-letter.pdf'))
        self.assertEqual(response.status_code, 200,
                         response.json if response.is_json else '')
        self.assertIn(
            r'\href{https://example.com/portfolio}{Portfolio}', compiler.call_args.args[0])
        self.assertIn(r'\textbf{\Large Taylor Example}',
                      compiler.call_args.args[0])

    def test_cross_account_writes_csrf_and_ai_suggestions(self):
        app_id = self._create()
        draft = self.client.get(self._path(app_id)).json
        self.assertEqual(self._put(app_id, 'resume', {
            'state': draft['state'], 'version': draft['version']}, csrf='wrong').status_code, 400)
        with patch('routes.documents.ai_proposal', return_value='Clearer point') as ai:
            result = self.client.post(self._path(app_id, 'resume') + '/suggest', json={
                'current': 'Current point', 'instruction': 'Improve clarity',
                'point_id': 'experience_1_bullet_1', 'mode': 'inline'},
                headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(result.json['proposal'], 'Clearer point')
        ai.assert_called_once()
        with self.client.session_transaction() as session:
            session['user_id'] = self.other.userID
        for kind in ('resume', 'cover-letter', 'cold-email'):
            self.assertEqual(self.client.get(
                self._path(app_id, kind)).status_code, 404)
        self.assertEqual(self._put(app_id, 'cover-letter',
                         {'text': 'Other'}).status_code, 404)
        self.assertEqual(self.client.get(self._path(
            app_id, 'cover-letter.pdf')).status_code, 404)

    def test_existing_saved_resume_is_made_editable_without_replacing_source(self):
        app = self.service.create_application(JobPosting('Example', 'Engineer',
                                                         'https://example.com/old', 'Python'), self.user.userID)
        old = compress_latex(load_master_resume(self.user.userID))
        self.service.update_resume_variant(app.id, self.user.userID, old)
        response = self.client.get(self._path(app.id))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.service.get_application(
            app.id, self.user.userID).resume_variant, old)
        self.assertEqual(response.json['selection_method'], 'existing')

    def test_explicit_reset_rebases_job_draft_on_current_master(self):
        app_id = self._create()
        before = self.service.get_application(
            app_id, self.user.userID).resume_variant
        save_master_resume(self.user.userID,
                           load_master_resume(self.user.userID).replace('Jake Ryan', 'Taylor Example'))
        response = self.client.post(self._path(app_id) + '/reset', json={},
                                    headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200, response.json)
        saved = self.service.get_application(
            app_id, self.user.userID).resume_variant
        self.assertNotEqual(before, saved)
        self.assertIn('Taylor Example', decompress_latex(saved))

    def test_three_ai_actions_use_distinct_suggestion_and_inline_prompts(self):
        prompts = []

        def completion(messages, **kwargs):
            prompts.append(messages[0]['content'])
            return 'Suggested text'
        with patch('services.job_documents.complete_text', side_effect=completion):
            for kind in ('resume', 'cover_letter', 'cold_email'):
                for mode in ('suggestion', 'inline'):
                    self.assertEqual(ai_proposal(kind, 'Python role', 'Existing text', 'Improve',
                                                 mode=mode), 'Suggested text')
        self.assertEqual(len(set(prompts)), 6)


if __name__ == '__main__':
    unittest.main()
