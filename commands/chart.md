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
Use CreateChart for standard plots or PythonExec with plotly for complex visualizations.
Use dark theme compatible colors.

### Step 4: Present
The UI will render the chart inline automatically.
