# Light CC Agent Quality Improvement Plan

Based on Anthropic's [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) recommendations.

**Scope:** Improve tool descriptions, system prompt, and add optional routing -- without changing the core agent loop architecture (which is already aligned with best practices).

---

## 1. Enrich Tool Descriptions

**Priority:** HIGH -- single biggest impact on tool selection accuracy and error rates.

**Problem:** Current descriptions are terse one-liners. Anthropic recommends treating tool descriptions like "a great docstring for a junior developer" with examples, edge cases, input formats, and limitations.

**Current state (examples):**

| Tool | Current Description |
|------|-------------------|
| Read | "Read a file's contents. Returns line-numbered text." |
| Edit | "Edit a file by replacing old_string with new_string." |
| Bash | "Execute a shell command. Returns stdout, stderr, and exit code." |
| Grep | "Search for a regex pattern across files. Returns matching lines with file paths and line numbers." |
| Write | "Write content to a file. Creates parent directories if needed." |
| WebFetch | "Fetch content from a URL. Supports GET and POST. Returns status code and body." |

**Target state:** Each description should cover:
1. What it does (one line)
2. When to use it vs alternatives
3. Input format with brief examples
4. Output format
5. Edge cases / limitations

### 1.1 File: `tools/read.py`

Change `description` in `register_tool()` and add detail to parameter descriptions.

```python
register_tool(
    name="Read",
    aliases=["read_file"],
    description=(
        "Read a file's contents. Returns line-numbered text (cat -n format). "
        "Use this instead of bash cat/head/tail. "
        "For large files, use offset and limit to read specific sections — "
        "e.g. offset=100, limit=50 reads lines 100-149. "
        "Default reads up to 2000 lines from the start. "
        "Can read text files, images (rendered visually), and PDFs. "
        "Returns an error if the path is a directory — use bash ls for directories."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read (e.g. C:/Users/me/project/src/main.py)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start from (0-based). Use with limit to read a slice of a large file.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read (default 2000). Reduce for large files when you only need a section.",
            },
        },
        "required": ["file_path"],
    },
    handler=handle_read,
)
```

### 1.2 File: `tools/edit.py`

```python
register_tool(
    name="Edit",
    aliases=["edit_file"],
    description=(
        "Edit a file by replacing an exact string match with new text. "
        "The old_string must appear exactly once in the file (including whitespace and indentation) "
        "or the edit will fail. To replace all occurrences, set replace_all=true. "
        "Prefer this over Write for modifications — it only changes what you specify. "
        "Use Write only for creating new files or complete rewrites."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "The exact text to find and replace. Must match the file content exactly, "
                    "including indentation (spaces/tabs). Include enough surrounding context "
                    "to make the match unique."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text. Must differ from old_string.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring a unique match (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    handler=handle_edit,
)
```

### 1.3 File: `tools/write.py`

```python
register_tool(
    name="Write",
    aliases=["write_file"],
    description=(
        "Write content to a file, creating it if it doesn't exist. "
        "Creates parent directories automatically. "
        "WARNING: Overwrites the entire file — for partial modifications, use Edit instead. "
        "Use this for: creating new files, or complete rewrites where Edit would be impractical."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write (e.g. C:/Users/me/project/output.txt)",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file. This replaces the entire file.",
            },
        },
        "required": ["file_path", "content"],
    },
    handler=handle_write,
)
```

### 1.4 File: `tools/bash.py`

```python
register_tool(
    name="Bash",
    aliases=["bash"],
    description=(
        "Execute a shell command via subprocess. Returns stdout, stderr, and exit code. "
        "Use for: git commands, package management, running executables, directory listings, "
        "curl for local APIs, system commands. "
        "Do NOT use for: reading files (use Read), editing files (use Edit), "
        "searching file contents (use Grep), finding files (use Glob). "
        "Commands run in the project directory with sandboxed permissions. "
        "Timeout default is 120s, max 600s."
    ),
    # ... rest unchanged
)
```

### 1.5 File: `tools/grep.py`

```python
register_tool(
    name="Grep",
    aliases=["grep"],
    description=(
        "Search for a regex pattern across files. Returns matching lines with file paths "
        "and line numbers. Uses ripgrep under the hood. "
        "Use this instead of bash grep/rg. "
        "Supports full regex syntax (e.g. 'def\\s+my_func', 'TODO|FIXME'). "
        "Filter by file type with the glob parameter (e.g. '*.py', '*.ts'). "
        "Default searches current directory recursively, max 50 results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for (e.g. 'class MyModel', 'import.*pandas')",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in. Defaults to project root.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. '*.py', 'src/**/*.ts')",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case insensitive search (default false)",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 50). Increase for broad searches.",
            },
        },
        "required": ["pattern"],
    },
    handler=handle_grep,
)
```

### 1.6 File: `tools/glob_tool.py`

```python
register_tool(
    name="Glob",
    aliases=["glob"],
    description=(
        "Find files matching a glob pattern. Returns file paths sorted by modification time "
        "(most recent first). Use this instead of bash find/ls for file discovery. "
        "Supports standard glob patterns: '**/*.py' (recursive), 'src/*.ts' (single dir), "
        "'*.{js,ts}' (multiple extensions). "
        "Use Grep instead if you need to search file contents, not just file names."
    ),
    # ... rest unchanged
)
```

### 1.7 File: `tools/web.py`

```python
# WebFetch
register_tool(
    name="WebFetch",
    aliases=["web_fetch"],
    description=(
        "Fetch content from a public HTTP/HTTPS URL. Returns status code and body text. "
        "IMPORTANT: Only for external URLs on the public internet. "
        "Do NOT use for local files (use Read), localhost (use Bash with curl), "
        "or file:// URLs (use Read). "
        "For web pages, returns extracted text content. For APIs, returns raw JSON. "
        "Supports GET and POST methods."
    ),
    # ... rest unchanged
)

# WebSearch
register_tool(
    name="WebSearch",
    aliases=["web_search"],
    description=(
        "Search the web using DuckDuckGo. Returns titles, URLs, and text snippets. "
        "Use this to find current information, documentation, or answers to factual questions. "
        "Follow up with WebFetch to read full pages from the results. "
        "Default returns 5 results."
    ),
    # ... rest unchanged
)
```

### 1.8 File: `tools/python_exec.py`

```python
register_tool(
    name="PythonExec",
    aliases=["python_exec"],
    description=(
        "Execute a Python script as a .py file. Preferred over Bash for Python code — "
        "avoids shell quoting issues on Windows. "
        "Use for: data analysis, computations, file processing, custom visualizations. "
        "The env var OUTPUT_DIR points to a writable directory for saving files — "
        "use `import os; out = os.environ['OUTPUT_DIR']` to get the path. "
        "Print output file paths to stdout for auto-rendering of images and charts in the UI. "
        "For charts: save as *.plotly.json for interactive rendering, *.png for static."
    ),
    # ... rest unchanged
)
```

### 1.9 Files: `tools/data_tools.py`, `tools/chart.py`, `tools/tasks.py`, `tools/subagent.py`, `tools/eval_optimize.py`, `tools/tool_search.py`

Apply the same pattern: expand each description to cover what/when/how/limitations. The existing descriptions for chart and eval_optimize are already reasonable -- focus on adding "when to use" and "when NOT to use" guidance.

### Implementation

Each tool file is self-contained. Edit the `register_tool()` call in each file:
- `description` string
- Parameter `description` strings within `input_schema`

No changes to handlers, no changes to the registry, no structural changes.

**Test:** After each change, verify tool schemas are valid by running:
```bash
python -c "from tools.registry import get_all_tool_schemas; schemas = get_all_tool_schemas(); print(f'{len(schemas)} tools loaded'); [print(f'  {s[\"name\"]}: {len(s[\"description\"])} chars') for s in schemas]"
```

---

## 2. Enrich the Base System Prompt

**Priority:** HIGH -- cheap to implement, directly improves tool selection and behavior.

**Problem:** The system prompt in `server.py` (`BASE_SYSTEM_PROMPT`) has good domain-specific guidance (chart themes, output directories, scheduling) but lacks tool selection strategy -- it doesn't tell Claude *when* to pick one tool over another.

**File:** `server.py`, lines 64-108

### 2.1 Add Tool Selection Guide

Add a new section to `BASE_SYSTEM_PROMPT` after the existing "Tool usage rules":

```python
BASE_SYSTEM_PROMPT = f"""...existing content...

Tool selection guide:
- To read a file: use Read (not bash cat/head/tail)
- To edit a file: use Edit for targeted changes, Write only for new files or full rewrites
- To search file contents: use Grep (not bash grep/rg)
- To find files by name/pattern: use Glob (not bash find/ls)
- To run Python code: use python_exec (not bash python -c)
- To fetch a web page: use web_fetch (external URLs only, never localhost)
- To search the web: use web_search, then web_fetch for full page content
- To run shell commands (git, curl, npm, etc.): use Bash
- For multi-step complex tasks: use Agent to spawn a sub-agent
- For iterative quality improvement: use eval_optimize (generator-evaluator loop)
- For data analysis: use load_data to load files, then query_data for pandas operations

When multiple tools could work, prefer the specialized tool over Bash. Specialized tools \
provide better structured output and are safer (sandboxed, validated).

...existing content...
"""
```

### 2.2 Add Error Recovery Guidance

Append to the system prompt:

```python
"""
Error handling:
- If a tool returns an error, read the error message carefully before retrying.
- If a file doesn't exist, check the path with Glob before assuming it was deleted.
- If Edit fails with "not unique", include more surrounding context in old_string.
- If a web_fetch fails, try web_search to find an alternative URL.
- Do not retry the same failing command more than twice — diagnose the issue first.
"""
```

### Implementation

Edit `BASE_SYSTEM_PROMPT` in `server.py`. Single string edit, no structural changes.

**Test:** Start the app, open a conversation, and check that the system prompt includes the new sections. You can verify with the `/context` command if available, or by checking logs.

---

## 3. Add Optional Model Routing

**Priority:** LOW -- optimization, not correctness. Implement when usage patterns justify it.

**Problem:** All requests go to the same model regardless of complexity. Simple lookups (file reads, calculations) could use a faster/cheaper model, while complex tasks (multi-file refactors, research) benefit from the most capable model.

### 3.1 Add Routing Config

**File:** `core/config.py`

Add routing configuration:

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Model routing
    routing_enabled: bool = False
    routing_rules: list[dict[str, str]] = Field(default_factory=lambda: [
        # Each rule: {"pattern": "regex on user message", "model": "model-id"}
        # First match wins. No match = default model.
        {"pattern": r"^(what time|what date|hello|hi|hey)\b", "model": "claude-haiku-4-5-20251001"},
    ])
    routing_default_model: str = ""  # empty = use settings.model
```

### 3.2 Add Router Function

**File:** `core/router.py` (new file)

```python
"""Optional model routing — classify input and select model.

Disabled by default. Enable via config: routing_enabled = true.
"""

from __future__ import annotations

import re
import logging
from core.config import settings

logger = logging.getLogger(__name__)


def select_model(user_message: str) -> str:
    """Select model based on routing rules. Returns model ID."""
    if not settings.routing_enabled:
        return settings.model

    text = user_message.strip().lower()

    for rule in settings.routing_rules:
        pattern = rule.get("pattern", "")
        model = rule.get("model", "")
        if pattern and model and re.search(pattern, text, re.IGNORECASE):
            logger.debug(f"Routing matched pattern '{pattern}' -> {model}")
            return model

    return settings.routing_default_model or settings.model
```

### 3.3 Integrate with Agent Handler

**File:** `handlers/agent_handler.py`

In `handle_user_message`, after skill matching and before calling `agent.run()`:

```python
# Select model (routing or default)
from core.router import select_model
active_model = select_model(data["text"])
# If a skill specifies a model, that takes precedence
if matched_skill and getattr(matched_skill, 'model', None):
    active_model = matched_skill.model

# ... then pass to agent.run():
messages = await agent.run(
    messages=messages,
    tools=tool_schemas,
    system=system,
    # ... existing callbacks ...
    model=active_model,  # was previously None (using config default)
)
```

### Implementation Notes

- Routing is **off by default** (`routing_enabled: false`). Zero impact until explicitly enabled.
- Rules are simple regex on user message text. No ML classification needed.
- Skills can override the routed model (a skill that needs Opus always gets Opus).
- Future: could add routing by tool count, conversation length, or skill type.

**Test:**
1. Set `routing_enabled = true` in config
2. Send "hello" -- should route to Haiku
3. Send "refactor the authentication module to use OAuth2" -- should route to default model
4. Verify via logs or `/context` command

---

## 4. Add Prompt Chaining Gates (Skills)

**Priority:** MEDIUM -- improves quality for multi-stage skills.

**Problem:** Skills run as a single agent turn (or series of tool calls). There's no mechanism to validate intermediate output before proceeding to the next stage. For example, a research skill that (1) searches, (2) reads sources, (3) synthesizes -- if the search returns bad results, the synthesis will be bad too.

### 4.1 Add Gate Definitions to Skill YAML

**File:** Skill YAML frontmatter (no code change needed -- just documentation of the pattern)

Skills already have system prompts. The simplest "gate" is an instruction in the system prompt:

```yaml
---
name: deep-research
description: Multi-source research with quality gates
tools: [WebSearch, WebFetch, Read, Write, PythonExec]
---

You are a research agent. Follow these stages strictly:

**Stage 1: Discovery**
Search for 3-5 relevant sources using WebSearch. List the URLs you found.
GATE: Before proceeding, verify you have at least 3 distinct sources from different domains.
If not, search again with different queries. Do not proceed until you have 3+ sources.

**Stage 2: Reading**
Fetch and read each source using WebFetch. Extract key claims and data points.
GATE: Verify each claim appears in at least 2 sources. Flag any single-source claims.

**Stage 3: Synthesis**
Write a summary that synthesizes the findings. Cite sources inline.
```

This works today with no code changes -- Claude follows gated instructions well. But for structural enforcement:

### 4.2 Add Structured Gates (Optional -- Code Change)

**File:** `core/agent.py`

Add a `gates` parameter to `run()` that inserts validation checks between tool-use turns:

```python
async def run(
    messages, tools, system, on_text, on_tool_start, on_tool_end,
    on_permission_check=None, max_turns=None, model=None,
    gates: list[dict] | None = None,  # NEW
) -> list[dict]:
    # ... existing loop ...

    # After tool execution, before next API call:
    if gates and _turn_number in [g["after_turn"] for g in gates]:
        gate = next(g for g in gates if g["after_turn"] == _turn_number)
        # Inject a system message asking Claude to evaluate the gate condition
        gate_msg = {
            "role": "user",
            "content": (
                f"QUALITY GATE: Before proceeding, evaluate: {gate['condition']}. "
                f"If the condition is NOT met, {gate.get('action', 'retry the previous step')}. "
                f"If it IS met, continue to the next stage."
            ),
        }
        messages.append(gate_msg)
```

**File:** `skills/registry.py` -- parse `gates` from skill YAML frontmatter.

### Implementation Notes

- Start with prompt-based gates (4.1) -- zero code changes, works today.
- Structural gates (4.2) are optional and only needed if prompt-based gates prove unreliable for specific skills.
- Gates are per-skill, not global -- most conversations don't need them.

---

## 5. Improve Parameter Descriptions

**Priority:** HIGH (bundle with Step 1)

**Problem:** Parameter descriptions in `input_schema` are minimal. Claude makes better choices when parameters have clear descriptions with examples.

This is done alongside Step 1 -- each tool's `input_schema.properties` gets expanded descriptions. Key improvements:

| Tool | Parameter | Current | Target |
|------|-----------|---------|--------|
| Read | file_path | "Absolute path to the file to read" | "Absolute path to the file to read (e.g. C:/Users/me/project/src/main.py)" |
| Read | offset | "Line number to start from (0-based, default 0)" | "Line number to start from (0-based). Use with limit to read a slice of a large file." |
| Grep | pattern | "Regex pattern to search for" | "Regex pattern to search for (e.g. 'class MyModel', 'import.\*pandas')" |
| Grep | glob | "Glob pattern to filter files (default: \*\*/\*)" | "Glob pattern to filter files (e.g. '\*.py', 'src/\*\*/\*.ts')" |
| Edit | old_string | "The exact text to find and replace" | "The exact text to find and replace. Must match the file content exactly, including indentation. Include enough surrounding context to make the match unique." |
| WebFetch | url | "The URL to fetch" | "The full URL to fetch (must be https:// or http://). Do not use for localhost or file:// paths." |

---

## Implementation Order

| Step | Description | Files Changed | Effort |
|------|-------------|---------------|--------|
| 1 + 5 | Tool descriptions + parameter descriptions | All `tools/*.py` files (12 files) | ~2 hours |
| 2 | System prompt enrichment | `server.py` | ~30 min |
| 3 | Model routing (optional) | `core/config.py`, `core/router.py` (new), `handlers/agent_handler.py` | ~1 hour |
| 4.1 | Prompt-based gates | Skill YAML files (documentation) | ~30 min |
| 4.2 | Structural gates (optional) | `core/agent.py`, `skills/registry.py` | ~2 hours |

**Recommended order:** Steps 1+5 and 2 first (highest impact, lowest risk). Step 3 when you have usage data. Step 4 when you build complex multi-stage skills.

---

## Validation

After implementing Steps 1-2, test with these scenarios to verify improvement:

1. **Tool selection test:** Ask "what files are in the src directory?" -- should use Glob, not Bash
2. **Edit guidance test:** Ask to change a function name -- Edit should include enough context for unique match
3. **Error recovery test:** Ask to read a nonexistent file -- should check with Glob before giving up
4. **Web tool test:** Ask to fetch localhost:8000 -- should use Bash with curl, not WebFetch
5. **Python preference test:** Ask to calculate something -- should use PythonExec, not Bash with python -c

Compare success rates before and after. The system prompt tool selection guide (Step 2) combined with richer tool descriptions (Step 1) should reduce tool misselection significantly.
