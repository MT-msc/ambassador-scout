# Cloud Build builds this image on every push to main; Cloud Run runs it.
FROM python:3.13-slim

# uv installs the exact versions pinned in uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .

ENV PATH="/app/.venv/bin:$PATH"
# Cloud Run provides PORT (8080 by default); app.py reads it.
CMD ["python", "app.py"]
