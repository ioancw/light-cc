# Light CC — User Guide

A self-hosted, multi-user agentic AI platform built on Claude. Ships with memory, scheduled agents, MCP integration, and a plugin system.

---

## 1. What Light CC is

A FastAPI server + Svelte frontend that gives you a chat interface backed by Claude, plus the surrounding scaffolding to make it useful for real work:

- A **clean agent loop** — Claude drives, no DAG framework.
- **Persistent memory** — Zettelkasten-style notes injected into prompts.
- **Scheduled agents** — full reasoning loops triggered by cron.
- **Tools and skills** — Python tools the agent can call, plus markdown guidance.
- **MCP integration** — works with any MCP server out of the box.
- **Plugins** — bundle skills/commands/agents/MCP into installable units.

It mimics Claude Code's abstractions exactly so artefacts created in CC can be deployed here.

---

## 2. Quickstart

### Install

```bash
git clone <repo> light_cc
cd light_cc
pip install -r requirements.txt
cp .env.example .env   # then fill in keys
```

### Required environment

```
ANTHROPIC_API_KEY=sk-ant-...
JWT_SECRET=<long-random-string>     # required in production
```

### Optional

```
TAVILY_API_KEY=tvly-...              # better web search
DATABASE_URL=postgresql+asyncpg://...   # default: sqlite at data/lightcc.db
REDIS_URL=redis://localhost:6379/0   # optional, used for session/cache
S3_BUCKET=...                         # optional, otherwise local filesystem
```

### Run

```bash
# Apply DB migrations
alembic upgrade head

# Start the server
python server.py
# or: uvicorn server:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000`. Register an account on first launch, then sign in.

### Docker

```bash
docker compose up
```

The included `docker-compose.yml` boots the app + Postgres + Redis. The observability stack (Grafana, Loki, OTEL collector) lives in `docker-compose.observability.yml`.

---

## 3. The chat interface

The main panel is a streaming chat with Claude. Notable features:

- **Model selector** — Sonnet (default), Haiku (cheap/fast), Opus (heavy reasoning). Set per-conversation.
- **Suggestion chips** — quick-launch prompts shown on a new chat. Configured in `config.yaml`.
- **Slash commands** — type `/` to invoke a shortcut (see §6).
- **File uploads** — drag a file in; it's stored under `data/uploads/` and made available to the agent.
- **Tool cards** — every tool call shows up as an expandable card with input + output.
- **Permission prompts** — destructive operations (file write, bash) ask for approval unless you've set a permission mode.
- **Conversation history** — left sidebar; full-text search across all your conversations.
- **Optimistic UI** — switching conversations shows a skeleton loader while the backend hydrates.

---

## 4. The concept stack

Light CC has six runtime concepts. Knowing how they relate prevents confusion.

| Concept | What it is | Where it lives |
|---|---|---|
| **Tool** | Python function with a JSON schema (Grep, WebSearch, Bash). Deterministic, callable. | `tools/*.py` |
| **MCP server** | A tool served over the MCP protocol by an external process. Language-agnostic, cross-model. | Listed in `.mcp.json` |
| **Skill** | Markdown instructions (`SKILL.md`) telling the agent *how* to approach a task. No code. | `skills/<name>/SKILL.md` |
| **Command** | A user-typed shortcut (`/morning-briefing`) that expands to a prompt. | `commands/*.md` |
| **Agent** | A named persistent definition: system prompt + allowed tools + model + optional schedule. | `agents/<name>/AGENT.md` |
| **Plugin** | A bundle that ships some combination of the above as one installable unit. | `plugins/<name>/` |

Tools are *what* the agent can do. Skills are *how* to do it. Agents are *named, reusable* configurations. Plugins are *how you ship them*.

---

## 5. Memory

Light CC has a first-class memory system. Memory is **scoped per user** by default, persisted to the database (with optional file fallback for backup/portability).

### Types

| Type | Purpose | Example |
|---|---|---|
| `user` | Who you are, your preferences | "I'm a quant researcher; explain math precisely." |
| `feedback` | Behaviour corrections | "Don't summarise diffs back to me." |
| `project` | Current work, deadlines, decisions | "Auth migration ships 2026-04-20." |
| `reference` | Pointers to external resources | "Bugs tracked in Linear project INGEST." |

### How memory is used

On every turn, relevant memories are injected into the system prompt. The agent reads them like context but doesn't see them as user messages.

After a conversation ends, a background job (`core/memory_extractor.py`) reads the transcript and proposes new memories. You approve or reject in the **Memory Panel** (sidebar).

### Slash commands

- `/remember <text>` — save a memory immediately
- `/recall <topic>` — list memories matching a topic
- `/search <text>` — full-text search across conversations + memories

### Memory storage

- **Default**: SQLite (`data/lightcc.db`), table `memories`.
- **Production**: Postgres via `DATABASE_URL`.
- **Files**: `data/users/<user_id>/memory/*.md` — written as a backup; the DB is authoritative.

---

## 6. Slash commands

Type `/` in the chat to see all available commands. Bundled commands:

| Command | What it does |
|---|---|
| `/analyze <file-or-url>` | Summarise a paper, dataset, or webpage |
| `/chart <description>` | Build a chart from data in the conversation |
| `/export` | Export the current conversation to markdown |
| `/github <owner/repo>` | Inspect a GitHub repo via the GitHub MCP/skill |
| `/recall <topic>` | List memories matching a topic |
| `/remember <text>` | Save a memory |
| `/revert` | Rewind the conversation to before a tool call (uses checkpoints) |
| `/run <agent-name>` | Trigger an agent manually |
| `/search <text>` | Search across conversations and memories |

Commands are just markdown files in `commands/`. To add one, drop in `commands/my-cmd.md`:

```markdown
---
description: What this command does
---
The prompt that gets sent. $ARGUMENTS is replaced with whatever the user typed after the command.
```

---

## 7. Agents

An **agent** is a named configuration: system prompt + allowed tools + model + optional cron schedule. Agents can be:

- **Manually triggered** from the UI or via `/run <name>`
- **Scheduled** via cron (e.g. `0 8 * * 1-5` for weekday mornings)
- **API-triggered** via `POST /api/agents/run`
- **Webhook-triggered**

### Bundled agents

- **`morning-briefing`** — daily news + calendar + email digest (cron-driven)
- **`person-research`** — deep-dive research on a named person

### Defining an agent

`agents/my-agent/AGENT.md`:

```markdown
---
name: my-agent
description: Short summary
model: claude-sonnet-4-6           # optional
tools: [WebSearch, WebFetch]       # optional; null = all tools
max-turns: 15
timeout: 300
trigger: cron                       # manual | cron | webhook | api
cron: "0 8 * * 1-5"
timezone: Europe/London
memory-scope: user                  # user | agent | none
webhook-url: https://...            # called on completion (optional)
---

You are a helpful agent. Your job is to ...
```

### The Agent Panel

The frontend has a panel for managing agents:

- List all your agents (yours + YAML-loaded + plugin-shipped)
- Create / edit / delete from the UI
- View run history with full transcripts
- Manually trigger any agent
- See next scheduled run

### Sub-agents

Within a conversation, the main agent can spawn a sub-agent for an isolated subtask via the `Subagent` tool. The sub-agent has its own context window and tool budget, and returns a summary to the parent.

---

## 8. Tools

Tools are Python functions the agent calls. Light CC ships with 12, mirroring Claude Code's set:

| Tool | Purpose |
|---|---|
| `Read` | Read a file by absolute path |
| `Write` | Create or overwrite a file |
| `Edit` | Exact-string replace in a file |
| `Glob` | Find files by glob pattern |
| `Grep` | Regex search across files |
| `Bash` | Run a shell command |
| `WebFetch` | Fetch a URL (SSRF-protected) |
| `WebSearch` | Search the web (Tavily / DuckDuckGo) |
| `PythonExec` | Execute Python in a sandbox |
| `Chart` | Generate a chart |
| `Subagent` | Spawn an isolated sub-agent |
| `Tasks` | Track multi-step task lists |
| `Skill` | Invoke a skill at runtime (mirrors Claude Code) |

All tools have enriched 5-part descriptions (what / when / inputs / outputs / edge cases) following Anthropic's "writing tools for agents" guidance.

### Permissions

Tools that touch the filesystem or shell go through `core/permissions.py`. Modes (set per session):

- **`default`** — destructive operations prompt for approval
- **`acceptEdits`** — edits auto-approved, shell still prompts
- **`bypassPermissions`** — no prompts (use carefully)
- **`plan`** — agent can read but not write or run anything

### Adding a tool

```python
from tools.registry import register_tool

async def my_handler(tool_input: dict) -> str:
    ...
    return json.dumps({"result": ...})

register_tool(
    name="MyTool",
    aliases=["my_tool"],
    description="...",
    input_schema={...},
    handler=my_handler,
)
```

Drop the file in `tools/` and import it from `tools/__init__.py`.

---

## 9. Web search (Tavily)

`WebSearch` uses Tavily as the primary backend (better LLM-tuned snippets and a synthesised answer field), falling back to DuckDuckGo when no key is set.

Set `TAVILY_API_KEY` in `.env`. Get a key at <https://app.tavily.com>.

The tool exposes Tavily's useful params:
- `search_depth`: `basic` (fast) or `advanced` (deeper crawl)
- `topic`: `general`, `news`, or `finance`
- `include_answer`: returns a synthesised answer alongside results
- `include_domains` / `exclude_domains`: domain filters

---

## 10. MCP servers

Light CC speaks MCP (Model Context Protocol) — the cross-model standard for tool servers. Add servers to a `.mcp.json` file at the project root (or inside any plugin):

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

MCP tools are namespaced as `<server>__<tool>` (e.g. `github__create_issue`) to avoid collisions with built-in tools.

---

## 11. Plugins

A **plugin** is a folder bundling skills, commands, agents, and MCP servers as one installable unit. Light CC uses the same `.claude-plugin/plugin.json` layout as Claude Code, so plugins authored in CC can be deployed here without changes.

### Plugin layout

```
my-plugin/
    .claude-plugin/
        plugin.json           # required manifest
    .mcp.json                 # optional MCP servers
    commands/
        my-command.md
    skills/
        my-skill/SKILL.md
    agents/
        my-agent/AGENT.md
```

Skills, commands, and agents loaded from a plugin are auto-namespaced as `plugin-name:thing-name`. Plugin agents are stored in the DB with `source='plugin:<plugin-name>'`, so uninstalling cleanly removes them.

### Plugin CLI

```bash
# Install from a git URL or local path
python scripts/plugin_cli.py install https://github.com/user/my-plugin.git
python scripts/plugin_cli.py install ./local-plugin

# List
python scripts/plugin_cli.py list

# Update (git pull)
python scripts/plugin_cli.py update my-plugin

# Uninstall
python scripts/plugin_cli.py uninstall my-plugin
```

Restart the server after install/update to activate.

### Manifest

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "What this plugin does",
  "author": "Author Name",
  "license": "MIT",
  "min_lightcc_version": "0.9.0",
  "dependencies": {
    "python": ["requests>=2.28"],
    "npm": []
  },
  "permissions": {
    "tools": ["WebFetch", "Bash"],
    "mcp_servers": ["my-server"]
  }
}
```

User-edited agents (`source='user'`) are never overwritten by a plugin sync — fork freely.

See `docs/plugin-spec.md` for the full spec.

---

## 12. Multi-user

Light CC is multi-tenant by design. Each user gets:

- Isolated conversations
- Isolated memory (`memory_scope: user` is the default)
- Their own seeded copy of every YAML-defined and plugin-shipped agent
- Their own usage tracking

Auth is JWT-based (`core/auth.py`). Registration is enabled by default; disable with `auth.registration_enabled: false` in `config.yaml`.

Admin users can be promoted via SQL (`UPDATE users SET is_admin=true WHERE email=...`).

---

## 13. Configuration

Two files:

### `config.yaml`

Server-wide defaults committed to the repo. Highlights:

- `model` — default model for new conversations
- `max_tokens`, `max_context_tokens`, `max_turns` — generation limits
- `compression_threshold` — auto-compress conversation when context fills past this fraction
- `routing_enabled` + `routing_rules` — regex-driven model routing (Haiku/Sonnet/Opus tiers)
- `paths.*` — where to look for skills, commands, plugins, agents
- `available_models` — what shows up in the model selector
- `suggestions` — chips on the empty-state new-chat screen
- `auth.*` — registration toggle, JWT expiry
- `hooks` — event-driven shell commands (Claude Code compatible)

### `.env`

Secrets and per-environment settings. See `.env.example`. Loaded via python-dotenv at startup.

---

## 14. API surface

Every UI feature has a REST equivalent. Key endpoints:

| Route | Purpose |
|---|---|
| `POST /api/auth/register` | Create user |
| `POST /api/auth/login` | Get JWT |
| `GET /api/conversations` | List your conversations |
| `GET /api/conversations/{id}` | Full transcript |
| `WS /ws` | Chat WebSocket (typed message protocol, see `core/ws_models.py`) |
| `GET /api/agents` | List your agents |
| `POST /api/agents` | Create agent |
| `POST /api/agents/{id}/run` | Trigger by ID |
| `POST /api/agents/run` | Trigger by name |
| `GET /api/agents/{id}/runs` | Run history |
| `GET /api/memory` | List your memories |
| `POST /api/memory` | Save memory |
| `GET /api/schedules` | List scheduled prompts |
| `GET /api/files/{id}` | Download an uploaded file |
| `GET /api/usage` | Token usage stats |

All routes (except `/api/auth/*`) require `Authorization: Bearer <jwt>`.

OpenAPI docs at `http://localhost:8000/docs`.

---

## 15. Observability

When `docker-compose.observability.yml` is up:

- **OTEL collector** receives traces from the agent loop and tools
- **Loki** aggregates structured logs (each log line carries `user_id`, `cid`, `tool_name`)
- **Grafana** dashboards at `http://localhost:3000`

Without it, logs go to stdout in JSON format with the same context fields.

---

## 16. Troubleshooting

**"Agent didn't pick up my new YAML file"**
Restart the server. YAML agents are synced once at startup (and on user signup).

**"My plugin's agent shows the old prompt after editing"**
Plugin agents are upserted at plugin load. Restart the server, or uninstall+reinstall the plugin.

**"I edited a plugin agent in the UI and it disappeared on restart"**
It didn't — your edits are preserved. The DB row's `source` was changed to `user`, which protects it from plugin syncs but means it stays even after the plugin is uninstalled.

**"WebSearch is slow / returning weird results"**
Check `TAVILY_API_KEY` is set. Without it, the tool falls back to DuckDuckGo (less reliable). Try `search_depth: advanced` for research queries.

**"Permission prompts keep appearing for the same tool"**
Set `permission_mode` on the conversation, or grant per-tool persistent approval in the UI.

**"My MCP server isn't loading"**
Check the server logs at startup — MCP failures are logged as warnings, not crashes. Verify the `command` in `.mcp.json` resolves on your `PATH`.

---

## 17. Where to look in the code

| You want to... | Look at |
|---|---|
| Understand the agent loop | `core/agent.py` |
| Add a tool | `tools/<name>.py` + `tools/__init__.py` |
| Add a skill | `skills/<name>/SKILL.md` |
| Add an agent | `agents/<name>/AGENT.md` |
| See how agents are persisted | `core/db_models.py` (`AgentDefinition`, `AgentRun`) |
| Understand plugin loading | `core/plugin_loader.py` |
| Tweak the system prompt | `server.py` (search for "system prompt") |
| Change WS message types | `core/ws_models.py` |
| See the scheduler | `core/scheduler.py` |
| Look at the frontend chat | `frontend/src/components/` |
