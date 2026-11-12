# SPDX-FileCopyrightText: 2018-2020 CERN.
# SPDX-License-Identifier: MIT

FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci --legacy-peer-deps --no-audit --no-fund
COPY ui/ ./
RUN GENERATE_SOURCEMAP=false \
    REACT_APP_INVENIO_UI_URL="" \
    REACT_APP_INVENIO_REST_ENDPOINTS_BASE_URL=/api \
    REACT_APP_ENV_NAME=staging \
    PUBLIC_URL="" npm run build

FROM ghcr.io/inveniosoftware/invenio:debian-python3.14

RUN apt-get update -y && apt-get install -y --no-install-recommends nodejs git ca-certificates && rm -rf /var/lib/apt/lists/*

RUN python -m site
RUN python -m site --user-site

# Install Invenio
ENV WORKING_DIR=/opt/invenio_app_ils
ENV INVENIO_INSTANCE_PATH=${WORKING_DIR}/var/instance
ENV INVENIO_STATIC_URL_PATH='/invenio-assets'
ENV INVENIO_STATIC_FOLDER=${INVENIO_INSTANCE_PATH}/invenio-assets

# Keep frontend-only changes out of the Python dependency/asset build cache.
RUN mkdir -p ${WORKING_DIR}/src
WORKDIR ${WORKING_DIR}/src
COPY pyproject.toml README.rst LICENSE ./
COPY invenio_app_ils/ ./invenio_app_ils/
COPY scripts/ ./scripts/

# Install/create static files
RUN mkdir -p ${INVENIO_INSTANCE_PATH}
RUN mkdir -p ${INVENIO_STATIC_FOLDER}

RUN ./scripts/bootstrap

COPY railway_wsgi.py ./railway_wsgi.py
COPY data/catalogue/ ./data/catalogue/
COPY --from=frontend /ui/build /opt/invenio_app_ils/ui

# Set folder permissions
RUN chgrp -R 0 ${WORKING_DIR} && \
    chmod -R g=u ${WORKING_DIR}

RUN chown -R invenio:root ${WORKING_DIR}
USER 1000

# Run an HTTP server directly behind Railway's HTTPS proxy.
CMD ["/bin/sh", "scripts/start-railway"]
