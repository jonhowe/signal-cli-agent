SHELL := /bin/bash

REPO := $(shell pwd)
USER_SYSTEMD_DIR := $(HOME)/.config/systemd/user

AGENT_UNIT := signal-agent.service
DBUS_UNIT  := signal-cli-dbus.service

# Repo-owned generated artifacts (safe to delete)
GENERATED_RULES_YAML := ./rules.yaml
GENERATED_SYSTEMD_AGENT := ./systemd/$(AGENT_UNIT)
GENERATED_SYSTEMD_DBUS  := ./systemd/$(DBUS_UNIT)

# Shipped example rule output (safe to delete; user rules are NOT)
EXAMPLE_RULE := ./rules.d/example-rules.yaml

# Defaults for test target (override like: make test TEST_SENDER=... TEST_MESSAGE=...)
TEST_SENDER ?= +15551234567
TEST_MESSAGE ?= disk?

.PHONY: help
help:
	@echo "Targets:"
	@echo "  quickstart         - configure + install + start"
	@echo "  configure          - run configure.py (generate rules + units + example rule)"
	@echo "  install            - configure + install user units + daemon-reload"
	@echo "  start              - enable + start services"
	@echo "  stop               - stop services"
	@echo "  restart            - restart services"
	@echo "  status             - show status"
	@echo "  logs               - tail logs (systemd install)"
	@echo "  uninstall          - stop + disable + remove INSTALLED user units (no repo files touched)"
	@echo "  clean              - remove repo-generated files ONLY (keeps user rules in rules.d/)"
	@echo "  pytest             - run unit tests (pytest)"
	@echo "  test               - run agent in --test mode locally"
	@echo ""
	@echo "Docker Targets:"
	@echo "  docker-pull        - pull the published image from GHCR"
	@echo "  docker-build       - build container locally"
	@echo "  docker-up          - bring up container (no forced build)"
	@echo "  docker-up-pull     - pull then bring up container"
	@echo "  docker-up-build    - force rebuild then bring up container"
	@echo "  docker-down        - stop container"
	@echo "  docker-logs        - tail raw container logs"
	@echo "  docker-logs-filter - tail filtered logs (only actionable message blocks)"
	@echo "  docker-configure   - generate ./config from templates (runs configure.py inside the container; no forced build)"
	@echo "  docker-configure-build - same as docker-configure but forces image rebuild"
	@echo "  docker-link        - link device (no forced build)"
	@echo "  docker-link-build  - link device (forces image rebuild)"
	@echo "  docker-sync        - sync contacts/groups (no forced build)"
	@echo "  docker-sync-build  - sync contacts/groups (forces image rebuild)"
	@echo ""
	@echo "Test overrides:"
	@echo "  make test TEST_SENDER=+15551234567 TEST_MESSAGE='disk?'"
	@echo "  make test TEST_MESSAGE='tail journal 10'"

.PHONY: quickstart
quickstart: install start

.PHONY: configure
configure:
	python3 ./configure.py --mode systemd

.PHONY: install
install: configure
	mkdir -p "$(USER_SYSTEMD_DIR)"
	install -m 0644 "$(GENERATED_SYSTEMD_AGENT)" "$(USER_SYSTEMD_DIR)/$(AGENT_UNIT)"
	install -m 0644 "$(GENERATED_SYSTEMD_DBUS)"  "$(USER_SYSTEMD_DIR)/$(DBUS_UNIT)"
	systemctl --user daemon-reload

.PHONY: start
start:
	systemctl --user enable --now $(DBUS_UNIT)
	systemctl --user enable --now $(AGENT_UNIT)

.PHONY: stop
stop:
	- systemctl --user stop $(AGENT_UNIT)
	- systemctl --user stop $(DBUS_UNIT)

.PHONY: restart
restart:
	- systemctl --user restart $(DBUS_UNIT)
	- systemctl --user restart $(AGENT_UNIT)

.PHONY: status
status:
	@echo "== $(DBUS_UNIT) =="
	- systemctl --user status $(DBUS_UNIT) --no-pager
	@echo
	@echo "== $(AGENT_UNIT) =="
	- systemctl --user status $(AGENT_UNIT) --no-pager

.PHONY: logs
logs:
	@# Filter noisy signal-cli blocks; keep full blocks for actionable messages.
	@# Actionable = has a Body: line, and (if globals.command_prefix is set) the Body starts with that prefix.
	journalctl --user -u $(AGENT_UNIT) -u $(DBUS_UNIT) -f --no-pager -o short-iso | \
		python3 ./scripts/logs_filter.py --rules ./rules.yaml

.PHONY: uninstall
uninstall:
	@echo "Uninstalling *installed* user units only (repo files and rules.d/* are preserved)…"
	- systemctl --user stop $(AGENT_UNIT)
	- systemctl --user stop $(DBUS_UNIT)
	- systemctl --user disable $(AGENT_UNIT)
	- systemctl --user disable $(DBUS_UNIT)
	- rm -f "$(USER_SYSTEMD_DIR)/$(AGENT_UNIT)"
	- rm -f "$(USER_SYSTEMD_DIR)/$(DBUS_UNIT)"
	systemctl --user daemon-reload

.PHONY: clean
clean:
	@echo "Removing repo-generated files only (user rules in rules.d/ are preserved)…"
	- rm -f "$(GENERATED_RULES_YAML)"
	- rm -f "$(GENERATED_SYSTEMD_AGENT)"
	- rm -f "$(GENERATED_SYSTEMD_DBUS)"
	- rm -f "$(EXAMPLE_RULE)"

.PHONY: test
test:
	@echo "Running local test (no DBus, no Signal send)…"
	@echo "  sender:  $(TEST_SENDER)"
	@echo "  message: $(TEST_MESSAGE)"
	python3 ./signal-agent.py ./rules.yaml --test --sender "$(TEST_SENDER)" --message "$(TEST_MESSAGE)"

.PHONY: pytest
pytest:
	pytest -q


# ----------------------------
# Docker (Option A: internal DBus)
# ----------------------------
COMPOSE_FILE := docker/compose/docker-compose.yml
DOCKER_RULES ?= ./config/rules.yaml

.PHONY: docker-pull docker-build docker-up docker-up-pull docker-up-build docker-down docker-logs docker-logs-filter
.PHONY: docker-configure docker-configure-build docker-link docker-link-build docker-sync docker-sync-build

docker-pull:
	docker compose -f $(COMPOSE_FILE) pull

docker-build:
	docker compose -f $(COMPOSE_FILE) build

docker-up:
	docker compose -f $(COMPOSE_FILE) up -d

docker-up-pull:
	docker compose -f $(COMPOSE_FILE) pull
	docker compose -f $(COMPOSE_FILE) up -d

docker-up-build:
	docker compose -f $(COMPOSE_FILE) up -d --build

docker-down:
	docker compose -f $(COMPOSE_FILE) down

docker-logs:
	docker compose -f $(COMPOSE_FILE) logs -f --no-color signal-agent

docker-logs-filter:
	@# Show only full signal-cli message blocks where Body starts with the command prefix ("!").
	docker compose -f $(COMPOSE_FILE) logs -f --no-color signal-agent 2>&1 | \
		sed -u 's/^[^|]*| //' | \
		awk 'function flush(){ if (keep && buf!="") print buf "\n"; buf=""; keep=0 } \
		     /^Envelope from:/ { flush(); buf=$$0 ORS; next } \
		     buf!="" { buf=buf $$0 ORS; if ($$0 ~ /^[[:space:]]*Body:[[:space:]]*!/) keep=1; if ($$0=="") flush(); next } \
		     END { flush() }'

docker-configure:
	@if [ -z "$(PHONE)" ]; then \
		echo "ERROR: set PHONE=+15551234567"; \
		exit 2; \
	fi
	mkdir -p config data
	docker compose -f $(COMPOSE_FILE) run --rm signal-agent python3 /app/configure.py --mode container --config-dir /config --phone "$(PHONE)"

docker-configure-build:
	@if [ -z "$(PHONE)" ]; then \
		echo "ERROR: set PHONE=+15551234567"; \
		exit 2; \
	fi
	mkdir -p config data
	docker compose -f $(COMPOSE_FILE) run --rm --build signal-agent python3 /app/configure.py --mode container --config-dir /config --phone "$(PHONE)"

docker-link:
	docker compose -f $(COMPOSE_FILE) run --rm signal-agent link

docker-link-build:
	docker compose -f $(COMPOSE_FILE) run --rm --build signal-agent link

docker-sync:
	docker compose -f $(COMPOSE_FILE) run --rm signal-agent sync

docker-sync-build:
	docker compose -f $(COMPOSE_FILE) run --rm --build signal-agent sync