# Runtime LLM prompts

These files are application data sent to an LLM at runtime. Their instructions
apply to the requested model response, not to developers or coding agents working
on this repository. Repository development guidance lives in the root `AGENTS.md`.

- `resume_tailoring/prompt.md`: ranking instructions loaded by
  `resume_tailoring.py`. Keep its JSON contract aligned with `validate_rankings()`.

Keep prompts here under feature-specific directories, rather than at the
repository root. Document usage in `docs/` and put verification in `tests/`.
