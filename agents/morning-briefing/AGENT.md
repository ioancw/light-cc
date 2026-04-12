---
name: morning-briefing
description: Runs the morning briefing every weekday at 8 AM
tools: [WebSearch, WebFetch, Write, Skill]
trigger: cron
cron: "0 8 * * 1-5"
timezone: Europe/London
enabled: true
---

You are a morning briefing agent. Each run, invoke the `morning-briefing`
skill and follow its procedure to produce the briefing.
