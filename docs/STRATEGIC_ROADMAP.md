# Light CC -- Strategic Roadmap

Produced 2026-04-11. Framed as an acquisition-grade review of where to take Light CC next.

---

## Context

Light CC is a self-hosted, multi-user agentic AI platform built on Claude. Core strengths:

- Clean agent loop (no DAG framework, model-driven control flow)
- Skills vs. Tools separation (markdown guidance vs. Python code)
- First-class memory system (Zettelkasten, user-scoped, injected into prompts)
- Scheduled agents (cron-based full reasoning loops)
- Sub-agent spawning with typed constraints
- MCP integration from day 1
- Production-ready multi-tenancy (auth, persistence, Redis, observability)

## Strategic Thesis

General-purpose Claude wrappers are a commodity. Light CC's differentiation is the combination of memory + scheduling + extensibility + data tools. The roadmap pushes into territory where that combination creates defensible value: a research/knowledge platform with team collaboration and a distributable plugin ecosystem.

---

## Phase A: Foundation Hardening

**Goal**: Close gaps that block everything else. Prerequisite to all other phases.
**Effort**: 1-2 weeks.
**Corresponds to**: Deferred Phase 7 from refactoring plan + test coverage gap.

| Item | What | Why |
|------|------|-----|
| A1 | ~~DB-backed memory~~ **ALREADY DONE** | Memory model, migration, and manager with DB-first + file fallback all exist. |
| A2 | Integration test suite for agent loop + tool execution | 15-20 tests covering: tool dispatch, streaming, permission checks, context compression, sub-agent spawning. Every future phase builds on untested ground without this. |
| A3 | Optimistic UI (loading skeletons, spinners on conversation switch) | Polish that makes everything after this feel production-grade. |
| A4 | Tool description enrichment (per AGENT_QUALITY_PLAN.md) | Direct agent quality improvement. Every subsequent feature benefits from better tool use. |

---

## Phase B: First-Class Agents

**Goal**: Ship named, persistent, independently invocable agent definitions. The single highest-leverage feature.
**Effort**: 2-3 weeks.
**Depends on**: Phase A.
**Corresponds to**: Agent API plan (memory: `project_agent_api.md`).

| Item | What | Files |
|------|------|-------|
| B1 | `AgentDefinition` DB model (name, description, model, system_prompt, tools, max_turns, timeout, memory_scope, permissions) | `core/db_models.py`, new migration |
| B2 | Agent CRUD REST API (`POST/GET/DELETE /api/agents`, `POST /api/agents/{name}/run`, `GET /api/agents/{name}/sessions`) | New `routes/agents.py` |
| B3 | Agent definitions loadable from YAML files in `agents/` dir (same pattern as skills) | `core/agent_loader.py` |
| B4 | Wire agent runs into existing `core/scheduler.py` (cron trigger) and `core/job_queue.py` (async execution) | Modify scheduler to reference agent definitions |
| B5 | Webhook callback on agent run completion (POST to user-configured URL) | `core/webhooks.py` |
| B6 | Frontend: Agent management panel (list, create, edit, view run history) | New `AgentPanel.svelte`, route in sidebar |

**Key design decision**: Agent definitions are a superset of skills. A skill is an agent with `trigger: chat_only`. This unifies the model rather than maintaining two parallel systems.

**Why this is the strategic move**: Unlocks the developer platform story (headless API), the team story (shared agents), and the marketplace story (distributable agent definitions). It's the thing that separates "chat app" from "agent runtime."

---

## Phase C: Research Kernel + Math Tools

**Goal**: Vertical differentiation as a research platform.
**Effort**: 3-4 weeks.
**Depends on**: Phase A. Partially Phase B (agents can use kernel).
**Corresponds to**: Layers 1-3 from research tool plan (memory: `project_research_tool_plan.md`).

| Item | What | Notes |
|------|------|-------|
| C1 | `KernelManager` + `KernelExec` tool (IPython kernel per session) | Layer 1. PythonExec stays stateless -- this is additive. |
| C2 | `Artifact` DB model + `tools/artifacts.py` (SaveArtifact, SearchArtifacts, GetArtifact) | Layer 2. Knowledge persistence beyond conversations. |
| C3 | `routes/artifacts.py` + `ArtifactPanel.svelte` | Frontend for browsing/searching artifacts. |
| C4 | `tools/sympy_tools.py` -- SymbolicMath (simplify, diff, integrate, solve) | Layer 3. Returns `{result, latex, steps}`. |
| C5 | `tools/finance_tools.py` -- FinanceCompute (Black-Scholes, VaR, yield curves) | Layer 3. Primary vertical differentiator. |
| C6 | `MathResult.svelte` renderer (KaTeX display of structured tool output) | Frontend for math/finance results. |

---

## Phase D: Team & Collaboration Layer

**Goal**: Move from single-user to team-capable.
**Effort**: 2-3 weeks.
**Depends on**: Phase B (agents exist as first-class entities).

| Item | What | Why |
|------|------|-----|
| D1 | Team/org model in DB (Team, TeamMembership, roles: admin/member/viewer) | Foundation for all collaboration. |
| D2 | Shared memory spaces (team-scoped memories visible to all members' agents) | Institutional knowledge that persists across people. |
| D3 | Shared agent definitions (team-owned agents, not just user-owned) | A team's "morning briefing" agent runs for everyone. |
| D4 | Conversation sharing (read-only link, or invite collaborator to active session) | "Look at what Claude found" without screenshots. |
| D5 | Activity feed / audit log (who ran what agent, what tools were called, what artifacts were created) | Required for any team deployment -- accountability. |

---

## Phase E: Notebook Mode

**Goal**: Formalize exploratory work into reproducible documents.
**Effort**: 2-3 weeks.
**Depends on**: Phase C (kernel for cell execution, artifacts for knowledge linking).
**Corresponds to**: Layer 4 from research tool plan.

| Item | What | Notes |
|------|------|-------|
| E1 | Notebook + NotebookCell DB models | Markdown/code/output/chart cells with position ordering. |
| E2 | `routes/notebooks.py` -- CRUD, cell execution, reorder, export as .ipynb | REST API. |
| E3 | `tools/notebook_tools.py` -- NotebookAddCell, NotebookUpdateCell | Agent can populate notebooks. |
| E4 | `NotebookView.svelte` + chat/notebook mode toggle | Frontend. |
| E5 | Convert conversation to notebook (extract code cells + outputs) | Key workflow: explore in chat, formalize in notebook. |

---

## Phase F: Plugin Marketplace & Distribution

**Goal**: Make the plugin spec real and distributable.
**Effort**: 2 weeks.
**Depends on**: Phase B (agent definitions are the core unit being packaged).
**Corresponds to**: `docs/plugin-spec.md` (spec exists, needs implementation).

| Item | What | Notes |
|------|------|-------|
| F1 | `scripts/plugin_cli.py` -- install/update/uninstall from git URL or local path | Spec exists, needs implementation. |
| F2 | Plugin dependency resolution (auto-install Python/npm deps from `plugin.json`) | |
| F3 | Plugin registry API (`GET /api/plugins`, `POST /api/plugins/install`) | Admin-only. |
| F4 | Package agent definitions as plugins (agent YAML + tools + skills = installable unit) | Bridges Phase B and plugin system. |
| F5 | Template plugins: `finance-research`, `morning-briefing`, `github-monitor` | Showcase + onboarding. |

---

## Phase G: Research Projects

**Goal**: Tie conversations, notebooks, artifacts, and agents into coherent research units.
**Effort**: 1-2 weeks.
**Depends on**: Phases C, D, E all complete.
**Corresponds to**: Layer 5 from research tool plan.

| Item | What | Notes |
|------|------|-------|
| G1 | Project + ProjectMembership DB models | Links conversations, notebooks, artifacts. |
| G2 | `routes/projects.py` + `tools/project_tools.py` | ProjectContext injected into system prompt. |
| G3 | `ProjectNav.svelte` in sidebar, project badge in topbar | Frontend. |
| G4 | Conversation/notebook list filters by active project | |
| G5 | Project-scoped agents (agent runs in project context, sees project artifacts) | |

---

## Sequencing

```
Week  1-2:   [A] Foundation (DB memory, tests, UI polish, tool descriptions)
Week  3-5:   [B] First-Class Agents (API, scheduling, webhooks, UI)
Week  5-9:   [C] Research Kernel + Math Tools (kernel, artifacts, sympy, finance)
Week  8-11:  [D] Team Collaboration (orgs, shared memory, shared agents)
Week 10-13:  [E] Notebook Mode (cells, execution, export, conversion)
Week 12-14:  [F] Plugin Marketplace (CLI, registry, templates)
Week 14-16:  [G] Research Projects (project entity, scoping, navigation)
```

Phases C/D and D/E overlap because they're largely independent work streams.

---

## Priority Call

If only one thing gets built next, it should be **Phase B (First-Class Agents)** after completing **Phase A (foundation)**. Reasoning:

1. 80% of building blocks already exist (agent loop, sub-agents, scheduler, job queue, skills).
2. Unlocks developer platform (headless API), team story (shared agents), and marketplace (distributable definitions).
3. Makes scheduled agents dramatically more powerful (named, configured, versioned vs. raw prompts).
4. Transforms Light CC from "chat app" to "agent runtime" -- the positioning that matters for acquisition or growth.

---

## Relationship to Existing Plans

| Existing plan | Where it lands |
|---------------|----------------|
| Refactoring Phase 7 (DB memory, optimistic UI) | Phase A |
| Research tool plan Layers 1-3 | Phase C |
| Research tool plan Layer 4 | Phase E |
| Research tool plan Layer 5 | Phase G |
| Agent API plan (`project_agent_api.md`) | Phase B |
| Plugin spec (`docs/plugin-spec.md`) | Phase F |
| Agent quality plan (`AGENT_QUALITY_PLAN.md`) | Phase A, item A4 |
