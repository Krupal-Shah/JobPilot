# Development guide

## Start here

Read [the project overview](docs/PROJECT_CONTEXT.md) for requirements and workstreams. Shared coding-agent guidance lives in [AGENTS.md](AGENTS.md). Feature proposals remain proposals until the team agrees on their scope.

For frontend work, follow [the shared UI style guide](docs/UI_STYLE_GUIDE.md) for colors, typography, components, responsive behavior, and the common page layout.

Before editing, check the branch and existing work:

```sh
git branch --show-current
git status --short
```

Work on a feature branch using the team's chosen naming convention. Coordinate changes to shared routes, navigation, layouts, and database/API contracts. Review the diff before staging so unrelated work is not included.

## Local setup

See [README.md](README.md#get-started) for Linux setup, optional LLM
configuration, and extension installation. The virtual environment directory is
`venv`; Bash shortcuts are `bash install.sh` and `bash run.sh`.

## Verification

Run the [Python and JavaScript test commands](README.md#tests). No lint tooling
is configured. See [resume tailoring](docs/RESUME_TAILORING.md) for the optional
local compiler integration test.

A baseline route smoke check, after installing dependencies:

```sh
venv/bin/python -c "from app import app; c = app.test_client(); assert c.get('/').status_code == 302; assert c.get('/api/applications').status_code == 401; print('Route smoke checks passed')"
```

This checks rendering and API authentication, not JavaScript behavior or a complete feature flow.

For a feature, also verify its happy path, invalid inputs, empty/error states, and relevant persistence behavior. For UI changes, check mobile layout, keyboard use, and browser console errors.

Before handoff:

```sh
git diff --check
git diff --stat
git status --short
```

These diff commands cover tracked changes; inspect new untracked files separately. Summarize behavior changes, actual verification, and any integration dependencies in the review description.

## Working with an agent

Give the agent a bounded task, expected behavior, and relevant constraints. For example:

> Read AGENTS.md and inspect the current tracking implementation. Plan adding a follow-up date to an application, including affected files, database/API changes, and verification. Wait for agreement before implementing.

Once the plan is agreed:

> Implement the agreed follow-up-date plan. Preserve unrelated work, verify the behavior, and summarize the diff and checks. Do not push.

Codex discovers repository `AGENTS.md` guidance at session startup. Start a fresh session after changing it and ask the agent to summarize the loaded project guidance. See [the official AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md). For other assistants, explicitly attach or ask them to read `AGENTS.md` if their instruction discovery is not configured to use it.

Maintain shared rules in `AGENTS.md` rather than copying them into multiple assistant-specific files. No MCP server, paid API, or global agent configuration is required by this repository. Keep local machine paths and personal preferences out of shared guidance.
