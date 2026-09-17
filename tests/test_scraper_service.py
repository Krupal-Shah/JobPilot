"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import unittest

from services.scraper import JobScraperService


class ScraperServiceTests(unittest.TestCase):
    def setUp(self):
        self.scraper = JobScraperService()

    def test_extract_job_posting_reads_title_and_description(self):
        html = """
        <html><head><title>Software Engineer Intern</title></head>
        <body>
            <script>window.__IGNORED__ = true;</script>
            <h1>Software Engineer Intern</h1>
            <p>Build things.   Ship them.</p>
        </body></html>
        """
        job = self.scraper.extract_job_posting(html, "https://boards.acme.com/jobs/123")

        self.assertEqual(job.title, "Software Engineer Intern")
        self.assertEqual(job.company, "Acme")
        self.assertIn("Build things.", job.description)
        self.assertNotIn("__IGNORED__", job.description)

    def test_extract_job_posting_falls_back_when_title_missing(self):
        job = self.scraper.extract_job_posting("<body><p>No title here</p></body>", "https://example.com/job")
        self.assertEqual(job.title, "Untitled role")

    def test_get_current_job_description_accepts_plain_text(self):
        description = self.scraper.get_current_job_description("Already-extracted   visible   text.")
        self.assertEqual(description, "Already-extracted visible text.")

    def test_get_current_job_description_joins_separate_elements_by_line(self):
        html = "<body><p>First</p>\n\n\n\n<p>Second</p></body>"
        description = self.scraper.get_current_job_description(html)
        self.assertEqual(description, "First\nSecond")

    def test_get_current_job_description_empty_input(self):
        self.assertEqual(self.scraper.get_current_job_description(""), "")


if __name__ == "__main__":
    unittest.main()
