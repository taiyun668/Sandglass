"""Deterministic HTTP fallback for one complete Sandglass v2 package.

Prefer ``sandglass user-source`` in normal use. This script validates the
complete top-level v2 shape and loopback destination, then delegates schema and
transaction authority to Sandglass preflight or commit.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


PACKAGE_FIELDS = {
    "source",
    "revision",
    "receipts",
    "accounts",
    "quotas",
    "records",
    "expected",
}


def complete_package(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("package must be an object")
    if set(value) != PACKAGE_FIELDS:
        raise ValueError("package must contain the complete v2 import fields")
    json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def loopback_base(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("endpoint must be a local HTTP base URL")
    host = parsed.hostname.lower()
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("endpoint must stay on this computer") from exc
        if not address.is_loopback:
            raise ValueError("endpoint must stay on this computer")
    return endpoint.rstrip("/")


def send(
    value: object,
    endpoint: str,
    *,
    commit: bool = False,
    replace: bool = False,
    expected_active_revision: str | None = None,
) -> dict:
    base = loopback_base(endpoint)
    payload = complete_package(value)
    action = "commit" if commit else "preflight"
    envelope: object = payload
    if commit:
        envelope = {"package": payload, "replace": replace}
        if expected_active_revision is not None:
            envelope["expected_active_revision"] = expected_active_revision
    body = json.dumps(
        envelope,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/v2/user-source-imports/{action}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def status(endpoint: str, source: str = "") -> dict:
    base = loopback_base(endpoint)
    url = base + "/api/user-source-imports"
    if source:
        from urllib.parse import quote

        url += "?source=" + quote(source, safe="")
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def rollback(
    endpoint: str,
    *,
    source: str,
    expected_active_revision: str,
    target_revision: str | None,
) -> dict:
    base = loopback_base(endpoint)
    body = json.dumps(
        {
            "source": source,
            "target_revision": target_revision,
            "expected_active_revision": expected_active_revision,
        },
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base + "/v2/user-source-imports/rollback",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _request_json(request: urllib.request.Request) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": str(exc)}
        return exc.code, payload


def probe(endpoint: str) -> dict:
    """Prove the v2 POST route exists without changing product state."""

    base = loopback_base(endpoint)
    status_url = base + "/api/user-source-imports"
    before_status, before = _request_json(urllib.request.Request(status_url))
    runtime_status, runtime = _request_json(
        urllib.request.Request(base + "/api/attribution-diagnostics")
    )
    minute_status, minutes = _request_json(
        urllib.request.Request(
            base + "/api/user-sources/official-minutes?provider=claude&limit=1"
        )
    )
    preflight_status, preflight = _request_json(
        urllib.request.Request(
            base + "/v2/user-source-imports/preflight",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    )
    after_status, after = _request_json(urllib.request.Request(status_url))
    if before_status != 200 or after_status != 200:
        raise ValueError("Sandglass v2 import status route is unavailable")
    if runtime_status != 200 or minute_status != 200:
        raise ValueError("Sandglass runtime or official-minute route is unavailable")
    if preflight_status == 404 or preflight_status >= 500:
        raise ValueError("Sandglass v2 POST preflight route is unavailable")
    if before != after:
        raise ValueError("v2 preflight probe changed product state")
    return {
        "ready": True,
        "endpoint": base,
        "contract_version": before.get("contract_version"),
        "preflight_http_status": preflight_status,
        "preflight_ready_to_commit": preflight.get("ready_to_commit"),
        "runtime_identity_available": bool(runtime),
        "official_minute_route_available": isinstance(minutes, dict),
        "state_unchanged": True,
    }


def self_test() -> None:
    sample = {
        "source": "example.local-source",
        "revision": "rev-1",
        "receipts": [{"native_shape": {"kept": True}}],
        "accounts": [],
        "quotas": [],
        "records": [],
        "expected": {
            "receipts": 1,
            "accounts": 0,
            "quotas": 0,
            "records": 0,
            "total_tokens": 0,
        },
    }
    assert complete_package(sample)["receipts"] == sample["receipts"]
    assert loopback_base("http://127.0.0.1:7740") == "http://127.0.0.1:7740"
    try:
        loopback_base("https://example.com")
    except ValueError:
        pass
    else:
        raise AssertionError("non-loopback endpoint was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", nargs="?", type=Path, help="complete v2 package JSON")
    parser.add_argument("--endpoint", default="http://127.0.0.1:7740")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--expected-active-revision")
    parser.add_argument("--status-source")
    parser.add_argument("--rollback-source")
    parser.add_argument("--target-revision")
    parser.add_argument("--deactivate", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("v2 import self-test: PASS")
        return 0
    if args.probe:
        print(json.dumps(probe(args.endpoint), ensure_ascii=False, indent=2))
        return 0
    if args.status_source is not None:
        print(
            json.dumps(status(args.endpoint, args.status_source), ensure_ascii=False, indent=2)
        )
        return 0
    if args.rollback_source:
        if not args.expected_active_revision:
            parser.error("rollback requires --expected-active-revision")
        if bool(args.target_revision) == bool(args.deactivate):
            parser.error("rollback requires exactly one of --target-revision or --deactivate")
        print(
            json.dumps(
                rollback(
                    args.endpoint,
                    source=args.rollback_source,
                    expected_active_revision=args.expected_active_revision,
                    target_revision=None if args.deactivate else args.target_revision,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.package is None:
        parser.error("package is required for preflight or commit")
    if args.replace and not args.commit:
        parser.error("--replace is valid only with --commit")
    value = json.loads(args.package.read_text(encoding="utf-8"))
    result = send(
        value,
        args.endpoint,
        commit=args.commit,
        replace=args.replace,
        expected_active_revision=args.expected_active_revision,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
