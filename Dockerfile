FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python package
COPY . /app
RUN pip install --no-cache-dir -e ".[dev]"

# Create hermes home
RUN mkdir -p /root/.hermes

# Environment defaults (override at runtime)
ENV HERMES_HOME=/root/.hermes
ENV MEMORA_WORKSPACE=/root/hermes-workspace
ENV MEMORA_DAEMON_PORT=8742

EXPOSE 8742

CMD ["python", "-m", "memora.daemon", "--tunnel", "cloudflared"]
