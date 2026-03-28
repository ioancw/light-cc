---
description: Perform GitHub operations using the gh CLI
argument-hint: "[operation] [args]"
---

# GitHub

$ARGUMENTS

## Workflow

### Step 1: Parse Request
Determine the GitHub operation from the arguments.

### Step 2: Check Auth
Verify authentication with `gh auth status`.

### Step 3: Execute
Use the `github` skill for the appropriate gh CLI commands.

### Step 4: Present
Format results for readability.
