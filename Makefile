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
	@echo "  quickstart  - configure + install + start"
	@echo "  configure   - run configure.py (generate rules + units + example rule)"
	@echo "  install     - configure + install user units + daemon-reload"
	@echo "  start       - enable + start services"
	@echo "  stop        - stop services"
	@echo "  restart     - restart services"
	@echo "  status      - show status"
	@echo "  logs        - tail logs"
	@echo "  uninstall   - stop + disable + remove INSTALLED user units (no repo files touched)"
	@echo "  clean       - remove repo-generated files ONLY (keeps user rules in rules.d/)"
	@echo "  pytest      - run unit tests (pytest)"
	@echo "  test        - run agent in --test mode locally"
	@echo ""
	@echo "Test overrides:"
	@echo "  make test TEST_SENDER=+15551234567 TEST_MESSAGE='disk?'"
	@echo "  make test TEST_MESSAGE='tail journal 10'"

.PHONY: quickstart
quickstart: install start

.PHONY: configure
configure:
	python3 ./configure.py

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
COMPOSE_DEV_FILE := docker/compose/docker-compose.dev.yml

.PHONY: docker-build docker-up docker-down docker-logs docker-link docker-sync

docker-build:
	docker compose -f $(COMPOSE_FILE) build

docker-up:
	docker compose -f $(COMPOSE_FILE) up --build -d

docker-down:
	docker compose -f $(COMPOSE_FILE) down

docker-logs:
	docker logs -f signal-agent

docker-link:
	docker compose -f $(COMPOSE_FILE) run --rm signal-agent link

docker-sync:
	docker compose -f $(COMPOSE_FILE) run --rm signal-agent sync
