---
name: person-research
description: >
  Produce a pre-meeting briefing on a named individual by searching public
  sources (LinkedIn, company site, news, interviews, filings, social) and
  synthesising the findings into a structured markdown brief. Use this skill
  whenever the user asks to "research a person", "prep for a meeting with X",
  "brief me on Y", "who is Z", or pastes a LinkedIn URL asking for context.
  The output is a single markdown document, 400-700 words, written so the
  reader can scan it in under five minutes before walking into a meeting.
---

# Person Research Skill

Produce a structured, source-cited briefing on a specific individual by
searching public sources and synthesising a pre-meeting brief.

---

## Step 0 — Parse the target

Extract from the user's prompt:

- **Name** (required). Normalise to full name if possible.
- **Company** (if given). Otherwise infer or leave blank.
- **Context** (if given): the reason for the meeting, e.g. "partnership",
  "sales call", "investor intro", "interviewing me", "podcast guest".
  Use this to weight which angles matter.

If the name is ambiguous (common name, no company), say so up front in the
brief and narrow to the most likely match given the context.

---

## Step 1 — Run the source sweep

Run these searches in this order. Stop early only if you already have rich
material; otherwise cover at least the first four.

```
"<Full Name>" <Company>                           — anchor search
"<Full Name>" LinkedIn                            — role/tenure/bio
"<Full Name>" <Company> announcement OR press     — company news mentions
"<Full Name>" interview OR podcast                — voice/views
"<Full Name>" blog OR Substack OR Medium          — what they write about
"<Full Name>" conference OR keynote OR panel      — speaking history
"<Company>" about OR leadership                   — company context
"<Company>" recent news <current year>            — company momentum
```

Supplement as relevant:

- **Public company?** Search `<Company> 10-K executive officers` or proxy
  statement for an official bio.
- **Startup?** Search `<Company> Crunchbase funding` and `<Founder name>
  seed OR Series A`.
- **Technical role?** Search `<Full Name> GitHub` and check repos/READMEs.
- **Active on X?** Search `<Full Name> Twitter OR X` and fetch the profile
  if you find it.

For each hit that looks substantive, `WebFetch` the URL to get the full
text. Don't rely only on search snippets.

---

## Step 2 — Structure the output

Write the brief in the following exact section order. Each section is a
second-level heading (`##`). Omit a section only if you genuinely found
nothing relevant.

```markdown
# Briefing — <Full Name>, <Role> at <Company>

**Meeting context:** <one line, inferred or stated>
**Prepared:** <today's date>

## One-liner
<One sentence: who they are, why this meeting matters.>

## Background
<Career arc in 3-5 sentences. Where they studied, how they got to the
current role, notable past roles or exits. Ground every factual claim in
a source.>

## Current role
<What they actually do at the company. Scope of responsibility, tenure,
team size if known. Distinguish the title from the actual remit where
possible.>

## Company snapshot
<What the company does in plain English. Stage (public / private / recent
funding), size bracket, flagship products or clients, recent newsworthy
moves (launches, raises, acquisitions, layoffs, lawsuits).>

## Recent activity
<What this person has been saying or doing publicly in the last 6-12
months: conference talks, podcast appearances, op-eds, LinkedIn posts
with traction, product announcements they front. This is the best signal
for what's on their mind right now.>

## Talking points
<3-5 bullets: topics likely to resonate. Each bullet: the topic in bold,
then one sentence on why it lands with this person specifically given
what you found.>

## Watch-outs
<1-3 bullets: topics to handle carefully — recent company setbacks,
public positions they hold, sensitive personnel events, competitive
dynamics with anyone the user is associated with. If you find nothing
notable, write "None surfaced" rather than padding.>

## Suggested questions
<3-5 questions the user could ask that (a) show they've done their
homework, (b) open up substantive discussion, (c) aren't just flattery.
Frame each question around something specific you found.>

## Sources
<Bulleted list of URLs actually fetched, grouped as: Profile / Company /
News / Interviews-Posts. One line each.>
```

---

## Step 3 — Sourcing and honesty rules

- Every factual claim (a role, a tenure, a number, a position they hold)
  must come from a source you actually retrieved. If you're uncertain,
  hedge explicitly: "reportedly", "per their LinkedIn", "according to a
  2024 interview".
- **Do not fabricate.** If you can't find a piece of information, leave
  the field out or say "Not surfaced in public sources." Speculating
  about someone's views, background, or motives in a briefing is worse
  than omitting the section.
- Paraphrase. No quotes longer than 15 words, max one direct quote per
  source.
- If searches return nothing substantive (the person has little public
  footprint), say so plainly at the top of the brief and keep it short.
  A two-paragraph honest brief beats a padded one.
- If you find evidence of a different person with the same name being
  confused with the target, flag the ambiguity.

---

## Step 4 — Output

- Target length: **400-700 words** for the body (excluding headings and
  the Sources list).
- Plain markdown. No HTML.
- Save the brief to `outputs/briefings/<slug>.md` using `Write`, where
  `<slug>` is `<lastname>-<company>-<YYYYMMDD>` in lowercase with dashes.
  Create the directory if it doesn't exist.
- Return the brief inline in your final response as well, so the user can
  read it immediately without opening the file.

---

## Notes on source priority

| Source | Best for |
|---|---|
| LinkedIn (public view) | Role, tenure, career arc, endorsements/posts |
| Company site (About/Leadership) | Official bio, stated remit |
| Recent press | What they've been associated with publicly |
| Podcasts/interviews | Voice, views, what they care about |
| Blog posts / Substack | Unfiltered current thinking |
| SEC filings (if public co) | Authoritative bios, compensation, history |
| Crunchbase (if private) | Funding, investors, board |
| GitHub (if technical) | Actual work product, collaborators |
| X/Twitter | Live opinions, reactions, who they engage with |

If LinkedIn blocks WebFetch (common — it often does), pivot to Google's
cached snippet of the LinkedIn page via search, and cross-reference with
the company's own site.
