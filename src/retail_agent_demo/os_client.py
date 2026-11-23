# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Minimal OpenSearch REST client.

Sends requests to a local URL (for example an SSM port forward) while presenting the real domain
hostname for the Host header, TLS server name (SNI), certificate verification and SigV4 signing, so
the connection is verified against the domain's certificate even though it goes through localhost.
Supports basic auth and SigV4.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter

from .config import Settings, load_settings
from .waits import pause


class _HostnameAdapter(HTTPAdapter):
    """Verify the certificate and send SNI for the domain's real hostname, not for localhost."""

    def __init__(self, hostname: str, **kwargs):
        self._hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["server_hostname"] = self._hostname
        kwargs["assert_hostname"] = self._hostname
        super().init_poolmanager(*args, **kwargs)


class OSClient:
    # POST paths that are safe to repeat if the first attempt never returned.
    _RETRYABLE_POST = ("_search", "_delete_by_query", "_mget", "_bulk", "_predict", "_deploy", "_undeploy", "/mcp")

    def __init__(self, settings: Settings | None = None, timeout: float = 30, retries: int = 3):
        """timeout: seconds to wait for a response. Requests normally finish in well under 2 s, but on the
        managed service an occasional request never returns, so fail fast and retry (reads and idempotent
        writes only) rather than wait minutes."""
        self.s = settings or load_settings()
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.mount("https://", _HostnameAdapter(self.s.host))
        if not self.s.verify_tls:
            # Unverified TLS is a local-debugging escape hatch only; refuse it for anything but a
            # localhost tunnel so it cannot silently disable verification against a real endpoint.
            local_host = urlparse(self.s.local_url).hostname or ""
            if local_host not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(
                    f"OS_VERIFY_TLS=false is only allowed when OS_LOCAL_URL points at localhost, not {local_host!r}")
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- internals ---------------------------------------------------------
    def _signed_headers(self, method: str, path: str, body: str | None, content_type: str = "application/json") -> dict:
        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        creds = boto3.Session().get_credentials().get_frozen_credentials()
        headers = {"Host": self.s.host, "Content-Type": content_type}
        if self.s.service == "aoss":
            # OpenSearch Serverless requires the payload hash as a signed header.
            headers["x-amz-content-sha256"] = hashlib.sha256((body or "").encode()).hexdigest()
        req = AWSRequest(method=method, url=f"https://{self.s.host}{path}", data=body, headers=headers)
        SigV4Auth(creds, self.s.service, self.s.region).add_auth(req)
        return dict(req.headers)

    # -- public API --------------------------------------------------------
    def request(self, method: str, path: str, body: dict | list | str | None = None,
                timeout: float | None = None, content_type: str = "application/json") -> dict:
        data = body if (body is None or isinstance(body, str)) else json.dumps(body)
        if self.s.auth == "sigv4":
            headers = self._signed_headers(method, path, data, content_type)
            auth = None
        else:
            headers = {"Host": self.s.host, "Content-Type": content_type}
            auth = (self.s.user, self.s.password)
        retryable = method in ("GET", "DELETE", "HEAD") or any(t in path for t in self._RETRYABLE_POST)
        attempts = 1 + (self.retries if retryable else 0)
        # The AssumeRole denial below happens before the request does any work, so it is also safe to repeat
        # for agent execution, whose timeouts are not (a repeat could run the agent twice).
        denial_attempts = 1 + (self.retries if (retryable or "_execute" in path) else 0)
        for attempt in range(max(attempts, denial_attempts)):
            try:
                resp = self.session.request(
                    method,
                    f"{self.s.local_url}{path}",
                    data=data,
                    headers=headers,
                    auth=auth,
                    verify=self.s.verify_tls,
                    timeout=timeout or self.timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt >= attempts - 1:
                    raise
                pause(1 + attempt)
                continue
            # A single data node intermittently fails to assume the connector role (seen after a domain
            # blue/green: a node's calls never reach Bedrock). The request is routed round-robin, so a retry
            # usually lands on a healthy node; see docs/limitations.md.
            if resp.status_code == 403 and "sts:AssumeRole" in resp.text and attempt < denial_attempts - 1:
                pause(0.5)
                continue
            break
        if resp.status_code >= 300:
            raise OSError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.text else {}

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, body: dict | list | str | None = None, timeout: float | None = None,
             content_type: str = "application/json") -> dict:
        return self.request("POST", path, body, timeout, content_type)

    def put(self, path: str, body: dict | list | None = None) -> dict:
        return self.request("PUT", path, body)
