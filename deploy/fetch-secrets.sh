#!/usr/bin/env bash
#
# Fetches secrets from SSM Parameter Store and writes them to
# /run/ticket-monitor/env (tmpfs — wiped on reboot, never touches disk).
#
# Ran as root by systemd's ExecStartPre before the monitor starts. The IAM
# role attached to the EC2 instance must allow ssm:GetParameter on
# /ticket-monitor/*.
#
# Parameter names (SecureString):
#   /ticket-monitor/ticketmaster-api-key
#   /ticket-monitor/telegram-bot-token
#   /ticket-monitor/telegram-chat-id
#
# Region is read from IMDSv2; override with AWS_REGION if running elsewhere.

set -euo pipefail

ENV_DIR="/run/ticket-monitor"
ENV_FILE="$ENV_DIR/env"
SERVICE_USER="ticket-monitor"
PREFIX="/ticket-monitor"

# IMDSv2 for region discovery. Honors an explicit AWS_REGION override.
if [ -z "${AWS_REGION:-}" ]; then
    TOKEN="$(curl -sS -X PUT 'http://169.254.169.254/latest/api/token' \
        -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
    if [ -n "$TOKEN" ]; then
        AWS_REGION="$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
            'http://169.254.169.254/latest/meta-data/placement/region' || true)"
    fi
fi
export AWS_REGION="${AWS_REGION:-us-east-1}"

mkdir -p "$ENV_DIR"
chmod 750 "$ENV_DIR"

get() {
    aws ssm get-parameter \
        --name "$1" \
        --with-decryption \
        --query 'Parameter.Value' \
        --output text
}

# Fail loudly if required params are missing — no silent degraded startup.
TM_KEY="$(get "$PREFIX/ticketmaster-api-key")"
TG_TOKEN="$(get "$PREFIX/telegram-bot-token")"
TG_CHAT="$(get "$PREFIX/telegram-chat-id")"

# Write atomically (temp file + mv) so a crash mid-write can't leave a
# partial env file that the service then sources.
TMP="$(mktemp "$ENV_DIR/env.XXXXXX")"
{
    printf 'TICKETMASTER_API_KEY=%s\n' "$TM_KEY"
    printf 'TELEGRAM_BOT_TOKEN=%s\n'   "$TG_TOKEN"
    printf 'TELEGRAM_CHAT_ID=%s\n'     "$TG_CHAT"
    # Headless host — no WSL, no desktop alerts.
    printf 'NOTIFY_DESKTOP=0\n'
    printf 'NOTIFY_SOUND=0\n'
} > "$TMP"

chmod 640 "$TMP"
chown root:"$SERVICE_USER" "$TMP"
mv -f "$TMP" "$ENV_FILE"
