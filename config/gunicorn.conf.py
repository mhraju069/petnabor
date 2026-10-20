"""Gunicorn settings (docker-compose web: `gunicorn -c config/gunicorn.conf.py`)."""


def child_exit(server, worker):
    # Multi-process Prometheus metrics: forget a worker that has exited, so
    # its gauges don't keep showing (see api/core/metrics.py).
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
    except Exception:
        pass
