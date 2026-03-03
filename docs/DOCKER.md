# Docker

This repo supports running **signal-cli** and **signal-cli-agent** in a single Docker container with an **internal session D-Bus** (Option A).
This keeps DBus self-contained (no host DBus socket mapping) and matches the agent’s DBus-based integration with `signal-cli`.

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

## Prerequisites

- Docker Engine + Docker Compose plugin (Linux recommended)
- A Signal mobile app installed on your phone (primary device)

## Quick start

From the repo root:

```bash
mkdir -p config data

# Put your rendered rules.yaml here (agent reads /config/rules.yaml in container)
python3 configure.py
cp ./rules.yaml ./config/rules.yaml

# Build the image
docker compose -f docker/compose/docker-compose.yml build
```

## Provisioning (primary flow): link device manually (ASCII QR)

`signal-cli` must be provisioned once. The **recommended** approach is linking this instance as a **secondary device** and scanning a QR code from the Signal mobile app.

1) Generate an ASCII QR code in your terminal:

```bash
docker compose -f docker/compose/docker-compose.yml run --rm signal-agent link
```

2) On your phone:
- Signal → **Settings** → **Linked devices** → **Link new device** → scan the QR

3) (Recommended) Do a one-time sync to pull down groups/contacts after linking:

```bash
docker compose -f docker/compose/docker-compose.yml run --rm signal-agent sync
```

4) Start the stack:

```bash
docker compose -f docker/compose/docker-compose.yml up -d
docker logs -f signal-agent
```

### Where state is stored

- `./data/signal-cli` is mounted to `/data/signal-cli` in the container and holds `signal-cli` device state.
- `./config/rules.yaml` is mounted to `/config/rules.yaml` and is what the agent reads.

### Important: DBus account object path (multi-account daemon)

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

## REST API testing (optional)

If you’ve enabled the REST API in `rules.yaml` and mapped/bound it to localhost:

```bash
curl -sS http://127.0.0.1:8787/health
```

## Troubleshooting

### “signal-cli is not provisioned yet”
Run the provisioning step:

```bash
docker compose -f docker/compose/docker-compose.yml run --rm signal-agent link
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
