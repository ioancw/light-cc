# Frontend Typography Reference

Source of truth for fonts and sizes used across the Svelte frontend. All sizes
are in `px` unless noted. Variables are defined in `src/styles/themes.css`.

## Font Families

| Token           | Stack                                             | Role                                                 |
| --------------- | ------------------------------------------------- | ---------------------------------------------------- |
| `--font-ui`     | `'DM Sans', system-ui, -apple-system, sans-serif` | UI chrome: topbar, buttons, hints, chat input        |
| `--font-mono`   | `'Geist Mono', monospace`                         | Code, tool blocks, labels, kbd, badges, chart axes   |
| `--font-prose`  | `'Source Serif 4', Georgia, serif`                | Rendered markdown, headings inside messages          |

Use variables (`font-family: var(--font-ui)`), never literal family names in
component CSS.

## Global Defaults — `src/styles/global.css`

| Context              | Family        | Size | Line height | Notes                                   |
| -------------------- | ------------- | ---- | ----------- | --------------------------------------- |
| `body`               | `--font-ui`   | 14   | 1.6         | Base for all non-prose UI               |
| `.kbd`               | `--font-mono` | 11   | --          | Inline key hint                         |

## Prose (`.msg-prose`) — Rendered Markdown

| Element            | Family         | Size | Line height | Notes                                  |
| ------------------ | -------------- | ---- | ----------- | -------------------------------------- |
| Paragraph / base   | `--font-prose` | 16   | 1.7         | `letter-spacing: 0.006em`              |
| Inline `code`      | `--font-mono`  | 12   | --          | `color: --fg-bright`                   |
| `pre code` block   | `--font-mono`  | 12   | 1.65        | `white-space: pre`                     |
| `.code-block-header` | `--font-mono` | 11   | --          | Uppercase, `letter-spacing: 0.12em`    |
| `.copy-btn`        | `--font-mono`  | 11   | --          | `letter-spacing: 0.08em`               |
| `h1`               | `--font-prose` | 26   | 1.3         | `letter-spacing: -0.015em`, weight 600 |
| `h2`               | `--font-prose` | 21   | 1.35        | weight 600                             |
| `h3`               | `--font-prose` | 18   | 1.4         | weight 600                             |
| `blockquote`       | `--font-prose` | 16   | --          | Italic                                 |
| Table body         | `--font-mono`  | 12   | --          |                                        |
| Table header `th`  | `--font-mono`  | 11   | --          | Uppercase, `letter-spacing: 0.04em`    |
| KaTeX display math | (KaTeX)        | 1.1em | --         | Relative to surrounding size           |

## Topbar & Model Dropdown — `Loom.svelte`

| Element              | Family        | Size | Notes                               |
| -------------------- | ------------- | ---- | ----------------------------------- |
| `.topbar-title`      | `--font-ui`   | 14   | weight 500, color `--fg-bright`     |
| `.topbar-btn` icons  | --            | 13×13 SVG | 30×30 button (26×26 <900, 28×28 <768) |
| `.model-trigger`     | `--font-ui`   | 12   | 10 at ≤768                          |
| `.model-option`      | `--font-ui`   | 13   |                                     |

## Chat Input — `InputBar.svelte`

| Element                | Family        | Size | Line height | Notes                              |
| ---------------------- | ------------- | ---- | ----------- | ---------------------------------- |
| `.input-textarea`      | `--font-ui`   | 15   | 1.6         | 14 at ≤768                         |
| `.input-hints`         | `--font-ui`   | 12   | --          |                                    |
| `.thinking-status`     | `--font-ui`   | 12   | --          | color `--dim`                      |
| `.autocomplete-name`   | `--font-mono` | 12   | --          | weight 600, color `--accent-soft`  |
| `.autocomplete-hint`   | `--font-ui`   | 11   | --          | italic                             |
| `.autocomplete-desc`   | `--font-ui`   | 11   | 1.4         |                                    |
| `.scroll-hint`         | inherit       | 11   | --          | `letter-spacing: 0.05em`           |

## Empty State — `ChatArea.svelte`

| Element              | Family         | Size | Notes                                       |
| -------------------- | -------------- | ---- | ------------------------------------------- |
| `.empty-mark`        | `--font-mono`  | 24   | Bracket + pulsing dot                       |
| `.empty-title`       | `--font-prose` | 32   | weight 400, `letter-spacing: -0.02em`, 1.15 |
| `.empty-meta`        | `--font-mono`  | 11   | Uppercase, `letter-spacing: 0.18em`         |
| `.empty-cta`         | `--font-mono`  | 12   | Uppercase, `letter-spacing: 0.12em`         |
| `.suggestion-chip`   | `--font-ui`    | 13   |                                             |

## Tool Calls — `ToolCall.svelte`

All tool-call UI uses `--font-mono` except `.tool-description` (UI font).

| Element                 | Size | Line height | Notes                                |
| ----------------------- | ---- | ----------- | ------------------------------------ |
| `.tool-name`            | 12   | --          | weight 500                           |
| `.tool-result-preview`  | 12   | --          |                                      |
| `.tool-duration`        | 12   | --          |                                      |
| `.tool-chevron`         | 8×8 SVG | --      |                                      |
| `.tool-summary`         | 11   | --          |                                      |
| `.tool-section-label`   | 11   | --          | Uppercase, `letter-spacing: 0.18em`  |
| `.tool-copy-btn`        | 11   | --          | `letter-spacing: 0.05em`             |
| `.tool-code` / streaming | 11  | 1.65        |                                      |
| `.tool-file-content`    | 11   | 1.65        | `tab-size: 4`                        |
| `.tool-bash-cmd`        | 11   | 1.65        | weight 600                           |
| `.tool-match-row`       | 11   | 1.5         |                                      |
| `.tool-file-row`        | 11   | 1.5         |                                      |
| `.tool-diff-del/add`    | 11   | 1.65        |                                      |
| `.tool-param-row`       | 11   | 1.5         |                                      |
| `.tool-error-label`     | 11   | --          | Uppercase, `letter-spacing: 0.1em`   |
| `.tool-error-msg`       | 11   | 1.6         |                                      |
| `.tool-meta`            | 11   | --          | `letter-spacing: 0.03em`             |
| `.tool-empty`           | 11   | --          | Italic                               |
| `.tool-description`     | 12   | 1.55        | `--font-ui`                          |
| `.tool-expand-btn`      | 10   | --          | `letter-spacing: 0.08em`             |

## Charts — `Chart.svelte` + `lib/plotly.js`

| Element            | Family        | Size | Notes                  |
| ------------------ | ------------- | ---- | ---------------------- |
| `.chart-title`     | --            | 12   | weight 600             |
| `.chart-loading`   | `--font-mono` | 11   |                        |
| `.chart-error`     | `--font-mono` | 11   | color `--red`          |
| Plotly body font   | `--font-mono` | 11   | color `--fg`           |
| Plotly title       | `--font-mono` | 14   | color `--fg-bright`    |
| Plotly tick labels | `--font-mono` | 10   | color `--fg-dim`       |
| Plotly axis title  | `--font-mono` | 11   | color `--fg-dim`       |
| Plotly legend      | `--font-mono` | 10   |                        |
| Plotly hover label | `--font-mono` | 11   |                        |

## Conventions

- **11px mono + uppercase + wide letter-spacing** is the recurring label
  pattern (tool section labels, empty-meta, CTA, code-block headers).
- **Serif headings (`--font-prose`)** appear only inside `.msg-prose` and the
  empty state. Everything else uses sans or mono.
- **Line heights**: prose 1.7, UI body 1.6, mono readouts 1.5–1.65.
- **Negative letter-spacing** (`-0.015em` to `-0.02em`) only on serif display
  sizes (`h1`, `h2`, `.empty-title`).

## Responsive breakpoints

| Breakpoint | Effects                                                               |
| ---------- | --------------------------------------------------------------------- |
| ≤900px     | `.topbar-btn` shrinks to 26×26, gap tightens to 4px                   |
| ≤768px     | Sidebar collapses; topbar 44px, status hidden; input 14px; hints trim |
