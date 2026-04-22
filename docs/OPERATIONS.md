# Light CC — Operations Runbook

A checklist for the first 30 minutes of an incident, and the boring maintenance jobs. All commands assume you are SSH'd into the VPS, in the compose project directory, with `docker compose` available.

> For initial deploy, see [`../DEPLOY.md`](../DEPLOY.md). This file covers **running** Light CC after it's already up.

## Quick triage

When the app looks unresponsive, run these four in parallel before diving deeper:

```bash
docker compose ps                                         # which containers are healthy?
docker compose exec app curl -sf localhost:8000/health/ready ; echo exit=$?
docker compose logs --tail=200 app | tail -40
docker compose exec redis redis-cli INFO stats | head -20
```

If health is green but a user reports a stuck conversation, see **§1**. If migrations or a bad deploy broke things, see **§2**. Otherwise work through the sections below.

---

## 1. Stuck agent recovery

An agent is "stuck" when `is_generating(cid)` returns True but no tokens are streaming — the task is hung waiting on a tool call, a model request, or a dead subscriber.

### Self-service (user triggers)
The Svelte client sends a `cancel_generation` WS event; internally that calls `core.agent_runs.cancel_task(cid)` (see `handlers/ws_router.py:284-289`). If the user can still see the UI, the Stop button fixes this.

### Operator fallback (no admin "kill session" endpoint yet)
There is no HTTP admin route to terminate an individual agent task today — `core.agent_runs._agent_tasks` is an in-process dict keyed by `cid`. The two workable escalations:

1. **Restart just the app container** (kills every in-flight task, Postgres + Redis stay up):
   ```bash
   docker compose restart app
   ```
   Clients reconnect automatically; scheduled agents resume at their next cron tick.

2. **Identify the culprit before restarting.** Grep the logs for the `cid`:
   ```bash
   docker compose logs app --tail=2000 | grep "\[.*:<cid-suffix>\]"
   ```
   The log format is `asctime LEVEL [session_id:cid] logger: message` (see `core/log_context.py:22`).

> Gap to close: an admin `POST /api/admin/agents/cancel` endpoint that cancels a task by `cid` across replicas. Not built yet — tracked in the Perplexity-readiness plan (Tier R, post-Tier-S work).

---

## 2. Rollback

### App code

```bash
# On the VPS
git log --oneline -5                    # identify the last-good commit
git revert <bad-sha>                     # creates a revert commit on master
docker compose up -d --build app         # rebuild and restart only the app
```

Don't use `git reset --hard` on a deployed branch — revert preserves history and is safer to roll forward from.

### Database migrations

Alembic runs `upgrade head` automatically on every boot (`scripts/entrypoint.sh`). To roll back:

```bash
docker compose exec app alembic current   # current revision
docker compose exec app alembic history   # full chain
docker compose exec app alembic downgrade -1   # revert one step
```

Multi-replica deploys are safe: `alembic/env.py` uses a Postgres advisory lock (`pg_advisory_lock(42)`) to serialize concurrent upgrades.

**Not reversible by `downgrade`:**
- Data migrations that drop a source column after backfilling a destination — the original data is gone.
- Migrations whose `downgrade()` is `pass` or a stub.

Before rolling back across a data migration, snapshot the DB first:
```bash
docker compose exec postgres pg_dump -U lightcc lightcc | gzip > /tmp/pre-rollback.sql.gz
```

---

## 3. WebSocket timeout tuning

The WS path is three hops: browser → Caddy → Uvicorn → app handler. Each has its own timeout.

| Layer | Where | Current value |
|---|---|---|
| Browser reconnect | `frontend/src/ws.js` | Exponential backoff on `onerror` |
| Caddy proxy | `Caddyfile` `reverse_proxy @websocket` block | Default (no explicit timeout) |
| Uvicorn keep-alive | `scripts/entrypoint.sh` (no flag set) | 5s default |
| App handshake | `handlers/ws_router.py:88` (`asyncio.wait_for(..., timeout=10.0)`) | 10s for first auth message |

To tune:

- **Stretch Caddy's WS timeout** (for long-running tool calls):
  ```caddyfile
  reverse_proxy @websocket app:8000 {
      transport http {
          read_timeout 5m
          write_timeout 5m
      }
  }
  ```
- **Raise Uvicorn keep-alive** by editing the `CMD` line in the Dockerfile:
  ```
  CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30"]
  ```
- **Loosen the auth-message timeout** in `handlers/ws_router.py:88` only if clients are on genuinely bad networks — the current 10s is generous for real browsers.

---

## 4. Queue depth monitoring

The scheduler enqueues background agent runs onto arq, which is backed by Redis. arq's default queue key is `arq:queue`.

```bash
# Pending + in-flight job counts
docker compose exec redis redis-cli ZCARD arq:queue             # pending (sorted set)
docker compose exec redis redis-cli KEYS "arq:job:*" | wc -l     # total jobs in store

# Light CC's own Redis keys (see core/redis_store.py, core/auth.py, core/rate_limit.py)
docker compose exec redis redis-cli --scan --pattern "lcc:session:*" | wc -l
docker compose exec redis redis-cli --scan --pattern "lcc:conv:*"    | wc -l
docker compose exec redis redis-cli SCARD lcc:revoked_tokens
docker compose exec redis redis-cli --scan --pattern "lcc:rl:*"      | head

# Live notification channel (pub/sub)
docker compose exec redis redis-cli PUBSUB CHANNELS "lcc:*"
```

Healthy queue depth depends on workload; a steady-state arq queue should drain within seconds to low minutes. If `ZCARD arq:queue` keeps climbing, the worker is either down or stuck — check `docker compose logs app | grep -i arq`.

---

## 5. Scaling to multiple replicas

Light CC is **single-replica by default**. Moving to 2+ app replicas requires all of the following:

### Already shared-state (safe to replicate)
- Auth + session data: Postgres.
- Rate limits: Redis sliding window (`core/rate_limit.py`).
- Token revocation: Redis set (`core/auth.py:_REVOKED_TOKENS_KEY`).
- Background jobs: arq on Redis.
- Pub/sub notifications: Redis channel `lcc:notifications` (`core/redis_store.py:206`).

### Replica-affine state (will break)
- `core.agent_runs._agent_tasks` — in-process dict of running asyncio Tasks keyed by `cid`. Cancellation only works from the replica that owns the task.
- `core.checkpoints._checkpoints` — in-process memory; survives WS reconnect within a replica, lost on failover.
- `core.scheduler._user_senders` — maps `user_id → WS send callback`, which is a replica-local function pointer.
- `core.rate_limit._user_limits` / `_ws_buckets` — in-memory token buckets used when Redis is NOT configured. With Redis configured in prod they are unused.

The WS affinity problem is solved by session-sticky load balancing (Caddy `lb_policy ip_hash`) + Redis pub/sub for cross-replica broadcasts. The scheduler's `_user_senders` map would need to become a Redis lookup for cross-replica scheduled notifications.

### File-path dependencies
- User workspaces live at `/app/data/<user_id>/{workspace,outputs,uploads,memory}` on the app container's writable volume (`core/sandbox.py`).
- Plugins live at `/app/plugins` on the `app-plugins` named volume.

Multi-replica requires either:
1. A shared filesystem (NFS, AWS EFS, etc.) mounted at both paths on all replicas, or
2. An S3-backed storage driver (not implemented — would be a new `core/storage.py`).

**Recommendation for now:** stay single-replica until user volume justifies it. A single KVM VPS handles a Perplexity-pilot-sized deployment comfortably.

---

## 6. Reading the logs

Logging config: `core/log_context.py`. Output is plain-text to stdout (not JSON yet), with a `[session_id:cid]` context prefix so you can trace a single conversation across log lines:

```
2026-04-18 14:32:11,082 INFO [sess-ab12:cid-9f3e] core.agent: tool_call Bash started
```

### Common filters

```bash
# Live tail, errors only
docker compose logs -f app | grep -E " ERROR | CRITICAL "

# Everything for a single conversation (copy the cid from the UI URL or WS message)
docker compose logs app --tail=5000 | grep ":cid-9f3e\]"

# Everything for a user session (session_id is stable per WS connection)
docker compose logs app --tail=5000 | grep "\[sess-ab12:"

# Rate-limit rejections
docker compose logs app | grep -i "rate limit\|429"

# Failed auth
docker compose logs app | grep -iE "invalid token|token has been revoked|Too many attempts"
```

### Turning up verbosity
`LOG_LEVEL=DEBUG` in `.env` + `docker compose up -d app` bumps the root logger; revert to `INFO` after debugging — DEBUG is chatty on the WebSocket path.

### Switching to JSON
If/when you wire structlog for JSON output, replace the formatter in `core/log_context.py:22-23` with a `JSONFormatter`. Consumers like Loki and Datadog ingest that cleanly; `jq` then replaces `grep`.

---

## 7. Rotating `JWT_SECRET`

Rotation is a **hard cutover** today: all existing access + refresh tokens become invalid simultaneously and every user re-logs in.

### Why it's hard-cutover
`decode_token` validates with `settings.jwt_secret` (`core/auth.py`). There's no dual-secret grace window, no per-user token version bump. The moment the secret changes, every previously-issued JWT fails signature verification.

### Rotation recipe

```bash
# 1. Generate a new secret
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

# 2. Back up the old secret (in case you need to roll back)
grep ^JWT_SECRET .env > /root/jwt_secret.backup.$(date +%s)

# 3. Update .env (use sed to avoid shell-history exposure)
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW_SECRET|" .env

# 4. Restart just the app — Postgres + Redis keep their state
docker compose up -d app

# 5. Confirm
docker compose exec app python3 -c "from core.config import settings; print('len', len(settings.jwt_secret))"
```

### Post-rotation

- All users will see "Invalid or expired token" on their next API call and be redirected to login. Post an announcement before you rotate.
- `lcc:revoked_tokens` in Redis is now meaningless (those JTIs belonged to the old secret) but takes up no material space; optionally `redis-cli DEL lcc:revoked_tokens`.
- API tokens (`lcc_*` prefix, stored opaquely in Postgres) are **not** affected — they don't use the JWT secret.

### Planned improvement
A future session should add a `kid` (key ID) header to new tokens plus a rolling `{jwt_secret, jwt_secret_previous}` config so the next rotation can be a 24h grace window instead of a hard cutover.

---

## 8. Routine maintenance

### Backups
`docker-compose.prod.yml` runs a nightly `pg_dump` sidecar writing to `./backups/lightcc-<ts>.sql.gz` with 14-day rotation (tunable via `BACKUP_RETENTION_DAYS`). Copy those off-box weekly:

```bash
rsync -az <vps>:/srv/light_cc/backups/ ~/backups/light_cc/
```

Restore drill: `gunzip -c lightcc-<ts>.sql.gz | docker compose exec -T postgres psql -U lightcc lightcc`.

### Docker image hygiene

```bash
docker system df                        # disk used by images + build cache
docker image prune -f                   # drop dangling images
docker builder prune --filter until=168h -f   # build cache older than 1 week
```

### Certificate expiry
Caddy auto-renews. Sanity-check with:
```bash
echo | openssl s_client -servername <domain> -connect <domain>:443 2>/dev/null | openssl x509 -noout -dates
```

### Alembic drift check before a release
```bash
docker compose exec app alembic check   # compares ORM models to current DB
```
