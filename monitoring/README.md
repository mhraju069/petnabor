# Server monitoring (Prometheus + Grafana)

Prometheus collects numbers every 30 seconds; Grafana shows them.

| What | Collected by | Dashboard |
|---|---|---|
| App: requests, errors, response times per endpoint, DB queries | Django (`/metrics/`, token-protected) | **PetNabor overview** |
| Background jobs: done / failed / retried per task | Celery events via Flower | PetNabor overview |
| Server: CPU, memory, disk, network | node-exporter | Node Exporter Full |
| Each container: CPU, memory | cAdvisor | Cadvisor exporter |
| Postgres: connections, load, size, locks | postgres-exporter | PostgreSQL Database |
| Redis: memory, clients, commands | redis-exporter | Redis |

Everything listens on `127.0.0.1` only. Data is kept 15 days (max 5 GB).
Needs about **1 GB of free RAM** on the server.

## First-time setup (on the server, in the project folder)

1. A token so only Prometheus can read the app's metrics:

   ```bash
   openssl rand -hex 32 > monitoring/metrics_token
   echo "METRICS_TOKEN=$(cat monitoring/metrics_token)" >> .env
   ```

2. A Grafana admin password:

   ```bash
   echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 18)" >> .env
   grep GRAFANA_ADMIN_PASSWORD .env   # note it down
   ```

3. Rebuild the app (new package; Celery now sends task events) and start
   monitoring:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d --build
   ```

   Tip — so plain `docker compose …` commands (and `docker-up.sh`) include
   monitoring too, add this line to `.env`:

   ```bash
   COMPOSE_FILE=docker-compose.yml:docker-compose.monitoring.yml
   ```

4. Host nginx: hide `/metrics/` from the internet (second lock besides the
   token). Add the `location ^~ /metrics/ { return 404; }` block from
   `nginx/app.petnabor.com.host.conf` to the live config,
   `/etc/nginx/sites-available/app.petnabor.com` (inside the `listen 443`
   server, before `location /`). Then
   `sudo nginx -t && sudo systemctl reload nginx`.

## Opening Grafana

From your computer, through an SSH tunnel:

```bash
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 -L 5555:localhost:5555 user@your-server
```

Then open http://localhost:3000 (user `admin`, the password from step 2) →
Dashboards → **PetNabor** → **PetNabor overview**.
Also: http://localhost:9090/targets (Prometheus — every target should be UP)
and http://localhost:5555 (Flower, Celery tasks).

To have Grafana on its own HTTPS address instead, see `nginx-grafana.conf`.

## Checks

- Prometheus → Status → Targets: `django`, `celery`, `postgres`, `redis`,
  `node`, `cadvisor` all **UP**.
- `django` DOWN with 404 → `METRICS_TOKEN` in `.env` and
  `monitoring/metrics_token` differ, or the web container wasn't restarted.
- `celery` UP but no task numbers → the worker wasn't restarted with `-E`.

## Later

Alerts (e-mail / Telegram / Slack when the app is down, errors spike, disk
fills up): Grafana → Alerting. Suggested rules: web app down 2 min;
5xx above 5% for 5 min; p95 above 2 s for 10 min; disk above 85 %;
any Celery task failing repeatedly.
