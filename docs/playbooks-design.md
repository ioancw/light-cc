# Playbooks: encode-vs-decide for agent chaining

Design notes from conversation on 2026-04-17/18. Captures the reasoning behind a
proposed "playbook" primitive that sits between free-form `Agent` delegation and
fully-encoded workflows.

## The question

When chaining agents together, should the LLM figure out the sequence at
runtime, or should we encode the sequence ahead of time? Both have a place;
this doc lays out when each fits and proposes a hybrid mechanism that lets us
*discover* sequences by running them, then promote successful ones to encoded
form.

## Context: where light_cc sits today

Two extremes already exist in the codebase:

- **Free-form (LLM-decided)** — the main agent calls `Agent` (`tools/subagent.py`)
  when it judges delegation is warranted. Subagent type, prompt, and timing are
  all chosen by the model.
- **Single-fire (encoded)** — `core/scheduler.py` triggers one `AgentDefinition`
  on a cron, via `run_agent_once` in `core/agent_runner.py`.

The middle is missing: a *declared sequence of agent steps where each step
still has LLM latitude inside it*. Not a DAG editor like n8n — a linear/branching
recipe in YAML, run by a thin orchestrator.

## How light_cc handles the three chaining decisions today

| Decision | Current state | Gap |
|---|---|---|
| Context passing between agents | Summary-only. `on_tool_start` clears `output_parts` so only post-last-tool text reaches the parent. Capped at 10k chars. | No structured handoff (JSON schema in/out). |
| Depth limit | Hard cap at 2 — `Agent` is in `EXCLUDED_TOOLS` so subagents can't recurse. | No per-agent override for legitimate 3-deep cases. |
| Termination | Per-call `max_turns` (default 20), `timeout` (300s), token tracking. | No parent-level *total* budget across children. |

Primitives are solid. The missing piece is the encode-vs-decide layer.

## When to encode vs let the LLM decide

Pick per-use-case, not per-system:

| Axis | LLM decides | Encoded |
|---|---|---|
| Frequency | Rare / ad-hoc | Runs often |
| Input shape | Novel each time | Stable |
| Cost sensitivity | Low (one-shot) | High (every run) |
| Reproducibility | Don't need it | Need to debug / A-B test |
| Failure cost | Low (you retry) | High (prod artifact) |
| Exploration | Yes | No |

Concrete examples:

- **LLM-decided**: "help me understand this repo", "plan this refactor",
  Claude-Code-style coding. Structure is emergent.
- **Encoded**: morning briefing, scheduled report, PR triage. Shape is known.
- **Hybrid**: "research person X" — the *sequence* (gather → dedupe → synthesize)
  is stable, but the *decisions inside each step* (which sources? which angles?)
  need judgment.

Traps:
- Pure LLM-decide → non-reproducibility, cost drift, hard to test.
- Pure encoding → rebuilds n8n, loses the reason we didn't use n8n.

## Four chaining patterns worth supporting

1. **Delegation (parent → named child)** — Claude Code's `Agent` tool (formerly `Task`). Parent
   says "do X, report back." Child runs its own loop, returns a summary.
   Already supported. Use when: protect parent context from noise, or subtask
   has its own tool set.
2. **Fan-out (parent → N children in parallel)** — "Compare these 5 libraries"
   spawns 5 research agents simultaneously, parent synthesizes. Already
   supported via `run_in_background=true`. Use when: embarrassingly parallel.
3. **Hand-off (sequential, different personas)** — planner → executor → critic.
   Each sees the previous agent's output as input. Different from parent/child:
   it's a pipeline of peers. **Not first-class today.**
4. **Triggered (async, event-driven)** — scheduler/webhook wakes agent A, which
   may spawn agent B if it finds something interesting. Already supported via
   the scheduler + `trigger_agent_run`.

Patterns 1, 2, 4 are covered. Pattern 3 (hand-off) is what playbooks formalize.

## Use-case shapes

| Use case | Pattern | Why non-deterministic |
|---|---|---|
| Deep research | Fan-out + synthesis | Agent decides how many angles based on what it finds |
| Coding tasks | Parent + Explore subagents | Parent only delegates when search space warrants |
| Morning briefing | Triggered + sequential pipeline | Mostly linear but "skip boring sections" is judgment |
| Inbox/PR triage | Triggered + conditional delegation | Spawn deep-dive *only if* something needs attention |
| Multi-role workflows | Hand-off loop | Critic may send back to planner — loop count emergent |
| Long-running ops | Parent with checkpoint subagents | Parent decides when to summarize and restart fresh |

## Proposal: the playbook primitive

A YAML file declaring a sequence of agent steps. Each step is still a real
`run_agent_once` call — full LLM loop, full tool access. Only the *shape*
(which agents, in what order, with what inputs) is frozen.

```yaml
# agents/person-research/PLAYBOOK.yaml
steps:
  - agent: web-researcher
    prompt: "Find public info on {{subject}}. Return JSON {sources, key_facts}."
    output_schema: {sources: [...], key_facts: [...]}
  - agent: verifier
    prompt: "Cross-check: {{previous.key_facts}}"
    when: "{{previous.sources | length > 2}}"
  - agent: synthesizer
    prompt: "Brief on {{subject}} using {{steps.*.result}}"
```

Properties:
- **Shape encoded** — sequence, conditional gates, what feeds what.
- **Intelligence per-step** — each step is a real LLM loop.
- **Cheap to reason about** — diff a playbook, log it, replay with new inputs.
- **Falls back to free-form** — any step can be `agent: default,
  prompt: "figure out what to do next"` if you want LLM to drive a segment.

### Where it slots in the codebase

- New `core/playbook.py` — loads YAML, walks steps, calls `run_agent_once` per
  step, threads outputs through a context dict.
- New tool `RunPlaybook` next to `Agent` in `tools/subagent.py` — the main
  agent can invoke a playbook the same way it invokes a subagent.
- Schedule rows already reference an agent name; add `playbook_name` as a
  sibling column and the cron path gets playbooks for free.

Estimated size: ~400-600 lines of Python plus YAML schemas.

## The observe-then-encode pattern

The strongest insight from this design: **don't author playbooks up front,
discover them by running the LLM-decided version, then promote.**

This matches how humans write SOPs: do it manually a few times, notice the
pattern, write it down, refine when it breaks. Exploration → exploitation.

### What gets captured from a run

The LLM's trajectory is already a DAG — Agent calls, tool calls, branches.
Most ingredients exist:
- `AgentRun` rows (`core/db_models.py`)
- `parent_session_id` threading (`core/agent_runner.py:148`)
- Conversation messages with `tool_use` blocks

**Missing piece**: explicit `parent_run_id → child_run_id` edge so you can
reconstruct the tree without grepping sessions.

### Three "lock levels" — pick deliberately

| Level | What's frozen | What's still LLM | Generalizes? |
|---|---|---|---|
| **Shape** | Agent sequence (A→B→C) | Prompts, tool calls, decisions | Well — handles novel inputs |
| **Template** | Agents + parameterized prompts (`{{subject}}`) | Tool calls inside each step | OK — as long as inputs match the template |
| **Replay** | Agents + prompts + tool args | Nothing, basically | Badly — one lucky run doesn't generalize |

Most value lives at **Shape**. Level 3 is what people ask for first and regret —
brittle because the LLM's step-3 choice depended on a specific thing in step-2's
output that won't recur.

### The real risk: one good run isn't proof

A successful run might have made a choice that only works for that specific
input. Promote it, run with a new input, silent failure.

**Mitigation: shadow mode.** After promotion, every Nth invocation also runs
free-form in parallel, diff the outputs, alert on divergence. Cheap insurance;
tells you when the encoded path has drifted.

Each playbook gets a `shadow_rate: 0.1` field — run free-form alongside 10%
of the time, log divergences.

### Promotion flow (proposed)

1. User runs a task in free-form mode.
2. Likes the result, hits "save as playbook."
3. New endpoint `POST /api/runs/{id}/propose-playbook` — sends the trace
   + a meta-prompt to Claude: "describe the abstract sequence; mark what's
   input vs derived." Returns YAML for review.
4. User reviews/edits the YAML, approves.
5. Saved as a playbook in `agents/{name}/PLAYBOOK.yaml`. `RunPlaybook` tool
   and schedule integration come along for free.
6. Future runs use the playbook; shadow runs catch drift.

**Important caution**: resist promoting after a single success. Two or three
runs on different inputs first — catches "worked by luck" cases before they're
baked in.

## Implementation order

1. **Schema**: add `parent_run_id` FK to `AgentRun`. Alembic migration.
2. **Trace extraction**: helper that takes a `run_id` and returns the tree of
   child runs + their tool_use sequences from the conversation messages.
3. **Playbook engine**: `core/playbook.py` — YAML loader + step executor. Threads
   `{{previous}}`, `{{steps.*.result}}`, `{{input.*}}` through a context dict.
   Honours `when:` conditions.
4. **RunPlaybook tool**: register alongside `Agent` in `tools/subagent.py`.
5. **Schedule integration**: `playbook_name` column on Schedule, dispatch path
   in `core/scheduler.py`.
6. **Promotion endpoint**: `POST /api/runs/{id}/propose-playbook` with the
   meta-prompt that abstracts a trace into YAML.
7. **Shadow mode**: `shadow_rate` field on playbooks; runner spawns a parallel
   free-form run, persists diff for review.

Steps 1-4 unlock everything. 5-7 are polish.

## Why this isn't n8n

n8n: humans wire the graph, LLM is an optional node.
light_cc + playbooks: LLM is the runtime; playbooks are *learned shortcuts* for
shapes that recur. The LLM still drives novel work; playbooks just stop us
re-deriving the same shape every time.

The system stays agentic by default, deterministic only where determinism has
been earned by observation.
