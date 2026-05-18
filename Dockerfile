# AI Risk Gate — Docker action for GitHub
FROM python:3.11-slim

# Install git (some GH actions context wants it) + curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Trust mounted repos (git 2.35.2+ refuses cross-uid operations otherwise).
# Docker actions mount the host's checkout into the container, so the file
# owner differs from the container user. safe.directory='*' opts out.
RUN git config --global --add safe.directory '*'

WORKDIR /app

# Install Python deps (pinned for reproducibility)
RUN pip install --no-cache-dir \
    google-generativeai==0.8.3 \
    anthropic==0.39.0 \
    requests==2.32.3 \
    pydantic==2.9.2 \
    jinja2==3.1.4

COPY src/ /app/src/
COPY prompts/ /app/prompts/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

ENTRYPOINT ["python", "/app/src/main.py"]
