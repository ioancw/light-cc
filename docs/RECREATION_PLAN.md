# Light CC -- Complete Recreation Blueprint

This document contains everything needed to recreate Light CC from scratch on a fresh machine. It covers architecture, every file, every API, every CSS value, every protocol detail.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [Directory Structure](#3-directory-structure)
4. [Phase 1: Project Scaffolding](#phase-1-project-scaffolding)
5. [Phase 2: Core Infrastructure](#phase-2-core-infrastructure)
6. [Phase 3: Tool System](#phase-3-tool-system)
7. [Phase 4: Agentic Loop](#phase-4-agentic-loop)
8. [Phase 5: WebSocket Protocol](#phase-5-websocket-protocol)
9. [Phase 6: HTTP REST API](#phase-6-http-rest-api)
10. [Phase 7: Skills, Commands, Plugins](#phase-7-skills-commands-plugins)
11. [Phase 8: Frontend -- Complete UI Spec](#phase-8-frontend)
12. [Phase 9: Scheduler, Jobs, Memory](#phase-9-scheduler-jobs-memory)
13. [Phase 10: Observability](#phase-10-observability)
14. [Phase 11: Deployment](#phase-11-deployment)
15. [Appendix A: Complete CSS Design Tokens](#appendix-a-css-design-tokens)
16. [Appendix B: WebSocket Event Reference](#appendix-b-websocket-event-reference)
17. [Appendix C: Tool Schema Reference](#appendix-c-tool-schema-reference)

---

## 1. Project Overview

Light CC is a self-hosted, multi-user agentic coding assistant. It runs a FastAPI/WebSocket backend that drives a streaming tool-use loop against the Anthropic API, with a Svelte 5 frontend. Think "Claude Code as a web app" with:

- Streaming agentic loop with parallel tool execution
- Multi-conversation multiplexing over a single WebSocket
- Per-user workspaces with file sandboxing
- Plugin system (agentskills.io compatible)
- MCP server integration
- Scheduled autonomous agents (cron)
- File checkpointing and rollback
- 5 color themes, responsive layout
- Context compression with rollback
- Permission modes (default, auto_edit, plan, auto)

---

## 2. Technology Stack

### Backend
| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | >= 3.12 |
| Web framework | FastAPI | >= 0.115 |
| ASGI server | uvicorn | >= 0.30 |
| ORM | SQLAlchemy (async) | >= 2.0 |
| DB (dev) | SQLite + aiosqlite | >= 0.19 |
| DB (prod) | PostgreSQL + asyncpg | >= 0.29 |
| Cache/pubsub | Redis + hiredis | >= 5.0 |
| Migrations | Alembic | >= 1.13 |
| Job queue | arq | >= 0.26 |
| AI SDK | anthropic | >= 0.40 |
| MCP SDK | mcp | >= 1.26 |
| Auth | python-jose + bcrypt | jose >= 3.3, bcrypt >= 4.0 |
| HTTP client | httpx | latest |
| Data | pandas, plotly, matplotlib, kaleido | |
| Search | duckduckgo-search | |
| Logging | structlog >= 24.0 | |
| Tracing | opentelemetry-api/sdk >= 1.20 | |
| Metrics | prometheus-client >= 0.20 | |
| Storage | boto3 >= 1.34 (optional S3) | |
| Cron | croniter >= 2.0 | |
| Config | pyyaml, python-dotenv, pydantic >= 2.0 | |
| Process info | psutil >= 5.9 | |
| File locking | filelock >= 3.12 | |

### Frontend
| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | Svelte 5 (Runes API) | ^5.0.0 |
| Build tool | Vite | ^6.0.0 |
| Markdown | marked | ^15.0.0 |
| Sanitizer | DOMPurify | ^3.0.0 |
| Math | KaTeX | ^0.16.11 |
| Syntax | Prism.js | ^1.29.0 |
| Charts | plotly.js-dist-min | ^3.4.0 |

### Fonts (Google Fonts, `display=swap`)
| Font | Usage | Weights |
|------|-------|---------|
| DM Sans | `--font-ui` -- all UI text | 300, 400, 500, 600 (+ italic) |
| Geist Mono | `--font-mono` -- code, tool output, status | 300, 400, 500, 600 |
| Source Serif 4 | `--font-prose` -- message body text | 300-700 (+ italic 400, 500) |
| Lora | Decorative/fallback | 400, 500 (+ italic 400) |

---

## 3. Directory Structure

```
light_cc/
├── server.py                   # FastAPI app, system prompt builder, endpoints
├── worker.py                   # arq worker entry point
├── config.yaml                 # Default configuration
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Project metadata + tool config
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── alembic/
│   ├── env.py                  # Async Alembic runner
│   └── versions/*.py           # Migration files
├── core/
│   ├── agent.py                # THE agentic loop
│   ├── agent_types.py          # Sub-agent type definitions
│   ├── auth.py                 # JWT + bcrypt
│   ├── checkpoints.py          # File snapshot/revert
│   ├── client.py               # Anthropic client singleton
│   ├── config.py               # Pydantic Settings
│   ├── context.py              # Token counting + compression
│   ├── database.py             # Async engine + session factory
│   ├── db_models.py            # SQLAlchemy ORM models
│   ├── hooks.py                # Hook system
│   ├── job_queue.py            # arq/asyncio abstraction
│   ├── log_context.py          # Logging setup
│   ├── mcp_client.py           # MCP stdio + HTTP client
│   ├── models.py               # SkillDef, CommandDef, ToolDef Pydantic models
│   ├── permission_modes.py     # Permission mode enum + check logic
│   ├── permissions.py          # Blocked/risky pattern detection
│   ├── plugin_loader.py        # Plugin discovery/load/unload
│   ├── project_config.py       # CLAUDE.md discovery
│   ├── providers/              # Multi-provider support
│   │   ├── base.py             # Abstract provider
│   │   ├── registry.py         # Provider registry
│   │   ├── anthropic.py        # Anthropic provider
│   │   ├── openai.py           # OpenAI-compatible provider
│   │   └── ollama.py           # Ollama provider
│   ├── rate_limit.py           # Token bucket + Redis sliding window
│   ├── redis_store.py          # Redis helpers
│   ├── rules.py                # .claude/rules/*.md
│   ├── sandbox.py              # Per-user workspace paths + role-based write perms
│   ├── sandbox_exec.py         # Sanitized subprocess execution
│   ├── user_context.py         # current_user_id() helper for shared code
│   ├── schedule_crud.py        # Schedule DB CRUD
│   ├── scheduler.py            # Cron scheduler loop
│   ├── search.py               # Full-text conversation search
│   ├── session.py              # Two-tier session store
│   ├── storage.py              # Local/S3 abstract storage
│   ├── telemetry.py            # structlog + OTEL + Prometheus
│   ├── usage.py                # Token usage + cost tracking
│   └── ws_models.py            # WebSocket message Pydantic models
├── handlers/
│   ├── agent_handler.py        # handle_user_message, title gen, summarize
│   ├── commands.py             # /plugin and /schedule handlers
│   ├── media.py                # Image/chart/table extraction
│   └── ws_router.py            # WebSocket endpoint + event dispatch
├── routes/
│   ├── auth.py                 # /api/auth/*
│   ├── conversations.py        # /api/conversations/*
│   ├── schedules.py            # /api/schedules/*
│   ├── files.py                # /api/files/*
│   ├── admin.py                # /api/admin/*
│   └── usage.py                # /api/usage/*
├── tools/
│   ├── __init__.py             # Import-triggers all registrations
│   ├── registry.py             # register_tool, execute_tool, get_all_tool_schemas
│   ├── bash.py                 # Bash tool
│   ├── read.py                 # Read tool
│   ├── write.py                # Write tool
│   ├── edit.py                 # Edit tool
│   ├── glob_tool.py            # Glob tool
│   ├── grep.py                 # Grep tool
│   ├── python_exec.py          # PythonExec tool
│   ├── web.py                  # WebFetch + WebSearch
│   ├── chart.py                # CreateChart (18 types)
│   ├── data_tools.py           # LoadData + QueryData + ExportData
│   ├── subagent.py             # Agent + AgentStatus
│   ├── tasks.py                # TaskCreate + TaskUpdate + TaskList
│   ├── tool_search.py          # ToolSearch
│   ├── eval_optimize.py        # EvalOptimize
│   ├── chart_theme.py          # Plotly dark theme helper
│   └── d3_theme.py             # D3.js HTML wrapper
├── skills/
│   ├── loader.py               # SKILL.md parser
│   ├── registry.py             # Skill registry
│   └── <name>/SKILL.md         # Individual skills
├── commands/
│   ├── loader.py               # Command file parser
│   ├── registry.py             # Command registry
│   └── *.md                    # Command files
├── memory/
│   └── manager.py              # Memory system + tool registration
├── plugins/                    # Plugin directories
├── scripts/
│   ├── entrypoint.sh           # Docker entrypoint
│   ├── migrate_file_memories.py
│   └── plugin_cli.py
├── static/
│   ├── loom.html               # Classic (non-Svelte) UI
│   └── auth.html               # Classic auth page
├── data/                       # Runtime data (gitignored)
│   ├── lightcc.db              # SQLite database
│   ├── outputs/                # Global output dir
│   └── users/{user_id}/        # Per-user workspaces
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── svelte.config.js
    ├── index.html
    └── src/
        ├── main.js
        ├── App.svelte
        ├── state.svelte.js
        ├── theme.js
        ├── ws.js
        ├── api.js
        ├── styles/
        │   ├── global.css
        │   └── themes.css
        ├── lib/
        │   ├── markdown.js
        │   ├── utils.js
        │   └── plotly.js
        └── components/
            ├── Loom.svelte
            ├── Auth.svelte
            ├── Sidebar.svelte
            ├── ChatArea.svelte
            ├── InputBar.svelte
            ├── MessageBubble.svelte
            ├── ToolCall.svelte
            ├── StatusBar.svelte
            ├── Toast.svelte
            ├── PermissionDialog.svelte
            ├── Settings.svelte
            ├── FilePanel.svelte
            └── renderers/
                ├── Chart.svelte
                ├── Image.svelte
                ├── Table.svelte
                └── HtmlEmbed.svelte
```

---

## Phase 1: Project Scaffolding

### 1.1 Initialize Python project

Create `pyproject.toml`:
```toml
[project]
name = "light-cc"
version = "0.1.0"
requires-python = ">=3.12"
dynamic = ["dependencies"]

[tool.setuptools.dynamic]
dependencies = {file = ["requirements.txt"]}

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W"]
ignore = ["E501"]
```

### 1.2 Create `config.yaml`

```yaml
model: "claude-sonnet-4-6-20250514"
max_tokens: 4096
max_context_tokens: 180000
max_turns: 50
compression_threshold: 0.8
max_tool_result_chars: 50000

database_url: "sqlite+aiosqlite:///data/lightcc.db"
jwt_secret: "change-me-in-production"
jwt_algorithm: "HS256"

available_models:
  - claude-sonnet-4-6
  - claude-haiku-4-5-20251001
  - claude-opus-4-6

paths:
  skills_dirs: ["skills", ".claude/skills"]
  commands_dirs: ["commands"]
  plugins_dirs: ["plugins"]
  data_dir: "data"
  memory_dir: "data/users"

server:
  host: "0.0.0.0"
  port: 8000
  frontend: "svelte"
  allowed_origins: ["*"]

auth:
  registration_enabled: true
  jwt_expiry_hours: 1
  jwt_refresh_expiry_days: 7

hooks: {}
providers: []
```

### 1.3 Create Pydantic Settings model (`core/config.py`)

```python
class PathsConfig(BaseModel):
    skills_dirs: list[str] = ["skills", ".claude/skills", ".claude/commands", "~/.claude/skills"]
    commands_dirs: list[str] = ["commands"]
    plugins_dirs: list[str] = ["plugins"]
    data_dir: str = "data"
    memory_dir: str = "data/users"

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    frontend: str = "classic"
    allowed_origins: list[str] = ["*"]
    metrics_public: bool = False

class AuthConfig(BaseModel):
    registration_enabled: bool = True
    jwt_expiry_hours: int = 1
    jwt_refresh_expiry_days: int = 7

class ProviderConfig(BaseModel):
    name: str
    api_key_env: str | None = None
    base_url: str | None = None
    models: list[str] = []

class Settings(BaseModel):
    model: str = "claude-sonnet-4-6-20250514"
    title_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 4096
    max_context_tokens: int = 180000
    max_turns: int = 50
    compression_threshold: float = 0.8
    max_tool_result_chars: int = 50000
    python_path: str | None = None
    database_url: str = "sqlite+aiosqlite:///data/lightcc.db"
    redis_url: str | None = None
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    available_models: list[str] = [...]
    project_dir: str | None = None
    hooks: dict[str, list[dict]] = {}
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_prefix: str = "lightcc/"
    providers: list[ProviderConfig] = []
    paths: PathsConfig = PathsConfig()
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    suggestions: list[dict[str, str]] = [
        {"label": "Top business stories", "prompt": "/morning-briefing"},
        {"label": "Summarize a research paper", "prompt": "/analyze ..."},
        {"label": "Analyze a dataset", "prompt": "/analyze Upload a CSV"},
        {"label": "What can you do?", "prompt": "What tools and skills do you have?"},
    ]
```

Loaded from `config.yaml` + env vars. Validates `jwt_secret != "change-me-in-production"` when `ENV` is production.

`suggestions` controls the clickable chips on the new-chat empty state. Each entry is `{label, prompt}`. Prompts starting with `/` invoke the matching skill/command.

---

## Phase 2: Core Infrastructure

### 2.1 Database (`core/database.py`)

Async SQLAlchemy engine. SQLite uses `create_all()` for dev. PostgreSQL uses Alembic.

```python
async def init_db():
    # Create engine from settings.database_url
    # For SQLite: run_sync(Base.metadata.create_all)

async def get_db() -> AsyncSession:
    # Return new session from factory

async def shutdown_db():
    # Dispose engine
```

### 2.2 ORM Models (`core/db_models.py`)

All PKs are `uuid4().hex` (32-char hex strings). All timestamps are UTC-aware.

| Table | Columns |
|-------|---------|
| `users` | id, email (unique, indexed), password_hash, display_name, is_admin (default False), created_at |
| `conversations` | id, user_id (FK, indexed), title (default "New conversation"), model (nullable), created_at, updated_at, is_deleted (default False) |
| `messages` | id, conversation_id (FK, indexed), role ("user"/"assistant"), content (Text, JSON-encoded), token_count (nullable, deprecated), created_at |
| `usage_events` | id, user_id (FK, indexed), conversation_id (nullable, indexed), model, input_tokens, output_tokens, cost_usd, created_at |
| `schedules` | id, user_id (FK, indexed), name, cron_expression, prompt, user_timezone (String(50), default "UTC"), enabled, last_run_at, next_run_at, created_at, updated_at. Unique constraint on (user_id, name) |
| `schedule_runs` | id, schedule_id (FK, indexed), status, result, error, started_at, finished_at, tokens_used, conversation_id (FK, nullable) |
| `memories` | id, user_id (FK, indexed), title, content, memory_type (default "note"), tags (JSON text, with `tags_list` property), created_at, updated_at |
| `audit_events` | id, user_id (indexed), tool_name, tool_input_hash (sha256), result_summary (max 500), success, duration_ms, created_at |

### 2.3 Auth (`core/auth.py`)

- bcrypt for password hashing
- python-jose HS256 JWTs
- Access token: 1h, contains `{sub, email, exp, type: "access", jti}`
- Refresh token: 7 days, contains `{sub, exp, type: "refresh", jti}`
- Revocation via Redis set `lcc:revoked_tokens` (jti stored, TTL = remaining token life)
- `decode_token()` is sync, does NOT check revocation
- `is_token_revoked()` is async, checks Redis

### 2.4 Session Store (`core/session.py`)

Two-tier: **connection** (per WebSocket) + **conversation** (per cid).

Connection state keys: `user_id`, `permission_mode`, `user_system_prompt`, `project_config`, `project_rules`

Conversation state keys: `messages`, `datasets` (DataFrames, not serialized), `last_figure`, `conversation_id`, `active_model`, `tasks`, `active_files`, `_conn_id`

ContextVars: `_current_session_id`, `_current_cid`, `_active_tool_filter`

Redis write-through: dirty cids flushed every 5 seconds. Messages go to DB via `save_conversation()`.

Redis keys: `lcc:session:{sid}` (TTL 1h), `lcc:conv:{cid}` (TTL 4h)

### 2.5 Redis Store (`core/redis_store.py`)

Optional. All operations no-op gracefully when unavailable.

Helpers: `save_session_state`, `load_session_state`, `save_conv_session`, `load_conv_session`, `set_add`, `set_check`, `set_remove`, `publish_notification`

### 2.6 Sandbox (`core/sandbox.py` + `core/sandbox_exec.py`)

**Design principle**: Light CC is a shared multi-user platform, not an open dev environment. Users consume the shared Python codebase through skills and tools but cannot modify it. Only admins can edit shared project areas.

Per-user workspace: `data/users/{user_id}/{workspace,outputs,uploads,memory}/`

#### Role-based path validation (`UserWorkspace.validate_path`)

| Action | Regular user | Admin |
|--------|-------------|-------|
| Read project root (source, skills, etc.) | Yes | Yes |
| Write to own workspace/outputs/uploads/memory | Yes | Yes |
| Write to `skills/`, `commands/`, `.cortex/` | No | Yes |
| Write to project root (Python code, config) | No | No |

- `is_admin` is loaded from the User DB record on WebSocket connect and stored in the session via `connection_set(session_id, "is_admin", ...)`
- Null bytes rejected; paths resolved before checking
- No-user / "default" sessions bypass validation (legacy compatibility)

#### Bash command lockdown (`validate_bash_command`)

The Bash tool validates commands before execution to prevent workspace escapes:
- Blocked patterns: `cd /`, `cd ..`, redirects to absolute paths (`> /path`), `cp`/`mv` to absolute paths, `ln`, `mount`, `chroot`
- Admin users bypass these checks
- `cwd` is always set to the user's workspace directory (non-overridable)

#### User context for shared code (`core/user_context.py`)

Shared Python code (skills, library functions) must scope DB queries to the active user. Helpers:

```python
from core.user_context import current_user_id, current_user_id_or_none, is_admin

# Raises PermissionError if no user session -- use for mandatory scoping
uid = current_user_id()
db.execute("SELECT * FROM portfolios WHERE user_id = :uid", {"uid": uid})

# Returns None if no user session -- use for optional scoping
uid = current_user_id_or_none()

# Check admin status
if is_admin():
    # allow broader access
```

These read from the session ContextVar, which is set automatically for both interactive and scheduled task contexts.

#### Subprocess execution
- ENV whitelist: `PATH, PYTHONPATH, HOME, USERPROFILE, SYSTEMROOT, COMSPEC, TEMP, TMP, LANG, LC_ALL, TERM, MPLBACKEND, OUTPUT_DIR`
- ENV blocklist: `ANTHROPIC_API_KEY, OPENAI_API_KEY, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, GITHUB_TOKEN, GH_TOKEN, DATABASE_URL, JWT_SECRET, SECRET_KEY`
- Always injects: `MPLBACKEND=Agg`
- Linux containers: prefix with `unshare --net` for network isolation
- Default timeout: 120s interactive, 60s scheduled
- Output truncation: 50000 chars stdout, 10000 chars stderr

---

## Phase 3: Tool System

### 3.1 Tool Registry (`tools/registry.py`)

```python
def register_tool(name, description, input_schema, handler, aliases=[]):
    # Store in _TOOLS dict, aliases in _ALIASES

async def execute_tool(name, input) -> str:
    # Look up handler, call it, return JSON string result

def get_all_tool_schemas() -> list[dict]:
    # Returns Claude API format tool schemas
    # Includes MCP tools from mcp_client

def get_tool_schemas(names: list[str]) -> list[dict]:
    # Filtered subset for skill-constrained calls
```

### 3.2 Built-in Tools

| Tool | Key inputs | Handler behavior |
|------|-----------|-----------------|
| **Bash** | `command: str`, `timeout?: int (max 600)` | Shell exec via `asyncio.create_subprocess_shell` in thread executor; Windows multiline workaround via tempfile |
| **Read** | `file_path: str`, `offset?: int`, `limit?: int (default 2000)` | Returns `{content, total_lines, showing}` with `cat -n` style line numbers |
| **Write** | `file_path: str`, `content: str` | Creates parent dirs, snapshots checkpoint, writes file. Returns `{status, path, bytes}` |
| **Edit** | `file_path, old_string, new_string`, `replace_all?: bool` | Search-replace, snapshots first. Returns `{status, replacements}` |
| **Glob** | `pattern: str`, `path?: str` | pathlib glob, sorted by mtime. Returns `{files: [...], total}` |
| **Grep** | `pattern: str`, `path?, glob?, ignore_case?, max_results? (50)` | Regex search. Returns `{matches: [{file, line, content}], count}` |
| **PythonExec** | `script: str`, `timeout?: int` | Writes to temp .py, runs via subprocess. Returns `{stdout, stderr, exit_code, artifacts?}` |
| **WebFetch** | `url: str`, `method?, headers?, body?` | httpx request with SSRF protection (blocks private IPs, metadata endpoints). Returns `{status, content, content_type}` |
| **WebSearch** | `query: str`, `max_results? (5)` | DuckDuckGo search. Returns `{results: [{title, url, body}]}` |
| **CreateChart** | `chart_type: str (18 types)`, `x/y/z/labels/values/title/...` | Plotly figure generation, saves as .plotly.json. Returns `{path, type}` |
| **LoadData** | `file_path: str`, `name?: str` | CSV/Excel/JSON to pandas DataFrame in session. Returns `{name, rows, columns, head_html, describe_html}` |
| **QueryData** | `name: str`, `code: str` | Evaluates pandas expression. Returns result as JSON/HTML |
| **ExportData** | `name, file_path`, `format? (csv/excel/json)` | Exports DataFrame |
| **Agent** | `prompt: str`, `agent_type?, agent_id?, run_in_background?, description?` | Sub-agent (max 100 active, TTL 1h). Types: explorer, planner, coder, researcher, default |
| **AgentStatus** | `agent_id?: str` | Check background agent status |
| **TaskCreate** | `title: str`, `status?` | Session-scoped task list |
| **TaskUpdate** | `task_id: str`, `status: str` | |
| **TaskList** | (none) | |
| **ToolSearch** | `query: str`, `max_results? (5)` | Searches all registered + MCP tools |
| **EvalOptimize** | `task, criteria`, `max_iterations? (5, cap 10)` | Generator-evaluator loop |
| **SaveMemory** | `title: str`, `content: str` | DB-backed with file fallback |
| **ReadMemory** | `filename: str` (or id) | |
| **SearchMemory** | `query: str` | |
| **ListMemories** | (none) | |

### 3.3 MCP Tools

Discovered via `session.list_tools()` on MCP server connect. Namespaced as `{server_name}__{tool_name}`. Config from `.mcp.json`:

```json
{
  "mcpServers": {
    "name": {"command": "...", "args": [...], "env": {...}},
    "remote": {"type": "http", "url": "...", "headers": {...}}
  }
}
```

---

## Phase 4: Agentic Loop

### 4.1 Core Loop (`core/agent.py`)

```python
async def run(messages, tools, system, on_text, on_tool_start, on_tool_end,
              on_permission_check=None, max_turns=None, model=None) -> list[dict]:
```

Each iteration:
1. Compress context if above threshold (80% of max_context_tokens)
2. Stream response via `client.messages.stream()`
3. Buffer text deltas (don't send to UI yet)
4. Parse tool_use blocks as they complete
5. If tool calls present:
   a. Execute all tools in parallel (`asyncio.gather`)
   b. Per-tool: rate limit check -> permission check -> PreToolUse hooks -> execute -> PostToolUse hooks
   c. Detect errors via `parsed.get("error")` (truthy check)
   d. Audit each call (sha256 input hash, result summary, duration)
   e. THEN flush buffered text (so Steps appear above text in UI)
   f. Append tool results as user message
6. If no tool calls: flush text, done
7. Retry on API errors: exponential backoff (2^attempt + jitter, max 30s), 3 attempts
8. Record token usage per turn

### 4.2 Context Compression (`core/context.py`)

Triggered at `max_context_tokens * compression_threshold` (144k default).

- Keep last 8 messages intact
- Summarize older messages via Haiku (1024 max tokens)
- Inject as `[Prior conversation summary]` user message + acknowledgment
- Store snapshot for rollback

Token counting: SDK `count_tokens()` with fallback to `len(text) // 4`.

### 4.3 Checkpoints (`core/checkpoints.py`)

In-memory, keyed by cid. `Write` and `Edit` tools call `snapshot_file(cid, path, turn)` before modifying.

- `revert_last(cid)` -- reverts most recent turn
- `revert_to_turn(cid, turn)` -- reverts all at/after turn
- If file didn't exist before, revert deletes it
- Cleared on WebSocket disconnect

---

## Phase 5: WebSocket Protocol

### 5.1 Connection Flow

1. Client connects to `ws[s]://host/ws`
2. Origin validation (before accept)
3. IP rate limiting (5/min per IP)
4. `ws.accept()`
5. Auth: first-message `{"type": "auth", "data": {"token": "..."}}` (preferred) OR `?token=` query param (deprecated, logs warning)
6. Server sends `connected` event
7. Full-duplex event exchange
8. On disconnect: save all conversations, cancel tasks, fire SessionEnd hooks, cleanup

### 5.2 Wire Format

All messages: `{"type": "event_name", "data": {...}, "cid": "conversation_id"}`

The `cid` is client-chosen, identifies a conversation sub-session. Max 3 concurrent active agents per connection.

### 5.3 Client -> Server Events

| type | data fields | notes |
|------|------------|-------|
| `auth` | `{token}` | First-message auth |
| `user_message` | `{text}` | Requires cid |
| `permission_response` | `{request_id, allowed}` | |
| `cancel_generation` | `{}` | cid-specific or all |
| `clear_conversation` | `{}` | |
| `resume_conversation` | `{conversation_id}` | Load from DB |
| `revert_checkpoint` | `{turn?}` | |
| `list_checkpoints` | `{}` | |
| `fork_conversation` | `{conversation_id}` | |
| `set_system_prompt` | `{text}` | Per-connection |
| `set_permission_mode` | `{mode}` | |
| `cycle_permission_mode` | `{}` | |
| `generate_title` | `{}` | |
| `summarize_context` | `{}` | Min 6 messages |
| `set_model` | `{model}` | |
| `retry` | `{}` | Re-run last user message |
| `rollback_compression` | `{}` | |

### 5.4 Server -> Client Events

| type | data fields |
|------|------------|
| `connected` | `{session_id, model, available_models, skills, suggestions, user}` |
| `error` | `{message}` |
| `text_delta` | `{text}` |
| `tool_start` | `{tool_id, name, input}` |
| `tool_end` | `{tool_id, result, is_error}` |
| `image` | `{tool_id, name, mime_type, data_base64}` |
| `chart` | `{tool_id, title, plotly_json}` |
| `table` | `{tool_id, html}` |
| `html_embed` | `{tool_id, name, html}` |
| `permission_request` | `{request_id, tool_name, summary, permission_mode}` |
| `notification` | `{task_id, message}` |
| `skills_updated` | `{skills}` |
| `skill_activated` | `{name, description, type}` |
| `response_end` | `{}` |
| `generation_cancelled` | `{}` |
| `turn_complete` | `{conversation_id, usage, context_tokens}` |
| `conversation_loaded` | `{conversation_id, message_count, model, messages, context_tokens}` |
| `conversation_forked` | `{source_conversation_id, conversation_id, message_count}` |
| `title_updated` | `{conversation_id, title}` |
| `context_summarized` | `{original_count, new_count, summary}` |
| `model_changed` | `{model}` |
| `permission_mode_changed` | `{mode}` |
| `checkpoint_reverted` | `{reverted_files, remaining}` |
| `checkpoints` | `{entries}` |
| `schedule_result` | `{schedule_name, status, conversation_id}` |
| `task_update` | `{task_id, title, status}` |
| `compression_rolled_back` | `{message_count}` |

---

## Phase 6: HTTP REST API

### Auth -- `/api/auth`
- `POST /register` -- `{email, password, display_name}` -> `{access_token, refresh_token, user}`
- `POST /login` -- `{email, password}` -> tokens + user
- `POST /refresh` -- `{refresh_token}` -> new tokens (revokes old refresh token)
- `POST /logout` -- Bearer + optional `{refresh_token}` -> revokes both
- `GET /me` -- Bearer -> user info

Rate limit: 10 per IP per 5 minutes. HTTP `get_current_user` dependency checks revocation.

### Conversations -- `/api/conversations`
- `GET /` -- `?q=&limit=50&offset=0` (filters `is_deleted=False`)
- `GET /search` -- `?q=&limit=20` (full-text)
- `GET /{id}` -- returns messages (filters `is_deleted=False`)
- `PATCH /{id}` -- `{title?, model?}` (filters `is_deleted=False`)
- `POST /{id}/fork` -- creates copy
- `DELETE /{id}` -- soft-delete
- `POST /import` -- multipart Claude.ai markdown export

### Schedules -- `/api/schedules`
- `GET /` -- list user's schedules
- `POST /` -- `{name, cron_expression, prompt, user_timezone?}` (201). `user_timezone` defaults to "UTC", accepts IANA timezone names (e.g. "Europe/Bucharest")
- `GET /{id}`, `PATCH /{id}` (accepts `user_timezone`), `DELETE /{id}`
- `GET /{id}/runs` -- `?limit=20`
- `POST /{id}/run` -- trigger immediately (202)

### Files -- `/api/files`
- `GET /list?path=` -- list workspace directory
- `GET /read?path=` -- read text file (max 2MB)
- `GET /download?path=&token=` -- binary download
- `POST /upload?path=` -- multipart upload
- `DELETE /?path=` -- delete file/empty dir

All paths relative to `data/users/{user_id}/workspace/`, path-containment enforced.

### Usage -- `/api/usage`
- `GET /summary` -- aggregate totals
- `GET /by-model` -- per-model breakdown

### Admin -- `/api/admin` (is_admin required)
- `GET /users` -- all users with conversation counts
- `PATCH /users/{id}?is_admin=bool`
- `GET /usage` -- system-wide stats

### System
- `GET /health` -- DB + Redis + memory + sessions (503 if unhealthy)
- `GET /health/live` -- always 200
- `GET /health/ready` -- 503 if dependencies down
- `GET /metrics` -- Prometheus (localhost only unless `metrics_public=true`)

---

## Phase 7: Skills, Commands, Plugins

### 7.1 Skills (agentskills.io format)

**SkillDef** fields: `name, description, tools, argument_hint, disable_model_invocation, user_invocable, model, effort, context, paths, prompt`

**File format** (`skills/<name>/SKILL.md`):
```markdown
---
name: my-skill
description: Does something
tools: Read Grep Bash
argument_hint: <pattern>
user_invocable: true
---

Prompt content here with $ARGUMENTS substitution
```

Variable substitution: `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N`, `${CLAUDE_SESSION_ID}`, `${CLAUDE_SKILL_DIR}`, `` !`command` `` (dynamic)

Matching: exact name for `/name`, keyword scoring (threshold >= 2) for intent-based.

`context: fork` runs in isolated sub-agent.

### 7.2 Hot-Reloading

Skills and commands are loaded at startup and cached in memory. Editing a SKILL.md or command .md file on disk does NOT take effect until reloaded.

The registries (`skills/registry.py`, `commands/registry.py`) track which directories they loaded from in `_skills_dirs` / `_commands_dirs` lists. `reload_skills()` and `reload_commands()` re-read all files from those directories, preserving plugin-namespaced entries (those with `:` in the name).

`/reload` is a built-in slash command (handled in `agent_handler.py`) that:
1. Calls `reload_skills()` and `reload_commands()`
2. Clears the cached `project_config` and `project_rules` on the connection so they are re-read from disk on the next turn
3. Sends a `skills_updated` WebSocket event so the frontend autocomplete refreshes
4. Returns a count of reloaded skills and commands

`/reload` must appear in the built-in skills list sent on `connected` event and in the `skills_updated` payload after `/plugin` operations, so it shows in autocomplete.

### 7.3 Commands

`commands/*.md` with optional YAML frontmatter. Simpler than skills -- just name, description, argument_hint, prompt.

**Built-in slash commands** (hardcoded in `agent_handler.py`, not loaded from files):

| Command | Args | Behavior |
|---------|------|----------|
| `/context` | (none) | Shows token usage breakdown (system prompt, config, rules, memory, skills, tools, messages, total) |
| `/plugin` | `install\|list\|update\|uninstall <name-or-url>` | Plugin management; sends `skills_updated` on install/uninstall |
| `/schedule` | `create\|list\|enable\|disable\|delete\|runs\|run` | Schedule management. `create` accepts `--tz <IANA timezone>` flag (default UTC). List shows timezone per schedule. |
| `/reload` | (none) | Re-reads all skills, commands, project config, and rules from disk; sends `skills_updated` |

These must also appear in the `skills_for_client` list sent on the `connected` WebSocket event and after `/plugin` mutations, so they show in the frontend autocomplete.

### 7.4 Plugins

Directory with `.claude-plugin/plugin.json` manifest. May contain `.mcp.json`, `commands/`, `skills/`. All entries namespaced as `plugin-name:item-name`.

Load via `/plugin install <url-or-path>`. Unload removes commands, skills, and MCP servers from global registries.

### 7.4 Permission Modes

```
DEFAULT    -- ask for Write/Edit and risky shell commands
AUTO_EDIT  -- auto-approve Write/Edit, ask for risky shell
PLAN       -- read-only tools only
AUTO       -- approve everything (except BLOCKED_PATTERNS)
```

Cycle: DEFAULT -> AUTO_EDIT -> PLAN -> AUTO -> DEFAULT

BLOCKED_PATTERNS (always denied): `rm -rf /`, `mkfs`, `dd if=`, `> /dev/`, fork bomb, `chmod -R 777 /`, `--no-preserve-root`

RISKY_BASH_PATTERNS (require confirmation): `rm -rf`, `rm -r`, `drop table/database`, `truncate`, `shutdown`, `reboot`, `kill -9`, `pkill`

### 7.5 Hook System

Events: `PreToolUse`, `PostToolUse`, `SessionStart`, `SessionEnd`, `PromptSubmit`

Config in `config.yaml`:
```yaml
hooks:
  PreToolUse:
    - script: "./lint.sh"
      tools: ["Write", "Edit"]
      timeout: 30
```

Context passed on stdin as JSON. Non-zero exit on `PreToolUse` blocks the tool.

---

## Phase 8: Frontend -- Complete UI Spec

### 8.1 Project Setup

**package.json** deps: `svelte ^5.0.0`, `vite ^6.0.0`, `@sveltejs/vite-plugin-svelte ^5.0.0`, `dompurify ^3.0.0`, `katex ^0.16.11`, `marked ^15.0.0`, `plotly.js-dist-min ^3.4.0`, `prismjs ^1.29.0`

**vite.config.js**: port 5173, proxy `/api` and `/ws` to `localhost:8000`

**index.html**: favicon is inline SVG -- indigo rounded square (#6366f1) with white lightning bolt. Title: "Light CC". Loads Google Fonts (DM Sans, Geist Mono, Lora, Source Serif 4).

### 8.2 Layout Architecture

Root component: `Loom.svelte`

```
+-------+---------------------------+
|       |  Topbar (48px)           |
|       +---------------------------+
| Side  |                           |
| bar   |  ChatArea (flex: 1)       |
| 240px |  (messages scroll)        |
|       |                           |
|       +---------------------------+
|       |  InputBar                 |
+-------+---------------------------+
```

Grid: `grid-template-columns: var(--sidebar-w) 1fr; height: 100vh`

Sidebar collapses to `width: 0`. On mobile (<768px): fixed overlay, slides in.

### 8.3 Component Details

**Auth.svelte**: Full-page centered card. Logo: `Source Serif 4` 24px, "diamond Light CC". Tabs for Login/Register. Inputs with focus glow. Submit button with loading spinner.

**Sidebar.svelte**: Logo (20px indigo square with bolt), new chat button, search input, time-grouped conversation list. Items 13px, 8px padding, 6px radius. Active = bold + accent dot. Double-click to rename. Delete with confirmation. Footer with keyboard shortcut hints.

**ChatArea.svelte**: Message list with auto-scroll (threshold 80px from bottom). Empty state: centered logo + "Start a conversation" heading + suggestion chips. Suggestion chips are configurable pill buttons (`settings.suggestions` list of `{label, prompt}` objects, sent in the `connected` WS event). Clicking a chip dispatches a `lcc-suggestion` custom event which InputBar listens for and sends as a message. Prompts starting with `/` invoke the matching skill. Chips render as `border-radius: 20px`, `font-size: 13px`, flex-wrapped, max-width 480px. Falls back to "Type a message to begin" button if no suggestions configured.

**InputBar.svelte**: Textarea (15px, auto-resize, max 200px height) in rounded container (12px radius). Autocomplete dropdown for `/` commands. Send button (30px circle, inverted colors). Stop button (red). Attach button. Streaming dot indicator.

**MessageBubble.svelte**: Grid layout (26px avatar + content). User text: escaped HTML. Assistant text: rendered markdown (`@html renderMarkdown()`). Tool calls in bordered container. Actions (copy/retry/fork) on hover.

**ToolCall.svelte**: Collapsible. Status dot (amber pulse / green / red). Tool-specific rendering: Bash (command + stdout/stderr + exit code), Read (file content), Grep (match list), Glob (file list), Edit (diff view), Write (status). Expand/collapse toggle.

**StatusBar.svelte**: Connection dot (green/amber/red with glow), model name, token count.

**Toast.svelte**: Fixed bottom-center, z-index 6000. Backdrop blur. Auto-dismiss 2500ms.

**PermissionDialog.svelte**: Modal overlay with blur. Warning icon. Tool name + summary. Allow (green) / Deny (red) buttons. Escape = deny.

**Settings.svelte**: Right slide-in panel (360px). Profile, theme grid (5 themes with color swatches), keyboard shortcuts, logout.

**FilePanel.svelte**: Right slide-in panel (340px). Breadcrumb navigation, file list with icons, upload button.

### 8.4 Renderers

**Chart.svelte**: Plotly.js rendering with dark theme. Config: `{responsive: true, displayModeBar: false}`.

Plotly theme colorway: `['#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444', '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6', '#6366f1', '#facc15']`

Margins: `{l:48, r:16, t:44, b:40}`. Grid: `#1e1e26`. Ticks: `#8888a0`, size 10. Hover bg: `#16161c`.

**Image.svelte**: Inline with lightbox on click. Max 400px height. Lightbox: full-screen, 90vw/90vh max, backdrop blur.

**Table.svelte**: Scrollable container, mono 11px, striped header.

**HtmlEmbed.svelte**: Sandboxed iframe, auto-resizes to content.

### 8.5 Markdown Pipeline (`lib/markdown.js`)

1. Extract math (`$$...$$`, `\[...\]`, `$...$`, `\(...\)`) -> placeholders
2. `marked.parse()` with GFM + breaks
3. Code blocks: add header with language label + copy button (`data-code` attribute)
4. Restore math -> KaTeX render (display/inline)
5. DOMPurify sanitize (allow `iframe`, `data-code`, `sandbox`)
6. Prism highlight (lazy, after render)

Languages: python, javascript, typescript, bash, json, css, sql, yaml, markdown, c, cpp, java, go, rust, jsx, tsx, toml, r

### 8.6 State Management (`state.svelte.js`)

Svelte 5 `$state()` singleton. Keys:

- Auth: `authToken`, `refreshToken`, `user` (localStorage: `lcc_access_token`, `lcc_refresh_token`, `lcc_user`)
- Connection: `connected`, `connecting`, `sessionId`
- Conversations: `conversations` (Map), `currentId`
- Model: `availableModels`, `currentModel` (localStorage: `lcc_model`)
- UI: `theme` (localStorage: `lcc_theme`), `sidebarCollapsed` (localStorage: `lcc_sidebar_collapsed`)
- `toasts`, `pendingPermissions`, `totalTokens`, `skills`, `inlineStatus`, `needsScrollDown`, `scrollToBottom`

Conversation shape: `{id, serverId, title, messages, createdAt, updatedAt, titleGenerated, pinned, totalTokens, stub, model}`

Message shape: `{role, content, id, toolCalls, timestamp, streaming}`

ToolCall shape: `{id, name, input, result, status, startTime, duration, streamBuffer, images, tables, embeds, chart, is_error}`

### 8.7 WebSocket Client (`ws.js`)

- Reconnects with exponential backoff (1s -> 30s max)
- Close code 4001 = auth failure -> `clearAuth()`
- Sends: `{type, data, cid}`
- Handles all server events (see Section 5.4)

### 8.8 Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+B | Toggle sidebar |
| Ctrl+K | New conversation |
| Enter | Send message |
| Shift+Enter | New line |
| Escape | Dismiss dialog / deny permission |
| Tab/Enter in autocomplete | Accept |
| Arrow Up/Down in autocomplete | Navigate |
| Double-click conversation | Rename |

### 8.9 Z-index Stack

| Layer | z-index |
|-------|---------|
| Topbar | 10 |
| Autocomplete | 100 |
| Sidebar toggle | 201 |
| FilePanel overlay/panel | 299/300 |
| Settings overlay/panel | 399/400 |
| Permission dialog | 5000 |
| Toast | 6000 |
| Lightbox | 7000 |

---

## Phase 9: Scheduler, Jobs, Memory

### 9.1 Scheduler (`core/scheduler.py`)

Background asyncio task, checks every 30s for due schedules (`enabled=True AND next_run_at <= now`).

**Timezone-aware cron evaluation**: `_compute_next_run(cron_expression, base, user_tz)` converts the UTC base time to the user's local timezone (via `zoneinfo.ZoneInfo`), evaluates croniter in local time, then converts back to UTC for DB storage. This means `0 9 * * *` with `user_timezone="Europe/Bucharest"` fires at 9:00 EET/EEST (06:00/07:00 UTC depending on DST). The `user_timezone` field on the Schedule model defaults to "UTC".

**Enqueue-then-advance**: `next_run_at` is only advanced *after* successful `enqueue()`. If enqueue fails, the schedule retries on the next 30s loop iteration. Each schedule's enqueue failure is caught independently so one failure doesn't block others.

**Session context**: `_execute_schedule` sets ContextVars (`set_current_session`, `current_session_set("user_id", ...)`, `connection_set`) before running the agent, so tools can resolve user workspace/sandbox paths correctly.

**Result storage**: The full untruncated agent output is saved as the conversation message (so users see the complete result when clicking through from a notification). The `schedule_runs.result` column stores a 10k-character truncated summary for the runs history view.

Scheduled agents: max 20 turns, no risky tools, 60s timeout. Results saved as conversations. Pushes `schedule_result` WS event.

### 9.2 Job Queue (`core/job_queue.py`)

Uses arq (Redis) when available, falls back to `asyncio.create_task()`. Worker: `worker.py` (max_jobs=10, job_timeout=600).

### 9.3 Memory System (`memory/manager.py`)

DB-backed (`memories` table) with file fallback (`data/users/{user_id}/memory/*.md`). Memory listing injected into system prompt (titles only; full content via ReadMemory tool).

---

## Phase 10: Observability

### 10.1 Structured Logging
structlog: JSON in prod, console in dev.

### 10.2 Tracing
OpenTelemetry with OTLP gRPC exporter (only if `OTEL_EXPORTER_OTLP_ENDPOINT` set). FastAPI instrumentation.

### 10.3 Prometheus Metrics
Guard against re-registration. Localhost-only unless `metrics_public=true`.

| Metric | Type | Labels |
|--------|------|--------|
| `lcc_requests_total` | Counter | user_id, model |
| `lcc_agent_loop_duration_seconds` | Histogram | model |
| `lcc_tool_calls_total` | Counter | tool_name |
| `lcc_tool_call_duration_seconds` | Histogram | tool_name |
| `lcc_active_sessions` | Gauge | |
| `lcc_token_usage_total` | Counter | model, direction |
| `lcc_errors_total` | Counter | source |

### 10.4 Audit Trail
Every tool execution: sha256 input hash, 500-char result summary, duration_ms. Fire-and-forget DB write.

---

## Phase 11: Deployment

### 11.1 Dockerfile

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 11.2 docker-compose.yml

Three services: `app`, `postgres:16-alpine`, `redis:7-alpine`.

- `JWT_SECRET: "${JWT_SECRET:?Set JWT_SECRET in .env}"` (fail-fast)
- Healthchecks on all services
- `depends_on: condition: service_healthy`
- Resource limits on app: 2G memory, 2 CPUs
- Named volumes: `app-data`, `pg-data`, `redis-data`

### 11.3 Auto-Migration

On startup with PostgreSQL: runs `alembic upgrade head` inside `pg_advisory_lock(42)` to prevent multi-replica races.

### 11.4 Environment Variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ANTHROPIC_API_KEY` | Yes | | |
| `DATABASE_URL` | No | sqlite:///.../lightcc.db | |
| `REDIS_URL` | No | | Enables Redis features |
| `JWT_SECRET` | Prod | "change-me-in-production" | Must change in prod |
| `ENV` / `ENVIRONMENT` | No | | "production" enforces JWT |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | | Enables tracing |
| `S3_BUCKET` | No | | Enables S3 storage |
| `OPENAI_API_KEY` | No | | For OpenAI provider |

---

## Appendix A: Complete CSS Design Tokens

### Layout Variables
```css
--sidebar-w: 240px;
--radius: 6px;
--content-max-w: 720px;
--transition: cubic-bezier(0.4, 0, 0.2, 1);
```

### Font Stacks
```css
--font-ui: 'DM Sans', system-ui, -apple-system, sans-serif;
--font-mono: 'Geist Mono', monospace;
--font-prose: 'Source Serif 4', Georgia, serif;
```

### Theme: Midnight (default, `:root`)
```css
--bg: #0c0c0e;
--surface: #111115;
--surface2: #16161c;
--border: #1e1e26;
--border2: #28282e;
--muted: #4a4a5e;
--dim: #5a5a72;
--fg: #c4c4d4;
--fg-bright: #e8e8f2;
--fg-dim: #8888a0;
--accent: #6366f1;
--accent-soft: #818cf8;
--accent-glow: rgba(99,102,241,0.15);
--accent2: #f59e0b;
--accent2-soft: #fbbf24;
--accent2-glow: rgba(245,158,11,0.12);
--green: #10b981;
--green-soft: rgba(16,185,129,0.12);
--amber: #f59e0b;
--amber-soft: rgba(245,158,11,0.1);
--red: #ef4444;
--red-soft: rgba(239,68,68,0.1);
--blue: #38bdf8;
--blue-soft: rgba(56,189,248,0.1);
```

### Theme: Light (`[data-theme="light"]`)
```css
--bg: #fafaf8;
--surface: #ffffff;
--surface2: #f3f2ef;
--border: #e8e6e1;
--border2: #dddbd5;
--muted: #9a958c;
--dim: #706b63;
--fg: #3a3632;
--fg-bright: #1a1816;
--fg-dim: #5c574f;
--accent: #1a7f64;
--accent-soft: #1a7f64;
--accent-glow: rgba(26,127,100,0.06);
--accent2: #b45309;
--accent2-soft: #d97706;
--accent2-glow: rgba(180,83,9,0.06);
--green: #16a34a;
--green-soft: rgba(22,163,74,0.06);
--amber: #ca8a04;
--amber-soft: rgba(202,138,4,0.04);
--red: #dc2626;
--red-soft: rgba(220,38,38,0.05);
--blue: #0369a1;
--blue-soft: rgba(3,105,161,0.05);
```

### Theme: Dracula (`[data-theme="dracula"]`)
```css
--bg: #282a36;
--surface: #2d2f3d;
--surface2: #343647;
--border: #44475a;
--border2: #515470;
--muted: #6272a4;
--dim: #7889b8;
--fg: #cdd6f4;
--fg-bright: #f8f8f2;
--fg-dim: #a0aece;
--accent: #bd93f9;
--accent-soft: #caa6fc;
--accent-glow: rgba(189,147,249,0.15);
--green: #50fa7b;
--amber: #f1fa8c;
--red: #ff5555;
--blue: #8be9fd;
--accent2: #f1fa8c;
--accent2-soft: #f5fc9e;
```

### Theme: Solarized (`[data-theme="solarized"]`)
```css
--bg: #002b36;
--surface: #073642;
--surface2: #0a3f4e;
--border: #1a4f5e;
--border2: #2a5f6e;
--muted: #586e75;
--dim: #657b83;
--fg: #93a1a1;
--fg-bright: #eee8d5;
--fg-dim: #839496;
--accent: #268bd2;
--accent-soft: #4aa3e0;
--green: #859900;
--amber: #b58900;
--red: #dc322f;
--blue: #2aa198;
--accent2: #b58900;
--accent2-soft: #cb9a00;
```

### Theme: Nord (`[data-theme="nord"]`)
```css
--bg: #2e3440;
--surface: #3b4252;
--surface2: #434c5e;
--border: #4c566a;
--border2: #5a657a;
--muted: #616e88;
--dim: #7b88a1;
--fg: #d8dee9;
--fg-bright: #eceff4;
--fg-dim: #a3b1cc;
--accent: #88c0d0;
--accent-soft: #8fbcbb;
--green: #a3be8c;
--amber: #ebcb8b;
--red: #bf616a;
--blue: #5e81ac;
--accent2: #ebcb8b;
```

### Global CSS Key Values
- Body: `font-size: 14px`, `line-height: 1.6`, `-webkit-font-smoothing: antialiased`
- Selection: `background: var(--accent); color: #fff`
- Scrollbar: `6px` width, `var(--border2)` thumb, `3px` radius
- Focus ring: `2px solid var(--accent)`, `2px offset`
- Prose: `font-size: 17px`, `line-height: 1.7`, `letter-spacing: 0.006em`
- Prose light theme override: `font-weight: 430`
- Code inline: `font-size: 12px`, `background: var(--surface2)`, `border: 1px solid var(--border2)`, `padding: 2px 6px`, `border-radius: 4px`, `color: var(--accent-soft)`
- Code blocks: `font-size: 12px`, `line-height: 1.65`, `padding: 14px 16px`
- Headings: h1=26px, h2=21px, h3=18px, all `font-family: var(--font-prose)`, `letter-spacing: -0.015em`

### Prism Token Colors
```css
comment/prolog/doctype/cdata: var(--muted) italic
punctuation: var(--fg-dim)
property/tag/boolean/number/constant/symbol: var(--amber)
deleted: var(--red)
selector/attr-name/string/char/builtin/inserted: var(--green)
operator/entity/url: var(--fg-dim)
atrule/attr-value/keyword: var(--accent-soft)
function/class-name: var(--blue)
regex/important/variable: var(--amber)
decorator: var(--accent-soft)
```

---

## Appendix B: Animation Reference

| Name | Duration | Easing | From | To |
|------|----------|--------|------|----|
| `auth-in` | 0.4s | cubic-bezier(0.4,0,0.2,1) | opacity:0, translateY(12px), scale(0.98) | opacity:1, translateY(0), scale(1) |
| `slide-in` | 0.25s | cubic-bezier(0.4,0,0.2,1) | translateX(100%) | translateX(0) |
| `dialog-in` | 0.25s | cubic-bezier(0.4,0,0.2,1) | opacity:0, translateY(8px), scale(0.97) | opacity:1, translateY(0), scale(1) |
| `dropdown-in` | 0.15s | ease | opacity:0, translateY(-4px) | opacity:1, translateY(0) |
| `toast-in` | 0.25s | cubic-bezier(0.4,0,0.2,1) | opacity:0, translateY(10px) | opacity:1, translateY(0) |
| `msg-in` | 0.3s | ease | opacity:0, translateY(6px) | opacity:1, translateY(0) |
| `empty-fade-in` | 0.4s | ease | opacity:0 | opacity:1 |
| `fade-in` | 0.15s | ease | opacity:0 | opacity:1 |
| `spin` | 0.6s | linear | rotate(0) | rotate(360deg) |
| `thinking-pulse` | 1.2s | ease-in-out | opacity:0.3 | opacity:1 (infinite) |
| `streaming-pulse` | 1.2s | ease-in-out | opacity:0.3, scale(1) | opacity:1, scale(1.3) (infinite) |
| `dot-pulse` | 1.2s | ease-in-out | opacity:0.3 | opacity:1 (infinite) |
| `dot-bounce` | 1.4s | ease-in-out | opacity:0.2, translateY(0) | opacity:1, translateY(-2px) (delays: 0.15s, 0.3s) |
| `cursor-blink` | 0.75s | step-end | opacity:1 | opacity:0 (infinite) |

---

## Appendix C: System Prompt Structure

```
[BASE_SYSTEM_PROMPT]
  - Identity ("Light CC, helpful AI assistant with local machine access")
  - Environment info (OS, Python path)
  - Output directory
  - Tool usage guidelines (python_exec preferred, chart tool for simple charts,
    plotly.json for interactive, dark theme rules, auto-render via stdout paths,
    web_fetch SSRF rules, /schedule command, no emojis)
  - Model name

[## Project Instructions]   -- CLAUDE.md content
[## Project Rules]          -- .claude/rules/*.md (active files filtered)
[## User Instructions]      -- per-connection custom prompt
[## Active Skill]           -- matched skill/command prompt for this turn
[## Your Memory]            -- memory listing (titles + IDs)
[## Available Skills]       -- user-invocable /name list
[## Auto-Activated Skills]  -- intent-matched skill names
[## Available Commands]     -- command list
```

---

## Implementation Order (Recommended)

1. **Scaffolding**: pyproject.toml, config.yaml, Settings model, requirements.txt
2. **Database**: engine, ORM models, Alembic setup
3. **Auth**: JWT, bcrypt, token revocation
4. **Session store**: two-tier, ContextVars
5. **Tool registry**: register/execute/schema framework
6. **Core tools**: Bash, Read, Write, Edit, Glob, Grep
7. **Agent loop**: streaming, tool dispatch, retry logic
8. **WebSocket**: connection, auth, event dispatch
9. **Server**: FastAPI app, system prompt, startup/shutdown
10. **HTTP API**: auth routes, conversations, files
11. **Frontend shell**: Vite + Svelte, themes.css, global.css, layout
12. **Frontend auth**: Auth.svelte, state management, WS client
13. **Frontend chat**: ChatArea, MessageBubble, InputBar, ToolCall
14. **Frontend chrome**: Sidebar, StatusBar, Toast, Settings
15. **Advanced tools**: PythonExec, WebFetch, WebSearch, CreateChart, data tools
16. **Skills + Commands**: loader, registry, intent matching
17. **Plugins**: loader, install, unload
18. **MCP**: client, tool discovery, namespacing
19. **Memory**: DB + file backend, tools
20. **Scheduler**: cron loop, job queue, worker
21. **Context**: compression, rollback, token counting
22. **Checkpoints**: snapshot, revert
23. **Permissions**: modes, blocked/risky patterns
24. **Hooks**: system
25. **Observability**: structlog, OTEL, Prometheus, audit
26. **Deployment**: Dockerfile, docker-compose, entrypoint
27. **Frontend renderers**: Chart, Image, Table, HtmlEmbed
28. **Frontend panels**: FilePanel, PermissionDialog
29. **Polish**: autocomplete, keyboard shortcuts, responsive
30. **First-class agents**: agent definitions, agent API, persistent sessions (Phase 12)

---

## Phase 12: First-Class Agents (Future)

Elevate agents from implicit (skill + agent loop, gone when turn ends) to named, persistent, independently invocable entities. Modelled on the emerging industry consensus (NIST AI Agent Standards Initiative, Anthropic's Managed Agents pattern) but self-hosted.

### 12.1 Agent Definitions

YAML files or DB records defining a reusable agent:

```yaml
name: market-analyst
description: "Analyzes market data and produces reports"
model: claude-sonnet-4-6
system_prompt: |
  You are a market analyst with access to the shared
  research codebase. Always scope queries to the current user.
tools: [WebFetch, PythonExec, ReadFile, WriteFile]
max_turns: 50
timeout: 3600
memory:
  enabled: true          # persistent memory across sessions
  scope: agent           # agent-level (shared) or user (per-user)
permissions:
  network: true
  file_write: workspace_only
triggers:
  - cron: "0 9 * * 1-5"
  - webhook: true        # enables POST /api/agents/{name}/run
  - skill: true          # invocable as /market-analyst in chat
```

### 12.2 Agent Properties (Industry Consensus)

| Property | Implementation |
|----------|---------------|
| Goal-directed autonomy | System prompt defines objective; agent loop plans and executes |
| Tool use | Allowlisted tools per agent definition |
| Memory/persistence | Agent-scoped memory (shared across sessions) + user-scoped memory |
| Self-correction | Multi-turn agent loop with error detection and retry |

### 12.3 Agent REST API

```
POST   /api/agents                    -- create agent definition
GET    /api/agents                    -- list agents
GET    /api/agents/{name}             -- get agent definition
PATCH  /api/agents/{name}             -- update agent
DELETE /api/agents/{name}             -- delete agent
POST   /api/agents/{name}/run         -- trigger a run (async, returns session ID)
       Body: { "prompt": "...", "user_id": "...", "webhook_url": "..." }
GET    /api/agents/{name}/sessions    -- list sessions/runs
GET    /api/agents/{name}/sessions/{id} -- get session result + messages
POST   /api/agents/{name}/sessions/{id}/resume -- resume a paused/disconnected session
DELETE /api/agents/{name}/sessions/{id} -- cancel a running session
```

### 12.4 Agent Sessions

Long-running, resumable agent sessions with state persisted to DB:
- Session state: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled`
- Messages, tool calls, and memory checkpointed periodically (not just at turn end)
- Survives disconnects -- agent continues running, user reconnects to see progress
- Result stored as a conversation (viewable/continuable in chat UI)

### 12.5 Three Invocation Paths

| Path | How it works |
|------|-------------|
| **Chat** | User types `/market-analyst analyse AAPL` -- resolved as a skill, runs in the WebSocket session |
| **API** | External system calls `POST /api/agents/market-analyst/run` -- runs async via job queue, returns session ID for polling |
| **Schedule** | Cron trigger fires at configured time -- uses existing scheduler infrastructure |

### 12.6 Building Blocks Already in Place

- `core/agent.py` -- agentic tool-use loop (multi-turn, streaming)
- `tools/subagent.py` -- child agent spawning
- Skills with YAML frontmatter -- close to agent definitions (system prompt, tool filter, description)
- `core/scheduler.py` -- cron triggers with timezone support
- `core/job_queue.py` -- async execution (arq/Redis or asyncio)
- `core/sandbox.py` -- role-based scoped execution
- `core/user_context.py` -- user scoping for shared code

### 12.7 New Components Needed

- `core/agent_registry.py` -- CRUD for agent definitions (DB-backed + YAML loader)
- `core/agent_session.py` -- session lifecycle, checkpointing, resumption
- `routes/agents.py` -- REST API endpoints
- `alembic/versions/..._add_agents.py` -- `agent_definitions` and `agent_sessions` tables
- Frontend: agent management panel (list, create, view runs)

---

*This document is a complete blueprint. Every color, font, spacing value, API schema, protocol event, and architectural decision is captured above. A competent developer with this document and no access to the original codebase should be able to recreate Light CC faithfully.*
