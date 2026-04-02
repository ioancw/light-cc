---
name: create-skill
description: Interactively scaffold a new Claude Code skill or plugin skill. Use when the user wants to create, build, or scaffold a skill.
argument-hint: "[skill name or description]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

You are a skill creation assistant. Guide the user through creating a new Claude Code skill that conforms to the open standard.

## Process

### Step 1: Gather requirements

Ask the user for the following (skip any already provided via $ARGUMENTS):

1. **Skill name** - lowercase, hyphens only (e.g. `code-review`, `deploy-staging`)
2. **What it does** - a sentence describing the skill's purpose
3. **Where to create it** - ask the user to choose:
   - **Project skill**: `skills/<name>/SKILL.md` (available in this repo)
   - **Personal skill**: `~/.claude/skills/<name>/SKILL.md` (available in all your projects)
   - **Plugin skill**: `plugins/<plugin-name>/skills/<name>/SKILL.md` (namespaced under a plugin)
4. **Invocation style**:
   - User-invocable via slash command (default)
   - Auto-invoked by Claude based on description match
   - Background knowledge only (`user-invocable: false`)
   - Manual only, never auto-triggered (`disable-model-invocation: true`)
5. **Arguments** - does the skill accept arguments? If so, what hint to show?
6. **Tools needed** - which tools should be auto-allowed? (Read, Write, Edit, Bash, Grep, Glob, Agent, etc.)
7. **Any special options**:
   - Model override (opus, sonnet, haiku)
   - Effort level (low, medium, high, max)
   - Forked context (`context: fork` with an agent type)
   - Path restrictions (only activate for certain file patterns)
   - Dynamic context via shell commands (`` !`command` `` preprocessing)

Present these as a concise numbered checklist. Don't over-explain -- let the user answer naturally and infer defaults from context.

### Step 2: Confirm the plan

Before writing any files, show the user exactly what will be created:

- The directory path
- The full SKILL.md content with frontmatter and body
- Any supporting files if needed

Ask for confirmation or adjustments.

### Step 3: Write the files

Create the skill directory and SKILL.md file. If the user chose a plugin location, verify the plugin manifest exists (`.claude-plugin/plugin.json`); if not, create that too.

### Step 4: Verify

After creation, confirm the skill exists and show how to invoke it:
- `/skill-name` for project/personal skills
- `/plugin-name:skill-name` for plugin skills

### Step 5: Schedule (optional)

Ask the user if they'd like to schedule the skill to run automatically on a cron schedule using `/schedule`. If yes, help them set it up by asking:

1. **How often** - e.g. every hour, daily at 9am, weekly on Mondays
2. **With what arguments** - any default arguments to pass each run

Then invoke `/schedule` to create the trigger. Show the resulting schedule for confirmation.

## Spec reference

### SKILL.md frontmatter (all fields)

```yaml
---
name: skill-name                     # Optional. Defaults to directory name. Lowercase, numbers, hyphens (max 64 chars)
description: What the skill does     # Recommended. Front-load key use case. ~250 char max in listings
argument-hint: "[args]"              # Optional. Shown during autocomplete
disable-model-invocation: false      # Optional. true = Claude never auto-loads this
user-invocable: true                 # Optional. false = hidden from / menu, background knowledge only
allowed-tools: Read, Grep, Glob      # Optional. Tools allowed without permission prompts
model: opus                          # Optional. Model override when skill is active
effort: medium                       # Optional. low|medium|high|max
context: fork                        # Optional. Run in forked subagent context
agent: Explore                       # Optional. Subagent type when context: fork
paths: "**/*.js,**/*.ts"             # Optional. Glob patterns limiting auto-activation
shell: bash                          # Optional. Shell for !`command` blocks
---
```

### String substitutions available in skill body

- `$ARGUMENTS` - all arguments passed on invocation
- `$ARGUMENTS[N]` or `$N` - specific argument by 0-based index
- `${CLAUDE_SESSION_ID}` - current session ID
- `${CLAUDE_SKILL_DIR}` - directory containing this SKILL.md

### Plugin manifest (`.claude-plugin/plugin.json`)

```json
{
  "name": "plugin-name",
  "description": "What this plugin does",
  "version": "1.0.0",
  "author": { "name": "Author Name" }
}
```

### Plugin directory layout

```
my-plugin/
  .claude-plugin/
    plugin.json
  skills/
    skill-name/
      SKILL.md
  commands/         # Optional: .md files as slash commands
  agents/           # Optional: custom agent definitions
  hooks/            # Optional: hooks.json for event handlers
  .mcp.json         # Optional: MCP server configs
  settings.json     # Optional: default settings
```

## Guidelines

- Keep skill instructions focused and actionable -- avoid filler
- Front-load the description with the primary use case
- Only add `allowed-tools` for tools the skill genuinely needs
- Prefer project-level skills for repo-specific work, personal for cross-project utilities
- Use `context: fork` for exploratory or read-only skills that shouldn't pollute the main conversation
- Use `` !`command` `` for injecting live context (git state, API responses, etc.)
