FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Install dependencies first (layer-cache friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-editable --no-install-project

# Copy source and install project
COPY commute/ commute/
RUN uv sync --no-dev --no-editable

EXPOSE 5000

# RTT_API_TOKEN must be injected at runtime (never bake credentials into the image)
CMD ["/app/.venv/bin/commute-web", "--host", "0.0.0.0", "--no-browser"]
