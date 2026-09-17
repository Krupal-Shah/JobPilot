# JobPilot

A Flask application and Chrome extension for organizing job applications. The
web dashboard tracks bookmarked jobs, applications, interviews, offers, and
rejections. The extension captures job postings and helps fill application forms.
Optional LiteLLM integration suggests answers and ranks resume entries for a
job-specific tailored PDF. Manually unhiding more blocks can increase its page count.

## Get started

Run commands from the repository root. Python 3.12 is the tested Python version.
Node.js and npm are needed only for JavaScript tests; the frontend has no build
step. Chrome is needed to use the extension.

### Linux

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python app.py
```

The Bash shortcuts are `bash install.sh` and `bash run.sh` (starts both servers). They also work when
invoked by path from another directory.

Open **http://127.0.0.1:5050**, choose **Register**, and create a local account.
The root URL opens login for signed-out visitors and the dashboard for signed-in users.
Open **Dashboard** to add and manage applications. Drag cards or use each card's
stage selector to move them; the details dialog separates description headings,
paragraphs, and lists. The resume editor lets you move entries between Experience
and Leadership & activities with the Section menu. Projects stay in their own section.
SQLite tables and the local
`app.db` file are created automatically; no database server is required.
New passwords are hashed, and legacy plaintext passwords upgrade on login.
These launch commands enable Flask's development server and debugger.
Existing accounts without a local password remain in the database but cannot
sign in until an administrator assigns one.

## Configuration

The dashboard and deterministic autofill work without an LLM key. If you do not
already have a `.env`, copy `.env.example` to `.env` in the repository root.
Keep an existing `.env` and edit only the settings you need.

| Setting | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session signing secret; use the same value for both launchers. An empty value uses the local development fallback. |
| `AGENT_NAME` | LiteLLM `provider/model-id` available to your account. |
| `AGENT_API_KEY` | API key for that provider. |
| `GREENHOUSE_BOARDS` | Optional comma-separated Greenhouse board tokens for the Jobs tab. Omit it to use the starter boards. |

Process environment variables override `.env`. Set both `AGENT_NAME` and
`AGENT_API_KEY` to enable AI suggestions and resume ranking. Additional provider
credentials or custom endpoints are outside the current two-setting adapter.
Keep `.env`, personal resumes, and local databases out of commits.

The signed-in **Jobs** tab lists Canada-based junior software, data, and ML roles from the
configured public Greenhouse boards, with saved desired-title matches first.
The public API does not provide a global search across every company. See
[Greenhouse jobs](docs/GREENHOUSE_JOBS.md) for the starter boards and filtering rules.

Resume PDF compilation also needs `pdflatex` and the template's LaTeX packages.
See [resume tailoring](docs/RESUME_TAILORING.md) for installation and service usage.
Bookmarking from the extension captures the job description and saves a selected
resume draft for that job. With a configured AI model, existing blocks are ranked
for the posting; without it, keyword overlap selects blocks. The card's **Tailor
resume** button opens a side-by-side PDF and point editor. You can show or hide
blocks, edit/add/remove points, ask AI for proposed changes, review them as
before/after diffs, and save the version that autofill will attach. The match
percentage is a transparent keyword/skill guide, not an employer ATS score.
**Start new draft from current master** explicitly replaces that job's saved
resume if you want to rebase after updating your master.
The same card offers **Cover letter** (saved per job and attached to a detected
cover-letter file field) and **Cold email** (an unsaved LinkedIn message you can
copy). AI proposals for all three use `AGENT_NAME` and `AGENT_API_KEY` with separate
placeholder prompts in `assets/prompts/`. Starter text lives in
`assets/cover_letter_fallback.txt` and `assets/cold_email_template.txt` until
complete user templates are ready.

The extension reuses a saved job-specific resume during Fill, so edits are not
overwritten. Saved resume and cover-letter PDFs are compiled on demand using
`pdflatex`; only compressed resume LaTeX and cover-letter text are kept in SQLite.
If the posting was entered manually without a description, add one before
expecting meaningful tailoring or match feedback. If compilation or attachment
fails, the extension leaves that file field for review.

Open **Profile** in the navigation to view your account, edit and save autofill
details, and preview the master resume as a PDF. New autofill profiles start with
blank personal details and no fixed screening answers. The extension fills only
details you have entered; account login details remain separate. Upload a
template-compatible `.tex` master on Profile to replace your own copy and import
recognizable contact, education, and skills details. Your copy is saved in the
account's `users.master_resume` database column. The bundled
`assets/resumeTemplate.tex` remains the fallback until you upload or edit a master.
The page offers PDF preview when `pdflatex` is installed; PDFs are compiled on
demand and are not stored.

## Install pdflatex

Install this on the machine running Flask, outside the Python virtual environment.
`pip install -r requirements.txt` does not install the LaTeX compiler.

### Ubuntu / Debian

```sh
sudo apt-get update
sudo apt-get install texlive-latex-base texlive-latex-recommended texlive-latex-extra
pdflatex --version
```

These packages include the compiler and the packages used by the supplied master
resume. Enter your local administrator password if prompted.

After installation, restart both Flask servers from a new terminal and refresh
**Profile**. The server must be able to find `pdflatex` on its `PATH`. The profile
previews the full master; the extension compiles a separate tailored document for
each application. Both viewing saved resumes and autofill require the compiler. If compilation fails, check the template's packages and retry.

Startup removes the obsolete application and master PDF cache tables; compressed
resume source is preserved. You do not need to delete `app.db`. Generated files
exist only in a temporary compilation directory that is cleaned up; PDF bytes
are returned to the browser without a persistent server copy. SQLite can reuse
the freed space, though the database file may not immediately shrink.

## Chrome extension

If you used `bash run.sh`, both servers are already running; skip step 2.

1. Keep `app.py` running on port 5050 and log in at **http://127.0.0.1:5050**.
2. Start the extension server in a second terminal:

   ```sh
   venv/bin/python extension_server.py
   ```

3. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select this repository's `extension/` directory.
4. Open a job posting and use the extension's **Bookmark**, **Start application**,
   or **Fill this page** actions. Review filled values and AI suggestions before
   submitting; the extension does not click the final Submit button.

Both server processes expose the same routes and share the same database. The
extension calls ports 5050 and 8421. Use `127.0.0.1` consistently: a login cookie
for `localhost` is not a login cookie for `127.0.0.1`.
See [extension details](docs/SCRAPING_AND_EXTENSION.md) for supported hosts and API behavior.

## Tests

```sh
venv/bin/python -m unittest discover -s tests -v
npm ci
npm test
```
\venv\Scripts\python.exe`.
The JavaScript suite was verified with Node.js 24. Python provider calls are
mocked, and database service tests use temporary databases. No LLM key is needed.
The real PDF compiler test is skipped when `pdflatex` is unavailable.

For offline tests, set `LITELLM_LOCAL_MODEL_COST_MAP=True` before running Python
tests to disable LiteLLM's optional pricing metadata download:

```sh
LITELLM_LOCAL_MODEL_COST_MAP=True venv/bin/python -m unittest discover -s tests -v
```


## Project layout and conventions

| Path | Responsibility |
| --- | --- |
| `app.py` | Flask setup, shared template context, login redirect and dashboard routes. |
| `api.py` | Single registration entry point for application and extension JSON APIs. |
| `routes/` | Feature HTTP handlers: `applications.py`, `extension.py`, `auth.py`, `profile.py`; shared request checks in `validation.py`. |
| `services/` | Business logic, persistence services, shared DTOs/interfaces, and the LiteLLM adapter. |
| `db.py`, `schema.sql` | SQLite connections and schema initialization. |
| `templates/`, `static/` | Jinja templates, CSS, and JavaScript; shared layout under `templates/partials/`. |
| `extension/` | Chrome manifest, popup, content script, field scanner, and service worker. |
| `assets/` | Resume template and runtime prompts. |
| `tests/` | Python `test_*.py` and JavaScript `*.test.cjs` regression tests. |
| `extension_server.py` | Launcher for the extension's port, reusing `app.py`. |
| `install.sh`, `run.sh` | Bash dependency installation and app launch shortcuts. |

Use snake_case for Python modules. Service modules use concise feature names
(`applications.py`, `users.py`, `profiles.py`, `ai_answers.py`, `automation.py`,
`scraper.py`, and `resume_tailoring.py`); omit the redundant `_service` suffix.
Shared contracts and utilities stay in `dto.py`, `interfaces.py`, and `llm.py`. Put feature route handlers in `routes/`,
business logic in `services/`, and register new JSON blueprints through
`api.register_api()`. Keep feature CSS/JavaScript with the existing static assets.
The route modules remain separate so each feature is manageable; `api.py` owns
API registration. Public URLs and Flask endpoint names are preserved across the
file reorganization.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow,
[project context](docs/PROJECT_CONTEXT.md) for requirements and workstreams, and
the [UI style guide](docs/UI_STYLE_GUIDE.md) for frontend conventions.
