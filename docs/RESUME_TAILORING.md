# Resume tailoring

The extension calls the synchronous tailoring service, and the dashboard lets
users view the saved tailored resume for each application.

## File locations

- `services/resume_tailoring.py`: tailoring service, public function, and helpers.
- `assets/prompts/resume_tailoring/prompt.md`: runtime ranking prompt, not coding-agent guidance.
- `assets/resumeTemplate.tex`: fictional backup template; `assets/reesume.tex` demonstrates the extended layout.
- `assets/CoverLetterTemplate.tex`: one-page cover-letter PDF template.
- `services/resume_editor.py`: parses the supported template into editable fields and writes escaped text back into LaTeX.
- `users.master_resume`: private per-account master after upload or edit.
  Existing `master_resumes` SQLite rows and file-backed masters are migrated on read.
- `tests/test_resume_tailoring.py`: behavior and compiler checks.
- `.env`: local provider credentials; `.env.example` documents the two-key format.
- `docs/RESUME_TAILORING.md`: setup and integration documentation.

```python
from pathlib import Path
from services.resume_tailoring import tailor_resume, decompress_latex, TailoringError

master = Path("assets/resumeTemplate.tex").read_text(encoding="utf-8")
result = tailor_resume("Job description goes here", master)
Path("tailored.tex").write_text(result.latex, encoding="utf-8")
Path("tailored.pdf").write_bytes(result.pdf)
# Store result.compressed_latex in a TEXT column using your existing data layer.
assert decompress_latex(result.compressed_latex) == result.latex
```

For a captured posting, use `ResumeTailoringService`. It delegates description
extraction to `JobScraperService`, so callers do not need to parse page HTML:

```python
from services import JobScraperService, ResumeTailoringService

tailoring = ResumeTailoringService(JobScraperService())
result = tailoring.tailor_resume(captured_html, master)
```

Bookmarking a posting with a captured description creates a job-specific draft
in `applications.resume_variant`, together with the master snapshot and editable
selection state. The AI ranker chooses blocks when configured; a deterministic
keyword ranking is used otherwise. Failure to make a draft does not delete the
bookmark. The first saved draft shows the top two experience blocks with up to
four ranked points each, one project with up to three points, and one
extracurricular block with up to three points. Profile, publications, and
certifications are hidden by default. All original blocks and points remain in
the editor so the user can show them again. Dashboard **Tailor resume** exposes
section, block, and point visibility, point edits, new/removed points, and
reviewed AI proposals. A debounced, unsaved PDF preview recompiles after edits;
saving writes the revised source for that application only. **Start new draft from current master** explicitly
replaces that job's saved resume after a master change. **Cover letter** saves plain text for that application;
**Cold email** stays in the browser until copied. Their AI suggestions use the
same model configuration but separate prompts in `assets/prompts/`. Suggestions
receive parsed master-resume facts and must not invent applicant details.

The extension's authenticated `POST /api/extension/tailor-resume` compiles a saved
job version when one exists, preserving dashboard edits. For an uncaptured job,
it retains the older on-demand generation path. `Accept: application/pdf` returns
the PDF directly. No PDF is persisted. A saved cover letter is compiled from
the user's current master-resume header and `assets/CoverLetterTemplate.tex` by
`GET /api/applications/<id>/documents/cover-letter.pdf`. Letters are limited to
350 words and the download rejects output over one page. They can
be attached to a cover-letter file field during autofill.

The authenticated `GET /api/extension/resume?url=...`
finds the owning account's application, decompresses its saved LaTeX, and compiles
it on demand. Viewing never reruns ranking or changes the stored source. Existing
compressed-only records work immediately with an installed compiler. Missing
resumes return 404; invalid source or compilation failures return 422.
The schema removes obsolete PDF cache tables while preserving compressed source.

`services/resumes.py` loads each account's master for tailoring, AI-assisted
answers, and `/profile`. New accounts start from `assets/resumeTemplate.tex`.
The **Edit master resume** link opens `/profile/resume/edit`, which shows the
template's contact, education, experience, project, leadership, and skills text,
plus profile summary, publications, and certifications when present,
as form fields. Saving preserves untouched resume sections, writes edited text
back into the full LaTeX source, escapes LaTeX special characters, validates the supported structure, and stores
the result in the account's `users.master_resume` database column. The profile PDF preview uses that saved source.
Entries and bullet points can be added or removed in the editor. To keep
tailoring compatible, save requires at least the original available count capped
at two experience entries, one project, and one leadership entry, plus one point
in each entry. PDFs are compiled on demand
without a persistent PDF cache. Existing saved
application variants remain snapshots of the source used when they were created.
Profile also accepts UTF-8 `.tex` uploads up to 1 MB using this template structure,
including the section names and skills layout in `assets/reesume.tex`.
An upload replaces that account's master and imports recognizable contact,
education, location, and skills fields into the autofill profile; other profile
details remain unchanged. Education, experience, projects, leadership, coursework,
profile summary, publications, certifications, and skills may be absent. Unsupported
TeX structures and invalid contact values are rejected before saving. The bundled
template stays available as a backup.

`result.pdf` contains PDF file bytes, so a Flask handler can return it using
`send_file(BytesIO(result.pdf), mimetype="application/pdf", download_name="resume.pdf")`.
Catch `TailoringError` to display failure to the user. Run long operations in a
background worker if the screen needs asynchronous progress.

## Setup

Install `requirements.txt`. Copy `.env.example` to `.env` and set only:

```dotenv
AGENT_NAME=provider/model-id
AGENT_API_KEY=your-key
```

Use any LiteLLM-supported `provider/model-id` with a currently available
text-chat model from your account. `AGENT_NAME` includes
the model so no third configuration key is needed. Process environment values
override `.env`. Existing `.env` files are not modified by this feature.
Providers requiring additional credentials or endpoints are outside this two-key
configuration. Both resume ranking and autofill share `services/llm.py`; missing
configuration disables optional autofill and AI suggestions; bookmark selection
still works with keyword ranking.
Provider responses must contain text; rankings accept plain or fenced JSON and
are validated against every candidate ID. The adapter uses [LiteLLM completion](https://docs.litellm.ai/docs/completion/input).

Follow the [pdflatex installation steps](../README.md#install-pdflatex) for your OS.
Install `pdflatex` separately through TeX Live. On Ubuntu the supplied
template needs `texlive-latex-base`, `texlive-latex-recommended`, and
`texlive-latex-extra`. The compiler runs twice in a temporary directory, disables
shell escape, restricts file access, times out, and cleans up generated files.
Uploaded LaTeX is restricted to the supported editor structure and obvious file
I/O commands are rejected. This is not a complete TeX sandbox: isolate the
compiler in an OS/container sandbox before exposing uploads to untrusted users.

## Selection and supported format

The parser targets `assets/resumeTemplate.tex`, not arbitrary LaTeX templates:

- Tailored sections include `Experience`/`Work Experience`,
  `Projects`/`Technical Projects`, and `Leadership \& Activities`/
  `Extra-Curricular Activities` (also `Leadership` or `Leadership Experiences`).
- Entries start with `\resumeSubheading` or, for projects, `\resumeProjectHeading`,
  followed by one `\resumeItemListStart` ... `\resumeItemListEnd` bullet list.
- Coursework, when present, is one `\resumeItem{\textbf{Relevant Coursework:} Course, Course, ...}`.
  Course names may contain brace groups; commas inside those groups are preserved.
- Use the template's normal commands directly, without alternate macro wrappers,
  verbatim environments, conditional sections, or unusual TeX category codes.

The model receives only the job description and original candidate blocks with IDs,
each block once. The full master stays local for verbatim selection and PDF
compilation; it is not duplicated in the LLM payload. Every new ranking request
includes the candidate blocks again and does not depend on conversation memory.
[`assets/prompts/resume_tailoring/prompt.md`](../assets/prompts/resume_tailoring/prompt.md)
requires only three complete ID rankings. Invalid responses fail;
the application never accepts generated resume text. Selected entries retain
their original order and exact characters. All other sections and formatting
remain intact. Only unselected whole entries and coursework suffixes are deleted.

The first eight courses are kept in source order, then fewer if needed for space;
courses are not ranked by the model. Every candidate PDF is checked using
[pypdf's page count](https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html).
Up to two jobs, one project, and one leadership entry are selected from available
blocks. If they cannot fit even with zero courses, `PageLimitError` is raised.
Arbitrary source content cannot be guaranteed to fit without changing text or
layout. The function never shrinks fonts, crops pages, drops bullets, or silently
substitutes shorter lower-ranked entries. Fix the master before retrying.

Compression is lossless zlib encoded as base64 for database text storage. It is
not encryption. Decompress only values stored by the application.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests use fictional source, mocked LLM replies, and real PDF page structures.
The real compiler check runs when `pdflatex` is available. No paid model request
is made by the tests. A live provider call and compilation of the full supplied
template require the setup above.
