SHELL := /bin/bash

REPO := $(shell pwd)
USER_SYSTEMD_DIR := $(HOME)/.config/systemd/user

AGENT_UNIT := signal-agent.service
DBUS_UNIT  := signal-cli-dbus.service

.PHONY: help
help:
	@echo "Targets:"
	@echo "  quickstart  - configure + install + start"
	@echo "  configure   - run configure.py (generate rules + units)"
	@echo "  install     - configure + install user units + daemon-reload"
	@echo "  start       - enable + start services"
	@echo "  stop        - stop services"
	@echo "  restart     - restart services"
	@echo "  status      - show status"
	@echo "  logs        - tail logs"
	@echo "  uninstall   - stop + disable + remove units + daemon-reload"
	@echo "  clean       - remove generated files in repo"
	@echo "  test        - show test-mode example command"

.PHONY: quickstart
quickstart: install start

.PHONY: configure
configure:
	python3 ./configure.py

.PHONY: install
install: configure
	mkdir -p "$(USER_SYSTEMD_DIR)"
	install -m 0644 ./systemd/$(AGENT_UNIT) "$(USER_SYSTEMD_DIR)/$(AGENT_UNIT)"
	install -m 0644 ./systemd/$(DBUS_UNIT)  "$(USER_SYSTEMD_DIR)/$(DBUS_UNIT)"
	systemctl --user daemon-reload

.PHONY: start
start:
	systemctl --user enable --now $(DBUS_UNIT)
	systemctl --user enable --now $(AGENT_UNIT)

.PHONY: stop
stop:
	-systemctl --user stop $(AGENT_UNIT)
	-systemctl --user stop $(DBUS_UNIT)

.PHONY: restart
restart:
	-systemctl --user restart $(DBUS_UNIT)
	-systemctl --user restart $(AGENT_UNIT)

.PHONY: status
status:
	@echo "== $(DBUS_UNIT) =="
	-systemctl --user status $(DBUS_UNIT) --no-pager
	@echo
	@echo "== $(AGENT_UNIT) =="
	-systemctl --user status $(AGENT_UNIT) --no-pager

.PHONY: logs
logs:
	journalctl --user -u $(AGENT_UNIT) -u $(DBUS_UNIT) -f --no-pager

.PHONY: uninstall
uninstall:
	- systemctl --user stop $(AGENT_UNIT)
	- systemctl --user stop $(DBUS_UNIT)
	- systemctl --user disable $(AGENT_UNIT)
	- systemctl --user disable $(DBUS_UNIT)
	- rm -f "$(USER_SYSTEMD_DIR)/$(AGENT_UNIT)"
	- rm -f "$(USER_SYSTEMD_DIR)/$(DBUS_UNIT)"
	systemctl --user daemon-reload

.PHONY: clean
clean:
	- rm -f ./rules.yaml
	- rm -f ./systemd/$(AGENT_UNIT)
	- rm -f ./systemd/$(DBUS_UNIT)
	# generated from rules.d/*.yaml.in
	- find ./rules.d -maxdepth 1 -type f -name "*.yaml" ! -name "*.yaml.in" -print -delete

.PHONY: test
test:
	@echo "Run a local simulation (no DBus, no Signal send):"
	@echo "  python3 signal-agent.py ./rules.yaml --test --sender \"+15551234567\" --message \"disk?\""
	@echo
	@echo "With dry-run (suppresses sending in normal mode too):"
	@echo "  python3 signal-agent.py ./rules.yaml --test --sender \"+15551234567\" --message \"disk?\" --dry-run"