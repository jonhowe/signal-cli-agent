#!/usr/bin/env bash
set -euo pipefail

# Updates signal-cli if GitHub "latest" is newer than the installed version.
# Requires: curl, sed, sort (coreutils), tar, sudo

get_installed_version() {
  if ! command -v signal-cli >/dev/null 2>&1; then
    echo ""
    return 0
  fi

  # Typical outputs include:
  #   "signal-cli 0.13.8"
  #   "0.13.8"
  signal-cli --version 2>/dev/null \
    | head -n1 \
    | sed -E 's/^signal-cli[[:space:]]+//; s/[^0-9.].*$//'
}

get_latest_version() {
  curl -fsSL -o /dev/null -w '%{url_effective}' \
    https://github.com/AsamK/signal-cli/releases/latest \
    | sed -e 's/^.*\/v//'
}

ver_lt() {
  # returns 0 (true) if $1 < $2 using version sort
  [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" != "$2" ]] && [[ "$1" != "$2" ]]
}

installed="$(get_installed_version || true)"
latest="$(get_latest_version)"

if [[ -z "${latest}" ]]; then
  echo "Could not determine latest signal-cli version from GitHub." >&2
  exit 1
fi

if [[ -z "${installed}" ]]; then
  echo "signal-cli not found; will install latest v${latest}."
  do_update=1
elif ver_lt "${installed}" "${latest}"; then
  echo "Installed: ${installed}  ->  Latest: ${latest} (updating)"
  do_update=1
else
  echo "Installed: ${installed}  ->  Latest: ${latest} (already up to date)"
  do_update=0
fi

if [[ "${do_update}" -eq 1 ]]; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT

  tarball="signal-cli-${latest}-Linux-native.tar.gz"
  url="https://github.com/AsamK/signal-cli/releases/download/v${latest}/${tarball}"

  echo "Downloading: ${url}"
  curl -fL -o "${tmpdir}/${tarball}" "${url}"

  echo "Installing to /opt (requires sudo)..."
  sudo tar xf "${tmpdir}/${tarball}" -C /opt

  # Per your standard instructions (symlink into /usr/local/bin/)
  sudo ln -sf /opt/signal-cli /usr/local/bin/

  echo "Done. Current version:"
  signal-cli --version || true
fi