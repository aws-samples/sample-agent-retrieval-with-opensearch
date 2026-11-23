#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Keep an SSM port forward open from localhost to the demo app running on an EC2 host, reconnecting if it drops.
#
# Usage: scripts/app_tunnel.sh <instance-id> [local-port] [remote-port]   then open http://localhost:<local-port>
# (remote-port: 8503 is the port the app listens on; local-port defaults to 8504)
#
# The app listens on the instance's loopback only (infra/ec2/install_app.sh), so this session is the only way
# in, and starting it needs ssm:StartSession on that instance. Credentials come from the usual AWS CLI
# environment (wrap the command with your login tooling). The app talks to the collection from inside the VPC, so
# there is no collection tunnel to keep alive.
set -u
INSTANCE="${1:?instance id}"
PORT="${2:-8504}"
REMOTE="${3:-8503}"
# AWS_REGION only: login tools can set AWS_DEFAULT_REGION to another Region (us-west-2), which makes
# the instance look "not connected". Set AWS_REGION to use a different Region.
REGION="${AWS_REGION:-us-east-1}"

trap 'pkill -P $$ 2>/dev/null; exit 0' INT TERM
while true; do
  echo "$(date '+%F %T') opening http://localhost:${PORT} -> ${INSTANCE}:${REMOTE}"
  aws ssm start-session --region "${REGION}" --target "${INSTANCE}" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"${REMOTE}\"],\"localPortNumber\":[\"${PORT}\"]}"
  echo "$(date '+%F %T') session ended ($?), reconnecting in 5 s"
  sleep 5
done
