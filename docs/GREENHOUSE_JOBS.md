# Greenhouse jobs tab

The signed-in `/jobs` page reads the public [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html#list-jobs), using `GET /v1/boards/{board_token}/jobs` for each configured company. Greenhouse lists one company's published jobs per request; it has no global job-search endpoint. No API key is needed for these GET calls.

By default, the app checks the public boards for Affirm, BitGo, Cloudflare,
Coinbase, Cresta, Databricks, DRW, Duolingo, Figma, MongoDB, Roblox, Samsara,
Scale AI, StackAdapt, and Stripe.
Set `GREENHOUSE_BOARDS` in the process environment or local `.env` to replace
that list with comma-separated board tokens, for example:

```dotenv
GREENHOUSE_BOARDS=figma,cloudflare,coinbase
```

The server checks all published jobs on those boards and keeps computer-science-related
roles (including software, database, data, ML, security, and frontend) whose titles
indicate an internship, co-op, new graduate,
early-career, associate, junior, or entry-level level. Only postings whose listed
location clearly names Canada, a Canadian province, or a recognized Canadian
city are shown. Generic "Remote" or "In-Office" locations are excluded because
they do not establish Canadian eligibility. Senior, staff, principal,
lead, manager, and higher-level engineering titles are excluded. This is a
title-based filter; a company that does not put the level in its title may have
eligible jobs that are not shown. Each signed-in user's saved desired titles
rank matching results first. A search box filters the already selected jobs by
title, company, or location. Results are cached for 15 minutes in the web
process, and temporary board failures do not hide results from other boards.

The page links to the original posting to confirm requirements and apply. It
does not submit applications or store fetched listings in the database.
