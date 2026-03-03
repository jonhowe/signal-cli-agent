# docker/Dockerfile
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# Default pinned version (override at build time):
#   docker build --build-arg SIGNAL_CLI_VERSION=0.14.0 -f docker/Dockerfile .
ARG SIGNAL_CLI_VERSION=0.14.0

# Core runtime deps:
# - dbus/dbus-user-session: internal session bus (Option A)
# - openjdk: safe default for signal-cli usage
# - qrencode: ASCII QR for link provisioning flow
# - tini: PID 1 + signal handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl jq \
    python3 python3-pip python3-yaml python3-gi gir1.2-glib-2.0 python3-pydbus \
    dbus dbus-user-session \
    openjdk-17-jre-headless \
    qrencode \
    tini \
  && rm -rf /var/lib/apt/lists/*

# Log latest available release tag (GitHub Releases API), then install pinned version.
RUN set -eux; \
  LATEST_TAG="$(curl -fsSL https://api.github.com/repos/AsamK/signal-cli/releases/latest | jq -r .tag_name)"; \
  echo "signal-cli latest available: ${LATEST_TAG}"; \
  echo "signal-cli pinned install:  v${SIGNAL_CLI_VERSION}"; \
  \
  curl -fsSL -o /tmp/signal-cli.tgz \
    "https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}-Linux-native.tar.gz"; \
  \
  rm -rf /tmp/signalcli && mkdir -p /tmp/signalcli; \
  tar -xzf /tmp/signal-cli.tgz -C /tmp/signalcli; \
  rm -f /tmp/signal-cli.tgz; \
  \
  # Install executable regardless of archive layout
  if [ -f /tmp/signalcli/signal-cli ] && [ -x /tmp/signalcli/signal-cli ]; then \
    install -m 0755 /tmp/signalcli/signal-cli /usr/local/bin/signal-cli; \
  elif [ -f /tmp/signalcli/signal-cli/bin/signal-cli ] && [ -x /tmp/signalcli/signal-cli/bin/signal-cli ]; then \
    install -m 0755 /tmp/signalcli/signal-cli/bin/signal-cli /usr/local/bin/signal-cli; \
  else \
    echo "ERROR: Could not locate signal-cli executable in archive"; \
    echo "Archive contents:"; \
    find /tmp/signalcli -maxdepth 4 -type f -print; \
    exit 1; \
  fi; \
  rm -rf /tmp/signalcli; \
  signal-cli --version

WORKDIR /app

# Copy repository (requirements.txt optional)
COPY . /app

# Optional Python deps (do NOT fail build if requirements.txt is absent)
RUN if [ -f /app/requirements.txt ]; then \
      pip3 install --no-cache-dir -r /app/requirements.txt; \
    fi

# Copy entrypoint to a stable absolute path expected by compose/run flows
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8787

# tini as PID1; exec entrypoint at a fixed location
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/entrypoint.sh"]