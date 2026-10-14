"""Tests for mock/server.py (docs/TDD.md section 3, rules M1-M6; test list T-mock-1..4).

These tests talk to the mock directly and never touch a snippet or the network.
"""
import base64
import hashlib
import json
import sys

import pytest

pytestmark = pytest.mark.mock

PATH = "/s/{scenario}/api/v1/quantum-entropy?size={size}"
GOOD_AUTH = {"Authorization": "Bearer mock-test-token"}

# Live text (docs/POV.md, "Live API observations").
BODY_401 = '{"error":"Missing or malformed Authorization header (expected \'Bearer <token>\')"}'
BODY_400 = '{"error":"\'size\' must be an integer between 1 and 512"}'
BODY_403 = '{"error":"Forbidden or entropy quota reached"}'
BLOCK = bytes(range(256)) * 4


def get_path(mock, path):
    status, _, body = mock.request("GET", path, GOOD_AUTH)
    return status, body


def get(mock, scenario="ok", size="1", headers=GOOD_AUTH):
    return mock.request("GET", PATH.format(scenario=scenario, size=size), headers)


def test_ready_line_and_loopback_binding(mock):
    """M1: `READY <port>` is printed; the server listens on 127.0.0.1 only."""
    assert mock.ready_line == f"READY {mock.port}"
    assert mock.port > 0
    if sys.platform.startswith("linux"):
        hex_port = f"{mock.port:04X}"
        listening = [line.split()[1] for line in open("/proc/net/tcp").read().splitlines()[1:]
                     if line.split()[3] == "0A" and line.split()[1].endswith(":" + hex_port)]
        assert listening == ["0100007F:" + hex_port]


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic dXNlcjpwYXNz"},
    {"Authorization": "bearer lowercase-scheme"},
    {"Authorization": "mock-test-token"},
], ids=["missing", "bare-scheme", "empty-token", "basic", "lowercase-scheme", "no-scheme"])
def test_t_mock_1_missing_or_malformed_auth_is_401(mock, headers):
    """T-mock-1 (M2): a missing or malformed Authorization header gives the live 401 body."""
    status, hdrs, body = get(mock, headers=headers)
    assert status == 401
    assert body == BODY_401
    assert hdrs["content-type"] == "application/json"


def test_t_mock_1_auth_is_checked_before_size_and_scenario(mock):
    """M2: auth comes first, so a bad size or unknown scenario without a header is still 401."""
    for kwargs in ({"size": "0"}, {"scenario": "nope"}, {"scenario": "forbidden"}):
        status, _, body = get(mock, headers={}, **kwargs)
        assert (status, body) == (401, BODY_401)


@pytest.mark.parametrize("size", ["0", "513", "-1", "abc", "", "1.5", "1e2", "%201", "%2B1",
                                  "99999", "1%0A", "9" * 5000], ids=lambda v: v[:12] or "empty")
def test_t_mock_2_bad_size_is_400(mock, size):
    """T-mock-2 (M3): a size that is not an integer in 1..512 gives the live 400 body."""
    status, hdrs, body = get(mock, size=size)
    assert (status, body) == (400, BODY_400)
    assert hdrs["content-type"] == "application/json"


def test_t_mock_2_missing_or_repeated_size_is_400(mock):
    """M3: no `size`, or two of them, is invalid."""
    assert get_path(mock, "/s/ok/api/v1/quantum-entropy") == (400, BODY_400)
    assert get_path(mock, "/s/ok/api/v1/quantum-entropy?size=1&size=2") == (400, BODY_400)


def test_t_mock_2_size_is_checked_before_scenario(mock):
    """M3 before M4: a bad size on a failure scenario or an unknown scenario is still 400."""
    for scenario, size in (("forbidden", "0"), ("nope", "513")):
        status, _, body = get(mock, scenario, size)
        assert (status, body) == (400, BODY_400)


@pytest.mark.parametrize("size", [1, 2, 512])
def test_ok_returns_size_blocks_of_the_fixed_test_block(mock, size):
    """M4 `ok`: `random` holds `size` base64 blocks of bytes(range(256)) * 4."""
    status, hdrs, body = get(mock, "ok", str(size))
    assert status == 200
    assert hdrs["content-type"] == "application/json"
    assert hdrs["cache-control"] == "no-store"
    data = json.loads(body)
    assert list(data) == ["random", "size"]
    assert data["size"] == size
    assert len(data["random"]) == size
    assert all(base64.b64decode(item, validate=True) == BLOCK for item in data["random"])
    assert body == json.dumps(data, separators=(",", ":"))  # compact, like the live API


@pytest.mark.parametrize("scenario, status, body", [
    ("forbidden", 403, BODY_403),
    ("unauthorized", 401, BODY_401),
], ids=["forbidden", "unauthorized"])
def test_scenarios_with_live_bodies(mock, scenario, status, body):
    """M4: `forbidden` and `unauthorized` return the live text, `unauthorized` even with a
    well-formed header."""
    got = get(mock, scenario)
    assert (got[0], got[2]) == (status, body)
    assert got[1]["content-type"] == "application/json"


@pytest.mark.parametrize("scenario, status", [
    ("ratelimit", 429), ("error500", 500), ("unavailable", 503), ("teapot", 418),
])
def test_scenarios_with_illustrative_bodies(mock, scenario, status):
    """M4: the status codes; bodies are illustrative JSON with a single `error` string."""
    code, hdrs, body = get(mock, scenario)
    assert code == status
    assert hdrs["content-type"] == "application/json"
    assert list(json.loads(body)) == ["error"]
    assert "retry-after" not in hdrs


def test_redirect_is_a_302_to_the_ok_scenario(mock):
    """M4 `redirect`: 302 with a Location that points at this server's `ok` scenario."""
    status, hdrs, _ = get(mock, "redirect")
    assert status == 302
    assert hdrs["location"] == f"{mock.base}/s/ok/api/v1/quantum-entropy?size=1"
    assert get_path(mock, "/s/ok/api/v1/quantum-entropy?size=1")[0] == 200


def test_badjson_is_a_200_that_is_not_json(mock):
    """M4 `badjson`: 200 with an HTML body."""
    status, hdrs, body = get(mock, "badjson")
    assert status == 200
    assert hdrs["content-type"].startswith("text/html")
    with pytest.raises(json.JSONDecodeError):
        json.loads(body)


def test_t_mock_3_log_redacts_authorization(mock):
    """T-mock-3 (M5): the log shows `Bearer <redacted>`, never the raw token, and carries the
    token's SHA-256 so tests can check what was sent."""
    token = "raw-secret-token-DO-NOT-LOG-0123456789"
    assert get(mock, headers={"Authorization": f"Bearer {token}", "X-Probe": "1"})[0] == 200
    assert get(mock, "forbidden", headers={"Authorization": f"Bearer {token}"})[0] == 403
    assert get(mock, headers={})[0] == 401

    status, _, raw_log = mock.request("GET", "/__requests")
    assert status == 200
    assert token not in raw_log
    assert token.lower() not in raw_log.lower()
    assert base64.b64encode(token.encode()).decode() not in raw_log

    first, second, third = json.loads(raw_log)
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert first["method"] == "GET"
    assert first["path"] == "/s/ok/api/v1/quantum-entropy"
    assert first["query"] == "size=1"
    assert first["status"] == 200
    assert first["bearer_sha256"] == digest
    assert first["headers"]["authorization"] == "Bearer <redacted>"
    assert first["headers"]["x-probe"] == "1"
    assert (second["status"], second["path"]) == (403, "/s/forbidden/api/v1/quantum-entropy")
    assert second["bearer_sha256"] == digest
    assert third["status"] == 401
    assert third["headers"]["authorization"] == "<missing>"
    assert third["bearer_sha256"] is None


def test_t_mock_3_non_bearer_header_is_redacted_too(mock):
    """M5: any Authorization value is redacted, not just Bearer ones."""
    get(mock, headers={"Authorization": "Basic c2VjcmV0OnBhc3N3b3Jk"})
    _, _, raw_log = mock.request("GET", "/__requests")
    assert "c2VjcmV0OnBhc3N3b3Jk" not in raw_log
    entry = json.loads(raw_log)[0]
    assert entry["headers"]["authorization"] == "<redacted>"
    assert entry["bearer_sha256"] is None


def test_log_keeps_exact_token_whitespace_in_its_digest(mock):
    """M5: the digest covers the token exactly as sent (inner spaces count), so a test can
    tell whether a snippet trimmed."""
    get(mock, headers={"Authorization": "Bearer a b"})
    assert mock.requests()[0]["bearer_sha256"] == hashlib.sha256(b"a b").hexdigest()


def test_log_is_ordered_cleared_and_does_not_record_itself(mock):
    """M5: records are in arrival order, DELETE clears, and reads of the log are not logged."""
    get(mock, "ok")
    get(mock, "teapot")
    assert [r["status"] for r in mock.requests()] == [200, 418]
    assert [r["status"] for r in mock.requests()] == [200, 418]  # reading does not add entries
    status, _, body = mock.request("DELETE", "/__requests")
    assert (status, json.loads(body)) == (200, {"cleared": 2})
    assert mock.requests() == []


def test_t_mock_4_unknown_scenario_is_404(mock):
    """T-mock-4: a valid key on an unknown scenario returns 404, so typos fail loudly."""
    status, hdrs, body = get(mock, "typo")
    assert (status, body) == (404, '{"error":"unknown scenario"}')
    assert hdrs["content-type"] == "application/json"
    assert mock.requests()[0]["status"] == 404


def test_unknown_path_is_404(mock):
    """A path outside /s/<scenario>/api/v1/quantum-entropy gets a 404 after the auth check."""
    assert get_path(mock, "/api/v1/quantum-entropy?size=1") == (404, '{"error":"unknown path"}')
    assert mock.request("GET", "/api/v1/quantum-entropy?size=1")[0] == 401
