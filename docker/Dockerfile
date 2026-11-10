# Linux test harness for the test-engineering-agent Python policy code and CLI.
# The repository is NOT copied into the image; it is mounted read-only at /work at run time.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
        golang-go \
        cargo \
    && rm -rf /var/lib/apt/lists/*

# uv binary from the official image (pinned tag).
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /usr/local/bin/

# Non-root user: some tests create symlinks and check permissions, which must run as a normal user.
RUN useradd --create-home --uid 1000 --shell /bin/bash tester

# Use the image's Python only; no downloads and no uv cache, so tests need no network.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    HOME=/home/tester

WORKDIR /work

USER tester

CMD ["bash", "/work/docker/run-linux-checks.sh"]
