FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
# lib-opendefence-core is a git source until it is on the index
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim AS production
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
COPY --from=build /opt/venv /opt/venv
USER 65532:65532
ENTRYPOINT ["matrix-operator"]
