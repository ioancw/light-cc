"""Data tools — CSV/Excel loading, pandas operations, data export."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pandas as pd

from tools.registry import register_tool
from core.session import current_session_get, current_session_set


def get_datasets() -> dict[str, pd.DataFrame]:
    """Get the session-scoped datasets dict."""
    datasets = current_session_get("datasets")
    if datasets is None:
        datasets = {}
        current_session_set("datasets", datasets)
    return datasets


def _df_to_html(df: pd.DataFrame, max_rows: int = 20, title: str | None = None) -> str:
    """Convert a DataFrame to a styled HTML table for dark-mode rendering."""
    import html as html_mod
    from numbers import Number

    subset = df.head(max_rows)
    cols = list(subset.columns)

    # Show index if it's meaningful (not default 0,1,2...)
    show_index = not (
        isinstance(df.index, pd.RangeIndex)
        and df.index.start == 0
        and df.index.step == 1
    )

    # Detect column types for alignment and formatting
    numeric_cols = set()
    for c in cols:
        if pd.api.types.is_numeric_dtype(subset[c]):
            numeric_cols.add(c)

    # Build header
    idx_th = '<th class="idx"></th>' if show_index else ""
    ths = "".join(
        f'<th class="{"num" if c in numeric_cols else "txt"}">'
        f'{html_mod.escape(str(c))}</th>'
        for c in cols
    )
    thead = f"<thead><tr>{idx_th}{ths}</tr></thead>"

    # Build rows
    rows = []
    for idx, row in subset.iterrows():
        idx_td = (
            f'<td class="idx">{html_mod.escape(str(idx))}</td>' if show_index else ""
        )
        tds = []
        for c in cols:
            val = row[c]
            if pd.isna(val):
                tds.append('<td class="na">—</td>')
            elif c in numeric_cols:
                css = "num"
                # Color-code signed values
                if isinstance(val, Number) and not isinstance(val, bool):
                    if val > 0 and _looks_like_change(c):
                        css += " pos"
                    elif val < 0 and _looks_like_change(c):
                        css += " neg"
                tds.append(f'<td class="{css}">{_fmt_num(val)}</td>')
            else:
                tds.append(f'<td class="txt">{html_mod.escape(str(val))}</td>')
        rows.append(f"<tr>{idx_td}{''.join(tds)}</tr>")

    tbody = f"<tbody>{''.join(rows)}</tbody>"

    # Meta
    truncated = len(df) > max_rows
    meta_parts = [f"{len(df):,} rows", f"{len(cols)} cols"]
    if truncated:
        meta_parts.append(f"showing first {max_rows}")
    meta_text = " · ".join(meta_parts)

    title_html = (
        f'<div class="dt-title">{html_mod.escape(title)}</div>' if title else ""
    )

    return (
        f'{title_html}'
        f'<table class="data-table">{thead}{tbody}</table>'
        f'<div class="dt-meta">{meta_text}</div>'
    )


def _fmt_num(val) -> str:
    """Format a number for display."""
    if isinstance(val, float):
        abs_val = abs(val)
        if abs_val == 0:
            return "0"
        if abs_val >= 1_000_000:
            return f"{val:,.0f}"
        if abs_val >= 100:
            return f"{val:,.2f}"
        if abs_val >= 1:
            return f"{val:,.4g}"
        return f"{val:.4g}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


def _looks_like_change(col_name: str) -> bool:
    """Heuristic: does the column name suggest it's a change/delta/return value?"""
    name = col_name.lower()
    return any(kw in name for kw in (
        "change", "chg", "delta", "diff", "return", "pnl",
        "gain", "loss", "pct", "%", "growth", "var",
    ))


def _is_safe_expression(code: str) -> bool:
    """Block expressions that access dunder attributes (sandbox escape vectors)."""
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return False
        if isinstance(node, ast.Name) and node.id in ("exec", "eval", "compile", "__import__"):
            return False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("exec", "eval", "compile", "__import__", "open"):
                return False
    return True


async def handle_load_data(tool_input: dict[str, Any]) -> str:
    """Load CSV or Excel file into a named dataset."""
    file_path = tool_input.get("file_path", "")
    name = tool_input.get("name", "")

    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    from core.sandbox import validate_tool_path
    path, err = validate_tool_path(file_path)
    if err:
        return err

    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    if not name:
        name = path.stem

    try:
        if path.suffix.lower() in (".xls", ".xlsx"):
            df = pd.read_excel(path)
        elif path.suffix.lower() == ".json":
            df = pd.read_json(path)
        else:
            df = pd.read_csv(path)

        datasets = get_datasets()
        datasets[name] = df

        info = {
            "name": name,
            "rows": len(df),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "head_html": _df_to_html(df.head(10), max_rows=10, title="Preview"),
            "describe_html": _df_to_html(
                df.describe(include="all"), max_rows=50, title="Summary Statistics"
            ),
        }
        return json.dumps(info, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_query_data(tool_input: dict[str, Any]) -> str:
    """Run a pandas operation on a loaded dataset."""
    name = tool_input.get("name", "")
    code = tool_input.get("code", "")

    if not name:
        return json.dumps({"error": "No dataset name provided"})

    datasets = get_datasets()

    if name not in datasets:
        available = list(datasets.keys())
        return json.dumps({"error": f"Dataset '{name}' not found. Available: {available}"})
    if not code:
        return json.dumps({"error": "No code provided"})

    if not _is_safe_expression(code):
        return json.dumps({
            "error": "Expression blocked for safety. Avoid __dunder__ attributes and "
            "builtins like exec/eval/compile/__import__/open."
        })

    df = datasets[name]

    _SAFE_BUILTINS = {
        "len": len, "sum": sum, "min": min, "max": max, "abs": abs,
        "round": round, "sorted": sorted, "reversed": reversed,
        "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
        "range": range, "list": list, "dict": dict, "set": set, "tuple": tuple,
        "int": int, "float": float, "str": str, "bool": bool, "type": type,
        "isinstance": isinstance, "any": any, "all": all, "print": print,
        "True": True, "False": False, "None": None,
    }

    try:
        local_vars: dict[str, Any] = {"df": df, "pd": pd}
        exec(f"__result__ = {code}", {"__builtins__": _SAFE_BUILTINS}, local_vars)
        result = local_vars.get("__result__", None)

        if isinstance(result, pd.DataFrame):
            datasets[name] = result
            return json.dumps({
                "type": "dataframe",
                "rows": len(result),
                "columns": list(result.columns),
                "table_html": _df_to_html(result),
            })
        elif isinstance(result, pd.Series):
            result_df = result.to_frame(name=result.name or "value")
            return json.dumps({
                "type": "series",
                "length": len(result),
                "table_html": _df_to_html(result_df),
            })
        else:
            return json.dumps({"type": type(result).__name__, "value": str(result)[:5000]})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_export_data(tool_input: dict[str, Any]) -> str:
    """Export a dataset to a file."""
    name = tool_input.get("name", "")
    file_path = tool_input.get("file_path", "")
    fmt = tool_input.get("format", "csv")

    datasets = get_datasets()

    if not name or name not in datasets:
        return json.dumps({"error": f"Dataset '{name}' not found"})
    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    from core.sandbox import validate_tool_path
    path, err = validate_tool_path(file_path)
    if err:
        return err

    df = datasets[name]
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "excel":
            df.to_excel(path, index=False)
        elif fmt == "json":
            df.to_json(path, orient="records", indent=2)
        else:
            df.to_csv(path, index=False)

        return json.dumps({"status": "exported", "path": str(path), "rows": len(df)})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="LoadData",
    aliases=["load_data"],
    description="Load a CSV, Excel, or JSON file into a named dataset for analysis.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the data file"},
            "name": {"type": "string", "description": "Name for the dataset (defaults to filename)"},
        },
        "required": ["file_path"],
    },
    handler=handle_load_data,
)

register_tool(
    name="QueryData",
    aliases=["query_data"],
    description="Run a pandas expression on a loaded dataset. The dataset is available as 'df'.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Dataset name"},
            "code": {"type": "string", "description": "Pandas expression (e.g., 'df.groupby(\"col\").mean()')"},
        },
        "required": ["name", "code"],
    },
    handler=handle_query_data,
)

register_tool(
    name="ExportData",
    aliases=["export_data"],
    description="Export a loaded dataset to CSV, Excel, or JSON.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Dataset name"},
            "file_path": {"type": "string", "description": "Output file path"},
            "format": {"type": "string", "enum": ["csv", "excel", "json"], "description": "Output format"},
        },
        "required": ["name", "file_path"],
    },
    handler=handle_export_data,
)
