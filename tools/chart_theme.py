"""Unified chart theme for Light CC — matches the dark UI palette."""

from __future__ import annotations

# Color sequence for data series — designed for dark backgrounds,
# consistent with the UI's accent/semantic colors
SERIES_COLORS = [
    "#818cf8",  # soft indigo (accent)
    "#38bdf8",  # sky blue
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#a78bfa",  # violet
    "#fb923c",  # orange
    "#e879f9",  # fuchsia
    "#2dd4bf",  # teal
    "#f472b6",  # pink
    "#6366f1",  # indigo (stronger)
    "#facc15",  # yellow
]

# Plotly layout defaults — applied to every figure
LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(
        family="Geist Mono, monospace",
        size=11,
        color="#c4c4d4",
    ),
    title=dict(
        font=dict(size=14, color="#e8e8f2"),
        x=0.0,
        xanchor="left",
        pad=dict(l=4, t=4),
    ),
    margin=dict(l=48, r=16, t=44, b=40),
    colorway=SERIES_COLORS,
    xaxis=dict(
        gridcolor="#1e1e26",
        zerolinecolor="#28282e",
        linecolor="#28282e",
        tickfont=dict(size=10, color="#8888a0"),
        title_font=dict(size=11, color="#8888a0"),
    ),
    yaxis=dict(
        gridcolor="#1e1e26",
        zerolinecolor="#28282e",
        linecolor="#28282e",
        tickfont=dict(size=10, color="#8888a0"),
        title_font=dict(size=11, color="#8888a0"),
    ),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        borderwidth=0,
        font=dict(size=10, color="#8888a0"),
    ),
    hoverlabel=dict(
        bgcolor="#16161c",
        bordercolor="#28282e",
        font=dict(family="Geist Mono, monospace", size=11, color="#e8e8f2"),
    ),
    # Improve bar/pie defaults
    bargap=0.15,
    bargroupgap=0.1,
)


def apply_theme(fig) -> None:
    """Apply the Light CC theme to a Plotly figure in-place."""
    fig.update_layout(**LAYOUT)

    # Style individual traces for polish
    for trace in fig.data:
        ttype = trace.type

        if ttype == "scatter":
            if trace.mode and "lines" in trace.mode:
                trace.update(line=dict(width=2.5))
            if trace.mode and "markers" in trace.mode:
                trace.update(marker=dict(size=6, line=dict(width=0)))

        elif ttype == "bar":
            trace.update(
                marker=dict(
                    line=dict(width=0),
                    opacity=0.9,
                ),
            )

        elif ttype in ("pie", "sunburst", "treemap", "funnel"):
            trace.update(
                textfont=dict(
                    family="Geist Mono, monospace",
                    size=10,
                    color="#e8e8f2",
                ),
            )
            if ttype == "pie":
                trace.update(
                    marker=dict(line=dict(color="#0c0c0e", width=1.5)),
                    hole=0.35,
                )

        elif ttype == "heatmap" or ttype == "histogram2d":
            trace.update(
                colorscale=[
                    [0.0, "#0c0c0e"],
                    [0.2, "#312e81"],
                    [0.4, "#4338ca"],
                    [0.6, "#6366f1"],
                    [0.8, "#818cf8"],
                    [1.0, "#c7d2fe"],
                ],
            )

        elif ttype == "candlestick":
            trace.update(
                increasing=dict(line=dict(color="#10b981"), fillcolor="rgba(16,185,129,0.3)"),
                decreasing=dict(line=dict(color="#ef4444"), fillcolor="rgba(239,68,68,0.3)"),
            )

        elif ttype == "sankey":
            if hasattr(trace, "node") and trace.node is not None:
                trace.node.update(
                    color=SERIES_COLORS[:len(trace.node.label or [])],
                    line=dict(color="#0c0c0e", width=0.5),
                )
