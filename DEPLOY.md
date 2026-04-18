# Deploying Light CC to a Hostinger VPS

A step-by-step runbook for getting Light CC onto a Hostinger KVM VPS with HTTPS, Postgres, Redis, and nightly backups. End-to-end time: ~45 minutes assuming the VPS is already provisioned.

## Prerequisites

- **A domain name** (e.g. `lightcc.example.com`) — Hostinger or any registrar
- **A Hostinger VPS** — KVM 1 or larger. Shared hosting won't work; you need Docker.
- **An Anthropic API key** — https://console.anthropic.com
- **SSH access** to the VPS as root or a sudo user

## 1. Provision the VPS

1. Hostinger → VPS → KVM 1 (or larger). Ubuntu 22.04 or 24.04 LTS is the recommended image.
2. Wait for provisioning (~2 minutes). Note the public IPv4 address.
3. Set up SSH key auth (optional but strongly recommended). On your local machine:
   ```bash
   ssh-copy-id root@<vps-ip>
   ```

## 2. Point DNS at the VPS

In your domain's DNS panel, add:

| Type | Host | Value        | TTL  |
|------|------|--------------|------|
| A    | @    | `<vps-ip>`   | 300  |
| A    | www  | `<vps-ip>`   | 300  |

(Use a subdomain like `lightcc` as `Host` if Light CC is not at the apex.)

Wait for propagation — `dig +short lightcc.example.com` should return your VPS IP before continuing. This matters because Caddy obtains a Let's Encrypt certificate on first boot, which requires the hostname to already resolve.

## 3. Install Docker on the VPS

Ubuntu's default repos split the compose plugin out, so use Docker's official repo (ships Engine + Compose together):

```bash
ssh root@<vps-ip>
apt update && apt upgrade -y
apt install -y git ufw ca-certificates curl gnupg

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

Verify:
```bash
docker --version
docker compose version
```

## 4. Clone and configure

```bash
git clone https://github.com/<your-org>/light_cc.git /opt/light_cc
cd /opt/light_cc
cp .env.example .env
```

Edit `.env` — at minimum:
```bash
nano .env
```

- `ANTHROPIC_API_KEY` — your real key
- `JWT_SECRET` — generate with:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — your login + Let's Encrypt account email
- `DOMAIN` — your hostname from step 2
- `POSTGRES_PASSWORD` — a strong random password
- `DATABASE_URL` — use the same password:
  `postgresql+asyncpg://lightcc:<same-password>@postgres:5432/lightcc`
- `ENV=production`

## 5. First-run firewall (optional but recommended)

```bash
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (LetsEncrypt challenge + redirect)
ufw allow 443/tcp   # HTTPS
ufw allow 443/udp   # HTTP/3 (optional)
ufw --force enable
```

## 6. Launch

```bash
cd /opt/light_cc
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

First build takes 3-5 minutes (frontend + Python image). Watch the logs:
```bash
docker compose logs -f
```

You should see:
- `postgres` — `database system is ready to accept connections`
- `redis` — `Ready to accept connections`
- `app` — `Uvicorn running on http://0.0.0.0:8000` and `Admin user created: you@example.com`
- `caddy` — `certificate obtained successfully` for your domain
- `postgres-backup` — `starting. Retention: 14 days.`

## 7. Verify

From your laptop:
```bash
curl https://lightcc.example.com/health/ready
# {"status":"ready"}
```

Then open `https://lightcc.example.com` in a browser and log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

## 8. Operational essentials

### Logs
```bash
docker compose logs -f app        # application
docker compose logs -f caddy      # TLS / HTTP
docker compose logs -f postgres   # DB
```

### Backups
Nightly dumps go to `/opt/light_cc/backups/lightcc-YYYYMMDDTHHMMSSZ.sql.gz` with 14-day rotation (tuneable via `BACKUP_RETENTION_DAYS`). Copy off-host regularly:
```bash
# On your laptop
rsync -avz root@<vps-ip>:/opt/light_cc/backups/ ./lightcc-backups/
```

### Restore
```bash
docker compose exec -T postgres psql -U lightcc lightcc < <(gunzip -c backups/lightcc-XXXX.sql.gz)
```

### Update to a new version
```bash
cd /opt/light_cc
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
Migrations run automatically on boot (Alembic).

### Rotate JWT secret
Users will be logged out; API tokens survive.
```bash
# 1. Generate new
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# 2. Update .env JWT_SECRET
# 3. Restart
docker compose restart app
```

### Disable public registration
Edit `config.yaml`:
```yaml
auth:
  registration_enabled: false
```
Restart the app. You can still create users via the admin UI or direct DB insert.

## 9. Post-deploy checklist

- [ ] `curl https://<domain>/health/ready` returns 200
- [ ] Browser loads the login page over HTTPS without warnings
- [ ] Admin login works
- [ ] A chat turn completes successfully (tests Anthropic API connectivity)
- [ ] `/api/agents` lists your synced agents (`contract-reviewer`, `legal-researcher`, `morning-briefing`, `person-research`)
- [ ] Backup file appears in `/opt/light_cc/backups/` within 24h
- [ ] `docker compose logs` shows no recurring errors
- [ ] Off-host backup rsync is scheduled (cron or manual)

## Gotchas learned from the first deploy (2026-04-18)

Fixed in-tree now, but worth flagging for future SQLite→Postgres deploys:

- **`requirements.lock` can be a Windows trap.** A `pip freeze` from a conda env embeds `@ file:///C:/...` paths that fail on Linux. The Dockerfile installs from `requirements.txt` for that reason. If you regenerate a lock file, do it from inside a clean Linux container.
- **Migrations must actually be Postgres-compatible.** SQLite will silently swallow things Postgres won't:
  - `PRAGMA table_info(...)` → use `sa.inspect(conn).get_columns(...)`.
  - Boolean `server_default=sa.text("1"|"0")` → Postgres needs `sa.text("true"|"false")`.
  - Defensive column checks on missing tables → SQLite returns empty, Postgres raises `NoSuchTableError`.
- **Don't rely on `Base.metadata.create_all()` at boot.** Any table in `core/db_models.py` must have a migration that creates it; `create_all` just masks the gap on SQLite. Diff models vs the migration chain before shipping.
- **The entrypoint runs `alembic upgrade head` on boot** (see `scripts/entrypoint.sh`). If you fork the entrypoint, keep that line.

## Troubleshooting

**Caddy keeps retrying TLS**
DNS hasn't propagated, or ports 80/443 aren't reachable. Check:
```bash
dig +short <domain>
curl -I http://<domain>   # should return from your VPS
```

**`JWT_SECRET must be set to a secure value in production`**
You have `ENV=production` but `JWT_SECRET` is empty or still at the default. Generate one and restart.

**App loads but login says "Invalid credentials"**
The admin user wasn't created — check `docker compose logs app | grep -i admin`. Most common cause: `ADMIN_PASSWORD` contained a `$` that got interpreted by the shell. Quote it in `.env`.

**Postgres connection errors at boot**
`DATABASE_URL` password doesn't match `POSTGRES_PASSWORD`. They must be identical.

**`/metrics` returns 403**
Intentional — the endpoint only accepts localhost by default. If you want Prometheus to scrape it, set `server.metrics_public: true` in `config.yaml` and restrict network access via firewall instead.

## Scaling notes

- **Single-replica is fine** for small teams. Redis is optional at this tier.
- **Multi-replica** requires Redis (for rate-limit state and pub/sub) and a shared object-storage bucket (set `S3_BUCKET`).
- **Beyond one VPS** — move Postgres to a managed instance (Hostinger Cloud Database, Supabase, Neon), keep the app as a stateless container, and put a CDN in front of Caddy.
