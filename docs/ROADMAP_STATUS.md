# Roadmap Status

Snapshot of where Light CC sits relative to `docs/STRATEGIC_ROADMAP.md`, with Phase C (research kernel) explicitly deferred.

_Last updated: 2026-04-12_

---

## Done

### Phase A — Foundation Hardening
- **A1** DB-backed memory (was already in place at planning time)
- **A2** Integration test suite — `test_agent.py`, `test_subagent.py`, `test_memory_db.py`, `test_scheduler.py`, plus broader pass: `test_agent_loader.py`, `test_agent_runner.py`, `test_agents_api.py`, `test_memory_api.py`, `test_session.py` extensions, `test_skill_tool.py`
- **A3** Optimistic UI — `loadingConversations` state, skeleton on conversation switch
- **A4** Tool description enrichment — all 12 tools carry the 5-part treatment (what / when / input / output / edge cases); parameter descriptions carry inline examples; system prompt has Tool Selection Guide + Error Handling sections

### Phase B — First-Class Agents
- **B1** `AgentDefinition` + `AgentRun` DB models + Alembic migration
- **B2** YAML loader (`core/agent_loader.py`) — `agents/<name>/AGENT.md` with frontmatter
- **B3** Agent CRUD REST API (`routes/agents.py`)
- **B4** Execution engine (`core/agent_runner.py`)
- **B5** Scheduler integration — cron-triggered agents fire through the same job queue
- **B6** Webhook delivery (`core/webhooks.py`) on run completion
- **B7** Frontend agent panel (`AgentPanel.svelte`) — list, create/edit, run history, view-run linking

### Finishing pass (F1–F5) and follow-ups
- Memory Panel UI — backend REST routes + `MemoryPanel.svelte`
- Broader tests for extractor enqueue path + YAML loader edge cases
- Agent narration-leak fix (only final-turn text becomes the run result)
- `Skill` tool (`tools/skill_tool.py`) — agents invoke skills at runtime, mirrors Claude Code
- `morning-briefing` agent rewritten as a thin wrapper over the same-named skill
- `docs/agents-vs-skills.md` written to disambiguate the two concepts

---

## Deferred

### Phase C — Research Kernel + Math Tools
Pushed to the end of the roadmap. Do not default to C as "what comes next."

Blocks: **Phase E** (notebooks need the kernel) and **Phase G** (projects need C+D+E).

---

## Available to start next (not blocked on C)

### Phase D — Team & Collaboration
High leverage once past single-user.

| Item | What |
|---|---|
| D1 | Team/org DB model (Team, TeamMembership, roles: admin/member/viewer) |
| D2 | Shared memory spaces — team-scoped memories visible to all members' agents |
| D3 | Shared agent definitions — team-owned agents, not just user-owned |
| D4 | Conversation sharing — read-only link or invite collaborator to active session |
| D5 | Activity feed / audit log — who ran what agent, which tools, which artifacts |

### Phase F — Plugin Marketplace & Distribution
Strategically aligned with the plugin-unit model. Spec already exists in `docs/plugin-spec.md`.

| Item | What |
|---|---|
| F1 | `scripts/plugin_cli.py` — install/update/uninstall from git URL or local path |
| F2 | Plugin dependency resolution from `plugin.json` |
| F3 | Plugin registry API (`GET /api/plugins`, `POST /api/plugins/install`) |
| F4 | Package agent definitions as plugins (agent YAML + tools + skills = installable unit) |
| F5 | Template plugins: `finance-research`, `morning-briefing`, `github-monitor` |

---

## Blocked (pending C)

### Phase E — Notebook Mode
Needs Phase C's kernel for cell execution and artifacts for knowledge linking.

### Phase G — Research Projects
Needs C, D, and E.

---

## Next-step decision

With Phase A+B and the finishing pass complete, the real branch point is:

- **D (Team & Collaboration)** — unlocks multi-user value; biggest single item is the team/org DB model + migration
- **F (Plugin Marketplace)** — unlocks distribution; biggest single item is the plugin CLI + packaging agent definitions

Both are unblocked. Both are significant. Pick one; don't start both in parallel.
