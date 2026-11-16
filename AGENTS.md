# Repository instructions for Codex — InsightForge AI

- Read `SYSTEM_DESIGN.md` and `CODEX_HANDOFF.md` before planning or coding. If they are under `docs/`, use those paths instead.
- Current status: PLANNING ONLY until a human explicitly approves implementation.
- Inspect existing code, branch, `git status`, tests, migrations, and environment templates before proposing changes. Do not assume an empty repository.
- Follow release scope P0 MVP, P1 Professional, P2 Advanced; do not implement later phases unprompted.
- Prioritize correctness, maintainable modular code, explicit typing, CI, secure defaults, tests, and evidence-linked business calculations.
- Never execute unrestricted LLM SQL; use independent AST/policy validation, restricted read-only database role, server-authenticated tenant context, and negative security tests.
- Never put secrets or sensitive customer data in source, logs, examples or prompts. Demo data must be synthetic.
- Do not delete/overwrite existing work, change Git history/remotes, run destructive migrations, push, deploy, connect external accounts, or incur costs without approval.
- At each approved milestone, run available checks, document commands/results, and report failures accurately.
