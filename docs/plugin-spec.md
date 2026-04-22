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
