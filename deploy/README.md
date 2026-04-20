# EC2 deployment

Runs the monitor 24/7 on a single `t4g.nano` (~$3/mo) with systemd auto-restart,
Session Manager access (no SSH), and secrets in SSM Parameter Store.

cortex-scout is skipped on EC2 — API-only mode. Telegram alerts still work.
Desktop balloon/beep/browser-open auto-disable (no PowerShell on Linux).

## Prereqs

- AWS account + CLI configured (`aws configure`)
- A VPC with a public subnet (or private + NAT). Note the VPC id + subnet id.
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) installed locally

## 1. Store secrets in SSM Parameter Store

```bash
aws ssm put-parameter --name /ticket-monitor/ticketmaster-api-key \
  --type SecureString --value "YOUR_TM_KEY" --overwrite
aws ssm put-parameter --name /ticket-monitor/telegram-bot-token \
  --type SecureString --value "YOUR_BOT_TOKEN" --overwrite
aws ssm put-parameter --name /ticket-monitor/telegram-chat-id \
  --type SecureString --value "YOUR_CHAT_ID" --overwrite
```

## 2. Deploy the stack

```bash
aws cloudformation deploy \
  --stack-name ticket-monitor \
  --template-file deploy/cloudformation.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      VpcId=vpc-xxxxxxxx \
      SubnetId=subnet-xxxxxxxx \
      GitRepoUrl=https://github.com/YOU/ticketmaster.git
```

Leave `GitRepoUrl` blank if the code isn't on GitHub — then upload it manually
via Session Manager after step 3.

## 3. Access the instance

```bash
# Get the connect command
aws cloudformation describe-stacks --stack-name ticket-monitor \
  --query 'Stacks[0].Outputs' --output table

# Interactive shell
aws ssm start-session --target i-xxxxxxxx

# Tail the service log
sudo journalctl -u ticket-monitor -f
```

## 4. (If no GitRepoUrl) upload code manually

From your laptop, after the instance is up:

```bash
INSTANCE_ID=i-xxxxxxxx
tar czf /tmp/tm.tgz -C /home/tshaller/ticketmaster \
    monitor.py config.py requirements.txt start.sh deploy
aws ssm start-session --target "$INSTANCE_ID" \
    --document-name AWS-StartNonInteractiveCommand \
    --parameters command="sudo mkdir -p /opt/ticket-monitor && sudo chown ticket-monitor:ticket-monitor /opt/ticket-monitor"
# Then copy the tarball in via `aws s3 cp` through an S3 bucket, or via the
# Session Manager port-forward + scp. Simplest is to push to GitHub.
```

## Updating the monitor

SSH in and pull:

```bash
aws ssm start-session --target i-xxxxxxxx
sudo -u ticket-monitor git -C /opt/ticket-monitor pull
sudo systemctl restart ticket-monitor
```

## Teardown

```bash
aws cloudformation delete-stack --stack-name ticket-monitor
# Manually remove SSM params if you want them gone too:
aws ssm delete-parameters --names \
  /ticket-monitor/ticketmaster-api-key \
  /ticket-monitor/telegram-bot-token \
  /ticket-monitor/telegram-chat-id
```

## File map

| File | Role |
|---|---|
| `cloudformation.yaml` | EC2 + IAM role + egress-only SG. User-data inlined. |
| `ticket-monitor.service` | systemd unit (hardened, auto-restart) |
| `fetch-secrets.sh` | `ExecStartPre` hook — pulls SSM params into `/run/ticket-monitor/env` (tmpfs) |
| `user-data.sh` | Reference copy of the user-data inlined into the CFN template |

## Troubleshooting

**Service won't start, `journalctl` shows `fetch-secrets.sh` errors**
SSM parameters missing or IAM role misconfigured. Check:
```bash
aws ssm get-parameter --name /ticket-monitor/ticketmaster-api-key --with-decryption
```

**`dnf` fails in user-data**
Instance has no internet route. Confirm the subnet has an IGW + public IP, or
a NAT gateway for private subnets.

**Monitor runs but no Telegram alerts**
`sudo systemctl status ticket-monitor` then check `journalctl -u ticket-monitor`.
Env vars are loaded from `/run/ticket-monitor/env` — verify it exists and is
populated (readable only as root or the `ticket-monitor` user).
