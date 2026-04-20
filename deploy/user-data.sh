#!/usr/bin/env bash
#
# EC2 user-data: runs once on first boot as root. Prepares the host for
# the ticket monitor. The application code itself is pulled from a git
# repo (if GIT_REPO_URL is set by cfn-init substitution) or uploaded
# separately via Session Manager / scp after provisioning.
#
# This script is idempotent enough for CFN update-stack replacements.

set -euxo pipefail

APP_DIR="/opt/ticket-monitor"
SERVICE_USER="ticket-monitor"
GIT_REPO_URL="__GIT_REPO_URL__"  # substituted by CFN !Sub

# --- Packages ---
dnf -y update
dnf -y install python3 python3-pip git awscli

# --- Service user ---
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --home-dir "$APP_DIR" --shell /sbin/nologin "$SERVICE_USER"
fi

# --- Fetch code ---
if [ -n "$GIT_REPO_URL" ] && [ "$GIT_REPO_URL" != "__GIT_REPO_URL__" ]; then
    if [ ! -d "$APP_DIR/.git" ]; then
        # Service user owns the checkout so `git pull` from a deploy script
        # run via Session Manager doesn't need sudo.
        sudo -u "$SERVICE_USER" git clone "$GIT_REPO_URL" "$APP_DIR"
    fi
fi

# --- Python venv ---
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
    sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
    sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
fi

# --- Systemd unit ---
if [ -f "$APP_DIR/deploy/ticket-monitor.service" ]; then
    install -m 644 "$APP_DIR/deploy/ticket-monitor.service" \
        /etc/systemd/system/ticket-monitor.service
    chmod +x "$APP_DIR/deploy/fetch-secrets.sh"
    systemctl daemon-reload
    systemctl enable ticket-monitor.service
    systemctl start ticket-monitor.service || true  # may fail until secrets are in SSM
fi
