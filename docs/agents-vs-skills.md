# Agents vs Skills

A short note on the distinction, because it's easy to conflate.

## The one-line version

**Skills describe procedures. Agents execute.**

## The analogy

- **Skill** = a recipe card. Sits in a drawer. Does nothing on its own.
- **Agent** = a cook. Shows up for their shift at 8 AM. Can open the drawer and use a recipe card if they want.

Without an agent (or a user), a skill never runs. Without skills, an agent has to figure everything out from first principles every time.

## What each one has

| | Skill | Agent |
|---|---|---|
| **Purpose** | Reusable procedure | Autonomous worker |
| **Trigger** | Invoked on demand (user types `/name`, or another agent calls the `Skill` tool) | Wakes itself up — cron, webhook, manual run |
| **Lifecycle** | None — stateless text | Starts, executes, completes; produces an `AgentRun` row + conversation |
| **Stored as** | `skills/<name>/SKILL.md` | `agents/<name>/AGENT.md` + DB row (`AgentDefinition`) |
| **Runtime identity** | None — it's just text loaded into a system prompt | Owns a session, a user, tools, permissions |

## Concrete check

Look at two files in this repo:

- `skills/morning-briefing/SKILL.md` — when does this do anything? Only when someone invokes it. Delete every agent and every user, and the skill is inert.
- `agents/morning-briefing/AGENT.md` — when does this do anything? Every weekday at 8 AM, by itself. That's what makes it an agent.

## How they compose

Agents can invoke skills via the `Skill` tool (see `tools/skill_tool.py`). This mirrors Claude Code: an agent's inline `system_prompt` is its identity, and skills are procedures it can pull off the shelf during a run.

The shipped `morning-briefing` agent is a thin wrapper:

```yaml
---
name: morning-briefing
tools: [WebSearch, WebFetch, Write, Skill]
trigger: cron
cron: "0 8 * * 1-5"
---
You are a morning briefing agent. Each run, invoke the `morning-briefing`
skill and follow its procedure to produce the briefing.
```

Flow at 8 AM on a weekday:

1. Cron timer fires → scheduler picks up the `AgentDefinition` with `trigger=cron`
2. Agent wakes up, reads its own prompt
3. Agent calls `Skill(skill="morning-briefing")` → skill body is returned
4. Agent follows the skill's instructions, produces the briefing, persists the run

Drop the agent, and the skill still exists but nothing happens at 8 AM. Drop the skill, and the agent wakes up at 8 AM with no recipe to follow.

## Why keep them separate

- **Skills are reusable across contexts** — invoked from chat, from other skills, from multiple agents
- **Agents are the unit of autonomy** — scheduling, triggers, run history, webhooks live on the agent, not the skill
- **One skill can back many agents** — e.g. `morning-briefing` skill could power one agent on a weekday cron and another triggered by webhook from a team Slack

If agents owned their instructions inline with no skill indirection, you'd copy-paste the procedure every time you wanted a new trigger. Keeping procedures (skills) separate from execution (agents) is the same split as Unix scripts vs crontab entries.
