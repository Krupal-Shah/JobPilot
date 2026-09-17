from io import BytesIO
import json
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

from services import resume_tailoring as tailoring


def master_fixture():
    # Fictional and independent of the user's untracked master resume.
    parts = [r"\documentclass{article}", r"\begin{document}",
             r"\section{Education}",
             r"\resumeItem{\textbf{Relevant Coursework:} " +
             ", ".join(f"Course {i}" for i in range(12)) + "}"]
    for title, heading, count in [("Experience", "resumeSubheading", 3),
                                  ("Projects", "resumeProjectHeading", 3),
                                  (r"Leadership \& Activities", "resumeSubheading", 2)]:
        parts += [r"\section{" + title + "}", r"\resumeSubHeadingListStart"]
        for i in range(count):
            parts += ["\\" + heading + "{Role " + str(i) + "}{Date}" +
                      ("{Org}{City}" if heading == "resumeSubheading" else ""),
                      r"\resumeItemListStart",
                      r"\resumeItem{Built a tool; improved results by 10\%.}",
                      "% Keep this comment exactly: café",
                      r"\resumeItemListEnd"]
        parts += [r"\resumeSubHeadingListEnd"]
    return "\n".join(parts + [r"\section{Skills}", "Python, C++", r"\end{document}"])


def pdf_bytes(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class ResumeTailoringTests(unittest.TestCase):
    def setUp(self):
        self.master = master_fixture()
        self.blocks = tailoring.parse_blocks(self.master)
        self.rankings = {key: [b.id for b in reversed(blocks)]
                         for key, blocks in self.blocks.items()}

    def test_selection_is_only_deletion_and_preserves_whole_blocks(self):
        result = tailoring.select_blocks(self.master, self.blocks, self.rankings)
        removed = []
        for key, blocks in self.blocks.items():
            for block in blocks:
                if block.id in self.rankings[key][:tailoring.COUNTS[key]]:
                    self.assertIn(block.latex, result)
                else:
                    removed.append(block)
        expected = self.master
        for block in sorted(removed, key=lambda b: b.start, reverse=True):
            expected = expected[:block.start] + expected[block.end:]
        self.assertEqual(result, expected)
        parsed = tailoring.parse_blocks(result)
        self.assertEqual({k: len(v) for k, v in parsed.items()}, tailoring.COUNTS)

    def test_coursework_changes_only_suffix_of_course_list(self):
        expected = self.master.replace(", " + ", ".join(f"Course {i}" for i in range(8, 12)), "")
        self.assertEqual(tailoring.limit_coursework(self.master), expected)
        nested = self.master.replace("Course 0", r"\emph{Logic, Proofs}")
        self.assertIn(r"\emph{Logic, Proofs}", tailoring.limit_coursework(nested, 1))

    def test_compression_preserves_all_characters(self):
        text = self.master + "\r\n% λ résumé \\ { }  "
        self.assertEqual(tailoring.decompress_latex(tailoring.compress_latex(text)), text)

    def test_comments_do_not_create_candidates(self):
        text = self.master.replace(r"\section{Experience}",
                                   "% \\section{Experience}\n% \\resumeSubheading\n" + r"\section{Experience}")
        self.assertEqual(len(tailoring.parse_blocks(text)["experience"]), 3)

    def test_invalid_rankings_rejected(self):
        for invalid in [None, {}, {**self.rankings, "extra": []},
                        {**self.rankings, "experience": ["experience:0"] * 3},
                        {**self.rankings, "projects": ["invented", "projects:0", "projects:1"]},
                        {**self.rankings, "projects": [{}, [], True]}]:
            with self.subTest(invalid=invalid), self.assertRaises(tailoring.TailoringError):
                tailoring.validate_rankings(invalid, self.blocks)

    def test_missing_section_and_coursework_are_allowed_but_broken_blocks_fail(self):
        without_experience = self.master.replace("{Experience}", "{Other}")
        self.assertEqual(tailoring.parse_blocks(without_experience)['experience'], [])
        without_coursework = self.master.replace('Relevant Coursework', 'Classes')
        self.assertEqual(tailoring.limit_coursework(without_coursework), without_coursework)
        with self.assertRaises(tailoring.TailoringError):
            tailoring.parse_blocks(self.master.replace(r"\resumeItemListEnd", "", 1))

    def test_invalid_input_does_not_call_llm(self):
        with patch.object(tailoring, "rank_resume") as rank:
            for jd, resume in [("", self.master), ("job", None),
                               ("job", self.master.replace(r"\resumeItemListEnd", "", 1))]:
                with self.assertRaises(tailoring.TailoringError):
                    tailoring.tailor_resume(jd, resume)
            rank.assert_not_called()

    def test_main_reduces_courses_and_returns_matching_artifacts(self):
        one_page = pdf_bytes(1)
        with patch.object(tailoring, "rank_resume", return_value=self.rankings) as rank, \
                patch.object(tailoring, "latex_to_pdf", side_effect=[pdf_bytes(2), one_page]) as compile_pdf:
            result = tailoring.tailor_resume("Python developer", self.master)
        rank.assert_called_once()
        self.assertEqual(compile_pdf.call_count, 2)
        self.assertEqual(result.pdf, one_page)
        self.assertEqual(result.latex, compile_pdf.call_args.args[0])
        self.assertEqual(tailoring.decompress_latex(result.compressed_latex), result.latex)
        self.assertNotIn("Course 7", result.latex)

    def test_overlength_and_corrupt_pdf_never_return_success(self):
        for pdf, error in [(pdf_bytes(2), tailoring.PageLimitError),
                           (b"broken", tailoring.TailoringError),
                           (pdf_bytes(0), tailoring.TailoringError)]:
            with patch.object(tailoring, "rank_resume", return_value=self.rankings), \
                    patch.object(tailoring, "latex_to_pdf", return_value=pdf), \
                    self.assertRaises(error):
                tailoring.tailor_resume("developer", self.master)

    def test_provider_uses_two_generic_keys_and_ranking_prompt(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.rankings)))])
        with patch.dict("os.environ", {"AGENT_NAME": "gemini/test-model", "AGENT_API_KEY": "fake"}), \
                patch("litellm.completion", return_value=response) as completion:
            self.assertEqual(tailoring.rank_resume("job", self.blocks), self.rankings)
        args = completion.call_args.kwargs
        self.assertEqual(args["model"], "gemini/test-model")
        self.assertEqual(args["api_key"], "fake")
        payload = json.loads(args["messages"][1]["content"])
        self.assertEqual(set(payload), {"job_description", "candidates"})
        self.assertEqual(payload["job_description"], "job")
        self.assertEqual(payload["candidates"], {
            key: [{"id": b.id, "latex": b.latex} for b in blocks]
            for key, blocks in self.blocks.items()
        })

    def test_compiler_errors(self):
        with patch("shutil.which", return_value=None), self.assertRaisesRegex(tailoring.TailoringError, "pdflatex"):
            tailoring.latex_to_pdf(self.master)
        for outcome in [SimpleNamespace(returncode=1), subprocess.TimeoutExpired("pdflatex", 30)]:
            kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with patch("shutil.which", return_value="/fake/pdflatex"), \
                    patch("subprocess.run", **kwargs), self.assertRaises(tailoring.TailoringError):
                tailoring.latex_to_pdf(self.master)

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is not installed")
    def test_real_pdf_compiler(self):
        pdf = tailoring.latex_to_pdf(r"\documentclass{article}\begin{document}Example resume\end{document}")
        self.assertEqual(len(tailoring.PdfReader(BytesIO(pdf)).pages), 1)


if __name__ == "__main__":
    unittest.main()
