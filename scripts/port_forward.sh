#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Keep an SSM port forward open from localhost to the private OpenSearch Serverless collection endpoint, reconnecting if it drops.
#
# Usage: scripts/port_forward.sh <instance-id> <vpce-dns-name> [local-port]
#   e.g. scripts/port_forward.sh i-0123456789abcdef0 vpce-0abc123-xxxx.aoss-data.us-east-1.vpce.amazonaws.com 9301
#
# The instance must be an SSM-managed host inside (or routed to) the VPC of the collection endpoint. Credentials come from the
# usual AWS CLI environment, so wrap the command (for example with your SSO or role tooling) if needed.
# A session can look open yet carry no data after your laptop sleeps or your login expires: stop the
# `aws ssm start-session` process and this loop starts a fresh one within seconds.
set -u
INSTANCE="${1:?instance id}"
HOST="${2:?the collection VPC endpoint DNS name}"
PORT="${3:-9301}"
# AWS_REGION only: login tools can set AWS_DEFAULT_REGION to another Region (us-west-2), which makes
# the instance look "not connected". Set AWS_REGION to use a different Region.
REGION="${AWS_REGION:-us-east-1}"

trap 'pkill -P $$ 2>/dev/null; exit 0' INT TERM
while true; do
  echo "$(date '+%F %T') opening port forward localhost:${PORT} -> ${HOST}:443 via ${INSTANCE}"
  aws ssm start-session --region "${REGION}" --target "${INSTANCE}" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"${HOST}\"],\"portNumber\":[\"443\"],\"localPortNumber\":[\"${PORT}\"]}"
  echo "$(date '+%F %T') session ended ($?), reconnecting in 5 s"
  sleep 5
done
