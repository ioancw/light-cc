---
description: Create a chart or visualization
argument-hint: "[chart type] of [data description]"
---

# Create Chart

Create a visualization: $ARGUMENTS

## Workflow

### Step 1: Determine Chart Type
Parse the request to identify the chart type (bar, line, scatter, heatmap, pie, etc.).

### Step 2: Prepare Data
Load or reference the data source. Use the `visualization` skill for chart creation guidance.

### Step 3: Create the Chart
Use **CreateChart** — pass x_values/y_values (or a dataset name). For math plots (sine, polynomials, etc.) compute the arrays in a single short PythonExec, then hand them to CreateChart. Do not write `.plotly.json` files. Do not set colors or templates — the UI applies the theme.

### Step 4: Present
The UI will render the chart inline automatically.
