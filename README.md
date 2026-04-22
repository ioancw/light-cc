# Light CC

A self-hosted, multi-tenant agent platform built on the Claude Code plugin spec. Install plugins created in Claude Code, run them as agents for your team, and keep the data on your own infrastructure.

Light CC is a FastAPI + Svelte web app that runs as a single Docker Compose stack (app + Postgres + Redis + Caddy for TLS). It speaks the same plugin format as Anthropic's Claude Code, so anything built there ships here unchanged.

## Highlights

- **Skills-first.** Plugins bundle agents, commands, hooks, and MCP servers under one directory — the same layout Claude Code uses locally.
- **Multi-tenant by default.** Per-user workspaces, per-user agent definitions, JWT + API-token auth, admin-only plugin installs.
- **Sandboxed tool execution.** Bash and PythonExec run in `unshare`'d subprocesses with netns isolation, ulimits, and a read-only container root.
- **Scheduled + event-triggered agents.** Run agents on cron, on webhook, or on demand; results stream over WebSocket.
- **Works with a laptop or a VPS.** SQLite for local dev, Postgres + Redis for production. One `docker compose up` in either case.

## Quickstart (local)

```bash
git clone https://github.com/<owner>/light_cc.git
cd light_cc
cp .env.example .env   # fill in ANTHROPIC_API_KEY + ADMIN_* for first boot
docker compose up --build
```

Then open `http://localhost:8000`, log in with the admin credentials you set in `.env`, and send your first message.

## Production deploy

See [`DEPLOY.md`](DEPLOY.md) for a step-by-step Hostinger VPS runbook (HTTPS via Caddy + Let's Encrypt, Postgres, Redis, backups). Live reference deployment: https://wiggy.cloud.

## Documentation

- [`docs/`](docs/README.md) — user guide, plugin spec, agent/skill design notes, roadmap.
- [`DEPLOY.md`](DEPLOY.md) — production VPS runbook.
- [`docs/plugin-spec.md`](docs/plugin-spec.md) — Light CC's plugin format (matches Claude Code).
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — walk-through for end users.

## Project status

Light CC is pre-1.0. The core (auth, sandboxing, agents, scheduling, plugins, WS streaming) is in place and the reference deployment at wiggy.cloud is stable. Security hardening is tracked in internal plans; see [`docs/ROADMAP_STATUS.md`](docs/ROADMAP_STATUS.md) for what's next.

## License

See [`LICENSE`](LICENSE) if present; otherwise all rights reserved until a license is added.
