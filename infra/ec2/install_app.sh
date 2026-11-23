#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Install or update the demo app on an Amazon Linux 2023 host inside the collection's VPC.
# Run as root (scripts/deploy_app_to_ec2.py sends it through SSM). Idempotent: run it again to update.
#
#   APP_TARBALL_URL   pre-signed URL of the source tarball (src/, pyproject.toml, config/ids.json)
#   ENV_PARAMETER     SSM SecureString parameter holding the app's environment file
#   AWS_REGION        Region of that parameter (default us-east-1)
#   APP_BASE, APP_SERVICE, APP_PORT, APP_RUN_DIR
#                     where it installs, the systemd service name, the loopback port and the tmpfs directory for the
#                     environment file (the defaults match scripts/deploy_app_to_ec2.py).
#   APP_ROLE_ARN      optional. The app runs as this role: an AWS CLI profile that assumes it with the instance's own
#                     credentials is written for the service (the app uses no other permission of the instance role).
#
# What it sets up: a non-root service user, a Python 3.11 venv, and a systemd service that runs Streamlit on
# 127.0.0.1:8503 ONLY. The environment file is fetched into tmpfs at every start and never stored
# on disk. Nothing listens on a routable address; reach the app through an SSM port forward.
set -euo pipefail
: "${APP_TARBALL_URL:?}" "${ENV_PARAMETER:?}"
REGION="${AWS_REGION:-us-east-1}"
BASE="${APP_BASE:-/opt/sample-agent-ret}"
SVC="${APP_SERVICE:-sample-agent-ret-app}"
PORT="${APP_PORT:-8503}"
RUN_DIR="${APP_RUN_DIR:-/run/sample-agent-ret}"

command -v python3.11 >/dev/null || dnf install -y python3.11 python3.11-pip >/dev/null
id sample-agent-ret >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /sbin/nologin sample-agent-ret
mkdir -p "$BASE" "$BASE/state"  # state/ holds the saved outputs across page refreshes and deploys (the app dir is replaced on each deploy)

# code: replace the app directory, keep the venv
rm -rf "$BASE/app.new" && mkdir "$BASE/app.new"
curl -fsSL "$APP_TARBALL_URL" | tar -xz -C "$BASE/app.new"
rm -rf "$BASE/app.old" && { [ -d "$BASE/app" ] && mv "$BASE/app" "$BASE/app.old"; true; }
mv "$BASE/app.new" "$BASE/app"

[ -d "$BASE/venv" ] || python3.11 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q "pip==25.0.1"
"$BASE/venv/bin/pip" install -q -e "$BASE/app[app]"
chown -R sample-agent-ret:sample-agent-ret "$BASE"

if [ -n "${APP_ROLE_ARN:-}" ]; then
  cat > "$BASE/aws-config" <<CONF
[profile app]
role_arn = ${APP_ROLE_ARN}
credential_source = Ec2InstanceMetadata
region = ${REGION}
CONF
  chown sample-agent-ret:sample-agent-ret "$BASE/aws-config"
fi

# the environment file is fetched at start, into tmpfs, readable by root only (systemd reads it, not the app)
cat > "$BASE/fetch-env.sh" <<FETCH
#!/usr/bin/env bash
set -euo pipefail
install -d -m 700 ${RUN_DIR}
# the previous run's environment file is still loaded into this step (EnvironmentFile), and it may name an AWS profile
# that is the app's own restricted role; read the parameter with the instance's credentials, and only replace the file
# when the read succeeded
env -u AWS_PROFILE -u AWS_CONFIG_FILE aws ssm get-parameter --region ${REGION} --name ${ENV_PARAMETER} --with-decryption --query Parameter.Value --output text > ${RUN_DIR}/env.new
chmod 600 ${RUN_DIR}/env.new
mv ${RUN_DIR}/env.new ${RUN_DIR}/env
FETCH
chmod 700 "$BASE/fetch-env.sh"

cat > /etc/systemd/system/"${SVC}".service <<UNIT
[Unit]
Description=Sample demo app ${SVC} (Streamlit, loopback only)
After=network-online.target
Wants=network-online.target

[Service]
User=sample-agent-ret
WorkingDirectory=${BASE}/app
ExecStartPre=+${BASE}/fetch-env.sh
# the leading '-' lets systemd start the fetch step first; it reads the file again for the app process
Environment=APP_STATE_DIR=${BASE}/state
EnvironmentFile=-${RUN_DIR}/env
ExecStart=${BASE}/venv/bin/streamlit run src/retail_agent_demo/app.py --server.address 127.0.0.1 --server.port ${PORT} --server.headless true --browser.gatherUsageStats false
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/_stcore/health" || true)
  [ "$code" = "200" ] && break
  sleep 2
done
echo "service: $(systemctl is-active "$SVC")  health: ${code:-none}  listening: $(ss -ltn | awk -v p=":${PORT}$" '$4 ~ p {print $4}' | tr '\n' ' ')"
[ "$code" = "200" ]
