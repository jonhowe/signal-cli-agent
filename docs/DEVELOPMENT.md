# Development Guide

This document is for contributors who want to modify code, build images locally, or use the Makefile shortcuts.

If you just want to run the agent using Docker Compose + the published GHCR image, follow **README.md**.

---

## Repo layout (what’s code vs config/state)

- App code lives in the repo (and is baked into the image):
  - `signal-agent.py`, `plugins/`, `scripts/`, `templates/`, `docs/`, `docker/`

- Runtime configuration/state is mounted when running containers:
  - `./config/` → `/config`
  - `./data/` → `/data`

This separation is intentional and matches common production practices: **immutable image** + **mounted config/state**.

---

## Common Makefile flows (Docker)

These targets are convenience wrappers around `docker compose -f docker/compose/docker-compose.yml …`.

### Pull and run the published image

```bash
make docker-pull
make docker-configure PHONE=+1XXXXXXXXXX
make docker-link
make docker-sync
make docker-up
make docker-logs
```

### Rebuild locally (development)

If you’re editing the code and want your changes in the container image:

```bash
make docker-up-build
make docker-logs
```

For one-off commands that force a rebuild:

```bash
make docker-configure-build PHONE=+1XXXXXXXXXX
make docker-link-build
make docker-sync-build
```

### Stop the stack

```bash
make docker-down
```

---

## Running tests

```bash
make pytest
```

---

## Working on the container image

The Docker build uses:

- `docker/Dockerfile`

If you run into “permission denied” during builds, ensure runtime directories are excluded from the build context:

- add a `.dockerignore` that excludes `data/` and `config/`
- avoid baking tokens or runtime state into the image

---

## Publishing images to GHCR

This repo supports publishing container images to:

- `ghcr.io/jonhowe/signal-cli-agent`

Common tagging patterns:

- `:latest` for convenience
- `:vX.Y.Z` for releases
- `:sha-<commit>` for immutable pins

If a GitHub Actions workflow is configured to run on “Release published,” publishing a GitHub Release (e.g., `v0.1.0`) will automatically build and push the matching image tag.

---