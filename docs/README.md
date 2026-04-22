# Light CC — Documentation Index

A map of what lives in this directory. Start with `USER_GUIDE.md` if you're here to use Light CC; start with `plugin-spec.md` if you're here to ship a plugin.

## For users and operators

- [`USER_GUIDE.md`](USER_GUIDE.md) — end-user walk-through: chat, memory, scheduled agents, MCP, plugin install.
- [`../DEPLOY.md`](../DEPLOY.md) — production VPS runbook (HTTPS, Postgres, Redis, backups).
- [`OPERATIONS.md`](OPERATIONS.md) — day-two ops: incident triage, rollback, log filters, JWT rotation, scaling notes.

## Plugin + agent authoring

- [`plugin-spec.md`](plugin-spec.md) — Light CC's plugin format. Matches the Claude Code plugin spec exactly so plugins built in Claude Code ship here unchanged.
- [`agents-vs-skills.md`](agents-vs-skills.md) — short note on the distinction between agents and skills, since they're easy to conflate.
- [`playbooks-design.md`](playbooks-design.md) — design notes on the encode-vs-decide axis for agent chaining (April 2026).
- [`AGENT_QUALITY_PLAN.md`](AGENT_QUALITY_PLAN.md) — quality improvements aligned with Anthropic's "Building Effective Agents" guidance.

## Project direction

- [`STRATEGIC_ROADMAP.md`](STRATEGIC_ROADMAP.md) — acquisition-grade review of where Light CC is headed (April 2026).
- [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md) — current phase status; Phase C (research kernel) is explicitly deferred.
- [`RECREATION_PLAN.md`](RECREATION_PLAN.md) — blueprint for recreating Light CC from scratch; useful as a complete architecture reference.
