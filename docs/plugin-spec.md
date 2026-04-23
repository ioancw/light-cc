# Light CC Plugin Specification

## Directory Structure

```
my-plugin/
    .claude-plugin/
        plugin.json           # Required: plugin manifest
    .mcp.json                 # Optional: MCP server definitions
    skills/
        my-skill/
            SKILL.md          # Optional: skills (auto-namespaced as plugin-name:skill-name)
    commands/
        my-command.md         # Optional: legacy CC commands (loaded as `kind="legacy-command"` skills)
    agents/
        my-agent/
            AGENT.md          # Optional: agent definitions (auto-namespaced as plugin-name:agent-name)
```

> `skills/` is the canonical surface in CC 2.1+. `commands/*.md` is still
> supported as a drop-in compat path so plugins authored against older CC
> versions keep working — they're loaded into the same registry and dispatch
> identically to `/<plugin>:<command-name>`.

## plugin.json Schema

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

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique plugin identifier. Must match `^[a-z0-9][a-z0-9-]*$`. |
| `version` | string | Yes | Semver version (e.g., `1.0.0`). |
| `description` | string | Yes | One-line description shown in plugin list. |
| `author` | string | No | Plugin author name or handle. |
| `license` | string | No | SPDX license identifier. |
| `min_lightcc_version` | string | No | Minimum Light CC version required. |
| `dependencies.python` | string[] | No | Python packages to install (pip format). |
| `dependencies.npm` | string[] | No | NPM packages to install. |
| `permissions.tools` | string[] | No | Tool names the plugin's skills/commands need. |
| `permissions.mcp_servers` | string[] | No | MCP server names defined in `.mcp.json`. |

## Namespace Isolation

Skills loaded from plugins are automatically namespaced as `plugin-name:skill-name`. This prevents collisions between plugins. Users invoke them as `/plugin-name:skill-name` or by intent matching.

Commands and agents loaded from plugins follow the same namespacing convention.

Plugin-owned agents are persisted to the `AgentDefinition` table with `source='plugin:<plugin-name>'`. On uninstall, all rows with that source are deleted automatically. User-edited agents (`source='user'`) are never overwritten by a plugin sync.

Plugins **cannot** register skills, commands, or agents outside their namespace prefix. Attempting to do so will be rejected by the loader.

## Installation

Plugins are installed to the `plugins/` directory (configurable via `paths.plugins_dirs` in `config.yaml`).

```bash
# Install from git URL
python scripts/plugin_cli.py install https://github.com/user/my-plugin.git

# Install from local directory
python scripts/plugin_cli.py install /path/to/my-plugin

# List installed plugins
python scripts/plugin_cli.py list

# Update a plugin
python scripts/plugin_cli.py update my-plugin

# Uninstall a plugin
python scripts/plugin_cli.py uninstall my-plugin
```

## MCP Servers

Plugins can define MCP servers in `.mcp.json` following the standard MCP configuration format:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "."
    }
  }
}
```

MCP servers are started when the plugin loads and stopped when it unloads.

## Lifecycle

1. **Discovery**: On startup, Light CC scans `plugins/` for directories containing `.claude-plugin/plugin.json`.
2. **Validation**: The manifest is parsed and validated.
3. **Loading**: MCP servers are started, commands and skills are registered (namespaced).
4. **Runtime**: Skills and commands are available to users and the agent.
5. **Unload**: MCP servers are disconnected. Skills/commands remain registered until restart.

## Compatibility with Claude Code

Light CC aims to be a drop-in target for plugins authored against Claude
Code — skills, commands, and agents you built in CC should run unchanged.
The sections below document the small set of places where Light CC's
runtime diverges from CC, and why.

### Agent tool

- **`description` is optional** (CC parity). Missing or empty values
  default to `"Subagent run"`. Schema still lists it as a documented
  parameter so CC plugins that set it keep working.
- **No `resume` parameter.** CC did not historically support
  `Agent(resume=<id>, prompt=...)` — resumption has always been
  `SendMessage(to=<id>, message=...)`. Light CC matches that.
- **SendMessage is always enabled.** No
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` env var is required to
  continue a sub-conversation.
- **Builtin aliases (CC compat):** `Explore` → `explorer`,
  `Plan` → `planner`, `general-purpose` → `default`, resolved
  case-insensitively. Light CC's canonical names stay lowercase.
  User-defined `AgentDefinition`s still shadow builtins regardless
  of how the caller spelled the name.
- **Agent tool result payload is richer** than CC's (CC does not
  publish a field contract). Light CC extends the payload with:
  - `status`: `"completed"` | `"started"` (background) | `"failed"`
  - `total_duration_ms`, `total_tool_use_count`, `total_tokens`
  - `usage`: `{input_tokens, output_tokens}`

  Existing fields (`result`, `agent_id`, `run_id`, `subagent_type`)
  are preserved. Field names use snake_case per Python convention;
  CC plugins that only consume the existing fields see no change.

### Hooks

- **Light CC runs a subset of CC's hook events today:** `PreToolUse`,
  `PostToolUse`, `SessionStart`, `SessionEnd`, `PromptSubmit`,
  `SubagentStart`. The `SubagentStart` payload matches CC's shape
  (`{subagent_type, agent_id, parent_session_id, description}`).
- **`UserPromptSubmit` is named `PromptSubmit`** here — a pre-existing
  Light CC name that predates CC's rename; a follow-up pass will
  alias both to the CC spelling.
- Other CC events (`SubagentStop`, `Stop`, `PreCompact`,
  `PostCompact`, `PostToolUseFailure`, `PermissionRequest`,
  `Notification`) are not yet wired. Hook-config that references
  them loads without error but never fires.

### Intent routing

Light CC runs an intent router **in addition to** CC's
model-inference-based subagent dispatch. A CC plugin with carefully
tuned `description` fields may see slightly different dispatch
behaviour in Light CC because the router can short-circuit an
otherwise model-chosen builtin. Tune agent descriptions in
Light CC if you need deterministic routing.
