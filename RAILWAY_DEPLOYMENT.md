# Foslib Railway staging deployment

The application is not a standalone Python script. Configure the Railway `foslib-app` service to build using `docker/backend/Dockerfile` (build context is the repository root), rather than relying on Railpack's Python auto-detection.

The upstream Docker Compose stack also includes PostgreSQL, Redis, RabbitMQ, OpenSearch, and a separate frontend. A successful backend container build does **not** mean the full library service is operational. Provision and configure these dependencies separately, use secret environment variables, and perform schema/index/bootstrap initialization before opening the service to users.

This staging deployment must use fictional Alpha and Beta school data only. Never use the development credentials or placeholder secret in `docker-services.yml`.
