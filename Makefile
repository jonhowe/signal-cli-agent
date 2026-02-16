SHELL := /bin/bash
USER_SYSTEMD_DIR := $(HOME)/.config/systemd/user

UNITS := signal-cli-dbus.service signal-agent.service

.PHONY: help quickstart configure install start stop restart status logs uninstall clean

help:
	@echo "Targets:"
	@echo "  make quickstart  - configure, install, enable, and start services (recommended)"
	@echo "  make configure   - run configure.py to generate rules.yaml + rendered unit files"
	@echo "  make install     - copy units to $(USER_SYSTEMD_DIR), daemon-reload, enable, start"
	@echo "  make start       - start both services"
	@echo "  make stop        - stop both services"
	@echo "  make restart     - restart both services"
	@echo "  make status      - show status for both services"
	@echo "  make logs        - tail logs for both services"
	@echo "  make uninstall   - stop/disable and remove installed user units"
	@echo "  make clean       - remove generated files (rules.yaml + rendered unit files)"

quickstart: install
	@echo "Quickstart complete."

configure:
	python3 ./configure.py

install: configure
	@mkdir -p "$(USER_SYSTEMD_DIR)"
	@cp -f systemd/signal-cli-dbus.service "$(USER_SYSTEMD_DIR)/signal-cli-dbus.service"
	@cp -f systemd/signal-agent.service "$(USER_SYSTEMD_DIR)/signal-agent.service"
	@systemctl --user daemon-reload
	@systemctl --user enable $(UNITS)
	@systemctl --user restart $(UNITS)
	@echo "Installed + enabled + started user services."

start:
	systemctl --user start $(UNITS)

stop:
	systemctl --user stop signal-agent.service signal-cli-dbus.service

restart:
	systemctl --user restart $(UNITS)

status:
	@for u in $(UNITS); do \
		echo "== $$u =="; \
		systemctl --user status $$u --no-pager || true; \
		echo; \
	done

logs:
	@echo "Tailing logs (Ctrl+C to stop)…"
	journalctl --user -u signal-cli-dbus.service -f &
	journalctl --user -u signal-agent.service -f

uninstall:
	- systemctl --user stop $(UNITS)
	- systemctl --user disable $(UNITS)
	- rm -f "$(USER_SYSTEMD_DIR)/signal-agent.service" "$(USER_SYSTEMD_DIR)/signal-cli-dbus.service"
	- systemctl --user daemon-reload
	@echo "Uninstalled user services."

clean:
	rm -f rules.yaml
	rm -f systemd/signal-agent.service
	rm -f systemd/signal-cli-dbus.service
	@echo "Removed generated files."