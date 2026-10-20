# Finding out why a user got an error

Every request gets a **request id**. It's on every log line of that request
(`[req=3f9a1c2b7d4e]`), in the `X-Request-ID` response header, and — when
the server crashes — in the app's error message: *"(Error ID: 3f9a1c2b7d4e)"*.

Log lines look like:

```
2026-10-20 14:03:11 ERROR api.users.exception_handler [req=3f9a1c2b7d4e] Unhandled exception in PostViewSet
Traceback (most recent call last):
  ...
KeyError: 'privacy'
```

## 1. In Grafana (easiest)

**Dashboards → PetNabor → PetNabor logs**

| Panel | Use it for |
|---|---|
| Errors per service | *When* did errors start, and in which service (web, celery, db…) |
| Errors & tracebacks | The error lines, newest first |
| Failed requests (nginx 5xx) | Which URL failed, when, from which IP |
| All logs | Everything — narrow with **Service** and **Search** at the top |

- A traceback is many lines: click the error line → **Show context** to see
  the whole thing.
- Click a `req=…` value → everything that one request did.
- Set the time range (top right) around when the user saw the problem.

**Explore** (compass icon) → data source **Loki** for your own searches:

| Find | Query |
|---|---|
| One request, by id | `{service=~".+"} \|= "3f9a1c2b7d4e"` |
| Web app errors | `{service="web"} \|~ "(?i)error\|traceback"` |
| Background job failures | `{service="celery"} \|~ "(?i)error\|failed\|retry"` |
| An endpoint | `{service="nginx", log="access"} \|= "/api/post/posts/"` |
| Every 5xx | `{service="nginx", log="access"} \|~ "\" 5[0-9][0-9] "` |
| Database errors | `{service="db"} \|= "ERROR"` |

Logs are kept **14 days**.

## 2. Typical cases

- **User sent a screenshot with "Error ID: …"** → PetNabor logs → paste the
  id into **Search** (time range: last 7 days). That's the exact request,
  with its traceback.
- **"It didn't work at about 3 pm"** → PetNabor logs, time range 2:45–3:15 pm,
  look at *Errors & tracebacks* and *Failed requests*.
- **A photo/video never appeared, a push or e-mail never came** → those run
  in the background: Service = `celery`. The PetNabor overview dashboard's
  *Failing tasks* table shows which task.
- **Whole app down / 502** → Service = `web`: the last lines before it
  stopped say why (crash on startup, migration error, …).

## 3. On the server, without Grafana

```bash
docker compose logs --tail=100 web          # last 100 lines
docker compose logs -f web                  # follow live (Ctrl+C to stop)
docker compose logs --since=30m web | grep -A 30 "ERROR"   # errors + tracebacks
docker compose logs web | grep 3f9a1c2b7d4e # one request id
docker compose logs --tail=100 celery       # background jobs
sudo tail -f /var/log/nginx/error.log       # nginx (502s, timeouts)
```

## What this doesn't cover

Crashes **inside the phone app** (a screen that freezes or closes) never
reach the server. For those, add crash reporting to the app (e.g. Sentry —
free tier), which also shows the phone model, OS and the steps before it.
