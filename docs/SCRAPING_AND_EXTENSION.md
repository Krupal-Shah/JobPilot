# Scraping, automation, and the Chrome extension

Andy's workstream (per [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)): web scraping,
autofill automation, and the Chrome extension. This doc covers what exists,
how it's organized, and how another workstream plugs into it.

## Setup

From the repo root:

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

This installs Flask plus `litellm` (the configurable Tier 2 AI answer fallback,
see below), `pypdf` (validates compiled resume page counts), and
`python-dotenv` (loads `.env`).

The AI fallback is optional. To enable it, copy `.env.example` to `.env`
in the repo root and fill in `AGENT_NAME` and `AGENT_API_KEY`:

```sh
cp .env.example .env
```

`.env` is gitignored; never commit it. Without a key, `extension_server.py`
starts and runs exactly the same, just with Tier 2 always unavailable.

Then run both servers (see "API and two servers" below), and load the
extension unpacked in Chrome (see "Chrome extension" below).

## Route organization

`api.py` registers the JSON blueprints. Extension handlers are in
`routes/extension.py`, dashboard API handlers in `routes/applications.py`,
and shared validation in `routes/validation.py`. Authentication handlers and
session helpers live in `routes/auth.py`. Public URLs are unchanged.

## Service layer

`services/` holds interfaces (`services/interfaces.py`), structured data
objects (`services/dto.py`), and concrete implementations. A route (or
another teammate's module) constructs a service and calls it explicitly.
Nothing in this package runs on import, and there's no background job or
module-level singleton doing work you didn't ask for.

| Interface | Concrete implementation | Responsibility |
| --- | --- | --- |
| `ScraperService` | `JobScraperService` | Turn a captured page into a `JobPosting` |
| `ApplicationService` | `SqliteApplicationService` | CRUD and stage transitions on tracked applications |
| `AutomationService` | `RuleBasedAutomationService` | Match detected form field labels to answers (Tier 1) |
| `AIAnswerService` | `LiteLLMAnswerService` | Last resort answer for whatever Tier 1 could not match (Tier 2) |
| `ProfileService` | `SqliteProfileService` | Read and write the master candidate profile |

**For the resume-tailoring workstream:** call
`services.scraper.JobScraperService().get_current_job_description(html)`
directly, in-process, for the plain-text description of a posting. No
HTTP call needed if you're already inside the Flask app. `html` can be
either full page HTML or plain text you've already extracted.

## Database

SQLite (`app.db`, gitignored, created by `db.init_db()` on app startup), so
there is no Postgres dependency for anyone cloning the
repo. Schema in `schema.sql`: one `applications` table covering the whole
pipeline via its `stage` column (`bookmarked` to `applied` to `interview`
to `offer`/`rejected`), and one single-row `profile` table.

## API and two servers

Two Flask processes use the same application, routes, SQLite database, and
`SECRET_KEY`. This keeps authentication templates and redirects working on both
ports without duplicating app setup:

- **`app.py`** (`http://127.0.0.1:5050`): main launcher for the dashboard and APIs.
- **`extension_server.py`** (`http://127.0.0.1:8421`): second launcher for the port
  expected by the extension. `/api/extension` includes `/scrape`, `/autofill-plan`,
  `/tailor-resume`, `/resume`, and `/log`. Resume downloads use a stored tailored
  variant for the signed-in account, decompressing and compiling it on demand. Bookmarking
  with a captured description saves a selected resume draft. Each Fill action
  with a resume field calls `/tailor-resume`, which reuses that saved version and
  returns its compiled PDF (`Accept: application/pdf`). A saved cover letter is
  also attached when a cover-letter file field is detected. PDFs are not persisted.
  Failures leave the affected field unfilled and show a popup error. Without an AI
  key, bookmark selection uses keyword overlap. Fill first saves a bookmark before any form changes;
  existing application stages are preserved. The application survives a later
  failure, and its generated resume remains attached when moved to Applied
  manually.

Log in at `http://127.0.0.1:5050` (the Dashboard button opens this host).
Cookies for `localhost` are separate from cookies for `127.0.0.1`.
New passwords are hashed; existing plaintext passwords upgrade on successful login.

Run both to use the extension: `venv/bin/python app.py` and, in a second
terminal, `venv/bin/python extension_server.py`.

## Chrome extension (`extension/`)

- `field_scanner.js`: generic field detection and writing engine (no
  personal data, no site-specific hardcoding). Injected as a raw script,
  defines `window.AutofillScanner`.
- `content.js`: thin glue. Detects fields via the scanner, writes answers
  via the scanner. Never calls the backend itself (a fetch from
  content-script context is attributed to the page's own origin).
- `background.js`: the only thing that calls the Flask backend. Injects
  the scanner and content script into every frame of a tab (some sites
  embed the whole form in a same-origin iframe), asks
  `/api/extension/autofill-plan` how to answer detected fields, writes
  the answers back, and handles bookmarking via `/api/extension/scrape`
  plus `POST /api/applications`.
- `popup.html`/`popup.js`: "Fill this page", "Start application",
  "Bookmark", "Dashboard", styled with the same palette as
  `static/css/global.css`.

Load it via `chrome://extensions`, then Developer mode, then "Load
unpacked", then select the `extension/` folder. Requires both servers
running (see above); `manifest.json`'s `host_permissions` covers both
ports plus a curated list of common ATS vendor domains (Greenhouse,
Lever, Workday, iCIMS, SmartRecruiters, Workable). Those extra entries
exist because some postings (MongoDB's careers page is one) embed the
real application form in a genuinely cross-origin iframe, one
`activeTab` alone can't reach; `chrome.scripting.executeScript` with
`allFrames: true` only sees frames the extension has host permission
for. Adding a vendor here (rather than requesting `<all_urls>`) keeps
the permission scoped to known ATS platforms instead of every site the
user visits.

## Answer matching: two tiers

`services/automation.py`'s `RuleBasedAutomationService` (Tier 1)
always runs first. Built-in label mappings use only nonblank details saved
on the profile page; fixed screening answers start empty. It is deterministic
and does not require an API key.

`services/ai_answers.py`'s `LiteLLMAnswerService` (Tier 2) is a last
resort fallback for whatever Tier 1 could not match: open-ended essay
questions, or wording no pattern anticipated. It reads the configured master resume
as context and, for a select/radio/checkbox field, is only ever allowed
to return one of that field's real option values verbatim. Requires
`AGENT_NAME` and `AGENT_API_KEY` (see Setup above); with no keys, `is_available()` is
`False` and Tier 2 silently never runs. Every teammate without a key
still gets the full Tier 1 behavior unchanged.

Tier 2 is never consulted at all for EEO/demographic or compliance/legal
questions, regardless of key availability. See
`automation.is_sensitive_category()`. Those always stay flagged
for a human, since a wrong guess there is a compliance risk, not a UX
inconvenience. The popup shows Tier 2 answers under a distinct "AI
suggested, please verify" heading, never merged into "Completed".

## Detecting a real submission

The extension never clicks a site's real Submit button; a human always
does that. What it does do, once "Start application" has been clicked in
a tab, is quietly re-check that tab on every page load afterward for a
generic confirmation signal: "thank you for your application" -style
text, or a URL that looks like a confirmation/thank-you page with the
identity fields gone (`content.js`'s
`__runJobApplyBotCheckSubmission`, polled from `background.js`'s
`chrome.tabs.onUpdated` listener). The check is deliberately generic, not
site-specific, since this workstream can't predict every ATS's exact
wording.

On a match: the matching tracked application (looked up by URL) moves
from `bookmarked` to `applied` automatically, and a small banner is
injected into the page itself offering to open the next bookmarked job
in a new tab, so applying to a batch of postings doesn't require going
back to the popup after every single one.

## What's intentionally scaffolding

- `/dashboard` is a minimal application tracker page, not the final
  kanban board. That's Richard's workstream to build out.
- Accounts are authenticated. The `/profile` page shows account details, allows saving
  autofill values, and lets each account edit and preview its master resume as a PDF.
- New profiles start with blank details. Autofill leaves unanswered fields for
  manual review until the account owner saves profile details.
- The extension's "Apply to All" queue runner from the original prototype
  was deliberately left out of this port, to keep the browser-automation
  surface area small and reviewable.

## Known limitations

- Company-name detection from a URL is a host-based heuristic
  (`services/scraper.py::_guess_company_from_url`). On a shared
  ATS vendor domain (`boards.greenhouse.io`, `jobs.lever.co`) it can't
  recover the real company name from the URL path. Correct it by hand on
  the dashboard.
- The scraper only sees what's already rendered in the DOM when the
  extension captures the page. It does not wait for or trigger
  client-side rendering itself.

### Resume attachment compatibility

The worker resolves the resume input in the content script, then creates the
`File` and dispatches `input`/`change` in the page's `MAIN` execution world.
Greenhouse can ignore a file created in Chrome's isolated content-script world
even though it appears in the native input's `files` list. Attachment failures
are reported explicitly in the popup. Reload the extension after code changes.
