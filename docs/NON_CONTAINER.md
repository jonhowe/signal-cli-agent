# Non-container operation (systemd / bare-metal)

This document describes running **signal-cli-agent directly on a Linux host** (no Docker).
It is optional / legacy; the recommended deployment is Docker (see repo root `README.md` and **[DOCKER.md](DOCKER.md)**).

---

## What you need on the host

### Install `signal-cli`

Official repository: https://github.com/AsamK/signal-cli  
Official install docs: https://github.com/AsamK/signal-cli#installation

You must **register and verify** your Signal number with `signal-cli` before the agent can be used.

(Optional) Helper script:

```bash
./scripts/update-signal-cli.sh
```

### Install Python dependencies

At minimum:

- Python 3.10+
- `pydbus`
- `PyYAML`
- `python3-gi` (GLib bindings)

Example (Debian/Ubuntu-ish):

```bash
sudo apt-get update
sudo apt-get install -y python3-gi gir1.2-glib-2.0
pip3 install pydbus pyyaml
```

---

## One-command setup (systemd --user)

From the repository root:

```bash
make quickstart
```

If you prefer to run configure manually:

```bash
python3 ./configure.py --mode systemd --phone +1XXXXXXXXXX
make install
make start
```

What this does:

1) Runs `configure.py` in **systemd mode** (renders templates into repo files)  
2) Installs systemd **user** services  
3) Starts both services (signal-cli DBus daemon + agent)

Verify:

```bash
make status
make logs
```

---

## How configuration works (non-container)

In non-container mode, generated files live in the repo:

- `./rules.yaml` (rendered from `rules.yaml.in`)
- `./rules.d/*.yaml` (rendered shipped examples + your own rules)
- `./systemd/*.service` (rendered from `templates/systemd/*.service.in`)

The agent loads:

- `./rules.yaml`
- and (by default) `./rules.d/*.yaml`

---

## Boot-time startup (optional)

Systemd **user** services normally start when you log in.

To allow them to start at boot and keep running after logout:

```bash
sudo loginctl enable-linger <your-username>
```

Check:

```bash
loginctl show-user <your-username> | grep Linger
```

---

## Manual mode (no systemd)

Start the DBus daemon (session bus):

```bash
signal-cli -a +15551234567 daemon --dbus
```

Run the agent:

```bash
python3 signal-agent.py ./rules.yaml
```

---

## Test mode (no DBus, no Signal send)

Simulate a message locally:

```bash
python3 signal-agent.py ./rules.yaml \
  --test \
  --sender "+15551234567" \
  --message "! disk?"
```

Note: if you enable `globals.command_prefix`, your test message must include the prefix.

---

## Maintenance

### Uninstall services

```bash
make uninstall
```

Removes installed systemd **user** units only.

### Remove generated files

```bash
make clean
```

Removes generated configuration and rendered shipped templates only.

Your custom rules in `rules.d/` remain untouched.
