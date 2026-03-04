#!/usr/bin/env bash
set -euo pipefail

export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-/data}"
SIGNAL_CLI_CONFIG="${SIGNAL_CLI_CONFIG:-/data/signal-cli}"

mkdir -p "${XDG_CONFIG_HOME}" "${XDG_DATA_HOME}" "${SIGNAL_CLI_CONFIG}"
# Keep a predictable rules directory structure
mkdir -p "${XDG_CONFIG_HOME}/rules.d"

MODE="${1:-run}"

# If the user passes an arbitrary command (e.g. `sh -lc ...`), run it instead of
# mistakenly starting signal-cli/DBus/agent.
if [[ "${MODE}" != "run" && "${MODE}" != "link" && "${MODE}" != "sync" ]]; then
  exec "$@"
fi

case "${MODE}" in
  link)
    # Prints the provisioning URI and shows an ASCII QR when the URI appears.
    # Keeps running until linking completes (scan QR from Signal mobile app).
    signal-cli --config "${SIGNAL_CLI_CONFIG}" link -n "signal-cli-agent" 2>&1 \
      | while IFS= read -r line; do
          echo "${line}"
          if [[ "${line}" == sgnl://* ]]; then
            echo
            echo "Scan this QR in Signal -> Settings -> Linked devices -> Link new device:"
            echo "${line}" | qrencode -t utf8
            echo
          fi
        done
    ;;

  sync)
    # One-time sync after linking
    exec signal-cli --config "${SIGNAL_CLI_CONFIG}" receive
    ;;

  run)
    # Start a private SESSION bus inside the container (only needed for run mode).
    DBUS_SOCK="/tmp/dbus.sock"
    rm -f "${DBUS_SOCK}" || true
    dbus-daemon --session --address="unix:path=${DBUS_SOCK}" --fork
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${DBUS_SOCK}"

    # Start signal-cli daemon that owns org.asamk.Signal on this session bus
    signal-cli --config "${SIGNAL_CLI_CONFIG}" daemon --dbus &
    SC_PID="$!"

    # Wait until org.asamk.Signal appears on the bus
    for _ in $(seq 1 120); do
      if dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply \
          /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null \
          | grep -q "org.asamk.Signal"; then
        break
      fi
      sleep 0.25
    done

    if ! dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply \
        /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null \
        | grep -q "org.asamk.Signal"; then
      echo "ERROR: org.asamk.Signal not on DBus; signal-cli daemon likely failed to start."
      kill "${SC_PID}" 2>/dev/null || true
      exit 1
    fi

    # OPTIONAL: if SIGNAL_ACCOUNT is set, wait for the per-account object.
    # (This avoids starting the agent before the account object exists.)
    if [[ -n "${SIGNAL_ACCOUNT:-}" ]]; then
      DIGITS="${SIGNAL_ACCOUNT#+}"
      OBJ="/org/asamk/Signal/_${DIGITS}"
      echo "Waiting for account DBus object ${OBJ} ..."
      for _ in $(seq 1 120); do
        if dbus-send --session --dest=org.asamk.Signal --type=method_call --print-reply \
            "${OBJ}" org.freedesktop.DBus.Introspectable.Introspect >/dev/null 2>&1; then
          echo "Account object ready: ${OBJ}"
          break
        fi
        sleep 0.25
      done
    fi

    RULES_PATH="${XDG_CONFIG_HOME}/rules.yaml"
    if [[ ! -f "${RULES_PATH}" ]]; then
      echo "ERROR: Missing rules file: ${RULES_PATH}"
      echo
      echo "Create it by running configure.py in container mode. Example:"
      echo "  python3 /app/configure.py --mode container --config-dir ${XDG_CONFIG_HOME} --phone +15551234567"
      echo
      echo "Then re-run: docker compose up -d"
      kill "${SC_PID}" 2>/dev/null || true
      exit 2
    fi

    # Start agent (positional rules_path)
    exec python3 /app/signal-agent.py "${RULES_PATH}"
    ;;
esac
