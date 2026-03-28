"""D3 / HTML embed theme for Light CC — CSS and boilerplate matching the dark UI."""

# CSS that matches the Light CC dark theme — inject into any HTML embed
CSS = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0c0c0e;
    color: #c4c4d4;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    overflow: hidden;
  }
  svg { display: block; }
  text, .label { fill: #c4c4d4; font-size: 11px; }
  .axis text { fill: #8888a0; font-size: 10px; }
  .axis path, .axis line { stroke: #28282e; }
  .grid line { stroke: #1e1e26; stroke-opacity: 1; }
  .grid path { stroke: none; }
  .title { fill: #e8e8f2; font-size: 14px; font-weight: 600; }
  .subtitle { fill: #5a5a72; font-size: 11px; }
  .tooltip {
    position: absolute;
    background: #16161c;
    border: 1px solid #28282e;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 11px;
    color: #e8e8f2;
    pointer-events: none;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
  .legend text { fill: #8888a0; font-size: 10px; }
</style>
"""

# Color palette for D3 — same as chart_theme.py SERIES_COLORS
COLORS_JS = """
const colors = [
  '#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444',
  '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6',
  '#6366f1', '#facc15'
];
"""

# Full HTML boilerplate with D3 loaded
BOILERPLATE = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://d3js.org/d3.v7.min.js"></script>
{CSS}
</head>
<body>
<script>
{COLORS_JS}
const width = document.body.clientWidth;
const height = document.body.clientHeight;
// --- Your D3 code goes below ---
</script>
</body>
</html>
"""


def wrap_d3(script: str) -> str:
    """Wrap a D3 script in the themed HTML boilerplate.

    The script has access to: d3, colors, width, height.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://d3js.org/d3.v7.min.js"></script>
{CSS}
</head>
<body>
<script>
{COLORS_JS}
const width = document.body.clientWidth;
const height = document.body.clientHeight;
{script}
</script>
</body>
</html>
"""
