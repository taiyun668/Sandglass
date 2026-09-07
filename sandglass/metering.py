from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sandglass.models import parse_ts


CONTRACT_VERSION = 3
MAX_METERING_ENTITIES = 2_000
MAX_METERING_OBSERVATIONS = 20_000
MAX_METRICS_PER_OBSERVATION = 64

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_UNIT_RE = re.compile(r"^(?:1|[A-Z]{3}|[A-Za-z][A-Za-z0-9./-]{0,31}|\{[a-z][a-z0-9_.-]{0,31}\})$")
_DECIMAL_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")

_PACKAGE_FIELDS = {
    "contract_version",
    "source",
    "revision",
    "receipts",
    "entities",
    "observations",
    "expected",
}
_ENTITY_FIELDS = {
    "provider",
    "entity_id",
    "kind",
    "parent_entity_id",
    "label",
    "plan",
}
_OBSERVATION_FIELDS = {
    "observation_id",
    "provider",
    "upstream_provider",
    "service",
    "kind",
    "entity_id",
    "start_at",
    "end_at",
    "granularity",
    "model",
    "operation",
    "authority",
    "coverage",
    "resets_at",
    "dimensions",
    "metrics",
}
_METRIC_FIELDS = {"name", "value", "unit"}
_ENTITY_KINDS = {
    "account",
    "organization",
    "workspace",
    "team",
    "project",
    "api_key",
    "endpoint",
}
_OBSERVATION_KINDS = {"usage", "cost", "balance", "quota"}
_GRANULARITIES = {"request", "minute", "hour", "day", "billing_period", "instant", "custom"}
_AUTHORITIES = {
    "official_usage",
    "official_billing",
    "official_quota",
    "client_observed",
    "user_derived",
}
_COVERAGE = {"complete", "partial", "unknown"}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credential",
    "prompt",
    "completion",
    "content",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", text):
        raise ValueError(f"metering {field} requires a timezone-qualified timestamp")
    parsed = parse_ts(text)
    if parsed is None:
        raise ValueError(f"metering {field} is invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: object, *, field: str, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"metering {field} is invalid")
    return text


def _safe_name(value: object, *, field: str, allowed: set[str] | None = None) -> str:
    text = str(value or "").strip().lower()
    if not _NAME_RE.fullmatch(text) or (allowed is not None and text not in allowed):
        raise ValueError(f"metering {field} is invalid")
    return text


def _assert_secret_free(value: object, *, path: str = "package") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if key in _FORBIDDEN_KEYS or key.endswith("_secret") or key.endswith("_password"):
                raise ValueError(f"metering {path}.{raw_key} is not allowed in a secret-free package")
            _assert_secret_free(child, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


def _decimal_text(value: object, *, field: str) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"metering {field} must be an integer or decimal string")
    text = str(value).strip()
    if not _DECIMAL_RE.fullmatch(text):
        raise ValueError(f"metering {field} must be non-negative")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"metering {field} is invalid") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"metering {field} must be non-negative")
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _normalize_entities(values: object) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError("metering entities must be an array")
    if len(values) > MAX_METERING_ENTITIES:
        raise ValueError("too many metering entities in one import")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != _ENTITY_FIELDS:
            raise ValueError(f"metering entity {index} has invalid fields")
        provider = _safe_name(value.get("provider"), field=f"entity {index} provider")
        entity_id = _safe_id(value.get("entity_id"), field=f"entity {index} entity_id")
        kind = _safe_name(value.get("kind"), field=f"entity {index} kind", allowed=_ENTITY_KINDS)
        parent = _safe_id(
            value.get("parent_entity_id"),
            field=f"entity {index} parent_entity_id",
            optional=True,
        )
        label = str(value.get("label") or "").strip()
        plan = str(value.get("plan") or "").strip()
        if len(label) > 256 or len(plan) > 256:
            raise ValueError(f"metering entity {index} display field is too long")
        key = (provider, entity_id)
        if key in seen:
            raise ValueError(f"metering entity {index} is duplicated")
        seen.add(key)
        rows.append(
            {
                "provider": provider,
                "entity_id": entity_id,
                "kind": kind,
                "parent_entity_id": parent,
                "label": label,
                "plan": plan,
            }
        )
    known = {(row["provider"], row["entity_id"]) for row in rows}
    parents = {
        (row["provider"], row["entity_id"]): (
            (row["provider"], row["parent_entity_id"])
            if row["parent_entity_id"]
            else None
        )
        for row in rows
    }
    for index, row in enumerate(rows):
        parent = row["parent_entity_id"]
        if parent and (row["provider"], parent) not in known:
            raise ValueError(f"metering entity {index} parent is not in this revision")
        if parent == row["entity_id"]:
            raise ValueError(f"metering entity {index} cannot be its own parent")
        cursor = (row["provider"], row["entity_id"])
        visited: set[tuple[str, str]] = set()
        while cursor is not None:
            if cursor in visited:
                raise ValueError(f"metering entity {index} parent hierarchy contains a cycle")
            visited.add(cursor)
            cursor = parents.get(cursor)
    return rows


def _normalize_dimensions(value: object, *, index: int) -> dict[str, str | int | bool]:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError(f"metering observation {index} dimensions must be an object")
    normalized: dict[str, str | int | bool] = {}
    for raw_key, raw_value in value.items():
        key = _safe_name(raw_key, field=f"observation {index} dimension")
        if key in _FORBIDDEN_KEYS:
            raise ValueError(f"metering observation {index} dimension {key} is not allowed")
        if isinstance(raw_value, bool):
            child: str | int | bool = raw_value
        elif isinstance(raw_value, int):
            child = raw_value
        elif isinstance(raw_value, str) and len(raw_value) <= 256:
            child = raw_value
        else:
            raise ValueError(f"metering observation {index} dimension {key} is invalid")
        normalized[key] = child
    return dict(sorted(normalized.items()))


def _normalize_metrics(value: object, *, index: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > MAX_METRICS_PER_OBSERVATION:
        raise ValueError(f"metering observation {index} metrics must be a non-empty array")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for metric_index, metric in enumerate(value):
        if not isinstance(metric, dict) or set(metric) != _METRIC_FIELDS:
            raise ValueError(f"metering observation {index} metric {metric_index} has invalid fields")
        name = _safe_name(metric.get("name"), field=f"observation {index} metric name")
        unit = str(metric.get("unit") or "").strip()
        if not _UNIT_RE.fullmatch(unit):
            raise ValueError(f"metering observation {index} metric {name} unit is invalid")
        numeric = _decimal_text(metric.get("value"), field=f"observation {index} metric {name}")
        key = (name, unit)
        if key in seen:
            raise ValueError(f"metering observation {index} metric {name} is duplicated")
        seen.add(key)
        if name.startswith("gen_ai.tokens.") and unit != "{token}":
            raise ValueError(f"metering observation {index} metric {name} must use {{token}}")
        rows.append({"name": name, "value": numeric, "unit": unit})
    return sorted(rows, key=lambda item: (item["name"], item["unit"]))


def _normalize_observations(
    values: object, entities: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("metering observations must be an array")
    if len(values) > MAX_METERING_OBSERVATIONS:
        raise ValueError("too many metering observations in one import")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_signatures: set[str] = set()
    aggregate_ranges: dict[str, list[tuple[datetime, datetime]]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
            raise ValueError(f"metering observation {index} has invalid fields")
        observation_id = _safe_id(
            value.get("observation_id"), field=f"observation {index} observation_id"
        )
        if observation_id in seen_ids:
            raise ValueError(f"metering observation {index} id is duplicated")
        seen_ids.add(observation_id)
        provider = _safe_name(value.get("provider"), field=f"observation {index} provider")
        upstream = _safe_name(
            value.get("upstream_provider"), field=f"observation {index} upstream_provider"
        ) if str(value.get("upstream_provider") or "").strip() else ""
        service = _safe_name(value.get("service"), field=f"observation {index} service")
        kind = _safe_name(value.get("kind"), field=f"observation {index} kind", allowed=_OBSERVATION_KINDS)
        entity_id = _safe_id(
            value.get("entity_id"), field=f"observation {index} entity_id", optional=True
        )
        if entity_id and (provider, entity_id) not in entities:
            raise ValueError(f"metering observation {index} references an unknown entity")
        start_at = _utc_text(value.get("start_at"), field=f"observation {index} start_at")
        end_at = _utc_text(value.get("end_at"), field=f"observation {index} end_at")
        start = parse_ts(start_at)
        end = parse_ts(end_at)
        assert start is not None and end is not None
        if end < start:
            raise ValueError(f"metering observation {index} ends before it starts")
        granularity = _safe_name(
            value.get("granularity"), field=f"observation {index} granularity", allowed=_GRANULARITIES
        )
        if granularity == "instant" and end != start:
            raise ValueError(f"metering observation {index} instant must have one timestamp")
        if granularity not in {"request", "instant"} and end <= start:
            raise ValueError(f"metering observation {index} aggregate interval must be non-empty")
        authority = _safe_name(
            value.get("authority"), field=f"observation {index} authority", allowed=_AUTHORITIES
        )
        coverage = _safe_name(
            value.get("coverage"), field=f"observation {index} coverage", allowed=_COVERAGE
        )
        resets_at = ""
        if str(value.get("resets_at") or "").strip():
            resets_at = _utc_text(value.get("resets_at"), field=f"observation {index} resets_at")
        model = str(value.get("model") or "").strip()
        operation = str(value.get("operation") or "").strip()
        if len(model) > 256 or len(operation) > 128:
            raise ValueError(f"metering observation {index} model or operation is too long")
        dimensions = _normalize_dimensions(value.get("dimensions"), index=index)
        metrics = _normalize_metrics(value.get("metrics"), index=index)
        names = {metric["name"] for metric in metrics}
        token_breakdown = {name for name in names if name.startswith("gen_ai.tokens.")}
        if token_breakdown and "gen_ai.tokens.total" not in names:
            raise ValueError(
                f"metering observation {index} must carry source-defined gen_ai.tokens.total"
            )
        if kind == "cost" and "billing.cost" not in names:
            raise ValueError(f"metering observation {index} cost lacks billing.cost")
        if kind == "balance" and "billing.balance" not in names:
            raise ValueError(f"metering observation {index} balance lacks billing.balance")
        if kind == "quota" and not any(name.startswith("quota.") for name in names):
            raise ValueError(f"metering observation {index} quota lacks a quota metric")
        row = {
            "observation_id": observation_id,
            "provider": provider,
            "upstream_provider": upstream,
            "service": service,
            "kind": kind,
            "entity_id": entity_id,
            "start_at": start_at,
            "end_at": end_at,
            "granularity": granularity,
            "model": model,
            "operation": operation,
            "authority": authority,
            "coverage": coverage,
            "resets_at": resets_at,
            "dimensions": dimensions,
            "metrics": metrics,
        }
        signature = observation_signature(row)
        if signature in seen_signatures:
            raise ValueError(f"metering observation {index} duplicates an earlier observation")
        seen_signatures.add(signature)
        if granularity != "request" and kind in {"usage", "cost"}:
            for metric in metrics:
                series = metric_series_signature(row, metric)
                for prior_start, prior_end in aggregate_ranges.setdefault(series, []):
                    if start < prior_end and prior_start < end:
                        raise ValueError(
                            f"metering observation {index} overlaps an aggregate series in this revision"
                        )
                aggregate_ranges[series].append((start, end))
        rows.append(row)
    return rows


def observation_signature(row: dict[str, Any]) -> str:
    # A source-native id provides idempotence inside that source. It must not let
    # the same measured interval evade cross-source collision detection merely
    # by being renamed by another adapter.
    payload = {
        key: row[key]
        for key in sorted(_OBSERVATION_FIELDS - {"observation_id"})
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def metric_series_signature(row: dict[str, Any], metric: dict[str, str]) -> str:
    payload = {
        "provider": row["provider"],
        "upstream_provider": row["upstream_provider"],
        "service": row["service"],
        "kind": row["kind"],
        "entity_id": row["entity_id"],
        "model": row["model"],
        "operation": row["operation"],
        "dimensions": row["dimensions"],
        "metric": metric["name"],
        "unit": metric["unit"],
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def prepare_metering_package(package: object) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(package, dict) or set(package) != _PACKAGE_FIELDS:
        raise ValueError("metering package has invalid top-level fields")
    if package.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("metering package contract_version must be 3")
    _assert_secret_free(package)
    receipts = package.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("metering package requires non-empty native receipts")
    entities = _normalize_entities(package.get("entities"))
    observations = _normalize_observations(
        package.get("observations"),
        {(row["provider"], row["entity_id"]) for row in entities},
    )
    expected = package.get("expected")
    expected_fields = {"receipts", "entities", "observations", "metrics", "total_tokens"}
    if not isinstance(expected, dict) or set(expected) != expected_fields:
        raise ValueError("metering expected manifest has invalid fields")
    for field in expected_fields:
        value = expected.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"metering expected {field} must be a non-negative integer")
    total_tokens = 0
    metric_count = 0
    for row in observations:
        metric_count += len(row["metrics"])
        if row["kind"] != "usage":
            continue
        for metric in row["metrics"]:
            if metric["name"] == "gen_ai.tokens.total":
                value = Decimal(metric["value"])
                if value != value.to_integral_value():
                    raise ValueError("gen_ai.tokens.total must be an integer")
                total_tokens += int(value)
    actual = {
        "receipts": len(receipts),
        "entities": len(entities),
        "observations": len(observations),
        "metrics": metric_count,
        "total_tokens": total_tokens,
    }
    blockers = [
        {
            "code": "manifest_mismatch",
            "field": field,
            "expected": int(expected[field]),
            "actual": actual[field],
        }
        for field in sorted(expected_fields)
        if int(expected[field]) != actual[field]
    ]
    normalized = {
        "contract_version": CONTRACT_VERSION,
        "source": package.get("source"),
        "revision": package.get("revision"),
        "receipts": receipts,
        "entities": entities,
        "observations": observations,
        "expected": {field: int(expected[field]) for field in sorted(expected_fields)},
    }
    summary = {
        **actual,
        "contract_version": CONTRACT_VERSION,
        "providers": sorted({row["provider"] for row in entities + observations}),
        "services": sorted({row["service"] for row in observations}),
        "kinds": sorted({row["kind"] for row in observations}),
        "first_observation_at": min((row["start_at"] for row in observations), default=None),
        "last_observation_at": max((row["end_at"] for row in observations), default=None),
    }
    return normalized, {
        "summary": summary,
        "blockers": blockers,
        "observation_signatures": sorted(observation_signature(row) for row in observations),
    }


def metering_view(packages: list[dict[str, Any]]) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    signature_owner: dict[str, str] = {}
    exact_collisions: list[dict[str, str]] = []
    observations: list[dict[str, Any]] = []
    for package in packages:
        if package.get("contract_version") != CONTRACT_VERSION:
            continue
        source = str(package.get("source") or "")
        source_observations = list(package.get("observations") or [])
        sources.append(
            {
                "source": source,
                "revision": str(package.get("revision") or ""),
                "entities": len(package.get("entities") or []),
                "observations": len(source_observations),
            }
        )
        for row in source_observations:
            signature = observation_signature(row)
            prior = signature_owner.get(signature)
            if prior and prior != source:
                exact_collisions.append(
                    {"signature": signature, "first_source": prior, "second_source": source}
                )
            else:
                signature_owner[signature] = source
            observations.append({**row, "source": source, "exact_signature": signature})
    total_tokens_by_source: dict[str, int] = {}
    aggregate_series: dict[
        str, list[tuple[datetime, datetime, str, str, str]]
    ] = {}
    for row in observations:
        for metric in row["metrics"]:
            if row["kind"] == "usage" and metric["name"] == "gen_ai.tokens.total":
                total_tokens_by_source[row["source"]] = (
                    total_tokens_by_source.get(row["source"], 0) + int(Decimal(metric["value"]))
                )
            if row["granularity"] == "request" or row["kind"] not in {"usage", "cost"}:
                continue
            start = parse_ts(row["start_at"])
            end = parse_ts(row["end_at"])
            assert start is not None and end is not None
            aggregate_series.setdefault(metric_series_signature(row, metric), []).append(
                (
                    start,
                    end,
                    row["source"],
                    row["observation_id"],
                    row["exact_signature"],
                )
            )
    overlaps: list[dict[str, str]] = []
    overlap_signatures: set[str] = set()
    for series, ranges in aggregate_series.items():
        ordered = sorted(ranges, key=lambda item: (item[0], item[1], item[2], item[3]))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if right[0] >= left[1]:
                    break
                if left[2] == right[2] or not (left[0] < right[1] and right[0] < left[1]):
                    continue
                overlap_signatures.update((left[4], right[4]))
                overlaps.append(
                    {
                        "series": series,
                        "first_source": left[2],
                        "first_observation_id": left[3],
                        "second_source": right[2],
                        "second_observation_id": right[3],
                    }
                )
    accounting_candidate_total_tokens = 0
    for row in observations:
        if row["exact_signature"] in overlap_signatures or row["kind"] != "usage":
            continue
        for metric in row["metrics"]:
            if metric["name"] == "gen_ai.tokens.total":
                accounting_candidate_total_tokens += int(Decimal(metric["value"]))
    return {
        "contract_version": CONTRACT_VERSION,
        "sources": sorted(sources, key=lambda item: item["source"]),
        "entities": [
            {**entity, "source": str(package.get("source") or "")}
            for package in packages
            if package.get("contract_version") == CONTRACT_VERSION
            for entity in package.get("entities") or []
        ],
        "observations": observations,
        "source_total_tokens": dict(sorted(total_tokens_by_source.items())),
        "exact_cross_source_collisions": exact_collisions,
        "aggregate_cross_source_overlaps": overlaps,
        "accounting_candidate_total_tokens": accounting_candidate_total_tokens,
        "included_in_product_totals": False,
        "note": "metering observations remain source-scoped until product reconciliation is enabled",
    }
