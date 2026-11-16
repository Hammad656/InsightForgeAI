# InsightForge AI — how to use this handoff with Codex

This is a **planning and implementation specification**, not an implemented application.

## Files

- `SYSTEM_DESIGN.md` — final SDD: product scope, functional and nonfunctional requirements, architecture, data model, safe AI analytics, API contracts, security, tests, release milestones.
- `CODEX_HANDOFF.md` — explicit non-destructive repository audit and implementation procedure.
- `AGENTS.md` — suggested Codex repository instructions. **Merge** with any existing `AGENTS.md`; never overwrite it blindly.
- `figures/` — architecture, AI query safety, and data relationship diagrams used in the SDD.

## Quick start

1. Open your GitHub repository in Codex or its coding environment.
2. Add these files either at repository root, **or** move `SYSTEM_DESIGN.md` and `figures/` together into `docs/` and update references accordingly.
3. If `AGENTS.md` already exists, merge the guidance deliberately rather than replacing it.
4. Send the following prompt:

   **Read `SYSTEM_DESIGN.md` and `CODEX_HANDOFF.md` (under `docs/` if relocated). Inspect my existing repository and produce an M0 gap analysis, architecture conflict report, and M1 implementation plan. Do not edit code, commit, push, deploy, or incur costs. Await my approval before implementation.**

5. After reviewing the audit, explicitly approve M1 and progress through the release gates. Do not ask Codex to implement all phases simultaneously.

No cloud credentials or real company data are provided or required for the planning step.
