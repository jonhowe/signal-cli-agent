# Docker

This repo supports running **signal-cli** and **signal-cli-agent** in a single Docker container with an **internal session D-Bus** (Option A).

This keeps DBus self-contained (no host DBus socket mapping) and matches the agent’s DBus-based integration with `signal-cli`.

---

## Architecture

```mermaid
flowchart LR
  subgraph Host
    User((User))
    Phone[Signal app on phone]
    Curl[curl / REST client]
  end

  subgraph Container["Docker container: signal-agent (Option A)"]
    DBus[(dbus-daemon session)]
    SC[signal-cli daemon\n(DBus service)]
    Agent[signal-cli-agent]
    API[REST API]
  end

  User -->|scan QR| Phone
  Curl --> API
  API --> Agent
  Agent -->|DBus calls| DBus
  SC -->|registers service| DBus
```

---

## Prerequisites

- Docker Engine + Docker Compose plugin (Linux recommended)
- A Signal mobile app installed on your phone (primary device)

---

## Quick start

From the repo root:

```bash
mkdir -p config data
```

The default compose file is [docker-compose.yml](/home/jhowe/git/signal-cli-agent/docker-compose.yml) in the repo root, so you can use plain `docker compose ...` commands without `-f`.

### Optional: local overrides

If you need machine-specific changes, create `docker-compose.override.yml` in the repo root. Docker Compose merges it automatically with the base file.

Start from the example:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```

The example file is fully commented so it is safe to copy first and customize after that.

Use overrides for things like:

- setting `SIGNAL_ACCOUNT`
- pinning a specific image tag
- changing runtime options locally without editing the tracked base compose file

### Generate initial config

The container reads configuration from `/config`, which is a bind mount of `./config`.

You need at least:

- `./config/rules.yaml`
- optionally: `./config/rules.d/*.yaml` (recommended)

#### Option A (recommended): run `configure.py` inside the container

This avoids needing Python on the host:

```bash
docker compose run --rm signal-agent \
  python3 /app/configure.py --mode container --config-dir /config --phone +1XXXXXXXXXX
```

#### Option B: run `configure.py` on the host

```bash
python3 ./configure.py --mode container --config-dir ./config --phone +1XXXXXXXXXX
```

### Build the image

```bash
docker compose build
```

---

## Provisioning (primary flow): link device manually (ASCII QR)

`signal-cli` must be provisioned once. The recommended approach is linking this instance as a **secondary device** and scanning a QR code from the Signal mobile app.

1) Generate an ASCII QR code in your terminal:

```bash
docker compose run --rm signal-agent link
```

2) On your phone:

- Signal → **Settings** → **Linked devices** → **Link new device** → scan the QR

3) (Recommended) Do a one-time sync to pull down groups/contacts after linking:

```bash
docker compose run --rm signal-agent sync
```

4) Start the stack:

```bash
docker compose up -d
docker logs -f signal-agent
```

---

## Where state is stored

- `./data/signal-cli` is mounted to `/data/signal-cli` in the container and holds `signal-cli` device state.
- `./config/rules.yaml` is mounted to `/config/rules.yaml` and is what the agent reads.
- `./config/rules.d/` is mounted to `/config/rules.d/` and holds rule files.

---

## Important: DBus account object path (multi-account daemon)

When `signal-cli` is started in **multi-account** DBus mode (daemon started **without** `-a <ACCOUNT>`),
DBus methods like `sendMessage` are exposed on an account-specific object path:

`/org/asamk/Signal/_<phonenumber-without-leading-plus>`

To ensure the agent (and the REST API service plugin) can send messages, set this in `config/rules.yaml`:

```yaml
globals:
  signal:
    account: "+15551234567"
```

If omitted, the agent falls back to `globals.admin`.

---

## REST API testing (optional)

If you’ve enabled the REST API in `rules.yaml` and bound it to localhost (default `127.0.0.1:8787`):

```bash
curl -sS http://127.0.0.1:8787/health
```

---

## Troubleshooting

### “signal-cli is not provisioned yet”

Run the provisioning step:

```bash
docker compose run --rm signal-agent link
```

### Check what’s listening

On the host:

```bash
ss -ltnp | grep ':8787' || echo "nothing listening on 8787"
```

### View container logs

```bash
docker logs -f signal-agent
```
