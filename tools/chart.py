"""Chart tool — generate interactive charts from datasets or raw data."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tools.registry import register_tool
from tools.data_tools import get_datasets
from tools.chart_theme import apply_theme
from core.session import current_session_get, current_session_set

logger = logging.getLogger(__name__)


def _set_last_figure(fig: Any) -> None:
    """Store the last figure in the current session."""
    current_session_set("last_figure", fig)


def get_last_figure() -> Any:
    """Retrieve the last created figure from the current session."""
    return current_session_get("last_figure")


def _set_last_d3_spec(spec: dict) -> None:
    """Store the last D3 chart spec in the current session."""
    current_session_set("last_d3_spec", spec)


def get_last_d3_spec() -> dict | None:
    """Retrieve the last D3 chart spec from the current session."""
    return current_session_get("last_d3_spec")


# Chart types the D3 renderer currently handles. Others fall back to Plotly.
_D3_SUPPORTED = {"line"}


def _build_d3_line_spec(
    df, x: str, y: str, title: str, tool_input: dict[str, Any]
) -> dict:
    """Build a neutral chart spec for the D3 line renderer.

    Supports raw x_values/y_values arrays or a dataframe with named columns.
    Multiple series: pass `color` column (groups by value) or a list of y columns.
    """
    series: list[dict] = []

    if df is None or df.empty:
        return {"error": "No data for D3 line chart"}

    # Resolve x/y columns
    if not x:
        x = df.columns[0]
    if not y:
        y = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    color_col = tool_input.get("color")
    if color_col and color_col in df.columns:
        for group_val, sub in df.groupby(color_col):
            series.append({
                "name": str(group_val),
                "data": [
                    {"x": _coerce_x(xv), "y": _coerce_y(yv)}
                    for xv, yv in zip(sub[x].tolist(), sub[y].tolist())
                ],
            })
    else:
        series.append({
            "name": str(y),
            "data": [
                {"x": _coerce_x(xv), "y": _coerce_y(yv)}
                for xv, yv in zip(df[x].tolist(), df[y].tolist())
            ],
        })

    return {
        "type": "line",
        "title": title,
        "xLabel": tool_input.get("x_label", str(x)),
        "yLabel": tool_input.get("y_label", str(y)),
        "series": series,
    }


def _coerce_x(v):
    """x can be numeric or string. Pass through native-JSON types."""
    if hasattr(v, "item"):
        return v.item()
    return v


def _coerce_y(v):
    if hasattr(v, "item"):
        return v.item()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Chart types that don't use x/y in the standard way
_NO_XY_TYPES = {"pie", "sunburst", "treemap", "funnel", "sankey"}


async def handle_create_chart(tool_input: dict[str, Any]) -> str:
    """Create a chart from a loaded dataset or raw data arrays."""
    name = tool_input.get("name", "")
    chart_type = tool_input.get("chart_type", "bar")
    engine = tool_input.get("engine", "plotly")
    x = tool_input.get("x", "")
    y = tool_input.get("y", "")
    title = tool_input.get("title", "Chart")
    output_path = tool_input.get("output_path", "")
    color = tool_input.get("color")
    x_values = tool_input.get("x_values")
    y_values = tool_input.get("y_values")
    values = tool_input.get("values")
    labels = tool_input.get("labels")
    z = tool_input.get("z", "")

    try:
        import pandas as pd
        import plotly.express as px  # noqa: F401
        import plotly.graph_objects as go  # noqa: F401

        # Determine data source
        df = None
        if x_values is not None and y_values is not None:
            df = pd.DataFrame({"x": x_values, "y": y_values})
            if not x:
                x = "x"
            if not y:
                y = "y"
        elif values is not None and labels is not None:
            df = pd.DataFrame({"labels": labels, "values": values})
        elif name:
            datasets = get_datasets()
            if name not in datasets:
                available = list(datasets.keys())
                return json.dumps({
                    "error": f"Dataset '{name}' not found. Available: {available}. "
                    "Or provide x_values/y_values or labels/values arrays directly."
                })
            df = datasets[name]
        elif chart_type not in ("sankey", "gauge"):
            return json.dumps({
                "error": "Provide a dataset 'name', x_values/y_values arrays, "
                "or labels/values arrays."
            })

        # D3 path — currently only line charts. Falls through to Plotly otherwise.
        if engine == "d3":
            if chart_type in _D3_SUPPORTED:
                spec = _build_d3_line_spec(df, x, y, title, tool_input)
                if "error" in spec:
                    return json.dumps(spec)
                _set_last_d3_spec(spec)
                return json.dumps({
                    "status": "created",
                    "chart_type": chart_type,
                    "engine": "d3",
                    "title": title,
                    "inline": True,
                })
            logger.info(
                "D3 engine requested for chart_type=%r; not supported (only %s). "
                "Falling back to Plotly.",
                chart_type, sorted(_D3_SUPPORTED),
            )

        # Build the figure (Plotly)
        fig = _build_figure(chart_type, df, x, y, z, color, title, tool_input)

        if isinstance(fig, str):
            # Error message returned
            return fig

        # Apply unified theme
        apply_theme(fig)

        # Store for inline rendering
        _set_last_figure(fig)

        # Save to file if requested
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".png":
                fig.write_image(str(path))
            else:
                fig.write_html(str(path))

        result: dict[str, Any] = {
            "status": "created",
            "chart_type": chart_type,
            "engine": "plotly",
            "title": title,
            "inline": True,
            "saved_to": output_path or None,
        }
        if engine != "plotly":
            # Signal that we honored a fallback (e.g. engine="d3" + unsupported type).
            result["requested_engine"] = engine
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _build_figure(chart_type, df, x, y, z, color, title, tool_input):
    """Build a Plotly figure for the given chart type."""
    import plotly.express as px
    import plotly.graph_objects as go

    # ── Express-based charts ──
    express_funcs = {
        "bar": px.bar,
        "line": px.line,
        "scatter": px.scatter,
        "histogram": px.histogram,
        "box": px.box,
        "area": px.area,
        "violin": px.violin,
        "strip": px.strip,
    }

    if chart_type in express_funcs:
        kwargs = {"data_frame": df, "title": title}
        if x:
            kwargs["x"] = x
        if y:
            kwargs["y"] = y
        if color:
            kwargs["color"] = color
        return express_funcs[chart_type](**kwargs)

    # ── Pie / donut ──
    if chart_type == "pie":
        kwargs = {"data_frame": df, "title": title}
        if "labels" in (df.columns if df is not None else []):
            kwargs["names"] = "labels"
            kwargs["values"] = "values"
        else:
            if x:
                kwargs["names"] = x
            if y:
                kwargs["values"] = y
        if color:
            kwargs["color"] = color
        return px.pie(**kwargs)

    # ── Heatmap ──
    if chart_type == "heatmap":
        kwargs = {"data_frame": df, "title": title}
        if x:
            kwargs["x"] = x
        if y:
            kwargs["y"] = y
        if z:
            kwargs["z"] = z
            return px.density_heatmap(**kwargs)
        return px.density_heatmap(**kwargs)

    # ── Treemap ──
    if chart_type == "treemap":
        kwargs = {"data_frame": df, "title": title}
        path_cols = tool_input.get("path")
        if path_cols:
            kwargs["path"] = path_cols if isinstance(path_cols, list) else [path_cols]
        elif x:
            kwargs["path"] = [x]
        if y:
            kwargs["values"] = y
        elif "values" in (df.columns if df is not None else []):
            kwargs["values"] = "values"
        if color:
            kwargs["color"] = color
        return px.treemap(**kwargs)

    # ── Sunburst ──
    if chart_type == "sunburst":
        kwargs = {"data_frame": df, "title": title}
        path_cols = tool_input.get("path")
        if path_cols:
            kwargs["path"] = path_cols if isinstance(path_cols, list) else [path_cols]
        elif x:
            kwargs["path"] = [x]
        if y:
            kwargs["values"] = y
        elif "values" in (df.columns if df is not None else []):
            kwargs["values"] = "values"
        if color:
            kwargs["color"] = color
        return px.sunburst(**kwargs)

    # ── Funnel ──
    if chart_type == "funnel":
        kwargs = {"data_frame": df, "title": title}
        if x:
            kwargs["x"] = x
        if y:
            kwargs["y"] = y
        return px.funnel(**kwargs)

    # ── Waterfall ──
    if chart_type == "waterfall":
        fig = go.Figure(go.Waterfall(
            x=df[x].tolist() if x and x in df.columns else df.iloc[:, 0].tolist(),
            y=df[y].tolist() if y and y in df.columns else df.iloc[:, 1].tolist(),
            textposition="outside",
            connector=dict(line=dict(color="#28282e")),
        ))
        fig.update_layout(title=title)
        return fig

    # ── Radar / polar ──
    if chart_type == "radar":
        categories = df[x].tolist() if x and x in df.columns else df.iloc[:, 0].tolist()
        values = df[y].tolist() if y and y in df.columns else df.iloc[:, 1].tolist()
        # Close the polygon
        categories = categories + [categories[0]]
        values = values + [values[0]]
        fig = go.Figure(go.Scatterpolar(
            r=values,
            theta=categories,
            fill="toself",
            fillcolor="rgba(129,140,248,0.15)",
            line=dict(color="#818cf8", width=2),
        ))
        fig.update_layout(
            title=title,
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(
                    gridcolor="#1e1e26",
                    linecolor="#28282e",
                    tickfont=dict(size=9, color="#5a5a72"),
                ),
                angularaxis=dict(
                    gridcolor="#1e1e26",
                    linecolor="#28282e",
                    tickfont=dict(size=10, color="#8888a0"),
                ),
            ),
        )
        return fig

    # ── Sankey ──
    if chart_type == "sankey":
        source = tool_input.get("source", [])
        target = tool_input.get("target", [])
        value = tool_input.get("value", [])
        node_labels = tool_input.get("node_labels", [])

        if not source or not target or not value:
            return json.dumps({
                "error": "Sankey requires 'source', 'target', and 'value' arrays. "
                "Optionally provide 'node_labels'."
            })

        fig = go.Figure(go.Sankey(
            node=dict(
                label=node_labels or None,
                pad=15,
                thickness=20,
            ),
            link=dict(
                source=source,
                target=target,
                value=value,
            ),
        ))
        fig.update_layout(title=title)
        return fig

    # ── Candlestick (OHLC) ──
    if chart_type == "candlestick":
        fig = go.Figure(go.Candlestick(
            x=df[x].tolist() if x and x in df.columns else df.index.tolist(),
            open=df[tool_input.get("open", "open")],
            high=df[tool_input.get("high", "high")],
            low=df[tool_input.get("low", "low")],
            close=df[tool_input.get("close", "close")],
        ))
        fig.update_layout(title=title, xaxis_rangeslider_visible=False)
        return fig

    # ── Gauge ──
    if chart_type == "gauge":
        val = tool_input.get("gauge_value", 0)
        min_val = tool_input.get("gauge_min", 0)
        max_val = tool_input.get("gauge_max", 100)
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=val,
            title=dict(text=title, font=dict(size=14, color="#e8e8f2")),
            gauge=dict(
                axis=dict(range=[min_val, max_val], tickfont=dict(color="#8888a0")),
                bar=dict(color="#6366f1"),
                bgcolor="#16161c",
                bordercolor="#28282e",
                steps=[
                    dict(range=[min_val, (max_val - min_val) * 0.5 + min_val], color="#1e1e26"),
                    dict(range=[(max_val - min_val) * 0.5 + min_val, max_val], color="#28282e"),
                ],
            ),
        ))
        return fig

    # Fallback
    return json.dumps({"error": f"Unknown chart type: {chart_type}"})


_ALL_CHART_TYPES = [
    "bar", "line", "scatter", "histogram", "box", "area",
    "pie", "heatmap", "violin", "strip",
    "treemap", "sunburst", "funnel", "waterfall",
    "radar", "sankey", "candlestick", "gauge",
]

register_tool(
    name="CreateChart",
    aliases=["create_chart"],
    description=(
        "Render a themed, inline chart. Prefer this over PythonExec for any standard plot — "
        "line, scatter, bar, histogram, box, area, pie, heatmap, violin, treemap, sunburst, "
        "funnel, waterfall, radar, sankey, candlestick, gauge. "
        "Pass raw data via x_values/y_values (or labels/values), or reference a dataset loaded "
        "with LoadData by name. For simple math plots (sin(x), polynomial, etc.) compute the "
        "arrays in a short PythonExec, then pass them here — do NOT write a .plotly.json file. "
        "Only reach for PythonExec + plotly/matplotlib when this tool genuinely cannot express "
        "the chart (e.g. custom 3D, bespoke layouts, chart-of-charts)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Dataset name (loaded via load_data). Not needed with raw arrays.",
            },
            "chart_type": {
                "type": "string",
                "enum": _ALL_CHART_TYPES,
                "description": "Type of chart",
            },
            "engine": {
                "type": "string",
                "enum": ["plotly", "d3"],
                "description": "Rendering engine. Default 'plotly'.",
            },
            "x_label": {
                "type": "string",
                "description": "Axis label for x (defaults to the column name)",
            },
            "y_label": {
                "type": "string",
                "description": "Axis label for y (defaults to the column name)",
            },
            "x": {"type": "string", "description": "Column for x-axis / categories"},
            "y": {"type": "string", "description": "Column for y-axis / values"},
            "z": {"type": "string", "description": "Column for z-axis (heatmap intensity)"},
            "x_values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Raw x-axis values",
            },
            "y_values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Raw y-axis values",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Category labels (for pie, treemap, etc.)",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Numeric values (for pie, treemap, etc.)",
            },
            "title": {"type": "string", "description": "Chart title"},
            "color": {"type": "string", "description": "Column for color grouping"},
            "path": {
                "description": "Hierarchy path columns (treemap/sunburst)",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "output_path": {
                "type": "string",
                "description": "Optional file path to save (.html or .png)",
            },
            # Sankey-specific
            "source": {"type": "array", "items": {"type": "integer"}, "description": "Sankey source node indices"},
            "target": {"type": "array", "items": {"type": "integer"}, "description": "Sankey target node indices"},
            "value": {"type": "array", "items": {"type": "number"}, "description": "Sankey link values"},
            "node_labels": {"type": "array", "items": {"type": "string"}, "description": "Sankey node labels"},
            # Candlestick columns
            "open": {"type": "string", "description": "Column for open price (candlestick)"},
            "high": {"type": "string", "description": "Column for high price (candlestick)"},
            "low": {"type": "string", "description": "Column for low price (candlestick)"},
            "close": {"type": "string", "description": "Column for close price (candlestick)"},
            # Gauge
            "gauge_value": {"type": "number", "description": "Current value (gauge)"},
            "gauge_min": {"type": "number", "description": "Min value (gauge, default 0)"},
            "gauge_max": {"type": "number", "description": "Max value (gauge, default 100)"},
        },
        "required": ["chart_type"],
    },
    handler=handle_create_chart,
)
