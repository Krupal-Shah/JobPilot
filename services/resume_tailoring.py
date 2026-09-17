"""Selection-only tailoring for the LaTeX structure in assets/resumeTemplate.tex."""

import base64
from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zlib

from pypdf import PdfReader

from .scraper import JobScraperService
from .llm import complete_text


ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "assets" / "prompts" / "resume_tailoring" / "prompt.md"
COUNTS = {"experience": 2, "projects": 1, "leadership": 1}


class TailoringError(ValueError):
    """An input, ranking, or compilation problem suitable for a screen to handle."""


class PageLimitError(TailoringError):
    """The required verbatim entries do not fit on one page."""


@dataclass(frozen=True)
class TailoredResume:
    latex: str
    pdf: bytes
    compressed_latex: str


@dataclass(frozen=True)
class Block:
    id: str
    start: int
    end: int
    latex: str


def _masked_comments(latex):
    # Equal-length masking preserves source offsets. Escaped percent signs are text.
    return re.sub(r"\\.|%[^\n]*", lambda m: " " * len(m[0])
                  if m[0].startswith("%") else m[0], latex)


def parse_blocks(latex):
    """Locate whole entries; never reconstruct or normalize their source text."""
    source = _masked_comments(latex)
    body = source.find(r"\begin{document}")
    if body < 0 or source.count(r"\end{document}") != 1:
        raise TailoringError("Expected one complete LaTeX document.")
    sections = list(re.finditer(r"\\section\{([^{}]+)\}", source[body:]))
    result = {}
    for index, section in enumerate(sections):
        title = section[1].strip().lower()
        category = {"experience": "experience", "work experience": "experience",
                    "projects": "projects", "technical projects": "projects",
                    "leadership": "leadership", "leadership experiences": "leadership",
                    r"leadership \& activities": "leadership",
                    "extra-curricular activities": "leadership"}.get(title)
        if category is None:
            continue
        if category in result:
            raise TailoringError(f"Duplicate {category} section.")
        start = body + section.end()
        end = body + sections[index + 1].start() if index + \
            1 < len(sections) else source.index(r"\end{document}")
        region = source[start:end]
        heading = "resumeProjectHeading" if category == "projects" else "resumeSubheading"
        markers = list(re.finditer(
            r"\\(" + heading + r"|resumeItemListStart|resumeItemListEnd)\b", region))
        if (len(markers) % 3 or
                [m[1] for m in markers] != [heading, "resumeItemListStart", "resumeItemListEnd"] * (len(markers) // 3)):
            raise TailoringError(
                f"Unsupported {category} blocks; expected a heading and one bullet list per entry.")
        blocks = []
        for i in range(0, len(markers), 3):
            a, b = start + markers[i].start(), start + markers[i + 2].end()
            blocks.append(Block(f"{category}:{i // 3}", a, b, latex[a:b]))
        result[category] = blocks
    for category in COUNTS:
        result.setdefault(category, [])
    return result


def validate_rankings(rankings, candidates):
    if not isinstance(rankings, dict) or set(rankings) != set(COUNTS):
        raise TailoringError(
            "The LLM must return exactly three ranking arrays.")
    for category, blocks in candidates.items():
        ids = rankings[category]
        expected = {block.id for block in blocks}
        if (not isinstance(ids, list) or any(not isinstance(x, str) for x in ids)
                or len(ids) != len(expected) or set(ids) != expected):
            raise TailoringError(
                f"Invalid {category} ranking: every original ID is required exactly once.")
    return rankings


def rank_resume(job_description, candidates):
    """Call any LiteLLM provider/model that supports text chat and one API key."""
    payload = {
        "job_description": job_description,
        "candidates": {key: [{"id": b.id, "latex": b.latex} for b in blocks]
                       for key, blocks in candidates.items()},
    }
    try:
        content = complete_text(
            [{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
             {"role": "user", "content": json.dumps(payload)}],
        )
        # Text-only providers commonly wrap JSON in a Markdown code fence.
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        rankings = json.loads(fenced[1] if fenced else content)
    except Exception:
        # Provider errors may contain credentials or the submitted resume.
        raise TailoringError(
            "LLM ranking failed. Check provider configuration and retry.") from None
    return validate_rankings(rankings, candidates)


def select_blocks(master_resume, candidates, rankings):
    validate_rankings(rankings, candidates)
    removals = []
    for category, blocks in candidates.items():
        selected = set(rankings[category][:COUNTS[category]])
        removals.extend((b.start, b.end)
                        for b in blocks if b.id not in selected)
    latex = master_resume
    for start, end in sorted(removals, reverse=True):
        latex = latex[:start] + latex[end:]
    return latex


def limit_coursework(latex, limit=8):
    """Keep the first N courses verbatim; split only commas outside brace groups."""
    if type(limit) is not int or not 0 <= limit <= 8:
        raise TailoringError(
            "Coursework limit must be between zero and eight.")
    source = _masked_comments(latex)
    matches = list(re.finditer(
        r"\\resumeItem\{\s*\\textbf\{Relevant Coursework:?\}:?", source))
    if not matches:
        return latex
    if len(matches) != 1:
        raise TailoringError(
            "Expected one resumeItem with a bold Relevant Coursework label.")
    start = matches[0].end()
    depth = 0
    commas = []
    end = None
    for token in re.finditer(r"\\.|[{},]", source[start:]):
        char = token[0]
        pos = start + token.start()
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                end = pos
                break
            depth -= 1
        elif char == "," and depth == 0:
            commas.append(pos)
    if end is None:
        raise TailoringError("Unclosed Relevant Coursework item.")
    cut = start if limit == 0 else (
        commas[limit - 1] if len(commas) >= limit else end)
    return latex[:cut] + latex[end:]


def latex_to_pdf(latex):
    """Compile trusted template-based LaTeX locally and return the PDF file bytes.

    For public uploads run this worker in an OS/container sandbox. TeX is code;
    a temporary directory and disabled shell escape are not a complete sandbox.
    """
    executable = shutil.which("pdflatex")
    if not executable:
        raise TailoringError(
            "pdflatex is required. Install TeX Live or MiKTeX with the template's packages.")
    with tempfile.TemporaryDirectory(prefix="resume-") as directory:
        workdir = Path(directory)
        (workdir / "resume.tex").write_text(latex, encoding="utf-8")
        env = {**os.environ, "openin_any": "p", "openout_any": "p"}
        try:
            for _ in range(2):
                completed = subprocess.run(
                    [executable, "-no-shell-escape", "-interaction=nonstopmode",
                     "-halt-on-error", "resume.tex"], cwd=directory, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                )
                if completed.returncode:
                    raise TailoringError(
                        "LaTeX compilation failed. Check syntax and installed template packages.")
        except subprocess.TimeoutExpired:
            raise TailoringError(
                "LaTeX compilation exceeded the time limit.") from None
        except OSError:
            raise TailoringError("Could not run the LaTeX compiler.") from None
        pdf_path = workdir / "resume.pdf"
        if not pdf_path.is_file():
            raise TailoringError("LaTeX compilation produced no PDF.")
        return pdf_path.read_bytes()


def compress_latex(latex):
    """Lossless UTF-8 + zlib + base64, suitable for a database TEXT column."""
    return base64.b64encode(zlib.compress(latex.encode("utf-8"), level=9)).decode("ascii")


def decompress_latex(value):
    return zlib.decompress(base64.b64decode(value, validate=True)).decode("utf-8")


def tailor_resume(job_description, master_resume):
    """Return LaTeX, a verified one-page PDF, and DB-ready compressed LaTeX.

    Both arguments are text (master_resume is full LaTeX, not a filename/PDF).
    Raises TailoringError on failure; never returns an overlength PDF.
    """
    if not isinstance(job_description, str) or not job_description.strip():
        raise TailoringError("Job description must be nonempty text.")
    if not isinstance(master_resume, str) or not master_resume.strip():
        raise TailoringError("Master resume must be nonempty LaTeX text.")
    candidates = parse_blocks(master_resume)
    limit_coursework(master_resume)  # Validate before spending an LLM call.
    rankings = rank_resume(job_description, candidates)
    selected = select_blocks(master_resume, candidates, rankings)
    previous = None
    for course_count in range(8, -1, -1):
        latex = limit_coursework(selected, course_count)
        if latex == previous:
            continue
        previous = latex
        pdf = latex_to_pdf(latex)
        try:
            page_count = len(PdfReader(BytesIO(pdf)).pages)
        except Exception:
            raise TailoringError(
                "Compiler output is not a readable PDF.") from None
        if page_count == 1:
            return TailoredResume(latex, pdf, compress_latex(latex))
        if page_count == 0:
            raise TailoringError("Compiler output has no pages.")
    raise PageLimitError(
        "The selected resume entries exceed one page. Shorten the master resume or adjust its layout before retrying."
    )


class ResumeTailoringService:
    """Tailor a resume using a description extracted from a captured page."""

    def __init__(self, scraper_service=None):
        self._scraper_service = scraper_service or JobScraperService()

    def tailor_resume(self, page_content, master_resume):
        job_description = self._scraper_service.get_current_job_description(
            page_content)
        return tailor_resume(job_description, master_resume)
