FROM python:3.12-slim

WORKDIR /app

# git is a hard runtime dependency (app/git_store.py imports gitpython,
# which shells out to the real `git` binary) — not included in the slim
# base image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY static ./static
COPY templates ./templates

ENV PRAXIS_WORKSPACE_DIR=/data/workspace
EXPOSE 8000
# --forwarded-allow-ips='*' — trusts X-Forwarded-Proto from whatever
# reverse proxy sits in front (Traefik, in agorae's deployment), so
# request.url_for() builds https:// URLs even though Traefik itself
# terminates TLS and forwards plain HTTP to this container. Without it,
# uvicorn only trusts forwarded headers from 127.0.0.1 (its own default),
# so a redirect_uri built via url_for() came out as http://, which OIDC
# providers correctly reject as not matching the registered (https)
# redirect_uris. Safe here because this container is never reachable
# except through that reverse proxy.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
