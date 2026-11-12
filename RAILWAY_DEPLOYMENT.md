# Foslib Railway staging deployment

The application is not a standalone Python script. Configure the Railway `foslib-app` service to build using `docker/backend/Dockerfile` (build context is the repository root), rather than relying on Railpack's Python auto-detection.

The upstream Docker Compose stack also includes PostgreSQL, Redis, RabbitMQ, OpenSearch, and a separate frontend. A successful backend container build does **not** mean the full library service is operational. Provision and configure these dependencies separately, use secret environment variables, and perform schema/index/bootstrap initialization before opening the service to users.

This staging deployment must use fictional Alpha and Beta school data only. Never use the development credentials or placeholder secret in `docker-services.yml`.

The backend Dockerfile now builds the React ILS frontend and serves it with
Gunicorn on the same origin as the API. Use `/bin/sh scripts/start-railway` as
the app start command, port 5000, and `/ping` as its health check. The worker
uses the same image with `celery -A invenio_app.celery worker --loglevel=INFO
--concurrency=2` and needs references to the app's `INVENIO_*` variables.

Set `INVENIO_DEBUG=False`, `INVENIO_DEBUG_TB_ENABLED=False`, and include the
public hostname and `healthcheck.railway.app` in `INVENIO_TRUSTED_HOSTS`.
Configure `INVENIO_PROXYFIX_CONFIG` for one Railway proxy (`x_for=1`,
`x_proto=1`, all other values zero), and set the UI/API/SPA URLs to the public
HTTPS origin. Set `INVENIO_CSRF_HEADER=X_CSRFTOKEN` to match the ILS client.

Dependency variables belong on each dependency service: RabbitMQ requires
its own generated default user, password, vhost and exported `RABBITMQ_URL`;
the app references this URL. Redis authentication is configured on Redis;
the app references its URL with databases 0 through 3. OpenSearch is private
only, single-node, and its data volume must be owned by UID/GID 1000. Check
`/_cluster/health` rather than relying on a running container status alone.
RabbitMQ uses a persistent volume and a stable node name. The app's file
storage volume is mounted at `/opt/invenio_app_ils/data`.

Initialize an empty database with `ils db create`, then create indices and
the indexing queue using `ils index init` and `ils index queue init`. Never
use `ils setup` on a live instance: it drops tables and indices and clears
Redis. The upstream demo command also creates known passwords; use explicitly
fictional fixtures and separately generated credentials instead.
