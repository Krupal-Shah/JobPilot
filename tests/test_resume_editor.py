"""Resume editing, persistence, and account isolation."""
import tempfile
import unittest
import hashlib
import json
from io import BytesIO
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import db
from services.resume_editor import apply_editor_values, default_structure, parse_editor_document
from services.resume_tailoring import TailoringError, latex_to_pdf, parse_blocks
from services.resumes import load_master_resume, MASTER_RESUME_PATH
from services.profiles import SqliteProfileService
from services.resume_import import profile_updates_from_latex
from services.users import SqliteUserService


class ResumeEditorTests(unittest.TestCase):
    @staticmethod
    def extended_source():
        source = MASTER_RESUME_PATH.read_text(encoding='utf-8')
        optional = (r'\section{Profile Summary}' + '\n'
                    r'\resumeSubHeadingListStart\item Fictional software graduate.' + '\n'
                    r'\resumeSubHeadingListEnd' + '\n'
                    r'\section{Publications}' + '\n'
                    r'\resumeSubHeadingListStart'
                    r'\resumePublicationItem{Example paper}{Example venue}{https://example.com/paper}{2026}'
                    r'\resumeSubHeadingListEnd' + '\n'
                    r'\section{Certifications}' + '\n'
                    r'\resumeSubHeadingListStart'
                    r'\resumeProjectHeading{Example certificate}{2026}'
                    r'\resumeItemListStart\resumeItem{Completed the example course.}'
                    r'\resumeItemListEnd\resumeSubHeadingListEnd' + '\n')
        return source.replace(r'\section{Education}', optional + r'\section{Education}', 1)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        from app import app
        app.config.update(TESTING=True, SECRET_KEY='resume-editor-test')
        self.client = app.test_client()
        self.users = SqliteUserService()
        self.user = self.users.create_user(
            'Editor', 'editor@example.com', 'password')
        self.other = self.users.create_user(
            'Other', 'other@example.com', 'password')
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.userID

    def _form(self):
        response = self.client.get('/profile/resume/edit')
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session['resume_csrf_token']
        document = parse_editor_document(load_master_resume(self.user.userID))
        return {field.name: field.value for field in document.fields} | {
            'csrf_token': token,
            'source_version': hashlib.sha256(load_master_resume(self.user.userID).encode()).hexdigest(),
            'structure': json.dumps(default_structure(document)),
        }

    def _upload(self, source, filename='master.tex', token=None):
        if token is None:
            self.client.get('/profile')
            with self.client.session_transaction() as session:
                token = session['profile_csrf_token']
        return self.client.post('/profile/resume/upload', data={
            'csrf_token': token,
            'master_resume': (BytesIO(source if isinstance(source, bytes) else source.encode()), filename),
        }, content_type='multipart/form-data')

    def test_upload_replaces_master_resume_and_populates_profile(self):
        original = MASTER_RESUME_PATH.read_text(encoding='utf-8')
        profile_service = SqliteProfileService()
        profile_service.save_profile(self.user.userID, replace(
            profile_service.get_profile(self.user.userID), desired_titles=['Backend Engineer'],
            address_line_1='123 Example Street'))
        source = original.replace('Jake Ryan', 'Taylor Example').replace(
            'jake@su.edu', 'taylor@example.com')
        response = self._upload(source)
        self.assertEqual(response.status_code, 302)
        with db.connect() as conn:
            self.assertEqual(conn.execute(
                'SELECT master_resume FROM users WHERE userID = ?',
                (self.user.userID,)).fetchone()[0], source)
        self.assertEqual(load_master_resume(self.user.userID), source)
        self.assertEqual(load_master_resume(self.other.userID), original)
        self.assertEqual(MASTER_RESUME_PATH.read_text(
            encoding='utf-8'), original)
        profile = SqliteProfileService().get_profile(self.user.userID)
        self.assertEqual((profile.first_name, profile.last_name, profile.full_name),
                         ('Taylor', 'Example', 'Taylor Example'))
        self.assertEqual(profile.email, 'taylor@example.com')
        self.assertEqual(profile.school, 'University of Alberta')
        self.assertEqual((profile.city, profile.province), ('Edmonton', 'AB'))
        self.assertIn('Python', profile.skills)
        self.assertEqual(profile.desired_titles, ['Backend Engineer'])
        self.assertEqual(profile.address_line_1, '123 Example Street')
        self.assertEqual(self.client.get(
            '/profile/master-resume.tex').data, source.encode())
        with patch('services.resumes.latex_to_pdf', return_value=b'%PDF-upload') as compiler:
            self.assertEqual(self.client.get(
                '/profile/master-resume.pdf').data, b'%PDF-upload')
        compiler.assert_called_once_with(source)
        self.assertEqual(self.client.get(
            '/profile/resume/edit').status_code, 200)
        self.assertIn(b'Master resume uploaded', self.client.get(
            '/profile', follow_redirects=True).data)

    def test_extended_resume_upload_and_optional_sections_remain_editable(self):
        source = self.extended_source()
        self.assertEqual(self._upload(source).status_code, 302)
        self.assertEqual(load_master_resume(self.user.userID), source)
        document = parse_editor_document(source)
        groups = {group.title: group for group in document.groups}
        self.assertIn('Profile Summary', groups)
        self.assertIn('Publications', groups)
        self.assertIn('Certifications', groups)
        self.assertEqual(next(field.value for field in document.fields
                              if field.name == 'contact_portfolio'), 'https://example.com/portfolio')
        self.assertIn('PyTorch', profile_updates_from_latex(source)['skills'])
        data = self._form()
        data['profile_summary'] = 'A concise, verified summary.'
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 302)
        self.assertIn('A concise, verified summary.',
                      load_master_resume(self.user.userID))

    def test_uploaded_resume_can_omit_tailoring_sections(self):
        source = self.extended_source()
        for title in ('Work Experience', 'Technical Projects', 'Extra-Curricular Activities'):
            start = source.index(r'\section{' + title + '}')
            next_section = source.find(r'\section{', start + 1)
            source = source[:start] + source[next_section:]
        self.assertEqual({key: len(value) for key, value in parse_blocks(source).items()},
                         {'experience': 0, 'projects': 0, 'leadership': 0})
        self.assertEqual(self._upload(source).status_code, 302)
        self.assertEqual(self.client.get(
            '/profile/resume/edit').status_code, 200)

    def test_invalid_upload_preserves_previous_master_and_profile(self):
        original = load_master_resume(self.user.userID)
        self.assertEqual(self._upload(original).status_code, 302)
        profile = SqliteProfileService().get_profile(self.user.userID)
        cases = [
            (b'not tex', 'master.txt', 400),
            (b'\xff', 'master.tex', 400),
            (b'\n', 'master.tex', 422),
            (b'\x00' + original.encode(), 'master.tex', 422),
            (b'x' * (1024 * 1024 + 1), 'master.tex', 413),
            (original.replace('Jake Ryan',
             'Invalid \\input{secret}'), 'master.tex', 422),
        ]
        for source, filename, status in cases:
            with self.subTest(filename=filename, source=source[:20]):
                self.assertEqual(self._upload(
                    source, filename).status_code, status)
                self.assertEqual(load_master_resume(
                    self.user.userID), original)
                self.assertEqual(SqliteProfileService().get_profile(
                    self.user.userID), profile)
        self.assertEqual(self._upload(
            original, token='invalid').status_code, 400)
        self.assertEqual(load_master_resume(self.user.userID), original)

    def test_uploaded_resume_migrates_legacy_database_master(self):
        original = MASTER_RESUME_PATH.read_text(encoding='utf-8')
        legacy = original.replace('Jake Ryan', 'Legacy Master')
        with db.connect() as conn:
            conn.execute('INSERT INTO master_resumes (userID, latex) VALUES (?, ?)',
                         (self.user.userID, legacy))
        self.assertEqual(load_master_resume(self.user.userID), legacy)
        uploaded = original.replace('Jake Ryan', 'New Master')
        self.assertEqual(self._upload(uploaded).status_code, 302)
        self.assertEqual(load_master_resume(self.user.userID), uploaded)
        with db.connect() as conn:
            self.assertEqual(conn.execute(
                'SELECT master_resume FROM users WHERE userID = ?',
                (self.user.userID,)).fetchone()[0], uploaded)

    def test_profile_write_failure_restores_previous_master(self):
        original = MASTER_RESUME_PATH.read_text(encoding='utf-8')
        self.assertEqual(self._upload(original).status_code, 302)
        changed = original.replace('Jake Ryan', 'Taylor Example')
        with patch('routes.profile._profile_service.save_profile', side_effect=RuntimeError('database failed')):
            with self.assertRaisesRegex(RuntimeError, 'database failed'):
                self._upload(changed)
        self.assertEqual(load_master_resume(self.user.userID), original)

    def test_editor_exposes_all_template_sections_as_text_fields(self):
        response = self.client.get('/profile/resume/edit')
        for text in (b'Contact', b'Education', b'Experience 1', b'Projects 1',
                     b'Leadership &amp; activities 1', b'Technical skills', b'Bullet 1'):
            self.assertIn(text, response.data)
        self.assertIn(b'Edit master resume', self.client.get('/profile').data)
        self.assertNotIn(b'name="contact_name" value="\\textbf', response.data)

    def test_contact_fields_show_visible_email_and_plain_link_targets(self):
        source = load_master_resume(self.user.userID)
        document = parse_editor_document(source)
        fields = {field.name: field.value for field in document.fields}
        self.assertEqual(fields['contact_email'], 'jake@su.edu')
        self.assertEqual(fields['contact_linkedin'],
                         'https://linkedin.com/in/...')
        self.assertEqual(fields['contact_github'], 'https://github.com/...')
        escaped = source.replace('mailto:x@x.com', r'mailto:x\@x.com')
        escaped = escaped.replace(
            r'\underline{jake@su.edu}', r'\underline{x\@x.com}')
        self.assertEqual(
            {field.name: field.value for field in parse_editor_document(escaped).fields}[
                'contact_email'],
            'x@x.com',
        )
        page = self.client.get('/profile/resume/edit').get_data(as_text=True)
        self.assertIn(
            'name="contact_email" type="email" maxlength="200" value="jake@su.edu"', page)
        self.assertIn(
            'name="contact_linkedin" type="url" maxlength="500" value="https://linkedin.com/in/..."', page)
        self.assertIn('rows="2"', page)

    def test_save_updates_only_edited_spans_and_escapes_latex(self):
        original = load_master_resume(self.user.userID)
        document = parse_editor_document(original)
        values = {field.name: field.value for field in document.fields}
        self.assertEqual(apply_editor_values(
            original, document, values), original)
        values['experience_1_bullet_1'] = r'Built C# & Python at 50%; no \input{secret}'
        values['skill_1'] = 'Python, C# & SQL'
        updated = apply_editor_values(original, document, values)
        self.assertIn(
            r'Built C\# \& Python at 50\%; no \textbackslash{}input\{secret\}', updated)
        self.assertIn(r'Python, C\# \& SQL', updated)
        self.assertEqual(parse_editor_document(
            updated).groups[-1].fields[0].value, 'Python, C# & SQL')
        self.assertIn('Software Developer Intern', updated)

    def test_edited_contact_values_round_trip_through_editor(self):
        original = load_master_resume(self.user.userID)
        document = parse_editor_document(original)
        values = {field.name: field.value for field in document.fields}
        values['contact_name'] = r'Taylor {A} & Co.'
        values['contact_linkedin'] = 'https://example.com/person?role=dev&level=2'
        updated = apply_editor_values(original, document, values)
        reopened = {
            field.name: field.value for field in parse_editor_document(updated).fields}
        self.assertEqual(reopened['contact_name'], values['contact_name'])
        self.assertEqual(reopened['contact_linkedin'],
                         values['contact_linkedin'])

    def test_saved_master_is_per_user_and_used_by_preview_and_extension(self):
        original = load_master_resume(self.user.userID)
        data = self._form()
        data['contact_name'] = 'Taylor Example'
        data['experience_1_bullet_1'] = 'Built a reliable service.'
        response = self.client.post(
            '/profile/resume/edit', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Master resume saved', response.data)
        self.assertIn(b'Edit master resume', response.data)
        saved = load_master_resume(self.user.userID)
        self.assertIn(r'\scshape Taylor Example', saved)
        self.assertIn('Built a reliable service.', saved)
        self.assertEqual(load_master_resume(self.other.userID), original)
        self.assertEqual(self.client.get(
            '/profile/master-resume.tex').data, saved.encode())
        with patch('services.resumes.latex_to_pdf', return_value=b'%PDF-edited') as compiler:
            self.assertEqual(self.client.get(
                '/profile/master-resume.pdf').data, b'%PDF-edited')
        compiler.assert_called_once_with(saved)
        with patch('routes.extension.configuration', return_value=('', '')), \
                patch('routes.extension.latex_to_pdf', return_value=b'%PDF-upload') as upload_compiler:
            result = self.client.post('/api/extension/tailor-resume', json={
                'url': 'https://example.com/job', 'html': '<p>Example job</p>'},
                headers={'Accept': 'application/pdf'})
        self.assertEqual(result.status_code, 200)
        upload_compiler.assert_called_once_with(saved)
        with self.client.session_transaction() as session:
            session['user_id'] = self.other.userID
        self.assertEqual(self.client.get(
            '/profile/master-resume.tex').data, original.encode())

    def test_invalid_or_expired_form_does_not_change_master(self):
        original = load_master_resume(self.user.userID)
        data = self._form()
        data['contact_name'] = 'Changed'
        data['contact_linkedin'] = 'https://['
        response = self.client.post('/profile/resume/edit', data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Please correct the highlighted fields', response.data)
        self.assertEqual(load_master_resume(self.user.userID), original)
        data['contact_linkedin'] = 'https://linkedin.com/in/example'
        data['csrf_token'] = 'invalid'
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 400)
        self.assertEqual(load_master_resume(self.user.userID), original)

    def test_stale_editor_does_not_overwrite_newer_save(self):
        first = self._form()
        stale = dict(first)
        first['contact_name'] = 'First Save'
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=first).status_code, 302)
        stale['contact_name'] = 'Stale Save'
        response = self.client.post('/profile/resume/edit', data=stale)
        self.assertEqual(response.status_code, 409)
        self.assertIn(b'changed in another tab', response.data)
        self.assertIn('First Save', load_master_resume(self.user.userID))

    def test_add_and_remove_entries_and_points_save_as_one_master(self):
        data = self._form()
        structure = json.loads(data['structure'])
        structure['experience'] = structure['experience'][:2]
        structure['experience'][0]['bullets'] = structure['experience'][0]['bullets'][1:]
        structure['experience'][0]['bullets'].append(
            'experience_1_bullet_new_1')
        data['experience_1_bullet_new_1'] = 'Added impact with C# & SQL.'
        structure['projects'].append({
            'id': 'projects_new_1', 'bullets': ['projects_new_1_bullet_new_1'],
        })
        data.update({
            'projects_new_1_0': 'New project', 'projects_new_1_1': '2026',
            'projects_new_1_bullet_new_1': 'Built a useful feature.',
        })
        structure['leadership'] = structure['leadership'][:-1]
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 302)
        saved = load_master_resume(self.user.userID)
        blocks = parse_blocks(saved)
        self.assertEqual(len(blocks['experience']), 2)
        self.assertEqual(len(blocks['projects']), 8)
        self.assertEqual(len(blocks['leadership']), 3)
        self.assertNotIn('Undergraduate Research Assistant', saved)
        self.assertNotIn(data['experience_1_bullet_1'],
                         blocks['experience'][0].latex)
        self.assertIn(r'Added impact with C\# \& SQL.',
                      blocks['experience'][0].latex)
        self.assertIn('New project', saved)
        reopened = parse_editor_document(saved)
        self.assertIn('projects_8_bullet_1', {
                      field.name for field in reopened.fields})
        self.assertEqual(self.client.get(
            '/profile/resume/edit').status_code, 200)
        again = self._form()
        again['projects_8_0'] = 'Revised project'
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=again).status_code, 302)
        self.assertEqual(
            len(parse_blocks(load_master_resume(self.user.userID))['projects']), 8)
        self.assertIn('Revised project', load_master_resume(self.user.userID))
        if __import__('shutil').which('pdflatex'):
            self.assertTrue(latex_to_pdf(load_master_resume(
                self.user.userID)).startswith(b'%PDF'))

    def test_new_experience_and_leadership_entries_compile(self):
        data = self._form()
        structure = json.loads(data['structure'])
        for category, fields in (
            ('experience', ('Engineer', '2026', 'Example Co', 'Remote')),
            ('leadership', ('Organizer', '2025', 'Example Club', 'Edmonton')),
        ):
            entry_id = f'{category}_new_1'
            structure[category].append(
                {'id': entry_id, 'bullets': [f'{entry_id}_bullet_new_1']})
            for index, value in enumerate(fields):
                data[f'{entry_id}_{index}'] = value
            data[f'{entry_id}_bullet_new_1'] = 'Delivered a helpful result.'
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 302)
        saved = load_master_resume(self.user.userID)
        self.assertEqual(len(parse_blocks(saved)['experience']), 4)
        self.assertEqual(len(parse_blocks(saved)['leadership']), 5)
        self.assertIn(r'\resumeSubheading', saved)
        self.assertIn('Example Club', saved)

    def test_move_experience_to_leadership_preserves_entry_and_points(self):
        data = self._form()
        structure = json.loads(data['structure'])
        moved = structure['experience'].pop()
        structure['leadership'].insert(0, moved)
        data['structure'] = json.dumps(structure)
        original = load_master_resume(self.user.userID)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 302)
        saved = load_master_resume(self.user.userID)
        blocks = parse_blocks(saved)
        self.assertEqual(len(blocks['experience']), len(
            parse_blocks(original)['experience']) - 1)
        self.assertEqual(len(blocks['leadership']), len(
            parse_blocks(original)['leadership']) + 1)
        self.assertIn(data[moved['bullets'][0]], blocks['leadership'][0].latex)
        self.assertEqual(self.client.get(
            '/profile/resume/edit').status_code, 200)

    def test_rejects_duplicate_or_incompatible_section_move(self):
        data = self._form()
        original = load_master_resume(self.user.userID)
        structure = json.loads(data['structure'])
        structure['leadership'].append(structure['experience'][0])
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 400)
        structure = json.loads(self._form()['structure'])
        structure['projects'].append(structure['experience'].pop())
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 400)
        self.assertEqual(load_master_resume(self.user.userID), original)

    def test_rejects_removing_required_entries_or_all_points(self):
        original = load_master_resume(self.user.userID)
        data = self._form()
        structure = json.loads(data['structure'])
        structure['experience'] = structure['experience'][:1]
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 400)
        self.assertEqual(load_master_resume(self.user.userID), original)
        structure = json.loads(self._form()['structure'])
        structure['projects'][0]['bullets'] = []
        data['structure'] = json.dumps(structure)
        self.assertEqual(self.client.post(
            '/profile/resume/edit', data=data).status_code, 400)
        self.assertEqual(load_master_resume(self.user.userID), original)

    def test_saved_latex_compiles_when_compiler_is_available(self):
        if not __import__('shutil').which('pdflatex'):
            self.skipTest('pdflatex unavailable')
        original = load_master_resume(self.user.userID)
        document = parse_editor_document(original)
        values = {field.name: field.value for field in document.fields}
        values['contact_name'] = 'Taylor & Example'
        values['experience_1_bullet_1'] = 'Built Python and C# systems by 50%.'
        updated = apply_editor_values(original, document, values)
        try:
            pdf = latex_to_pdf(updated)
        except TailoringError as exc:
            self.fail(f'Edited master did not compile: {exc}')
        self.assertTrue(pdf.startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
