# Tools and Skills — How They Work in Light CC

## Tools

Tools are **functions Claude can call**. Each tool is:

1. An async Python function that takes a dict and returns a string
2. A JSON schema describing its inputs
3. A name and description

Claude reads the tool descriptions and **decides which tool to call** based on the user's request. You don't need to explicitly tell it which tool to use — it figures it out from the descriptions.

### Registered Tools

| Tool | What it does |
|------|-------------|
| `bash` | Execute shell commands via subprocess |
| `read` | Read file contents with optional line range |
| `write` | Write content to a file |
| `edit` | Search and replace within a file |
| `grep` | Regex search across files |
| `glob` | Find files by pattern |
| `load_data` | Load CSV/Excel/JSON into a named dataset |
| `query_data` | Run a pandas expression on a loaded dataset |
| `export_data` | Export a dataset to CSV/Excel/JSON |
| `create_chart` | Generate a Plotly chart from a dataset |
| `subagent` | Spawn a nested agent loop for sub-tasks |
| `save_memory` | Save information for future conversations |
| `search_memory` | Search saved memories by keyword |
| `list_memories` | List all saved memory entries |

### Creating a New Tool

Drop a Python file in `tools/` with this pattern:

```python
async def handle_my_tool(tool_input: dict) -> str:
    # Do whatever you want
    return json.dumps({"result": "whatever"})

register_tool(
    name="my_tool",
    description="What it does — Claude reads this to decide when to use it",
    input_schema={
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "What this param is for"},
        },
        "required": ["param"],
    },
    handler=handle_my_tool,
)
```

Then import it in `tools/__init__.py`. That's it — Claude can now use it.

**The description matters.** A good description means Claude picks the right tool. A vague one means it guesses wrong:

```python
# Bad — Claude won't know when to use this
description="Do stuff with data"

# Good — Claude knows exactly when this applies
description="Run a SQL query against the PostgreSQL database. Returns rows as JSON."
```

### Tool Ideas

| Tool | Purpose |
|------|---------|
| `sql_query` | Run SQL against Postgres/SQLite/DuckDB directly |
| `api_fetch` | HTTP GET/POST to external APIs with auth |
| `create_pdf` | Generate PDF reports |
| `python_exec` | Run Python in-process (faster than bash, shares loaded datasets) |
| `send_email` | Email results/reports |
| `download_url` | Download a file from a URL |
| `ask_user` | Pause and ask the user a question mid-loop |
| `show_plotly` | Render a Plotly chart inline in the chat |
| `schedule_task` | Run a skill on a cron schedule |

---

## Skills

Skills are **instructions that guide Claude** — they're prompt injections, not code. A skill is a markdown file with YAML frontmatter that:

1. Gets appended to the system prompt when activated
2. Optionally filters which tools Claude can see

### Skill Format

```markdown
---
name: data-analysis
description: Analyze CSV and Excel data files
tools: [bash, read, write, load_data, query_data, create_chart]
---

You are a data analysis assistant. When the user provides data files:

1. First load the data and examine its structure
2. Provide a summary of the dataset
3. Perform the analysis
4. Create visualizations when appropriate
```

### How Skills Activate

1. **Explicit**: User types `/data-analysis` — exact match on skill name
2. **Keyword**: User says "analyze this CSV" — keyword matching against skill name and description
3. **No match**: All tools available, base system prompt only

### Skills Can Contain Code

A skill can embed code that Claude executes via the `bash` tool:

```markdown
---
name: load-from-bloomberg
description: Load Bloomberg data
---

Run this Python script via bash:

python3 -c "
import blpapi
# ... actual code here ...
"
```

The skill is still a prompt — Claude reads the code and calls `bash` to run it.

### Creating a New Skill

Drop a `.md` file in `skills/`. Auto-discovered on startup. No Python required — anyone can write a skill.

---

## Tools vs Skills — The Key Distinction

| | Tools | Skills |
|---|---|---|
| **What they are** | Python functions Claude calls | Markdown instructions Claude follows |
| **Who creates them** | Developer (requires Python) | Anyone (just write a .md file) |
| **How they work** | Execute code and return results | Inject guidance into the system prompt |
| **Analogy** | Claude's **hands** | Claude's **brain/focus** |

**Skills guide the brain, tools are the hands.**

Without a skill active, Claude sees all tools and uses the base system prompt. A skill focuses Claude on a specific task and optionally restricts which tools are available.

---

## Skills as Soft Graphs

A skill is like a **lightweight, flexible DAG**. It constrains the direction without being rigid:

- A DAG says: "Go A → B → C. If B fails, stop."
- A skill says: "Here's the goal and the tools you should use. Figure out the path."

Skills give you the **focus** of a DAG without the **rigidity**. Claude can still adapt, retry, try different approaches — it's just working within a narrower space.

This is why this architecture uses a simple agentic loop instead of a DAG framework. The loop handles all the dynamic reasoning. Skills just point Claude in the right direction.

---

## Claude UI vs Claude Code vs Light CC

| | Claude (Chat UI) | Claude Code (CLI) | Light CC |
|---|---|---|---|
| Skills & tools | Bundled together as "skills" | Separated — skills are .md, tools are code | Separated (follows Claude Code) |
| Activation UI | "Activating skill: PDF" | Tool calls shown in terminal | Chainlit Steps API |
| Orchestration | Internal | Agentic loop | Same agentic loop |
| Extensibility | Can't add your own | Add skills via .claude/commands/ | Add skills as .md, tools as .py |

Claude UI bundles skills and tools into one concept. Claude Code and Light CC separate them, giving more flexibility — multiple skills can share the same tools, and tools work without any skill active.

---

## Wrapping External Libraries as Tools

When interfacing with an external library (e.g. a Python package wrapping a C++ quant library), there are three approaches to mapping functions to tools:

### Option 1: One Tool Per Function

```
load_trade, load_portfolio, price_trade, price_portfolio,
calc_pv01, calc_delta, calc_gamma, get_curve, build_curve...
```

- Claude sees 30+ tool schemas — eats tokens and can confuse the model
- Works if you have fewer than ~10 functions
- Gets unwieldy fast

### Option 2: One Mega-Tool

```
name: "quant_lib"
input: { "function": "price_trade", "args": {...} }
```

- Claude doesn't understand the individual functions well
- Schema is vague — Claude guesses at args
- Bad experience

### Option 3: Grouped by Operation (Recommended)

Group related functions into logical tools that match how a user thinks about the task:

```
trade_loader   — load_trade, load_portfolio, load_from_file
pricer         — price_trade, price_portfolio, price_book
risk           — calc_pv01, calc_delta, calc_gamma, calc_var
curves         — get_curve, build_curve, bump_curve
```

Each tool handles a logical operation and dispatches internally:

```python
async def handle_pricer(tool_input: dict) -> str:
    action = tool_input["action"]  # "price_trade", "price_portfolio", etc.

    if action == "price_trade":
        result = quant_lib.price_trade(
            trade_id=tool_input["trade_id"],
            curve=tool_input.get("curve", "live"),
        )
    elif action == "price_portfolio":
        result = quant_lib.price_portfolio(
            portfolio_id=tool_input["portfolio_id"],
        )

    return json.dumps(result)

register_tool(
    name="pricer",
    description="Price trades and portfolios. Actions: price_trade, price_portfolio, price_book.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["price_trade", "price_portfolio", "price_book"],
                "description": "Which pricing function to call",
            },
            "trade_id": {"type": "string"},
            "portfolio_id": {"type": "string"},
            "curve": {"type": "string", "description": "Curve to use (default: live)"},
        },
        "required": ["action"],
    },
    handler=handle_pricer,
)
```

### Then a Skill Ties the Tools Together

The skill knows the workflow. The tools expose the capabilities. Claude connects them.

```markdown
---
name: price-book
description: Load and price a trading book
tools: [trade_loader, pricer, curves, risk, create_chart]
---

When the user asks to price a book:
1. Use trade_loader to load the trades
2. Use curves to get/build the required curves
3. Use pricer to price the portfolio
4. Use risk to calculate sensitivities if requested
5. Present results in a table, create charts if helpful
```

### The Principle

**Group by what a user would ask for, not by what the library looks like.**

The user says "price my book" not "call `quant_lib.price_trade` then `quant_lib.calc_pv01`". The skill knows the workflow, the tools expose the capabilities, Claude connects them.

This way:
- Claude sees ~4 focused tools instead of 30 scattered functions
- Each tool's description is clear and specific
- The skill provides the orchestration logic as natural language
- Claude can still adapt if something fails or returns unexpected results
