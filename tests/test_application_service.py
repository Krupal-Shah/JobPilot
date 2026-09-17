"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import tempfile
import unittest
from pathlib import Path

import db
from services.applications import SqliteApplicationService
from services.users import SqliteUserService
from services.dto import ApplicationStage, JobPosting


class ApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._temp_dir.name) / "test.db"
        db.init_db()
        self.addCleanup(self._restore_db_path)
        self.service = SqliteApplicationService()
        self.user_id = SqliteUserService().create_user("Demo", "demo@example.com", "test").userID

    def _restore_db_path(self):
        db.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _demo_job(self, url="https://boards.example.com/jobs/1") -> JobPosting:
        return JobPosting(company="Example Co", title="Backend Intern", url=url, description="Demo posting.")

    def test_create_application_is_idempotent_per_url(self):
        url = "https://boards.example.com/jobs/dupe"
        self.service.create_application(self._demo_job(url=url), self.user_id)
        self.service.create_application(JobPosting('Example Co', 'Backend Intern', url, ''), self.user_id)

        self.assertEqual(len(self.service.list_applications(self.user_id)), 1)
        self.assertEqual(self.service.get_application_by_url(url, self.user_id).job.description,
                         'Demo posting.')

    def test_update_stage_sets_date_applied(self):
        created = self.service.create_application(self._demo_job(), self.user_id)
        updated = self.service.update_stage(created.id, self.user_id, ApplicationStage.APPLIED)

        self.assertEqual(updated.stage, ApplicationStage.APPLIED)
        self.assertIsNotNone(updated.date_applied)

    def test_update_stage_missing_application_raises(self):
        with self.assertRaises(ValueError):
            self.service.update_stage(999, self.user_id, ApplicationStage.APPLIED)

    def test_list_applications_filters_by_stage(self):
        first = self.service.create_application(self._demo_job(url="https://a.example.com/1"), self.user_id)
        self.service.create_application(self._demo_job(url="https://a.example.com/2"), self.user_id)
        self.service.update_stage(first.id, self.user_id, ApplicationStage.INTERVIEW)

        interview_stage_only = self.service.list_applications(self.user_id, stage=ApplicationStage.INTERVIEW)
        self.assertEqual(len(interview_stage_only), 1)
        self.assertEqual(interview_stage_only[0].id, first.id)

    def test_delete_application_removes_it(self):
        created = self.service.create_application(self._demo_job(), self.user_id)
        self.service.delete_application(created.id, self.user_id)

        self.assertEqual(self.service.list_applications(self.user_id), [])
        self.assertIsNone(self.service.get_application(created.id, self.user_id))

    def test_delete_missing_application_is_a_no_op(self):
        self.service.delete_application(999, self.user_id)  # should not raise

    def test_update_application_changes_only_given_fields(self):
        created = self.service.create_application(self._demo_job(), self.user_id)
        updated = self.service.update_application(created.id, self.user_id, notes="Referred by a friend")

        self.assertEqual(updated.notes, "Referred by a friend")
        self.assertEqual(updated.job.company, created.job.company)
        self.assertEqual(updated.job.title, created.job.title)

    def test_update_application_missing_application_raises(self):
        with self.assertRaises(ValueError):
            self.service.update_application(999, self.user_id, notes="x")


if __name__ == "__main__":
    unittest.main()
