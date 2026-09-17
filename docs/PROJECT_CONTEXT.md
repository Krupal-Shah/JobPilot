# Project overview

## Goal

Build a job application organizer for the CMPUT 402 Fall 2026 hackathon. The product combines a web dashboard with a Chrome extension to help job seekers organize applications, manage resume versions, and track employer responses.

The hackathon emphasizes rapid prototyping, collaboration, GitHub practices, usability, and creativity.

## Core requirements

| Area | Expected capabilities |
| --- | --- |
| Application tracking | Record company, position, date applied, and status; organize applications by stage; provide follow-up reminders |
| Resume management | Store a master resume and create tailored versions for individual applications |
| Response tracking | Record interviews, rejections, offers, and employer communications |
| Usability and accessibility | Simple navigation and responsive desktop/mobile layouts |
| Design and creativity | Visually appealing, functional presentation and distinctive features |

These areas form the evaluation criteria. Job postings and application statuses may be entered manually; a job-poster account system is not required.

## Architecture

- Backend: Flask.
- Frontend: server-rendered HTML/Jinja, CSS, and JavaScript.
- Persistence: SQLite through `db.py`, with tables defined in `schema.sql`.
- Chrome extension: reads job pages/forms and calls backend APIs; opens the web dashboard when needed.

## Workstreams

| Owner | Area |
| --- | --- |
| Andy | Web scraping, automation, and extension |
| Zane | Shared template and Flask skeleton |
| Krupal | Master resume management |
| Aara | Priority and sorting |
| Richard | Kanban board and application tracking |
| Vinay | Database and accounts |

## Current implementation

- `app.py`: Flask setup, login redirect, and authenticated dashboard.
- `api.py`: central registration for JSON API blueprints.
- `routes/`: application tracking, extension, authentication handlers.
- `services/`: application/profile/user persistence, scraping, autofill, and resume tailoring.
- `db.py` and `schema.sql`: shared SQLite database and schema.
- `templates/` and `static/`: Jinja pages, shared layout, CSS, and JavaScript.
- `extension/`: Chrome popup, field scanner, content script, and background worker.
- `extension_server.py`: shared app launcher on port 8421 for the extension.
- `install.sh` and `run.sh`: Bash setup and launch shortcuts.

Accounts and application tracking are implemented. New autofill profiles start blank;
`/profile` displays account details, saves edits to autofill values, and
lets each account upload, edit, and preview its LaTeX master resume as a PDF. Experience
entries can move between Experience and Leadership & activities in the editor. New accounts
start from the bundled template until upload or edit stores the source in
`users.master_resume`. A template-compatible `.tex` upload also fills recognizable
profile fields. Bookmarking a job with its captured description creates a saved
resume draft; dashboard cards open editors for the tailored resume, a saved cover
letter, and an unsaved LinkedIn outreach message. The extension reuses saved
resume edits and attaches a saved cover letter when a matching file field exists.
PDFs compile on demand. Without an AI key, initial block selection uses keyword
overlap and AI editing is unavailable. Unrelated LaTeX layouts remain unsupported.

See [README.md](../README.md) for setup, tests, and file organization conventions.

## Integration considerations

Coordinate user, job, application, resume, and communication identifiers through the shared database schema. Keep feature-specific templates, scripts, and styles isolated where practical to reduce conflicts in shared files.
