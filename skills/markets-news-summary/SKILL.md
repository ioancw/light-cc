---
name: markets-news-summary
description: >
  Produce a structured daily/weekly financial markets news summary by searching
  CNBC, Bloomberg, Yahoo Finance, TheStreet, and Motley Fool. Use this skill
  whenever the user asks for a markets summary, daily market wrap, financial news
  digest, or says something like "what's happening in markets", "give me a markets
  update", "summarise the news", or pastes a news feed URL they want digested.
  Also trigger when the user asks about macro themes (geopolitics, rates, oil,
  equities) in a market context. The output is a structured markdown brief
  suitable for ingestion into downstream tools, commentary workflows, or
  institutional morning notes.
---

# Markets News Summary Skill

Produce a structured, source-cited markets news brief by searching a fixed
source list and synthesising the results into a clean markdown document.

---

## Step 1 — Search

Run **all five** of these searches in sequence (or parallel if possible).
Use today's date in each query so results are fresh:

```
CNBC stock market today [Month Year]
Bloomberg markets news today [Month Year]
Yahoo Finance markets live [Month Year]
TheStreet stock market today [Month Year]
Motley Fool stock market today [Month Year]
```

Then run **one or two topical searches** for the dominant macro theme of the
day (e.g. `US Iran ceasefire oil market April 2026`, or `Fed rate decision
markets`, or `tariffs equities`). Use headlines from the first pass to infer
what the dominant theme is.

If the user pastes a specific feed URL (e.g. WSJ RSS), attempt `web_fetch` on
it first. If it returns binary/blocked, fall back to the search workflow above.

---

## Step 2 — Structure the Output

Write the summary in the following **exact section order**. Each section is a
second-level markdown heading (`##`). Omit a section only if there is genuinely
no relevant information from the searches.

```markdown
# Markets Brief — [Day, Date]

## The Macro Story
[1–2 paragraphs on the dominant geopolitical or macro driver of the week/day.
This is the lede. Ground everything else in it.]

## Equities
[Index performance: S&P 500, Nasdaq, Dow, Russell 2000. Weekly and daily moves
where available. Notable sector themes — what led, what lagged, why.]

## Rates & Fixed Income
[10Y Treasury yield level and daily change. Any Fed commentary. Inflation data
if released. Curve shape observations if data supports it.]

## Energy & Commodities
[WTI and Brent crude: levels, daily change, driver. Gold if notable.
Any supply/demand narrative from Strait of Hormuz, OPEC, etc.]

## FX
[EUR/USD, GBP/USD, and any cross that moved notably. Include level.
Only include if search results provide data — do not fabricate levels.]

## Stock Movers & Themes
[3–5 bullet points on individual stock stories: earnings, upgrades/downgrades,
M&A, sector rotations, notable analyst calls. Keep each to 1–2 sentences.]

## Sentiment & Data
[Consumer sentiment, PMI, CPI, PCE — any data releases. VIX level if available.
Credit market signals if notable (e.g. private credit redemptions).]

## What to Watch
[2–3 forward-looking items: upcoming data, geopolitical events, earnings,
central bank decisions. Draw from the news to identify what markets are
focused on next.]
```

---

## Step 3 — Citation and Sourcing Rules

- Every **specific claim** (a price level, a percentage move, a policy
  decision, a company action) must be wrapped in a `` tag referencing
  the search result index that supports it.
- Do **not** invent prices, yields, or corporate facts. If the searches don't
  surface a piece of data, omit that field rather than estimating.
- Paraphrase all source material — do not reproduce sentences verbatim.
  Keep any direct quotes under 15 words and limit to one per source.
- Prioritise recency: if two sources conflict on a level (e.g. intraday vs
  close), use the close/final figure and note the discrepancy if material.

---

## Step 4 — Output Format

The output is **plain markdown**. No HTML. No bullet-heavy formatting inside
the prose paragraphs — write in full sentences. Bullets are acceptable only
in the *Stock Movers & Themes* and *What to Watch* sections.

Target length: **400–600 words** for the body (excluding headings). This is
calibrated for ingestion into institutional morning note pipelines, LLM
context windows, or daily commentary workflows.

If the user specifies a different length or format (e.g. "keep it to 3 bullet
points per section", "give me just equities and rates", "output as JSON"),
honour that instruction and adapt the structure accordingly.

---

## Notes on Source Reliability

| Source | Best for |
|---|---|
| CNBC | US equity intraday narrative, Fed commentary, earnings |
| Bloomberg | Cross-asset levels, institutional flow, global macro |
| Yahoo Finance | Raw price data (indices, yields, FX, commodities) |
| TheStreet | Live blog detail, sector rotation, small/mid cap movers |
| Motley Fool | Individual stock stories, analyst calls, corporate actions |

If a source is paywalled or returns no useful content, skip it and note in
a comment `<!-- [source] unavailable -->` at the top of the document so
downstream consumers know coverage may be partial.
