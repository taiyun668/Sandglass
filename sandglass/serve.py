from __future__ import annotations

import gzip
import hashlib
import io
import ipaddress
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sandglass.accounts import load_accounts
from sandglass.attribution import known_identities as _known_identities
from sandglass.attribution import full_window_inference_allowed as _full_window_allowed
from sandglass.attribution import owns_minute as _owns_minute
from sandglass.attribution import supplemental_sources_for_account as _supplemental_sources
from sandglass.attribution import single_official_account_id as _single_account_id
from sandglass.cache import SessionCache
from sandglass.capabilities import provider_capabilities
from sandglass.collectors import collect_all
from sandglass.diagnostics import runtime_diagnostics
from sandglass.pace import annotate_windows
from sandglass.quota import (
    CACHE_TTL_SECONDS_BY_PROVIDER,
    attach_cached_quota,
    attach_live_quota,
    next_quota_fetch_at,
    quota_anchor,
    quota_anchor_source,
    refresh_quota_provider,
)
from sandglass.signals import QuotaSignalMonitor, record_quota_signal
from sandglass.models import TokenUsage, conversation_id, evidence_sources, parse_ts
from sandglass.report import build_report, parse_since
from sandglass.telemetry import (
    DecodeError,
    MAX_OTLP_BODY,
    TelemetryStore,
    candidate_user_source_ledger,
    apply_user_evidence,
    empty_otlp_response,
    ingest_otlp_logs,
    ingest_user_source_records,
    official_minute_rows,
    reconcile_telemetry_minutes,
    telemetry_status,
)
from sandglass.user_sources import (
    MAX_USER_SOURCE_BODY,
    MAX_USER_SOURCE_IMPORT_BODY,
    UserSourceStore,
)
from sandglass.resources import SOURCE_ROOT, WEB_DIR
from sandglass.runtime_provenance import (
    build_web_runtime_identity,
    record_runtime_identity,
    runtime_provenance,
)
from sandglass.product_mode import (
    attribution_mode,
    mode_payload,
    product_mode_path,
    set_attribution_mode,
)
from sandglass.paths import StateHomeAttestationError, require_canonical_state_home

MAX_USER_SOURCE_CONFIG_BODY = 32 * 1024


class _GzipBodyTooLarge(Exception):
    pass


def _bounded_gzip_decompress(payload: bytes) -> bytes:
    """Decode at most one byte beyond the accepted OTLP body size."""

    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as compressed:
        decoded = compressed.read(MAX_OTLP_BODY + 1)
    if len(decoded) > MAX_OTLP_BODY:
        raise _GzipBodyTooLarge
    return decoded


def loopback_host(value: str) -> str:
    """Accept only hosts that keep the unauthenticated dashboard on this PC."""

    host = str(value or "").strip()
    if host.lower() == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Sandglass only binds to localhost or an IPv4 loopback address") from exc
    if address.version != 4 or not address.is_loopback:
        raise ValueError("Sandglass only binds to localhost or an IPv4 loopback address")
    return host


def _window_index(payload: object, project) -> dict[str, object]:
    out: dict[str, object] = {}
    rows = payload.get("accounts", []) if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        for window in row.get("windows") or []:
            if isinstance(window, dict):
                out[f"{row.get('account_id')}|{window.get('label')}"] = project(window)
    return out


def _note_answer(kind: str, payload: object) -> object:
    """Write down what we just answered, then answer it.

    The panel is reconciled against an independent recomputation, and it cannot
    be reached to be asked: the native shell serves it over an in-process bridge
    with no socket. Recording the answer as it is returned keeps that check
    possible in every panel mode without opening a port that serves account data.
    """
    from sandglass import live_snapshot

    if kind == "local_windows":
        value = _window_index(payload, lambda w: {
            "total": int((w.get("usage") or {}).get("total_tokens") or 0),
            # The span the panel says it counted over. Reconciling against the
            # quota window instead compares two different spans and calls the
            # difference a discrepancy: a 5h boundary is re-derived from the
            # local timeline when the official one has expired.
            "from": w.get("counted_from") or "",
            "to": w.get("counted_to") or "",
            "boundary": w.get("boundary_source") or "",
            # Per day, so a reconciliation can compare the span where
            # attribution is actually promised instead of a window total that
            # mixes it with pre-intervention minutes nobody can attribute.
            "days": {str(d.get("day")): int(d.get("spent") or 0)
                     for d in (w.get("days") or []) if isinstance(d, dict)},
        })
    elif kind == "quota_windows":
        value = _window_index(
            payload, lambda w: [w.get("window_start"), w.get("resets_at")]
        )
    else:
        value = payload
    live_snapshot.record(kind, value)
    return payload


def api_payload(target: str, *, since: str | None = None,
                 live_quota: bool = True,
                 telemetry_receiver: dict[str, object] | None = None,
                 apply_supported: bool = False) -> object:
    """Return one dashboard API payload without requiring an HTTP transport."""
    parsed = urlparse(target)
    if parsed.path == "/api/report":
        query = parse_qs(parsed.query)
        selected = query.get("since", [since])[0]
        return _cached_report(selected, live_quota=live_quota)
    if parsed.path == "/api/quota":
        return _note_answer("quota_windows", _quota_payload(live=live_quota))
    if parsed.path == "/api/local-windows":
        return _note_answer("local_windows", _local_windows_payload(live=live_quota))
    if parsed.path == "/api/telemetry-status":
        return telemetry_status(receiver=telemetry_receiver)
    if parsed.path == "/api/user-sources":
        # A raw inbox entry needs no comparison against provider histories.
        # Avoid a cold full parse unless normalized telemetry actually exists.
        sessions = []
        if TelemetryStore().records():
            cache = SessionCache()
            try:
                sessions = collect_all(cache=cache)
            finally:
                cache.close()
        return _user_sources_payload(sessions)
    if parsed.path == "/api/user-source-imports":
        query = parse_qs(parsed.query)
        return UserSourceStore().import_status(
            str(query.get("source", [""])[0] or "").strip()
        )
    if parsed.path == "/api/model-api-metering":
        return UserSourceStore().metering()
    if parsed.path == "/api/user-sources/official-minutes":
        query = parse_qs(parsed.query)
        provider = str(query.get("provider", [""])[0] or "").strip()
        if not provider:
            raise ValueError("provider is required")
        try:
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["1000"])[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        cache = SessionCache()
        try:
            rows = official_minute_rows(
                collect_all(cache=cache),
                provider=provider,
                since=query.get("since", [None])[0],
                until=query.get("until", [None])[0],
            )
        finally:
            cache.close()
        page = rows[offset : offset + limit]
        return {
            "authority": "official_builtin",
            "provider": provider,
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "next_offset": offset + len(page)
            if offset + len(page) < len(rows)
            else None,
            "records": page,
        }
    if parsed.path == "/api/runtime-diagnostics":
        return runtime_diagnostics()
    if parsed.path == "/api/observer-status":
        from sandglass.observer import observer_status

        return observer_status()
    if parsed.path == "/api/attribution-diagnostics":
        return _note_answer("attribution_diagnostics", _attribution_diagnostics())
    if parsed.path == "/api/update":
        from sandglass.update import available_update

        offer = available_update(force="force" in parse_qs(parsed.query))
        # Keep capability separate from the offer.  A standalone ``serve``
        # process can display updates but has no desktop shell to quit before
        # the installer replaces files, so it must never expose apply.
        payload = dict(offer) if isinstance(offer, dict) else {}
        payload["apply_supported"] = bool(apply_supported)
        return payload
    if parsed.path == "/api/update/announcement":
        from sandglass.update import update_announcement

        return update_announcement()
    if parsed.path == "/api/product-mode":
        return _note_answer("product_mode", mode_payload())
    raise KeyError(parsed.path)


def _default_source_setting() -> dict[str, object]:
    return {
        "display_enabled": False,
        "accounts_enabled": True,
        "identity_enabled": False,
        "mapped_provider": "",
        "mapped_account_id": "",
        "totals_enabled": False,
        "full_window_enabled": False,
        "updated_at": "",
    }


def _source_stage(setting: dict[str, object]) -> str:
    if setting.get("full_window_enabled"):
        return "full_window_enabled"
    if setting.get("totals_enabled"):
        return "totals_included"
    if setting.get("identity_enabled") or setting.get("mapped_account_id"):
        return "account_mapped"
    return "displayed" if setting.get("display_enabled") else "stored_only"


def _user_sources_payload(sessions) -> dict[str, object]:
    store = UserSourceStore()
    inbox = store.mirror()
    import_status = {
        item["source"]: item
        for item in store.import_status().get("sources", [])
    }
    shadow = reconcile_telemetry_minutes(sessions)
    candidate_ledger = candidate_user_source_ledger(
        sessions,
        settings=store.settings(),
    )
    candidate_by_source = {
        str(row["source_id"]): row for row in candidate_ledger["sources"]
    }
    sources = {str(row["source"]): dict(row) for row in inbox["sources"]}
    settings = store.settings()
    accounts_by_source: dict[str, list[dict[str, object]]] = {}
    for account in store.accounts():
        accounts_by_source.setdefault(str(account.get("source") or ""), []).append(
            {
                "provider": str(account.get("provider") or ""),
                "account_id": str(account.get("account_id") or ""),
                "label": str(account.get("label") or ""),
            }
        )
    quota_count_by_source: dict[str, int] = {}
    for quota in store.quotas():
        quota_source = str(quota.get("source") or "")
        quota_count_by_source[quota_source] = quota_count_by_source.get(quota_source, 0) + 1
    for normalized in shadow.get("by_source", []):
        source = str(normalized.get("source_id") or "")
        if not source:
            continue
        row = sources.setdefault(
            source,
            {
                "source": source,
                "receipts": 0,
                "recognized_fields": [],
                "unknown_fields": [],
                "first_received_at": "",
                "last_received_at": "",
                "state": "normalized_only",
            },
        )
        row["normalized_shadow"] = {
            key: value
            for key, value in normalized.items()
            if key not in {"matches", "source", "source_id"}
        }
    for source, row in sources.items():
        revision_state = import_status.get(source)
        if revision_state:
            active = next(
                (
                    item
                    for item in revision_state.get("revisions", [])
                    if item.get("active")
                ),
                None,
            )
            row["import_revision"] = {
                "contract_version": 2,
                "active_revision": revision_state.get("active_revision"),
                "active_package_sha256": (active or {}).get("package_sha256", ""),
                "retained_revisions": len(revision_state.get("revisions", [])),
            }
        setting = dict(settings.get(source) or row.get("settings") or _default_source_setting())
        row["settings"] = setting
        row["product_use_stage"] = _source_stage(setting)
        normalized = row.get("normalized_shadow") or {}
        candidate = candidate_by_source.get(source) or {}
        row["candidate_accounting"] = candidate
        row["stages"] = {
            "display": {"available": True, "enabled": bool(setting["display_enabled"])},
            "account_discovery": {
                "available": bool(accounts_by_source.get(source)),
                "enabled": bool(setting.get("accounts_enabled", True)),
                "accounts": accounts_by_source.get(source, []),
                "count": len(accounts_by_source.get(source, [])),
                "quota_count": quota_count_by_source.get(source, 0),
            },
            "account_mapping": {
                "available": True,
                "enabled": bool(
                    setting.get("identity_enabled")
                    or setting["mapped_account_id"]
                ),
                "declared_identity_available": bool(
                    int(normalized.get("attributed_records") or 0)
                ),
                "declared_identity_enabled": bool(
                    setting.get("identity_enabled")
                ),
                "identity_candidate_minutes": int(
                    candidate.get("identity_candidate_minutes") or 0
                ),
                "identity_candidate_tokens": int(
                    candidate.get("identity_candidate_tokens") or 0
                ),
                "applies_exact_identity": bool(
                    (
                        setting.get("identity_enabled")
                        or setting["mapped_account_id"]
                    )
                    and int(candidate.get("identity_enabled_minutes") or 0)
                ),
                "identity_enabled_minutes": int(
                    candidate.get("identity_enabled_minutes") or 0
                ),
            },
            "totals": {
                "available": bool(
                    setting.get("totals_enabled")
                    or int(candidate.get("token_candidate_minutes") or 0)
                ),
                "enabled": bool(setting.get("totals_enabled")),
                "normalized_records": int(normalized.get("normalized_records") or 0),
                "exact_matches": int(normalized.get("exact_matches") or 0),
                "unmatched_records": int(normalized.get("unmatched_records") or 0),
                "token_mismatches": int(normalized.get("token_mismatches") or 0),
                "account_conflicts": int(normalized.get("account_conflicts") or 0),
                "candidate_minutes": int(candidate.get("token_candidate_minutes") or 0),
                "candidate_tokens": int(candidate.get("token_candidate_tokens") or 0),
                "included_minutes": int(candidate.get("token_enabled_minutes") or 0),
                "included_tokens": int(candidate.get("token_enabled_tokens") or 0),
                "blocked_minutes": int(candidate.get("blocked_source_minutes") or 0),
            },
            "full_window": {
                "available": bool(
                    setting.get("full_window_enabled")
                    or (
                        (
                            setting.get("mapped_account_id")
                            or setting.get("identity_enabled")
                        )
                        and (
                            int(candidate.get("identity_enabled_minutes") or 0)
                            or (
                                setting.get("totals_enabled")
                                and int(candidate.get("token_enabled_minutes") or 0)
                            )
                        )
                    )
                ),
                "enabled": bool(setting.get("full_window_enabled")),
                "depends_on_sources": [source]
                if setting.get("full_window_enabled")
                else [],
            },
        }
    inbox["sources"] = sorted(sources.values(), key=lambda row: str(row["source"]))
    return {
        "inbox": inbox,
        "normalized_shadow": shadow,
        "candidate_ledger": candidate_ledger,
    }


def configure_user_source(envelope: object) -> dict[str, object]:
    """Apply reversible display, identity, or Token choices for one source."""
    if not isinstance(envelope, dict):
        raise ValueError("user source configuration must be an object")
    allowed = {
        "source",
        "display_enabled",
        "accounts_enabled",
        "identity_enabled",
        "mapped_provider",
        "mapped_account_id",
        "totals_enabled",
        "full_window_enabled",
    }
    if set(envelope) - allowed:
        raise ValueError("unsupported user source configuration field")
    source = str(envelope.get("source") or "").strip()
    store = UserSourceStore()
    raw_sources = {str(row["source"]) for row in store.mirror()["sources"]}
    normalized_sources = {
        str(row.get("source") or "").removeprefix("user_adapter:")
        for row in TelemetryStore().records()
        if str(row.get("source") or "").startswith("user_adapter:")
    }
    if source not in raw_sources | normalized_sources:
        raise ValueError("unknown user source")

    display = envelope.get("display_enabled") if "display_enabled" in envelope else None
    accounts = envelope.get("accounts_enabled") if "accounts_enabled" in envelope else None
    identity = (
        envelope.get("identity_enabled") if "identity_enabled" in envelope else None
    )
    provider = envelope.get("mapped_provider") if "mapped_provider" in envelope else None
    account_id = envelope.get("mapped_account_id") if "mapped_account_id" in envelope else None
    totals = envelope.get("totals_enabled") if "totals_enabled" in envelope else None
    full_window = (
        envelope.get("full_window_enabled")
        if "full_window_enabled" in envelope
        else None
    )
    if (provider is None) != (account_id is None):
        raise ValueError("mapped provider and account must be changed together")
    if provider is not None:
        provider = str(provider).strip()
        account_id = str(account_id).strip()
        if provider or account_id:
            discovered = {
                (account.provider, account.account_id) for account in load_accounts()
            }
            if (provider, account_id) not in discovered:
                raise ValueError("mapped account is not currently discoverable")
    current = store.settings().get(source) or _default_source_setting()
    if accounts is not None and not isinstance(accounts, bool):
        raise ValueError("accounts_enabled must be a boolean")
    if accounts is False:
        # Revoking adapter account discovery is immediate and reversible. Any
        # per-record identity that depends on those accounts must stop with it.
        identity = False
        if current.get("identity_enabled") and not current.get("mapped_account_id"):
            full_window = False
    selected_identity = (
        current.get("identity_enabled", False) if identity is None else identity
    )
    selected_mapped_account = (
        current.get("mapped_account_id", "") if account_id is None else account_id
    )
    if selected_identity and selected_mapped_account:
        raise ValueError(
            "choose verified record identities or one whole-source account mapping"
        )
    source_row: dict[str, object] = {}
    needs_ledger = bool(
        accounts is not None
        or identity is not None
        or totals is True
        or full_window is True
        or (
            current.get("full_window_enabled")
            and (totals is False or provider is not None)
        )
    )
    if needs_ledger:
        proposed = dict(store.settings())
        proposed_setting = dict(proposed.get(source) or _default_source_setting())
        if totals is not None:
            proposed_setting["totals_enabled"] = totals
        if accounts is not None:
            proposed_setting["accounts_enabled"] = accounts
        if identity is not None:
            proposed_setting["identity_enabled"] = identity
        if full_window is not None:
            proposed_setting["full_window_enabled"] = full_window
        if provider is not None:
            proposed_setting["mapped_provider"] = provider
            proposed_setting["mapped_account_id"] = account_id
        if proposed_setting.get("identity_enabled") and proposed_setting.get(
            "mapped_account_id"
        ):
            raise ValueError(
                "choose verified record identities or one whole-source account mapping"
            )
        proposed[source] = proposed_setting
        cache = SessionCache()
        try:
            official_sessions = collect_all(cache=cache)
        finally:
            cache.close()
        ledger = candidate_user_source_ledger(
            official_sessions,
            settings=proposed,
        )
        source_row = next(
            (row for row in ledger["sources"] if row["source_id"] == source),
            {},
        )
        if identity is True:
            declared = {
                (str(row.get("provider") or ""), str(row.get("account_id") or ""))
                for row in TelemetryStore().records()
                if str(row.get("source") or "") == f"user_adapter:{source}"
                and str(row.get("account_id") or "")
            }
            discovered = {
                (account.provider, account.account_id) for account in load_accounts()
            }
            if proposed_setting.get("accounts_enabled", True):
                discovered.update(
                    (str(row.get("provider") or ""), str(row.get("account_id") or ""))
                    for row in store.accounts()
                    if str(row.get("source") or "") == source
                )
            if not declared:
                raise ValueError("user source has no declared record identities")
            if not proposed_setting.get("accounts_enabled", True):
                raise ValueError("enable discovered accounts before record identities")
            if declared - discovered:
                raise ValueError(
                    "user source contains identities that are not currently discoverable"
                )
            if int(source_row.get("identity_candidate_minutes") or 0) <= 0:
                raise ValueError("user source has no exact declared identity candidates")
        if (
            totals is True
            and int(source_row.get("token_candidate_minutes") or 0) <= 0
        ):
            raise ValueError("user source has no unambiguous Token candidates")
        if full_window is True:
            mapped = bool(
                proposed_setting.get("identity_enabled")
                or (
                    proposed_setting.get("mapped_provider")
                    and proposed_setting.get("mapped_account_id")
                )
            )
            contributes = bool(
                int(source_row.get("identity_enabled_minutes") or 0)
                or (
                    proposed_setting.get("totals_enabled")
                    and int(source_row.get("token_enabled_minutes") or 0)
                )
            )
            if not mapped or not contributes:
                raise ValueError(
                    "full-window inference requires enabled verified identity "
                    "and included evidence"
                )
    if totals is False and current.get("full_window_enabled"):
        if int(source_row.get("identity_enabled_minutes") or 0) <= 0:
            full_window = False
    if (
        provider is not None
        and current.get("full_window_enabled")
        and full_window is None
    ):
        still_contributes = bool(
            int(source_row.get("identity_enabled_minutes") or 0)
            or (
                (totals if totals is not None else current.get("totals_enabled"))
                and int(source_row.get("token_enabled_minutes") or 0)
            )
        )
        if not still_contributes:
            full_window = False
    if provider == "" and account_id == "" and current.get("full_window_enabled"):
        if not (identity if identity is not None else current.get("identity_enabled")):
            full_window = False
    if identity is False and current.get("full_window_enabled"):
        if not (provider if provider is not None else current.get("mapped_provider")):
            full_window = False
    setting = store.configure(
        source,
        display_enabled=display,
        accounts_enabled=accounts,
        identity_enabled=identity,
        mapped_provider=provider,
        mapped_account_id=account_id,
        totals_enabled=totals,
        full_window_enabled=full_window,
    )
    return {
        "ok": True,
        "setting": setting,
        "product_use_stage": _source_stage(setting),
    }


def reset_user_source(envelope: object) -> dict[str, object]:
    """Reset one disabled import while leaving every native source untouched."""

    if not isinstance(envelope, dict):
        raise ValueError("user source reset must be an object")
    if set(envelope) - {"source", "confirm"}:
        raise ValueError("unsupported user source reset field")
    source = str(envelope.get("source") or "").strip()
    if envelope.get("confirm") != source:
        raise ValueError("confirm must exactly match the source id")
    store = UserSourceStore()
    known = {str(row["source"]) for row in store.mirror()["sources"]}
    known.update(
        str(row.get("source") or "").removeprefix("user_adapter:")
        for row in TelemetryStore().records()
        if str(row.get("source") or "").startswith("user_adapter:")
    )
    if source not in known:
        raise ValueError("unknown user source")
    current = store.settings().get(source, {})
    if any(
        bool(current.get(field))
        for field in (
            "display_enabled",
            "accounts_enabled",
            "identity_enabled",
            "mapped_provider",
            "mapped_account_id",
            "totals_enabled",
            "full_window_enabled",
        )
    ):
        raise ValueError("disable every source control before resetting an import")
    normalized = TelemetryStore().reset_user_source(source)
    removed = store.reset(source)
    return {
        "ok": True,
        "source": source,
        "removed": {"normalized_records": normalized, **removed},
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, since: str | None = None, live_quota: bool = True,
                 allow_otlp: bool = True, telemetry_provider_enabled=None,
                 telemetry_receiver=None, telemetry_apply=None,
                 update_apply=None, **kwargs):
        self._since = since
        self._live_quota = live_quota
        self._allow_otlp = allow_otlp
        self._telemetry_provider_enabled = telemetry_provider_enabled
        self._telemetry_receiver = telemetry_receiver
        self._telemetry_apply = telemetry_apply
        self._update_apply = update_apply
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                self.send_error(403)
                return
        except ValueError:
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        try:
            payload = api_payload(
                self.path, since=self._since, live_quota=self._live_quota,
                telemetry_receiver=(self._telemetry_receiver()
                                    if self._telemetry_receiver else None),
                apply_supported=self._update_apply is not None,
            )
        except ValueError as exc:
            self.send_error(400, str(exc))
            return
        except KeyError:
            pass
        else:
            self._send_json(payload)
            return
        super().do_GET()

    # Set once this request's body has been consumed, so an error response
    # does not try to read a body that is already gone.
    _body_consumed = False

    def _read_body(self, length: int) -> bytes:
        self._body_consumed = True
        return self.rfile.read(length)

    def send_error(self, code, message=None, explain=None):  # noqa: A003
        """Read the body the client already sent, then refuse.

        Closing a socket that still has unread bytes in its receive buffer is
        an abortive close on Windows: the peer's next read fails with
        WSAECONNABORTED (10053) instead of seeing the status just written. Every
        refusal here happens before the body is read -- wrong content type, no
        length, path not served -- so every one of them could abort the
        connection rather than answer it.

        Measured on this handler: a 4 MiB body to a path it answers 404 came
        back as ConnectionAbortedError 9 times in 10, 1 MiB 3 times in 10, and
        small bodies almost always got through. That last part is why the suite
        failure looked intermittent and unrelated to the code it was in.

        A body larger than the most this server would ever read is not drained;
        that connection is allowed to die, which is the honest outcome of
        refusing to read it.
        """
        if not self._body_consumed:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except (TypeError, ValueError):
                length = 0
            if 0 < length <= MAX_OTLP_BODY:
                self._body_consumed = True
                remaining = length
                try:
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 1 << 16))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except OSError:
                    pass
        super().send_error(code, message, explain)

    def _read_json_body(self, limit: int) -> str | None:
        """The request body as text, or None after answering with the error."""
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_error(415, "JSON required")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(411)
            return None
        if length < 0 or length > limit:
            self.send_error(413)
            return None
        try:
            return self._read_body(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            self.send_error(400, str(exc))
            return None

    def do_POST(self) -> None:  # noqa: N802
        self._body_consumed = False
        parsed = urlparse(self.path)
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                self.send_error(403)
                return
        except ValueError:
            self.send_error(403)
            return
        if parsed.path == "/api/update/apply":
            if self._update_apply is None:
                self.send_error(404)
                return
            body = self._read_json_body(MAX_USER_SOURCE_CONFIG_BODY)
            if body is None:
                return
            try:
                self._send_json(self._update_apply(body))
            except ValueError as exc:
                self.send_error(400, str(exc))
            return
        if parsed.path == "/api/update/announcement/dismiss":
            from sandglass.update import dismiss_update_announcement

            body = self._read_json_body(MAX_USER_SOURCE_CONFIG_BODY)
            if body is None:
                return
            try:
                envelope = json.loads(body)
                version = envelope.get("version") if isinstance(envelope, dict) else None
                if not isinstance(version, str):
                    raise ValueError("announcement dismissal requires a version")
                self._send_json(dismiss_update_announcement(version))
            except (json.JSONDecodeError, ValueError) as exc:
                self.send_error(400, str(exc))
            return
        if parsed.path == "/api/telemetry-receiver":
            # Only where a receiver was handed in. Without one the panel is told
            # the receiver is not manageable and never draws the switch, so a
            # 404 here is the honest answer rather than a dead control.
            if self._telemetry_apply is None:
                self.send_error(404)
                return
            body = self._read_json_body(MAX_USER_SOURCE_CONFIG_BODY)
            if body is None:
                return
            try:
                self._send_json(self._telemetry_apply(body))
            except ValueError as exc:
                self.send_error(400, str(exc))
            return
        if parsed.path == "/api/user-sources/configure":
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "JSON required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411)
                return
            if length < 0 or length > MAX_USER_SOURCE_CONFIG_BODY:
                self.send_error(413)
                return
            try:
                envelope = json.loads(self._read_body(length).decode("utf-8"))
                result = configure_user_source(envelope)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self.send_error(400, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/user-sources/reset":
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "JSON required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411)
                return
            if length < 0 or length > MAX_USER_SOURCE_CONFIG_BODY:
                self.send_error(413)
                return
            try:
                envelope = json.loads(self._read_body(length).decode("utf-8"))
                result = reset_user_source(envelope)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self.send_error(400, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/product-mode":
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "JSON required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411)
                return
            if length < 0 or length > MAX_USER_SOURCE_CONFIG_BODY:
                self.send_error(413)
                return
            try:
                envelope = json.loads(self._read_body(length).decode("utf-8"))
                result = set_attribution_mode(str(envelope.get("attribution_mode") or ""))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self.send_error(400, str(exc))
                return
            self._send_json(result)
            return
        if not self._allow_otlp or parsed.path not in {
            "/v1/logs",
            "/v1/user-sources",
            "/v1/user-sources/accounts",
            "/v1/user-sources/quotas",
            "/v1/user-sources/records",
            "/v2/user-source-imports/preflight",
            "/v2/user-source-imports/commit",
            "/v2/user-source-imports/rollback",
            "/v3/user-source-imports/preflight",
            "/v3/user-source-imports/commit",
            "/v3/user-source-imports/rollback",
        }:
            self.send_error(404)
            return
        if parsed.path in {
            "/v1/user-sources",
            "/v1/user-sources/accounts",
            "/v1/user-sources/quotas",
            "/v1/user-sources/records",
            "/v2/user-source-imports/preflight",
            "/v2/user-source-imports/commit",
            "/v2/user-source-imports/rollback",
            "/v3/user-source-imports/preflight",
            "/v3/user-source-imports/commit",
            "/v3/user-source-imports/rollback",
        }:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "JSON required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411)
                return
            body_limit = (
                MAX_USER_SOURCE_IMPORT_BODY
                if parsed.path.startswith(
                    ("/v2/user-source-imports/", "/v3/user-source-imports/")
                )
                else MAX_USER_SOURCE_BODY
            )
            if length < 0 or length > body_limit:
                self.send_error(413)
                return
            try:
                envelope = json.loads(self._read_body(length).decode("utf-8"))
                if parsed.path.endswith("/user-source-imports/preflight"):
                    result = UserSourceStore().preflight_import(envelope)
                elif parsed.path.endswith("/user-source-imports/commit"):
                    result = UserSourceStore().commit_import(envelope)
                elif parsed.path.endswith("/user-source-imports/rollback"):
                    result = UserSourceStore().rollback_import(envelope)
                elif parsed.path == "/v1/user-sources":
                    result = UserSourceStore().append(envelope)
                else:
                    source = (
                        str(envelope.get("source") or "").strip()
                        if isinstance(envelope, dict)
                        else ""
                    )
                    known_sources = {
                        str(row["source"])
                        for row in UserSourceStore().mirror()["sources"]
                    }
                    validate_only = bool(
                        parsed.path == "/v1/user-sources/records"
                        and isinstance(envelope, dict)
                        and envelope.get("validate_only") is True
                    )
                    if source not in known_sources and not validate_only:
                        raise ValueError(
                            "preserve this source's native payload before adaptation"
                        )
                    if parsed.path == "/v1/user-sources/accounts":
                        result = UserSourceStore().append_accounts(envelope)
                    elif parsed.path == "/v1/user-sources/quotas":
                        result = UserSourceStore().append_quotas(envelope)
                    else:
                        result = ingest_user_source_records(envelope)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self.send_error(400, str(exc))
                return
            self._send_json(result)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"application/x-protobuf", "application/protobuf", "application/octet-stream"}:
            self.send_error(415, "OTLP/HTTP protobuf required")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(411)
            return
        if length < 0 or length > MAX_OTLP_BODY:
            self.send_error(413)
            return
        payload = self._read_body(length)
        content_encoding = self.headers.get("Content-Encoding", "").strip().lower()
        if content_encoding not in {"", "identity", "gzip"}:
            self.send_error(415, "unsupported content encoding")
            return
        if content_encoding == "gzip":
            try:
                payload = _bounded_gzip_decompress(payload)
            except _GzipBodyTooLarge:
                self.send_error(413)
                return
            except (OSError, EOFError):
                self.send_error(400, "invalid gzip body")
                return
        try:
            ingest_otlp_logs(
                payload,
                allowed_providers=self._telemetry_provider_enabled,
            )
        except DecodeError:
            self.send_error(400, "invalid OTLP protobuf")
            return
        data = empty_otlp_response()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)


class OtlpHandler(Handler):
    """Loopback receiver that exposes no dashboard files or account APIs."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_error(404)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        """Ingest only.

        do_GET was overridden here and do_POST was not, and the /api routes are
        matched before the OTLP gate -- so this listener, whose entire reason
        for existing is to be separate from the panel, accepted posts that
        reconfigure user sources, reset them, and change the attribution mode.
        The class said it exposed no account APIs the whole time.
        """
        if not urlparse(self.path).path.startswith("/v1/"):
            self.send_error(404)
            return
        super().do_POST()


_REPORT_LOCK = threading.Lock()


def _quota_payload(live: bool = True) -> dict:
    accounts = attach_live_quota(load_accounts()) if live else attach_cached_quota(load_accounts())
    views = []
    for account in accounts:
        windows = annotate_windows(list(account.extra.get("windows") or []))
        views.append(
            {
                "provider": account.provider,
                "account_id": account.account_id,
                "label": account.label,
                "email": account.email,
                "plan": account.plan,
                "active": account.active,
                "account_source": account.extra.get("account_source") or "",
                "account_source_grade": account.extra.get("account_source_grade") or "",
                "account_source_official": account.extra.get("account_source_official"),
                "quota_source": account.extra.get("quota_source") or "",
                "quota_error": account.extra.get("quota_error") or "",
                "quota_fetched_at": account.extra.get("quota_fetched_at") or "",
                "quota_refresh_at": (
                    next_quota_fetch_at(
                        account.provider, account.extra.get("quota_fetched_at")
                    )
                    if account.extra.get("quota_source") == "live"
                    else ""
                ),
                "last_used_at": account.extra.get("last_used_at") or "",
                "last_usage_at": account.extra.get("last_usage_at") or "",
                "windows": windows,
            }
        )
    return {"accounts": views, "providers": provider_capabilities(accounts)}


_LOCAL_TTL_SECONDS = 30
_local_cache: dict = {
    "at": 0.0,
    "payload": None,
    "identity_stamp": None,
    "source_stamp": None,
    "inputs": None,
}
_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()


def _digest_json(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _user_identity_source_stamp() -> tuple:
    """Track SQLite main/WAL changes without creating either user store."""
    out = []
    for path in (TelemetryStore().path, UserSourceStore().path, product_mode_path()):
        for candidate in (path, Path(str(path) + "-wal")):
            try:
                stat = candidate.stat()
                out.append((str(candidate), stat.st_mtime_ns, stat.st_size))
            except OSError:
                out.append((str(candidate), 0, 0))
    return tuple(out)


def _project_identity_runs(
    runs: list[tuple[str, str]],
) -> list[tuple[str, str]] | None:
    """Sort identity rows by instant and collapse consecutive owners.

    Identity ledgers retain the timestamp spelling that their writer used, but
    diagnostics compare a timeline projection rather than the file's textual
    order. The source index is a stable tie-breaker for equal instants. An
    empty owner is meaningful (the v2 Codex ``unassigned`` boundary), so it is
    retained and only consecutive equal owners are collapsed.
    """
    parsed: list[tuple[datetime, int, tuple[str, str]]] = []
    for index, (at, owner) in enumerate(runs):
        stamp = parse_ts(at)
        if stamp is None:
            return None
        parsed.append((stamp, index, (str(at), str(owner))))
    parsed.sort(key=lambda item: (item[0], item[1]))
    collapsed: list[tuple[str, str]] = []
    for _stamp, _index, run in parsed:
        if collapsed and collapsed[-1][1] == run[1]:
            continue
        collapsed.append(run)
    return collapsed


def _valid_legacy_identity_rows(value: object, identity_key: str) -> bool:
    """Validate a complete v1 identity ledger before exposing its evidence.

    Legacy ledgers are flat lists, unlike the schema-2 Codex event stream.  A
    single malformed row makes the whole snapshot unusable: silently dropping
    it would make the reported count and digest describe a different ledger.
    Keep the required fields aligned with the product's legacy reader while
    allowing forward-compatible extra fields.
    """
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and parse_ts(item.get("at")) is not None
        and bool(str(item.get(identity_key) or ""))
        for item in value
    )


def _run_evidence(runs: list[tuple[str, str]]) -> dict:
    """Non-identifying proof of the exact identity timeline held by this process."""
    projected = _project_identity_runs(runs)
    # Live identity sources are expected to be valid. Keep diagnostics
    # non-throwing if a mocked or legacy source contains a malformed row; the
    # disk reader below reports that source as unreadable explicitly.
    if projected is None:
        projected = [(str(at), str(owner)) for at, owner in runs]
    return {
        "count": len(projected),
        "first_at": projected[0][0] if projected else "",
        "last_at": projected[-1][0] if projected else "",
        "sha256": _digest_json(projected),
    }


def _disk_run_evidence(path: Path, identity_key: str) -> dict:
    """Describe one exact byte snapshot, bypassing the process identity cache."""
    snapshot = _read_disk_identity_snapshot(path, identity_key)
    return snapshot["evidence"]


def _read_disk_identity_snapshot(
    path: Path, identity_key: str, *, codex_v2_only: bool = False
) -> dict:
    """Read and validate one identity ledger directly, returning its runs.

    This deliberately does not call the accounts module's cached identity
    accessors.  ``runs`` is an internal normalized ``(timestamp, owner)``
    sequence; ``evidence`` retains the non-identifying diagnostics shape used
    by the existing endpoint.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    path_hash = hashlib.sha256(str(path.resolve()).lower().encode()).hexdigest()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = os.fstat(handle.fileno())
    except FileNotFoundError:
        evidence = {
            **_run_evidence([]),
            "readable": True,
            "captured_at": captured_at,
            "file_sha256": hashlib.sha256(b"").hexdigest(),
            "bytes": 0,
            "mtime_ns": 0,
            "path_sha256": path_hash,
            "stable_snapshot": True,
        }
        return {"runs": [], "evidence": evidence, "missing": True}
    except OSError:
        evidence = {
            **_run_evidence([]),
            "readable": False,
            "captured_at": captured_at,
            "file_sha256": "",
            "bytes": 0,
            "mtime_ns": 0,
            "path_sha256": path_hash,
            "stable_snapshot": False,
        }
        return {"runs": [], "evidence": evidence, "missing": False}
    evidence = {
        "captured_at": captured_at,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "mtime_ns": int(after.st_mtime_ns),
        "path_sha256": hashlib.sha256(str(path.resolve()).lower().encode()).hexdigest(),
        "stable_snapshot": (
            before.st_mtime_ns == after.st_mtime_ns
            and before.st_size == after.st_size
            and after.st_size == len(raw)
        ),
    }
    try:
        stored = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        evidence = {**_run_evidence([]), "readable": False, **evidence}
        return {"runs": [], "evidence": evidence, "missing": False}
    if codex_v2_only and not (
        isinstance(stored, dict)
        and stored.get("schema") == 2
        and identity_key == "account_id"
    ):
        evidence = {**_run_evidence([]), "readable": False, **evidence}
        return {"runs": [], "evidence": evidence, "missing": False}
    if isinstance(stored, dict) and stored.get("schema") == 2 and identity_key == "account_id":
        from sandglass.accounts import _valid_codex_identity_events

        if not _valid_codex_identity_events(stored):
            evidence = {**_run_evidence([]), "readable": False, **evidence}
            return {"runs": [], "evidence": evidence, "missing": False}
        events = stored.get("events")
        if not isinstance(events, list):
            evidence = {**_run_evidence([]), "readable": False, **evidence}
            return {"runs": [], "evidence": evidence, "missing": False}
        runs = [
            (
                str(item["at"]),
                str(item.get("account_id") or "")
                if item.get("kind") == "observed"
                else "",
            )
            for item in events
            if isinstance(item, dict) and item.get("at")
        ]
    elif isinstance(stored, list):
        if not _valid_legacy_identity_rows(stored, identity_key):
            evidence = {**_run_evidence([]), "readable": False, **evidence}
            return {"runs": [], "evidence": evidence, "missing": False}
        runs = [
            (str(item["at"]), str(item[identity_key]))
            for item in stored
        ]
    else:
        evidence = {**_run_evidence([]), "readable": False, **evidence}
        return {"runs": [], "evidence": evidence, "missing": False}
    projected = _project_identity_runs(runs)
    if projected is None:
        evidence = {**_run_evidence([]), "readable": False, **evidence}
        return {"runs": [], "evidence": evidence, "missing": False}
    evidence = {**_run_evidence(projected), "readable": True, **evidence}
    return {"runs": projected, "evidence": evidence, "missing": False}


def _attribution_diagnostics() -> dict:
    """Expose enough live-process evidence to diagnose drift without account data."""
    from sandglass.accounts import (
        claude_identity_runs,
        codex_identity_runs,
        grok_identity_runs,
        identity_write_evidence,
        identity_source_stamp,
    )
    from sandglass.paths import meter_home

    stamp = identity_source_stamp()
    source_stamp = _user_identity_source_stamp()
    cached_stamp = _local_cache.get("identity_stamp")
    cached_payload = _local_cache.get("payload")
    cached_at = float(_local_cache.get("at") or 0.0)
    run_rows = {
        "claude": {
            "live": _run_evidence(claude_identity_runs()),
            "path": meter_home() / "claude-official-identity-runs.json",
            "key": "account_id",
        },
        "codex": {
            "live": _run_evidence(codex_identity_runs()),
            "path": meter_home() / "codex-official-identity-events-v2.json",
            "key": "account_id",
        },
        "grok": {
            "live": _run_evidence(grok_identity_runs()),
            "path": meter_home() / "grok-official-identity-runs.json",
            "key": "account_id",
        },
    }
    for row in run_rows.values():
        disk = _disk_run_evidence(row["path"], row["key"])
        path = row["path"]
        writer = identity_write_evidence(path)
        disk["last_write"] = writer
        disk["last_write_matches_file"] = bool(writer) and (
            writer.get("file_sha256") == disk.get("file_sha256")
        )
        live = row["live"]
        live.update(
            {
                "disk": disk,
                "matches_disk": (
                    disk["readable"]
                    and disk["stable_snapshot"]
                    and live["sha256"] == disk["sha256"]
                ),
            }
        )
    return {
        "process_started_at": _PROCESS_STARTED_AT,
        "state_home_sha256": hashlib.sha256(
            str(meter_home().resolve()).lower().encode("utf-8")
        ).hexdigest(),
        "identity_source_stamp_sha256": _digest_json(stamp),
        "identity_runs": {
            provider: row["live"] for provider, row in run_rows.items()
        },
        "local_windows_cache": {
            "present": cached_payload is not None,
            "age_seconds": max(0.0, time.monotonic() - cached_at) if cached_at else None,
            "identity_stamp_matches_disk": cached_stamp == stamp if cached_stamp is not None else None,
            "user_source_stamp_matches_disk": (
                _local_cache.get("source_stamp") == source_stamp
                if _local_cache.get("source_stamp") is not None
                else None
            ),
            "payload_sha256": _digest_json(cached_payload) if cached_payload is not None else "",
        },
        "runtime": runtime_provenance(),
    }


def _double_counted(session) -> bool:
    """Nothing is double counted -- kept as the record of a wrong turn.

    A Codex subagent inherits the parent's session_id, which looked like the same
    conversation logged twice. It is not: the parent's total_token_usage tracks only
    its own turns (measured on a development machine, its net growth and the sum of
    its own per-turn deltas agreed to within one percent), and a subagent's counter starts from its own first
    turn rather than inheriting the parent's context. Both are real, separate spend.
    """
    return False


def _switch_runs_for(provider: str):
    """The account-switch timeline for a provider, or None if it does not switch."""
    from sandglass.attribution import switch_runs_for

    return switch_runs_for(provider)


def _rolling_local_window_bounds(
    account, runs, sessions: list, known: set[str], single_owner: str,
    minutes: float,
):
    """Rebuild one local-only rolling window from owned token timestamps.

    This never reconstructs the account-wide quota percentage. It only answers
    which of this computer's already-recorded token minutes belong to the current
    rolling interval, and remains explicitly ineligible for full-window inference.
    """
    duration = timedelta(minutes=minutes)
    moments = []
    for session in sessions:
        if session.timeline is None:
            moment = parse_ts(session.ended_at) or parse_ts(session.started_at)
            if (
                moment is not None
                and (session.usage.total_tokens or session.usage.calls)
                and _owns_minute(account, runs, session, moment, known, single_owner)
            ):
                moments.append(moment)
            continue
        for at, delta in session.timeline:
            moment = parse_ts(at)
            if (
                moment is not None
                and (delta.total_tokens or delta.calls)
                and _owns_minute(account, runs, session, moment, known, single_owner)
            ):
                moments.append(moment)
    start = None
    for moment in sorted(moments):
        if start is None or moment >= start + duration:
            start = moment
    if start is None:
        return None, None
    end = start + duration
    if datetime.now(timezone.utc) >= end:
        return None, None
    return start, end


def _windows_for(
    account, peers: list, sessions: list, windows: list,
    single_account_owner_id: str | None = None,
) -> list:
    # Per-account local stats follow the session's assigned identity. Do not
    # re-slice by the last CLI switch time — that made a 7d bar equal the 5h bar.
    mine = [s for s in sessions if s.provider == account.provider and not _double_counted(s)]
    runs = _switch_runs_for(account.provider)
    known = _known_identities(peers)
    single_owner = (
        _single_account_id(peers, attribution_mode())
        if single_account_owner_id is None
        else str(single_account_owner_id or "")
    )
    stale_quota = account.extra.get("quota_source") == "stale"
    out = []
    for window in windows:
        start = parse_ts(window.get("window_start"))
        end = parse_ts(window.get("resets_at"))
        anchor_iso, anchor_kind, gap = quota_anchor(
            account.provider, account.account_id, str(window.get("label") or "")
        )
        anchored = anchor_kind == "reset"
        anchor = parse_ts(anchor_iso) if anchored else None
        trusted_anchor = anchored and quota_anchor_source(
            account.provider, account.account_id, str(window.get("label") or "")
        ).startswith("official_live")
        boundary_source = "official_quota_window"
        # A period that has already ended is not the current one, however fresh
        # the reading that carried it. Claude is polled every three minutes, so
        # a reset landing between polls left the panel counting into the window
        # that just closed: it showed the previous period's whole total as the
        # new period's usage, frozen, for up to a full interval -- and the
        # tokens actually spent since the reset were outside `end` and counted
        # nowhere. Seen live: (figure withheld) held across four readings after a
        # 16:19:59 reset, then dropping (figure withheld) when the poll caught up.
        rolled_over = False
        period_over = end is not None and end <= datetime.now(timezone.utc)
        # Only a period that was still running when we read it. That is the
        # reset landing between two polls, and the schedule tells us exactly
        # where the next one starts. A provider handing back a period that had
        # already ended before the fetch is a different thing, and inventing
        # periods forward from it would be covering for it rather than saying so.
        fetched = parse_ts(account.extra.get("quota_fetched_at"))
        if period_over and start is not None and fetched is not None and end > fetched:
            # These periods are contiguous and fixed length -- 06:19:59,
            # 11:19:59, 16:19:59 for a five-hour window -- so the one after the
            # reset begins exactly where the last ended. Rolling forward is
            # arithmetic on what the provider already published, not a guess,
            # and it is only in use for the minutes between the reset and the
            # next poll. used% still describes the period that ended, which is
            # why no full-window estimate is offered until the poll catches up.
            span = end - start
            if span > timedelta(0):
                now_utc = datetime.now(timezone.utc)
                while end <= now_utc:
                    start, end = end, end + span
                boundary_source = "official_period_rollover"
                rolled_over = True
        if stale_quota:
            label = str(window.get("label") or "")
            minutes = window.get("window_minutes")
            if trusted_anchor and anchor is not None and not period_over:
                if start is None or anchor > start:
                    start = anchor
                boundary_source = "official_reset_anchor"
                gap = False
            elif start is not None and end is not None and end > datetime.now(timezone.utc):
                # Claude's weekly reset schedule is fixed per account. A stale
                # percentage cannot drive the bar, but its still-future period
                # boundary can safely partition already-recorded local tokens.
                boundary_source = "retained_provider_schedule"
            elif (
                account.provider == "claude"
                and label in {"5h", "primary"}
                and isinstance(minutes, (int, float))
                and minutes > 0
            ):
                start, end = _rolling_local_window_bounds(
                    account, runs, mine, known, single_owner, float(minutes)
                )
                boundary_source = "local_token_timeline"
            else:
                continue
            if start is None:
                continue
            if not trusted_anchor:
                anchored = False
                gap = True
        else:
            if start is None:
                continue
            # A reset card zeroes used% without moving the period, so the period's own
            # start no longer bounds the work that percentage covers.
            if anchored and anchor is not None and anchor > start:
                start = anchor
            elif anchored:
                anchored = False
        usage = TokenUsage()
        touched: set[str] = set()
        sources: set[str] = set()
        # used% describes the period it was read for. After a rollover it is
        # the previous period's figure, so folding a full window out of it
        # extrapolates from the wrong one.
        inference_allowed = not (stale_quota or rolled_over)
        # Buckets span the window itself, not the last seven calendar days: the bar
        # above shows used% for this period, so the chart under it must cover the same
        # ground or the two answer different questions.
        per_day: dict[str, TokenUsage] = {}
        per_day_sources: dict[str, set[str]] = {}
        for session in mine:
            deltas = session.timeline
            if deltas is None:
                ts = parse_ts(session.ended_at) or parse_ts(session.started_at)
                if ts is None or ts < start or (end is not None and ts > end):
                    continue
                if not _owns_minute(account, runs, session, ts, known, single_owner):
                    continue
                usage = usage.add(session.usage)
                sources.update(evidence_sources(session))
                inference_allowed = inference_allowed and bool(
                    session.extra.get("full_window_inference_allowed", True)
                )
                touched.add(conversation_id(session))
                continue
            for at, delta in deltas:
                moment = parse_ts(at)
                if moment is None or moment < start or (end is not None and moment > end):
                    continue
                if not _owns_minute(account, runs, session, moment, known, single_owner):
                    continue
                usage = usage.add(delta)
                sources.update(evidence_sources(session))
                sources.update(
                    _supplemental_sources(account, runs, session, moment, single_owner)
                )
                inference_allowed = inference_allowed and _full_window_allowed(
                    session, moment
                )
                key = moment.astimezone().date().isoformat()
                per_day[key] = per_day.get(key, TokenUsage()).add(delta)
                per_day_sources.setdefault(key, set()).update(evidence_sources(session))
                per_day_sources[key].update(
                    _supplemental_sources(account, runs, session, moment, single_owner)
                )
                touched.add(conversation_id(session))
        out.append(
            {
                "label": window.get("label"),
                "usage": usage.as_dict(),
                "sessions": len(touched),
                "evidence_sources": sorted(sources),
                "full_window_inference_allowed": inference_allowed,
                "counted_from": start.isoformat(),
                # Both edges, not just the near one. A reconciliation that has
                # to guess the far edge ends up testing its own guess, and a
                # rolling local window's far edge is not the official one.
                "counted_to": end.isoformat() if end is not None else "",
                "boundary_source": boundary_source,
                "days": _day_series(start, end, per_day, per_day_sources),
                # True = the window was cut short by a detected reset, so used% covers
                # less than the period and folding it out to a full window is nonsense.
                "reset_anchored": anchored,
                # True = observation stopped long enough that a reset could have gone
                # unseen. Same consequence: do not extrapolate from used%.
                "observation_gap": gap,
            }
        )
    return out


def _day_series(
    start, end, per_day: dict, per_day_sources: dict[str, set[str]] | None = None
) -> list:
    """One entry per local day the window covers, oldest first, gaps filled with zero.

    Capped so a 30-day period stays a readable strip rather than a hairline comb.
    """
    first = start.astimezone().date()
    today = datetime.now().astimezone().date()
    last = min(end.astimezone().date(), today) if end is not None else today
    if last < first:
        last = first
    span = (last - first).days + 1
    if span > 14:
        first = last - timedelta(days=13)
        span = 14
    out = []
    for i in range(span):
        day = first + timedelta(days=i)
        usage = per_day.get(day.isoformat()) or TokenUsage()
        out.append(
            {
                "day": day.isoformat(),
                # What the quota was charged for. The bars plot this; output is
                # kept alongside because it answers a different question.
                "spent": usage.total_tokens,
                "made": usage.output_tokens,
                "calls": usage.calls,
                "evidence_sources": sorted(
                    (per_day_sources or {}).get(day.isoformat()) or set()
                ),
            }
        )
    return out


def _activity_days(account, sessions: list, span: int | None = None,
                   peers: list | None = None,
                   single_account_owner_id: str | None = None) -> dict:
    """Per-day production for an exact recent span or all attributable history.

    Days are the machine's own calendar days, so a bar lines up with what the user
    remembers doing that day rather than with a UTC boundary. ``None`` begins at
    the first directly attributable day, while still returning at least the 182
    days needed by the fixed 26-week heatmap.
    """
    today = datetime.now().astimezone().date()
    mine = [s for s in sessions if s.provider == account.provider and not _double_counted(s)]
    runs = _switch_runs_for(account.provider)
    # Same identity set as the windows above, or the timeline naming a sibling
    # would read as unknown here and quietly fall back to session-level slicing.
    known = _known_identities(peers or [account])
    single_owner = (
        _single_account_id(peers or [account], attribution_mode())
        if single_account_owner_id is None
        else str(single_account_owner_id or "")
    )
    owned: dict[str, TokenUsage] = {}
    owned_sources: dict[str, set[str]] = {}
    for session in mine:
        for at, delta in session.timeline or []:
            moment = parse_ts(at)
            if moment is None or not _owns_minute(
                account, runs, session, moment, known, single_owner
            ):
                continue
            key = moment.astimezone().date().isoformat()
            owned[key] = (owned.get(key) or TokenUsage()).add(delta)
            owned_sources.setdefault(key, set()).update(evidence_sources(session))
            owned_sources[key].update(
                _supplemental_sources(account, runs, session, moment, single_owner)
            )
    if span is None:
        earliest = min((datetime.fromisoformat(day).date() for day in owned),
                       default=today)
        first = min(earliest, today - timedelta(days=181))
    else:
        first = today - timedelta(days=max(1, span) - 1)
    order = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    total = TokenUsage()
    days = []
    for day in order:
        usage = owned.get(day.isoformat()) or TokenUsage()
        total = total.add(usage)
        days.append(
            {
                "day": day.isoformat(),
                # What the quota was charged for. The bars plot this; output is
                # kept alongside because it answers a different question.
                "spent": usage.total_tokens,
                "made": usage.output_tokens,
                "calls": usage.calls,
                "evidence_sources": sorted(owned_sources.get(day.isoformat()) or set()),
            }
        )
    all_sources = sorted(
        {source for sources in owned_sources.values() for source in sources}
    )
    return {
        "days": days,
        "spent": total.total_tokens,
        "made": total.output_tokens,
        "calls": total.calls,
        "cache_read": total.cache_read_tokens,
        "span": len(order),
        "evidence_sources": all_sources,
    }


def _local_windows_payload(live: bool = True) -> dict:
    """Tokens burned on THIS computer since each open window last reset.

    Split out of /api/report because it needs a log scan: /api/quota stays fast and
    the panel fills these in afterwards. Scope differs from the quota bar on purpose --
    the bar counts every device on the account, this counts only this machine.
    """
    from sandglass.accounts import identity_source_stamp

    with _REPORT_LOCK:
        now = time.monotonic()
        # The stamp and all inputs are captured under the same lock as the
        # projection.  A second writer cannot otherwise land an identity event
        # between the source read and the cache entry that claims to represent
        # it.
        stamp = identity_source_stamp()
        source_stamp = _user_identity_source_stamp()
        cached = _local_cache["payload"]
        if (
            cached is not None
            and now - _local_cache["at"] < _LOCAL_TTL_SECONDS
            and _local_cache.get("identity_stamp") == stamp
            and _local_cache.get("source_stamp") == source_stamp
        ):
            return cached
        accounts = attach_live_quota(load_accounts()) if live else attach_cached_quota(load_accounts())
        cache = SessionCache()
        sessions = collect_all(cache=cache)
        cache.close()
        sessions = apply_user_evidence(
            sessions,
            settings=UserSourceStore().settings(),
        )
        mode = attribution_mode()
        rows = []
        for account in accounts:
            windows = annotate_windows(list(account.extra.get("windows") or []))
            peers = [a for a in accounts if a.provider == account.provider]
            single_owner = _single_account_id(peers, mode)
            rows.append(
                {
                    "provider": account.provider,
                    "account_id": account.account_id,
                    "windows": _windows_for(
                        account, peers, sessions, windows, single_owner
                    ),
                    "activity": _activity_days(
                        account, sessions, None, peers, single_owner
                    ),
                }
            )
        payload = {"accounts": rows}
        _local_cache["payload"] = payload
        _local_cache["at"] = now
        _local_cache["identity_stamp"] = stamp
        _local_cache["source_stamp"] = source_stamp
        _local_cache["inputs"] = {
            "accounts": accounts,
            "sessions": sessions,
            "mode": mode,
        }
        return payload


def _attribution_projection(payload: object) -> dict[tuple[str, str, str], dict]:
    """Return only the stable attribution facts in a local-windows payload."""
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
        raise RuntimeError("attribution payload is malformed")
    out: dict[tuple[str, str, str], dict] = {}
    for account in payload["accounts"]:
        if not isinstance(account, dict):
            raise RuntimeError("attribution account row is malformed")
        provider = str(account.get("provider") or "")
        account_id = str(account.get("account_id") or "")
        windows = account.get("windows")
        if not provider or not account_id or not isinstance(windows, list):
            raise RuntimeError("attribution account identity is malformed")
        for window in windows:
            if not isinstance(window, dict):
                raise RuntimeError("attribution window is malformed")
            label = str(window.get("label") or "")
            usage = window.get("usage")
            days = window.get("days")
            if not label or not isinstance(usage, dict) or not isinstance(days, list):
                raise RuntimeError("attribution window facts are malformed")
            total = usage.get("total_tokens")
            if not isinstance(total, int) or isinstance(total, bool):
                raise RuntimeError("attribution total is malformed")
            day_values: dict[str, int] = {}
            for day in days:
                if not isinstance(day, dict) or not isinstance(day.get("day"), str):
                    raise RuntimeError("attribution day is malformed")
                spent = day.get("spent")
                if not isinstance(spent, int) or isinstance(spent, bool):
                    raise RuntimeError("attribution day total is malformed")
                day_values[day["day"]] = spent
            key = (provider, account_id, label)
            if key in out:
                raise RuntimeError("duplicate attribution projection key")
            out[key] = {"total_tokens": total, "days": day_values}
    return out


def _self_check_window_projection(
    account, peers: list, sessions: list, cached_window: dict,
    runs, mode: str,
) -> dict:
    """Recompute one window using fixed bounds and the supplied identity runs."""
    start = parse_ts(cached_window.get("counted_from"))
    end = parse_ts(cached_window.get("counted_to"))
    if start is None:
        raise RuntimeError("cached attribution window has no counted_from")
    if cached_window.get("counted_to") and end is None:
        raise RuntimeError("cached attribution window has malformed counted_to")
    cached_days = cached_window.get("days")
    if not isinstance(cached_days, list):
        raise RuntimeError("cached attribution window has no day series")
    day_keys = [str(item.get("day")) for item in cached_days if isinstance(item, dict)]
    if len(day_keys) != len(cached_days) or any(not day for day in day_keys):
        raise RuntimeError("cached attribution window has malformed day keys")
    known = _known_identities(peers)
    single_owner = _single_account_id(peers, mode)
    total = TokenUsage()
    daily: dict[str, TokenUsage] = {}
    for session in sessions:
        if session.provider != account.provider or _double_counted(session):
            continue
        if session.timeline is None:
            moment = parse_ts(session.ended_at) or parse_ts(session.started_at)
            if (
                moment is not None
                and moment >= start
                and (end is None or moment <= end)
                and (session.usage.total_tokens or session.usage.calls)
                and _owns_minute(account, runs, session, moment, known, single_owner)
            ):
                total = total.add(session.usage)
            continue
        for at, delta in session.timeline:
            moment = parse_ts(at)
            if (
                moment is None
                or moment < start
                or (end is not None and moment > end)
                or not _owns_minute(account, runs, session, moment, known, single_owner)
            ):
                continue
            total = total.add(delta)
            day = moment.astimezone().date().isoformat()
            daily[day] = (daily.get(day) or TokenUsage()).add(delta)
    return {
        "total_tokens": total.total_tokens,
        "days": {day: (daily.get(day) or TokenUsage()).total_tokens for day in day_keys},
    }


def _check_attribution_self_check() -> bool:
    """Compare cached Codex attribution with an independent direct-disk replay."""
    from sandglass import live_snapshot
    from sandglass.diagnostics import (
        clear_component_failure,
        record_attribution_self_check_result,
        record_component_failure,
    )
    from sandglass.paths import meter_home

    # The observer must never clear a panel's attribution result.  An unset
    # role is also non-authoritative, as are tests and helper processes.
    if getattr(live_snapshot, "_ROLE", "") != "panel":
        return False

    def fail(reason: str) -> bool:
        record_component_failure("attribution_self_check", RuntimeError(reason))
        record_attribution_self_check_result("fail", reason)
        return False

    with _REPORT_LOCK:
        inputs = _local_cache.get("inputs")
        cached_payload = _local_cache.get("payload")
        if cached_payload is None:
            # A panel that has not produced its first local-windows answer yet
            # is not an unhealthy attribution result.
            return False
        if not isinstance(inputs, dict):
            return fail("attribution self-check inputs unavailable")
        try:
            cached_projection = _attribution_projection(cached_payload)
        except RuntimeError as exc:
            return fail(str(exc))
        codex_targets = {key for key in cached_projection if key[0] == "codex"}
        if not codex_targets:
            # This installation has no Codex attribution to verify.  Clear a
            # stale result from an earlier account state, but do not touch the
            # disk ledger or manufacture a failure for a non-applicable check.
            clear_component_failure("attribution_self_check")
            return True

        path = meter_home() / "codex-official-identity-events-v2.json"
        snapshot = _read_disk_identity_snapshot(path, "account_id", codex_v2_only=True)
        evidence = snapshot["evidence"]
        if snapshot.get("missing") or not evidence.get("readable"):
            return fail("attribution self-check Codex ledger unavailable")
        if not evidence.get("stable_snapshot"):
            return fail("attribution self-check Codex ledger unstable")

        accounts = inputs.get("accounts")
        sessions = inputs.get("sessions")
        mode = inputs.get("mode")
        if not isinstance(accounts, list) or not isinstance(sessions, list) or not isinstance(mode, str):
            return fail("attribution self-check fixed inputs malformed")
        fresh_projection = {
            key: value for key, value in cached_projection.items() if key[0] != "codex"
        }
        codex_accounts = [account for account in accounts if account.provider == "codex"]
        for account in codex_accounts:
            peers = codex_accounts
            reference_rows = {
                key[2]: value
                for key, value in cached_projection.items()
                if key[0] == "codex" and key[1] == account.account_id
            }
            windows = list(account.extra.get("windows") or [])
            for window in windows:
                label = str(window.get("label") or "")
                if not label:
                    return fail("attribution self-check window label unavailable")
                reference = reference_rows.get(label)
                if reference is None:
                    # Added windows are a meaningful mismatch.  There is no
                    # cached bound to reuse, so do not invent a replay result.
                    return fail("attribution self-check account or window changed")
                cached_window = None
                for row in cached_payload["accounts"]:
                    if row.get("provider") != "codex" or row.get("account_id") != account.account_id:
                        continue
                    for candidate in row.get("windows") or []:
                        if candidate.get("label") == label:
                            cached_window = candidate
                            break
                if cached_window is None:
                    return fail("attribution self-check account or window changed")
                fresh_projection[("codex", account.account_id, label)] = (
                    _self_check_window_projection(
                        account, peers, sessions, cached_window,
                        snapshot["runs"], mode,
                    )
                )
            if set(reference_rows) != {
                str(window.get("label") or "") for window in windows
            }:
                return fail("attribution self-check account or window changed")
        if cached_projection != fresh_projection:
            return fail("attribution self-check attribution facts disagree")
        clear_component_failure("attribution_self_check")
        return True


def _start_attribution_self_check_watch(stop: threading.Event) -> threading.Thread:
    """Run the panel-only attribution replay independently of quota polling."""
    from sandglass import live_snapshot
    from sandglass.diagnostics import (
        attribution_self_check_heartbeat,
        record_attribution_self_check_result,
        record_attribution_self_check_success,
        record_component_failure,
    )

    def loop() -> None:
        while not stop.is_set():
            if getattr(live_snapshot, "_ROLE", "") != "panel":
                return
            try:
                if _check_attribution_self_check():
                    record_attribution_self_check_success()
            except Exception as exc:  # noqa: BLE001 - self-check must not kill panel
                if getattr(live_snapshot, "_ROLE", "") == "panel":
                    record_component_failure("attribution_self_check", exc)
                    # Never str(exc): it can carry a filesystem path or account
                    # data. Only the exception's type name is safe to log.
                    record_attribution_self_check_result(
                        "fail", f"exception:{type(exc).__name__}"
                    )
            if getattr(live_snapshot, "_ROLE", "") == "panel":
                live_snapshot.record(
                    "attribution_self_check", attribution_self_check_heartbeat()
                )
            if stop.wait(_SNAPSHOT_WATCH_SECONDS):
                return

    watcher = threading.Thread(
        target=loop, name="sandglass-attribution-self-check", daemon=True
    )
    watcher.start()
    return watcher


@contextmanager
def _attribution_self_check_watch_context():
    """Own a panel self-check watcher for exactly one UI/server lifecycle."""
    stop = threading.Event()
    watcher = _start_attribution_self_check_watch(stop)
    try:
        yield
    finally:
        stop.set()
        watcher.join()


def _cached_report(since: str | None, live_quota: bool = True) -> dict:
    with _REPORT_LOCK:
        cache = SessionCache()
        sessions = collect_all(cache=cache)
        cache.close()
        sessions = apply_user_evidence(
            sessions,
            settings=UserSourceStore().settings(),
        )
        return build_report(
            sessions,
            since=parse_since(since),
            live_quota=live_quota,
            attribution_mode=attribution_mode(),
        )


_IDENTITY_WATCH_SECONDS = 15.0
_SNAPSHOT_WATCH_SECONDS = 30.0
_SIGNAL_IDLE_SECONDS = 15.0


def _refresh_quota_signals(monitor: QuotaSignalMonitor) -> set[str]:
    events = monitor.poll_events()
    if not events:
        return set()
    if any("identity_changed" in reasons for reasons in events.values()):
        # Attribution observation stays independent from the provider quota
        # cache. Each provider call has its own failure boundary.
        try:
            from sandglass.accounts import note_current_identity

            note_current_identity(observe_codex=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            from sandglass.accounts import note_codex_identity

            note_codex_identity()
        except Exception:  # noqa: BLE001
            pass
    for provider, reasons in sorted(events.items()):
        try:
            result = refresh_quota_provider(provider)
            record_quota_signal(provider, reasons, result=result)
        except Exception as exc:  # noqa: BLE001 - one offline provider must not block another
            record_quota_signal(provider, reasons, error=str(exc))
    return set(events)


def _start_quota_watch(
    stop: threading.Event, live: bool
) -> tuple[threading.Thread, threading.Thread]:
    """Record quota observations on a timer, not on page requests.

    Grok publishes no usage history anywhere -- not in its session logs, not in its CLI
    log, and the billing endpoint only answers "right now" -- so a reset card is only
    ever visible as used% changing between two readings we took ourselves. Tying that to
    HTTP traffic meant a closed browser tab blinded the detector; this keeps it running
    for as long as the process does.
    """
    if not live:
        return ()

    def loop() -> None:
        from sandglass.accounts import note_codex_identity, note_current_identity

        next_identity = 0.0
        next_snapshot = 0.0
        next_quota = {
            provider: 0.0 for provider in CACHE_TTL_SECONDS_BY_PROVIDER
        }
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_identity:
                # Keep Codex attribution in its own provider-scoped call. A
                # Claude or Grok read failure must not suppress the Codex gap
                # or observation for this cycle.
                try:
                    note_current_identity(observe_codex=False)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    note_codex_identity()
                except Exception:  # noqa: BLE001
                    pass
                next_identity = now + _IDENTITY_WATCH_SECONDS
                # An identity read already in flight is allowed to finish, and
                # _stop_quota_watchers waits for it. Everything below this line
                # is fresh work -- a re-parse of every session file, then a
                # forced live request to each vendor -- and shutdown joins this
                # thread. Starting it after a stop delays the exit and spends a
                # vendor call on an answer nobody will read.
                if stop.is_set():
                    return

            if now >= next_snapshot:
                # Keep a reconcilable answer on disk even with no panel open.
                # An answer that exists only while somebody is looking at it
                # cannot be audited, which is how the panel reconciliation came
                # to run in one configuration and skip itself in the other.
                for endpoint in ("/api/product-mode", "/api/quota",
                                 "/api/local-windows", "/api/attribution-diagnostics"):
                    try:
                        api_payload(endpoint, live_quota=False)
                    except Exception:  # noqa: BLE001
                        continue
                next_snapshot = time.monotonic() + _SNAPSHOT_WATCH_SECONDS

            if stop.is_set():
                return

            due = {
                provider for provider, deadline in next_quota.items()
                if now >= deadline
            }
            if due:
                try:
                    # Force only the providers whose own interval elapsed. The
                    # previous shared 120-second outer loop silently turned the
                    # documented 90/180-second cadence into 120/240 seconds.
                    attach_live_quota(
                        load_accounts(), force=True, providers=due
                    )
                except Exception:  # noqa: BLE001 - one offline provider must not stop observation
                    pass
                refreshed = time.monotonic()
                for provider in due:
                    next_quota[provider] = (
                        refreshed + CACHE_TTL_SECONDS_BY_PROVIDER[provider]
                    )

            next_deadline = min(next_identity, next_snapshot, *next_quota.values())
            if stop.wait(max(0.05, next_deadline - time.monotonic())):
                return

    quota_thread = threading.Thread(
        target=loop, name="sandglass-quota-watch", daemon=True
    )
    quota_thread.start()

    def signal_loop() -> None:
        monitor = QuotaSignalMonitor()
        while not stop.wait(monitor.next_poll_delay(_SIGNAL_IDLE_SECONDS)):
            try:
                _refresh_quota_signals(monitor)
            except Exception:  # noqa: BLE001 - partial vendor writes are transient
                continue

    signal_thread = threading.Thread(
        target=signal_loop, name="sandglass-quota-signal-watch", daemon=True
    )
    signal_thread.start()
    return quota_thread, signal_thread


def serve(
    host: str = "127.0.0.1",
    port: int = 7740,
    open_browser: bool = True,
    since: str | None = None,
    live_quota: bool = True,
) -> int:
    from sandglass import live_snapshot

    try:
        require_canonical_state_home()
    except StateHomeAttestationError as exc:
        print(str(exc))
        return 2
    live_snapshot.set_role("panel")
    host = loopback_host(host)
    record_runtime_identity(
        build_web_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
        )
    )
    httpd = ThreadingHTTPServer((host, port), partial(Handler, since=since, live_quota=live_quota))
    url = f"http://{host}:{port}/"
    print(f"sandglass dashboard on {url}")
    print("Data is read from this computer only. Ctrl+C to stop.")
    stop = threading.Event()
    watchers: list[threading.Thread] = []
    if live_quota and os.name == "nt":
        from sandglass.observer import ensure_observer_running

        ensure_observer_running()
    elif live_quota:
        watchers.extend(_start_quota_watch(stop, True))
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        with _attribution_self_check_watch_context():
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stop.set()
        for watcher in watchers:
            watcher.join()
        httpd.server_close()
    return 0
