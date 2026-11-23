# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from unittest.mock import MagicMock

import pytest
import requests
from requests.adapters import HTTPAdapter

from retail_agent_demo.config import Settings
from retail_agent_demo.os_client import OSClient, _HostnameAdapter

HOST = "example.es.amazonaws.com"


def settings(**overrides) -> Settings:
    base = dict(host=HOST, local_url="https://localhost:9201", region="us-east-1", auth="basic",  # nosec B106 - test double, not a real secret
                user="admin", password="secret", verify_tls=True, rerank_region="us-west-2",  # pragma: allowlist secret
                rerank_model_arn="arn:aws:bedrock:us-west-2::foundation-model/cohere.rerank-v3-5:0",
                connector_role_arn=None)
    base.update(overrides)
    return Settings(**base)


def client(**overrides) -> OSClient:
    c = OSClient(settings(**overrides))
    c.session = MagicMock()
    return c


def response(status: int = 200, text: str = "{}") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    try:
        r.json.return_value = json.loads(text)
    except json.JSONDecodeError:
        pass  # error bodies are only ever sliced into the OSError message
    return r


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: calls.append(s))
    return calls


class TestRetries:
    @pytest.mark.parametrize("method", ["GET", "DELETE"])
    def test_reads_and_idempotent_writes_retry_after_timeouts_sleeping_one_then_two_seconds(self, sleeps, method):
        c = client()
        c.session.request.side_effect = [requests.exceptions.Timeout(), requests.exceptions.Timeout(),
                                         response(text='{"ok": true}')]
        assert c.request(method, "/demo-products/_doc/1") == {"ok": True}
        assert c.session.request.call_count == 3
        assert sleeps == [1, 2]

    def test_a_search_post_is_retried(self, sleeps):
        c = client()
        c.session.request.side_effect = [requests.exceptions.Timeout(), response()]
        assert c.post("/demo-products/_search", {"size": 1}) == {}
        assert c.session.request.call_count == 2
        assert sleeps == [1]

    def test_a_connector_creating_post_is_never_retried(self, sleeps):
        c = client()
        c.session.request.side_effect = requests.exceptions.Timeout()
        with pytest.raises(requests.exceptions.Timeout):
            c.post("/_plugins/_ml/connectors/_create", {"name": "x"})
        assert c.session.request.call_count == 1
        assert sleeps == []

    def test_a_put_is_never_retried(self, sleeps):
        c = client()
        c.session.request.side_effect = requests.exceptions.Timeout()
        with pytest.raises(requests.exceptions.Timeout):
            c.put("/demo-products", {"settings": {}})
        assert c.session.request.call_count == 1
        assert sleeps == []

    def test_after_exhausting_the_retries_the_last_exception_is_reraised(self, sleeps):
        c = client()  # retries=3, so four attempts in total
        c.session.request.side_effect = [requests.exceptions.Timeout()] * 3 + \
            [requests.exceptions.ConnectionError("last")]
        with pytest.raises(requests.exceptions.ConnectionError, match="last"):
            c.get("/demo-products/_count")
        assert c.session.request.call_count == 4
        assert sleeps == [1, 2, 3]


class TestResponses:
    def test_an_error_status_raises_oserror_shaped_so_callers_can_match_the_status(self):
        c = client()
        c.session.request.return_value = response(404, "no such index")
        with pytest.raises(OSError) as e:
            c.get("/missing")
        assert str(e.value).startswith("GET /missing -> 404:")
        assert " -> 404" in str(e.value)

    def test_a_server_error_raises_with_the_method_path_and_status(self):
        c = client()
        c.session.request.return_value = response(503, "unavailable")
        with pytest.raises(OSError) as e:
            c.put("/demo-products", {})
        assert str(e.value).startswith("PUT /demo-products -> 503:")

    def test_an_empty_body_becomes_an_empty_dict(self):
        c = client()
        c.session.request.return_value = response(200, "")
        assert c.get("/_cluster/health") == {}

    def test_a_json_body_is_returned_parsed(self):
        c = client()
        c.session.request.return_value = response(200, '{"count": 7}')
        assert c.get("/demo-products/_count") == {"count": 7}


class TestRequestShape:
    def test_a_per_call_timeout_overrides_the_default(self):
        c = client()
        c.session.request.return_value = response()
        c.post("/demo-products/_search", {}, timeout=5)
        assert c.session.request.call_args.kwargs["timeout"] == 5

    def test_the_client_timeout_is_used_when_no_timeout_is_given(self):
        c = OSClient(settings(), timeout=7)
        c.session = MagicMock()
        c.session.request.return_value = response()
        c.get("/demo-products/_count")
        assert c.session.request.call_args.kwargs["timeout"] == 7

    def test_basic_auth_sends_the_credentials_and_the_real_host_header_to_the_local_url(self):
        c = client()
        c.session.request.return_value = response()
        c.get("/x")
        assert c.session.request.call_args.args == ("GET", "https://localhost:9201/x")
        kwargs = c.session.request.call_args.kwargs
        assert kwargs["auth"] == ("admin", "secret")
        assert kwargs["headers"]["Host"] == HOST
        assert kwargs["verify"] is True

    def test_sigv4_uses_the_signed_headers_and_no_basic_auth(self, monkeypatch):
        signed = {"Host": HOST, "Authorization": "AWS4-HMAC-SHA256 sentinel", "X-Amz-Date": "x"}
        monkeypatch.setattr(OSClient, "_signed_headers", lambda self, *a, **k: signed)
        c = client(auth="sigv4")
        c.session.request.return_value = response()
        c.post("/demo-products/_search", {"size": 1})
        kwargs = c.session.request.call_args.kwargs
        assert kwargs["auth"] is None
        assert kwargs["headers"] is signed


class TestTlsGuards:
    def test_unverified_tls_is_allowed_for_a_localhost_tunnel(self):
        assert OSClient(settings(verify_tls=False, local_url="https://localhost:9201"))

    def test_unverified_tls_against_a_real_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="example.com"):
            OSClient(settings(verify_tls=False, local_url="https://example.com:9201"))

    def test_the_adapter_pins_sni_and_certificate_checks_to_the_domain_hostname(self, monkeypatch):
        captured = {}

        def capture(self, *args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(HTTPAdapter, "init_poolmanager", capture)
        _HostnameAdapter(HOST)  # HTTPAdapter.__init__ calls init_poolmanager
        assert captured["server_hostname"] == HOST
        assert captured["assert_hostname"] == HOST


ASSUME_ROLE_DENIED = ('{"error":{"reason":"Authorization error during prediction: User is not authorized to perform: '
                      'sts:AssumeRole on resource: arn:aws:iam::1:role/r"}}')


def test_a_bad_node_assume_role_denial_is_retried_and_lands_on_a_healthy_node(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: None)
    c = client()
    c.session.request.side_effect = [response(403, ASSUME_ROLE_DENIED), response(403, ASSUME_ROLE_DENIED),
                                     response(200, '{"ok": true}')]
    assert c.post("/ix/_search", {"query": {}}) == {"ok": True}
    assert c.session.request.call_count == 3


def test_assume_role_denials_stop_after_the_retry_budget_and_raise(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: None)
    c = client()
    c.session.request.return_value = response(403, ASSUME_ROLE_DENIED)
    with pytest.raises(OSError, match="-> 403"):
        c.post("/ix/_search", {"query": {}})
    assert c.session.request.call_count == 1 + c.retries


def test_an_ordinary_403_is_not_retried(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: None)
    c = client()
    c.session.request.return_value = response(403, '{"error":"no permissions for [indices:data/read/search]"}')
    with pytest.raises(OSError, match="-> 403"):
        c.post("/ix/_search", {"query": {}})
    assert c.session.request.call_count == 1


def test_a_non_idempotent_write_is_never_retried_on_assume_role_denial(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: None)
    c = client()
    c.session.request.return_value = response(403, ASSUME_ROLE_DENIED)
    with pytest.raises(OSError):
        c.post("/_plugins/_ml/connectors/_create", {})
    assert c.session.request.call_count == 1


def test_agent_execution_retries_an_assume_role_denial_but_not_a_timeout(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.os_client.pause", lambda s: None)
    c = client()
    c.session.request.side_effect = [response(403, ASSUME_ROLE_DENIED), response(200, '{"ok": true}')]
    assert c.post("/_plugins/_ml/agents/a1/_execute", {"parameters": {}}) == {"ok": True}
    c.session.request.reset_mock()
    c.session.request.side_effect = requests.exceptions.Timeout()
    with pytest.raises(requests.exceptions.Timeout):
        c.post("/_plugins/_ml/agents/a1/_execute", {"parameters": {}})
    assert c.session.request.call_count == 1  # a timed-out agent run is not repeated


def test_serverless_signs_for_aoss_with_a_payload_hash_header(monkeypatch):
    import hashlib

    from retail_agent_demo import os_client as osc

    class Creds:
        def get_frozen_credentials(self):
            from botocore.credentials import Credentials
            return Credentials("AKIAEXAMPLE", "secret")  # pragma: allowlist secret

    monkeypatch.setattr("boto3.Session", lambda: type("S", (), {"get_credentials": lambda self: Creds()})())
    c = osc.OSClient(settings(auth="sigv4", service="aoss", host="abc.aoss.us-east-1.on.aws"))
    headers = c._signed_headers("POST", "/idx/_search", '{"q": 1}')
    assert headers["x-amz-content-sha256"] == hashlib.sha256(b'{"q": 1}').hexdigest()
    assert "/aoss/aws4_request" in headers["Authorization"]
