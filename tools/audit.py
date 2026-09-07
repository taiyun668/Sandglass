"""Heterogeneous audit: check the panel's numbers by other means than it uses.

Every bug this project has had was found by comparing the panel against a
different method, and every one that survived a while did so because the check
had quietly reused the panel's own logic. So nothing here calls _owns_minute or
_windows_for. Ownership is re-derived from the switch ledger with a different
implementation, and the parse layer is checked against the vendors' own
cumulative counters.

    python -m tools.audit

Exit code is the number of failed checks.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandglass.accounts import (
    claude_identity_runs,
    codex_identity_runs,
    grok_identity_runs,
    load_accounts,
)
from sandglass.collectors import collect_all
from sandglass.models import parse_ts
from sandglass.paths import codex_home, meter_home, state_home_attestation

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    _results.append((PASS if ok else FAIL, name, detail))


def skip(name: str, detail: str) -> None:
    _results.append((SKIP, name, detail))


def audit_runtime_identity() -> None:
    """Refuse to compare against a live desktop from another checkout/build."""

    from sandglass.native_panel import load_native_bridge, native_shell_path
    from sandglass.resources import SOURCE_ROOT, WEB_DIR
    from sandglass.runtime_provenance import (
        build_runtime_identity,
        build_web_runtime_identity,
        process_is_running,
        runtime_identity_matches,
        runtime_provenance,
    )

    actual = runtime_provenance()
    pid = int(actual.get("process_id") or 0)
    if not pid:
        skip("运行本体一致", "没有运行中进程的资源自证，无法验证")
        return
    if not process_is_running(pid):
        skip("运行本体一致", "资源自证对应的进程已结束，无法验证")
        return
    if actual.get("runtime_role") == "web_server":
        expected = build_web_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
        )
        ok = runtime_identity_matches(expected, actual)
        check(
            "运行本体一致",
            ok,
            f"网页服务 HEAD {str((actual.get('source_root') or {}).get('git_head') or '-')[:12]}，"
            f"当前 HEAD {str((expected.get('source_root') or {}).get('git_head') or '-')[:12]}，"
            f"网页 {str((actual.get('web_index') or {}).get('content_sha256') or '-')[:12]}",
        )
        return
    runtime = native_shell_path()
    if runtime is None:
        check("运行本体一致", False, "当前检出缺少原生运行时")
        return
    try:
        bridge, mode, content = load_native_bridge(runtime)
        expected = build_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
            native_runtime=runtime,
            panel_bridge=bridge,
            bridge_content=content,
            bridge_mode=mode,
        )
    except Exception as exc:  # noqa: BLE001
        check("运行本体一致", False, f"当前资源无法解析：{exc}")
        return
    ok = runtime_identity_matches(expected, actual)
    check(
        "运行本体一致",
        ok,
        f"运行 HEAD {str((actual.get('source_root') or {}).get('git_head') or '-')[:12]}，"
        f"当前 HEAD {str((expected.get('source_root') or {}).get('git_head') or '-')[:12]}，"
        f"桥接 {str((actual.get('panel_bridge') or {}).get('content_sha256') or '-')[:12]}",
    )


def audit_observer_coverage() -> None:
    """The post-intervention accounting claim requires a live persisted observer."""
    from sandglass.observer import observer_status

    status = observer_status()
    check(
        "后台观测覆盖",
        status.get("active") is True,
        f"状态 {status.get('state')}，心跳年龄 {status.get('heartbeat_age_seconds')} 秒，"
        f"保留覆盖段 {status.get('retained_runs')}",
    )


def audit_identity_disk_snapshot() -> None:
    """Compare the API's exact disk bytes with an immediate independent read."""
    snapshot = _live_snapshot("身份账本磁盘自证", "attribution_diagnostics")
    if snapshot is None:
        return
    diagnostics = _reading(snapshot, "attribution_diagnostics") or {}
    # Read back at the directory the process said it resolved, not the one this
    # audit spells. When those differ the process is writing into a container's
    # redirected %LOCALAPPDATA%, and comparing against our own path would be
    # comparing two different files and calling the mismatch corruption.
    home = Path(snapshot.get("state_home") or meter_home())

    specs = {
        "claude": ("claude-official-identity-runs.json", "Claude"),
        "codex": (_CODEX_IDENTITY_EVENTS_NAME, "Codex"),
        "grok": ("grok-official-identity-runs.json", "Grok"),
    }
    for provider, (filename, label) in specs.items():
        evidence = (
            ((diagnostics.get("identity_runs") or {}).get(provider) or {}).get("disk")
            or {}
        )
        path = home / filename
        try:
            with path.open("rb") as handle:
                raw = handle.read()
                stat = os.fstat(handle.fileno())
            direct_sha = hashlib.sha256(raw).hexdigest()
            direct_size = len(raw)
            direct_mtime = int(stat.st_mtime_ns)
        except FileNotFoundError:
            direct_sha = hashlib.sha256(b"").hexdigest()
            direct_size = direct_mtime = 0
        except OSError as exc:
            check(f"{label} 身份账本磁盘自证", False, f"直接读取失败：{exc}")
            continue

        same_metadata = (
            int(evidence.get("bytes") or 0) == direct_size
            and int(evidence.get("mtime_ns") or 0) == direct_mtime
        )
        if not same_metadata:
            skip(
                f"{label} 身份账本磁盘自证",
                "API 取样后文件发生变化，无法把两个时刻当作同一份字节",
            )
            continue
        expected_path_sha = hashlib.sha256(
            str(path.resolve()).lower().encode()
        ).hexdigest()
        ok = (
            bool(evidence.get("stable_snapshot"))
            and evidence.get("file_sha256") == direct_sha
            and evidence.get("path_sha256") == expected_path_sha
        )
        check(
            f"{label} 身份账本磁盘自证",
            ok,
            f"字节 {direct_size}，原始 SHA {direct_sha[:12]}，"
            f"取样 {evidence.get('captured_at') or '-'}",
        )


# -- independent ownership -------------------------------------------------


class Ledger:
    """The switch timeline as a sorted array with binary search.

    Deliberately not the linear scan the panel uses: if that scan had an
    off-by-one at a boundary this would disagree with it.
    """

    def __init__(self, runs: list[tuple[str, str]]) -> None:
        pairs = sorted(
            (stamp, who)
            for stamp, who in ((parse_ts(at), who) for at, who in runs)
            if stamp is not None
        )
        self.times = [p[0] for p in pairs]
        self.whos = [p[1] for p in pairs]

    def at(self, moment) -> str:
        i = bisect.bisect_right(self.times, moment) - 1
        return self.whos[i] if i >= 0 else ""

    def explicitly_unassigned_at(self, moment) -> bool:
        """Whether the latest real boundary deliberately cleared ownership."""
        i = bisect.bisect_right(self.times, moment) - 1
        return i >= 0 and self.whos[i] == ""


def owner_of(session, moment, ledger: Ledger, known: set) -> str:
    """Independently apply direct identity before an observation-only ledger."""
    sources = session.extra.get("evidence_sources")
    if session.account_id and isinstance(sources, list) and any(
        str(source).startswith("user_adapter:") for source in sources
    ):
        return session.account_id
    minute_key = (
        moment.astimezone(timezone.utc)
        .replace(second=0, microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    minute_rows = session.extra.get("minute_identity_evidence")
    minute_row = minute_rows.get(minute_key) if isinstance(minute_rows, dict) else None
    direct = str((minute_row or {}).get("account_id") or "") if isinstance(minute_row, dict) else ""
    if direct and direct in known:
        return direct
    who = ledger.at(moment) if ledger.times else ""
    if who and who in known:
        return who
    # ``account_id`` on a Codex rollout can be a session-timeline majority
    # projection.  It summarizes the session, so it must not re-own a minute
    # where the authoritative v2 identity ledger explicitly says there is no
    # account.  The direct cases above are the only account_id evidence this
    # audit accepts: a mapped user adapter or an exact minute identity.
    return ""


def identities(account) -> set:
    return {n for n in (account.email, account.account_id) if n}


def _audit_single_official_owner_id(peers) -> str:
    """Independently mirror the product's active-only single-account rule."""
    active = [peer for peer in peers if peer.active]
    return active[0].account_id if len(active) == 1 else ""


def _audit_official_builtin_only(session) -> bool:
    """Independently identify records eligible for single-account projection."""
    raw = session.extra.get("evidence_sources")
    sources = (
        {
            str(source).strip()
            for source in raw
            if str(source).strip()
        }
        if isinstance(raw, (list, tuple, set))
        else set()
    )
    if not sources and session.provider in {"claude", "codex", "grok"}:
        sources = {f"official_builtin:{session.provider}"}
    return bool(sources) and all(source.startswith("official_builtin:") for source in sources)


# -- what this audit is actually looking at --------------------------------

_APP_MUTEX_NAME = r"Local\Sandglass.Desktop.SingleInstance"
_LEDGER_GLOB = "*identity-runs.json"
_CODEX_IDENTITY_EVENTS_NAME = "codex-official-identity-events-v2.json"
_AUDIT_JSON_MISSING = object()
_AUDIT_JSON_INVALID = object()


def _read_audit_json(path: Path):
    """Read bytes directly so this audit does not share product parsing."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _AUDIT_JSON_MISSING
    except OSError:
        return _AUDIT_JSON_INVALID
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _AUDIT_JSON_INVALID


def _audit_strict_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        stamp = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        return None
    return stamp


def _audit_valid_codex_identity_events(value: object) -> bool:
    """Independent schema-2 validator for the audit instrument."""
    if not isinstance(value, dict) or set(value) != {"schema", "events"}:
        return False
    if value.get("schema") != 2 or not isinstance(value.get("events"), list):
        return False
    previous = None
    for event in value["events"]:
        if not isinstance(event, dict):
            return False
        at = _audit_strict_utc(event.get("at"))
        if at is None or (previous is not None and at <= previous):
            return False
        previous = at
        kind = event.get("kind")
        if kind == "observed":
            if set(event) != {"at", "kind", "account_id"}:
                return False
            if not isinstance(event.get("account_id"), str) or not event["account_id"].strip():
                return False
        elif kind == "unassigned":
            if set(event) != {"at", "kind", "reason"}:
                return False
            if not isinstance(event.get("reason"), str) or not event["reason"].strip():
                return False
        else:
            return False
    return True


def _audit_valid_legacy_identity_rows(value: object, identity_key: str) -> bool:
    """Independently validate one complete legacy identity ledger snapshot."""
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and _audit_strict_utc(item.get("at")) is not None
        and bool(str(item.get(identity_key) or ""))
        for item in value
    )


def _identity_ledger_paths(home: Path) -> list[Path]:
    """Return every supported identity ledger in a state home.

    Codex v2 deliberately has a different filename from the legacy v1 roster.
    Keeping discovery in one helper prevents the state-home checks from
    silently dropping the only ownership-bearing file.
    """
    paths = set(home.glob(_LEDGER_GLOB))
    v2 = home / _CODEX_IDENTITY_EVENTS_NAME
    if v2.is_file():
        paths.add(v2)
    return sorted(paths, key=lambda path: path.name)


def _identity_ledger_rows(path: Path) -> list[tuple[str, str]]:
    """Read normalized identity boundaries without changing the ledger.

    The audit's own schema validator checks Codex v2 independently. The legacy
    Codex list is parsed only for inventory/summary purposes; ownership callers
    explicitly discard it below because it is roster evidence, not a timeline.
    A v2 ``unassigned`` event is represented by an empty owner and therefore
    remains a real boundary that can clear a prior owner.
    """
    value = _read_audit_json(path)
    if value is _AUDIT_JSON_MISSING or value is _AUDIT_JSON_INVALID:
        return []
    if path.name == _CODEX_IDENTITY_EVENTS_NAME:
        if not _audit_valid_codex_identity_events(value):
            return []
        return [
            (
                str(event["at"]),
                str(event.get("account_id") or "")
                if event.get("kind") == "observed"
                else "",
            )
            for event in value["events"]
        ]
    if not isinstance(value, list):
        return []
    key = "to" if path.name == "codex-official-identity-runs.json" else "account_id"
    if not _audit_valid_legacy_identity_rows(value, key):
        return []
    return [
        (str(item["at"]), str(item[key])) for item in value
    ]


def _ledger_summary(home: Path) -> tuple[int, str]:
    """Rows across every identity ledger in one home, and the newest mtime."""
    rows, newest = 0, 0.0
    for path in _identity_ledger_paths(home):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        rows += len(_identity_ledger_rows(path))
        newest = max(newest, stamp)
    return rows, (datetime.fromtimestamp(newest, timezone.utc).isoformat()[:19]
                  if newest else "-")


def _state_homes() -> list[Path]:
    """Every sandglass state directory on this machine that holds a ledger.

    One installation is meant to have exactly one. Windows app containers give
    each package its own redirected %LOCALAPPDATA%, so a Sandglass launched from
    inside one -- an agent's packaged CLI, say -- reads the same environment
    variable, runs the same meter_home(), and lands on a different directory.
    Both then advance, each holding identity events the other never sees, and an
    audit run outside the container reports confidently on the copy that stopped.
    """
    found: dict[str, Path] = {}
    for home in (meter_home(), meter_home().resolve()):
        if home.is_dir() and _identity_ledger_paths(home):
            found[str(home).lower()] = home
    base = os.environ.get("LOCALAPPDATA")
    if base:
        for home in Path(base).glob("Packages/*/LocalCache/Local/sandglass"):
            if home.is_dir() and _identity_ledger_paths(home):
                found[str(home).lower()] = home
    return [found[key] for key in sorted(found)]


def _sandglass_running() -> bool:
    """Whether a desktop instance is holding its single-instance mutex."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenMutexW(0x00100000, False, _APP_MUTEX_NAME)  # SYNCHRONIZE
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def _live_snapshot(name: str, *kinds: str) -> dict | None:
    """The freshest recorded answer that carries every reading this check needs.

    Asking the panel was the old way and it stopped working: the native shell
    serves its pages over an in-process bridge, so there is no socket to call,
    and the two checks that reconcile the panel skipped themselves for three
    days while reporting that the service was down. A running process writes its
    answers down instead, and this reads them.

    A running app with nothing written down is a failure, not an excuse -- these
    are exactly the checks that catch a process answering from something other
    than the file it claims to read. Only a stopped app honestly skips.
    """
    from sandglass.live_snapshot import live_snapshots

    snapshots = live_snapshots()
    for snapshot in snapshots:
        if all(kind in (snapshot.get("readings") or {}) for kind in kinds):
            return snapshot
    if not _sandglass_running():
        skip(name, "Sandglass 未运行")
        return None
    if snapshots:
        have = sorted({k for s in snapshots for k in (s.get("readings") or {})})
        check(name, False,
              f"{len(snapshots)} 个进程写了快照，但都缺 {'/'.join(kinds)}；现有 {have or '无'}")
    else:
        check(name, False, "Sandglass 在运行，但没有任何进程写下可对账的快照")
    return None


def _reading_at(snapshot: dict, kind: str) -> str:
    row = (snapshot.get("readings") or {}).get(kind)
    if isinstance(row, list):
        row = row[-1] if row else None
    return str(row.get("at") or "") if isinstance(row, dict) else ""


def _newer_local_windows(after: str, wait_seconds: float = 75.0):
    """The next local-window reading a live process writes, or None.

    The independent recomputation has to land between two readings of the panel,
    because usage keeps moving while the audit runs. The old code forced the
    second reading by sleeping past the payload cache; nothing here can force a
    process to answer, so this waits for one to write its next.
    """
    from sandglass.live_snapshot import live_snapshots

    deadline = time.monotonic() + wait_seconds
    while True:
        for snapshot in live_snapshots():
            if _reading_at(snapshot, "local_windows") > after:
                return _reading(snapshot, "local_windows")
        if time.monotonic() >= deadline:
            return None
        time.sleep(3)


def _reading(snapshot: dict, kind: str):
    row = (snapshot.get("readings") or {}).get(kind)
    if isinstance(row, list):
        row = row[-1] if row else None
    return row.get("value") if isinstance(row, dict) else None


def _window_totals(value) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for key, row in (value or {}).items():
        account, _, label = str(key).partition("|")
        try:
            out[(account, label)] = int(row["total"] if isinstance(row, dict) else row)
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _window_spans(value) -> dict[tuple[str, str], tuple]:
    """The span the panel says it counted over, per window."""
    out: dict[tuple[str, str], tuple] = {}
    for key, row in (value or {}).items():
        if not isinstance(row, dict):
            continue
        account, _, label = str(key).partition("|")
        out[(account, label)] = (
            parse_ts(row.get("from")), parse_ts(row.get("to")), str(row.get("boundary") or "")
        )
    return out


def _owner_timeline(path: Path) -> list[tuple[datetime, str]]:
    """One ledger as UTC ownership boundaries: (moment, identity), repeats collapsed.

    Rows are not the unit to compare. A ledger legitimately drops an observation
    whose identity matches the one before it -- it opens no new run and moves no
    boundary -- so two homes can hold different rows and still agree completely
    about who owned every minute. What a home knows is its semantic timeline;
    timestamps are parsed before ordering so equivalent UTC spellings compare
    as the same moment.
    """
    # The v1 Codex file is retained as a roster and must never establish
    # ownership. Claude/Grok continue to use their v1 account_id timelines.
    if path.name == "codex-official-identity-runs.json":
        return []
    pairs = []
    for at, who in _identity_ledger_rows(path):
        stamp = _audit_strict_utc(at)
        if stamp is None:
            continue
        pairs.append((stamp, who))
    out: list[tuple[datetime, str]] = []
    for stamp, who in sorted(pairs, key=lambda pair: pair[0]):
        # Empty v2 owners are explicit unassigned boundaries, unlike malformed
        # or incomplete v1 rows, which are simply ignored.
        if not out or out[-1][1] != who:
            out.append((stamp, who))
    return out


def _owner_at(timeline: list[tuple[datetime, str]], moment: object) -> str:
    """Return the owner at a UTC moment using semantic, not lexical, order."""
    target = _audit_strict_utc(moment)
    if target is None:
        return ""
    pairs = []
    for at, who in timeline:
        stamp = _audit_strict_utc(at)
        if stamp is not None:
            pairs.append((stamp, who))
    pairs.sort(key=lambda pair: pair[0])
    times = [stamp for stamp, _ in pairs]
    index = bisect.bisect_right(times, target) - 1
    return pairs[index][1] if index >= 0 else ""


def _timeline_divergence(canonical: Path, other: Path) -> dict[str, int]:
    """Per ledger, the moments the other home knows about and the canonical does not.

    Asymmetric on purpose. A home that holds less is a partial copy and carries
    no risk -- nothing is lost if it disappears. A home that names an owner where
    the canonical names nobody, or names a different one, is holding evidence
    that exists nowhere else, and that is the whole hazard.
    """
    out: dict[str, int] = {}
    for name in sorted({p.name for p in _identity_ledger_paths(other)}):
        mine = _owner_timeline(canonical / name)
        theirs = _owner_timeline(other / name)
        differ = 0
        for at, _ in theirs:
            other_owner = _owner_at(theirs, at)
            if other_owner and _owner_at(mine, at) != other_owner:
                differ += 1
        if differ:
            out[name] = differ
    return out


def audit_state_home_is_canonical() -> None:
    """Whether this audit can see the books at all.

    A Windows app container redirects writes under %LOCALAPPDATA% into its own
    package folder, and the redirection is copy-on-write: the directory still
    resolves to the spelled path, and only a file this process creates reveals
    where its bytes actually land. So an audit run from inside a container reads
    its own copy, compares that copy against itself, and reports agreement --
    which is exactly what happened here for a whole night, 51/51 clean, while
    the app running outside kept its books somewhere this audit never opened.

    Nothing else in this file can be trusted when this check fails, so it runs
    first and says so plainly rather than letting the rest look conclusive.
    """
    result = state_home_attestation(diagnostic=True)
    detail = (
        f"reason={result.get('reason') or '-'}，"
        f"target_sha256={str(result.get('target_path_sha256') or '')[:12]}，"
        f"final_sha256={str(result.get('final_path_sha256') or '')[:12]}"
    )
    check("状态目录本体", bool(result.get("ok")), detail)


def audit_identity_ledgers_readable() -> None:
    """A Sandglass-owned ledger that will not parse must be said out loud.

    The write path refuses to touch a damaged ledger, which is right: replacing
    it with a fresh partial history would destroy the evidence permanently. The
    read path then returns nothing, which is also right -- a partial timeline
    would attribute minutes to whoever it last knew about, and being wrong is
    worse than saying nothing.

    What is missing is anyone saying so. A damaged ledger and a provider that
    was never signed in produce the same empty answer, the same zeroes on the
    panel, and the same unattributed minutes in every other check here. The
    evidence is still on disk the whole time.
    """
    states = {}
    for provider in ("claude", "codex", "grok"):
        for name in (f"{provider}-official-identity-runs.json",
                     f"{provider}-identity-runs.json",
                     f"{provider}-observed-accounts.json"):
            value = _read_audit_json(meter_home() / name)
            if value is _AUDIT_JSON_MISSING:
                continue
            if name.endswith("identity-runs.json"):
                key = "to" if provider == "codex" else "account_id"
                states[name] = (
                    "ok"
                    if _audit_valid_legacy_identity_rows(value, key)
                    else "unreadable"
                )
            else:
                states[name] = "unreadable" if value is _AUDIT_JSON_INVALID else "ok"
        if provider == "codex":
            value = _read_audit_json(meter_home() / _CODEX_IDENTITY_EVENTS_NAME)
            if value is not _AUDIT_JSON_MISSING:
                states[_CODEX_IDENTITY_EVENTS_NAME] = (
                    "ok" if _audit_valid_codex_identity_events(value) else "unreadable"
                )
    broken = sorted(n for n, state in states.items() if state != "ok")
    check("身份账本可读", not broken,
          f"读不出的账本 {broken}" if broken
          else f"{len(states)} 份账本全部可解析")


def audit_observation_accounts(accounts) -> None:
    """Every account the observation store tracks must exist somewhere.

    quota-observations.json drives reset detection and the full-window estimate,
    and nothing else validates what goes into it. An id that appears in no
    roster and in no identity ledger was never signed in on this machine: it is
    a fixture, or a typo, or a fabrication, and it is sitting in the books
    looking exactly like evidence. Two of them arrived from an under-isolated
    test that recorded into the real state directory.

    A signed-out account is not this: the identity ledgers are append-only, so
    it stays known even after the roster drops it.
    """
    known = {a.account_id for a in accounts} | {a.email for a in accounts if a.email}
    for runs in (claude_identity_runs(), codex_identity_runs(), grok_identity_runs()):
        known |= {str(who) for _, who in runs}
    try:
        store = json.loads(
            (meter_home() / "quota-observations.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        skip("配额观测账号可溯", "没有观测库可读")
        return
    if not isinstance(store, dict):
        check("配额观测账号可溯", False, "观测库不是一个对象")
        return
    unknown = set()
    retired = set()
    for key, entry in store.items():
        parts = key.split(":")
        if len(parts) < 3 or parts[1] in known:
            continue
        opened = _book_opened_at(parts[0])
        seen = parse_ts(entry.get("seen_at")) if isinstance(entry, dict) else None
        # An account signed out before that provider's books opened may be
        # genuine and yet leave no trace anywhere else -- there was no ledger
        # to write it to at the time. After the books open there was, so an id
        # nothing else knows was never signed in here.
        if seen is not None and opened is not None and seen < opened:
            retired.add(parts[1])
        else:
            unknown.add(parts[1])
    detail = f"{len(store)} 条观测"
    if retired:
        detail += f"，开账前退役且仅存于观测库的账号 {len(retired)} 个"
    detail += "，无法溯源的账号 " + (str(sorted(unknown)) if unknown else "无")
    check("配额观测账号可溯", not unknown, detail)


def audit_state_home_singleton() -> None:
    """The books must live in one place, or every other check reads a copy.

    What makes a second directory dangerous is not that it exists -- a container
    can mirror the canonical one byte for byte and hold nothing -- but that it
    accumulates rows nobody else can see. So the verdict is about rows held
    nowhere else, and the count of those rows is the size of the blind spot.
    """
    homes = _state_homes()
    if not homes:
        check("状态目录唯一", False, "找不到任何身份账本，无法确定状态目录")
        return
    canonical = meter_home().resolve()
    stray, agreeing = [], []
    for home in homes:
        if home.resolve() == canonical:
            continue
        differ = _timeline_divergence(canonical, home)
        rows, stamp = _ledger_summary(home)
        if differ:
            spread = "，".join(f"{name} {count} 处" for name, count in differ.items())
            stray.append(f"{home}（归属分歧 {spread}，共 {rows} 条，最新 {stamp}）")
        else:
            agreeing.append(f"{home}（{rows} 条，归属一致）")
    rows, _ = _ledger_summary(canonical)
    detail = f"本体 {canonical}（{rows} 条）"
    if stray:
        detail += "；与本体不一致：" + "；".join(stray)
    if agreeing:
        detail += "；一致副本：" + "；".join(agreeing)
    check("状态目录唯一", not stray, detail)


# -- checks ----------------------------------------------------------------


def _codex_books_open_at():
    """When Sandglass was first observing at all, read straight off the disk.

    Codex opened at the first event of its own v2 identity stream -- the file
    whose ownership rule this check exists to test. That made the check
    satisfiable by the thing under test: absent, it skips; one event after a
    reset, it passes on almost nothing. Measured outside the app container on
    this machine, the shipped baseline examined a small fraction of the Codex tokens that the
    first moment Sandglass was observing puts in scope, and nearly all of those
    have no owner. The volumes are a development machine's own usage; the ratio
    is the finding, and the figures are deliberately not published.

    Codex is the only provider with nowhere else to ask. Its rollout files
    carry no identity at all -- session_meta holds id, session_id, originator,
    source, thread_source and cli_version, and nothing about an account -- so
    unlike Claude's observed-account snapshot or Grok's merged CLI log there is
    no vendor record of when this machine first saw a Codex identity. What every
    installation does have is the moment its observer first ran, and that is
    written by a different mechanism than the one under test.

    Measured before choosing this: applying it to all three providers instead
    would cut Grok's checked volume from (figure withheld) to (figure withheld), because
    Grok's ledger is merged out of the vendor's own rotating log and legitimately
    predates the install by eight days. Books open per provider, from whatever
    that provider actually wrote down.
    """
    value = _read_audit_json(meter_home() / "observer-coverage.json")
    if not isinstance(value, dict) or value.get("schema") != 1:
        return None
    for row in value.get("runs") or []:
        if not isinstance(row, dict):
            continue
        opened = _audit_strict_utc(row.get("started_at"))
        if opened is not None:
            return opened
    return None


def _book_opened_at(provider: str):
    """When Sandglass first wrote down this provider's identity, or None.

    The books open per provider, not per installation: a provider Sandglass had
    not yet seen has no identity evidence at all, and the state directory's own
    creation time says nothing about when that changed. Codex has no vendor
    identity record of its own and opens at the first observer run instead --
    see _codex_books_open_at, which also records why that is not applied to the
    others. Its v1 roster and observed-account snapshot remain discovery
    evidence, not ownership or book-opening evidence. Claude and Grok retain
    their existing observed-account plus official-run behavior.
    """
    if provider == "codex":
        return _codex_books_open_at()
    best = None
    names = [f"{provider}-observed-accounts.json",
             f"{provider}-official-identity-runs.json"]
    for name in names:
        try:
            rows = json.loads((meter_home() / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if name == _CODEX_IDENTITY_EVENTS_NAME:
            if not _audit_valid_codex_identity_events(rows):
                continue
            rows = rows["events"]
        elif not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            at = row.get("first_observed_at") or row.get("at")
            if at and (best is None or str(at) < best):
                best = str(at)
    return parse_ts(best) if best else None


def _ledger_rows_for(provider: str) -> list[tuple[str, str]]:
    """The boundaries behind _ledger_for, for callers that need the rows too."""
    if provider == "codex":
        return _identity_ledger_rows(meter_home() / _CODEX_IDENTITY_EVENTS_NAME)
    if provider == "claude":
        return claude_identity_runs()
    return grok_identity_runs()


def _ledger_for(provider: str) -> "Ledger":
    """The switch timeline, read by this instrument rather than by the product.

    Codex used to come from codex_identity_runs(), the product's own projection
    of the v2 stream. That was tolerable while the projection could only *name*
    an owner, because the name was then checked against the account roster
    independently. 094f4ac gave it a second power: an empty owner now silences
    a failure, reclassifying an unattributed minute as a deliberate coverage
    gap. An instrument whose docstring says it re-derives ownership by other
    means must not take that from the thing it is testing, and it does not have
    to -- _identity_ledger_rows validates the same file against this module's
    own schema check.

    Claude and Grok keep their product accessors: those ledgers are plain
    {at, who} lists this module has no separate parse for, and neither can
    silence anything.
    """
    if provider == "codex":
        return Ledger(_identity_ledger_rows(meter_home() / _CODEX_IDENTITY_EVENTS_NAME))
    if provider == "claude":
        return Ledger(claude_identity_runs())
    return Ledger(grok_identity_runs())


def audit_post_open_attribution(sessions, accounts) -> None:
    """After books open, every minute must be owned or explicitly unassigned.

    Coverage over the whole span answers a different question -- how much history
    could be recovered -- and folds this one into the same number. Before Sandglass
    had seen a provider there may be no identity evidence anywhere, and unattributed
    minutes are honest. After it has, a missing owner is a broken promise unless
    an explicit v2 ``unassigned`` boundary proves Sandglass was not observing.

    The two were mixed until a large Grok gap sat inside a passing
    coverage line for days -- the ledger named the owners, the account roster had
    forgotten them, and no check asked the question this one asks.
    """
    for provider in ("claude", "codex", "grok"):
        peers = [a for a in accounts if a.provider == provider]
        if not peers:
            continue
        opened = _book_opened_at(provider)
        if opened is None:
            skip(f"{provider} 开账后归属", "尚无开账记录，无法验证")
            continue
        ledger = _ledger_for(provider)
        known = set().union(*(identities(a) for a in peers))
        total = unattributed = explicit_gap = 0
        for session in sessions:
            if session.provider != provider or not session.timeline:
                continue
            for at, usage in session.timeline:
                moment = parse_ts(at)
                if moment is None or moment < opened:
                    continue
                tokens = usage.total_tokens
                total += tokens
                owner = owner_of(session, moment, ledger, known)
                if owner and any(owner in identities(a) for a in peers):
                    continue
                if provider == "codex" and ledger.explicitly_unassigned_at(moment):
                    explicit_gap += tokens
                else:
                    unattributed += tokens
        detail = (f"开账 {opened.isoformat()[:19]} 起 {total:,}，"
                  f"显式缺口 {explicit_gap:,}，异常未归属 {unattributed:,}")
        if total and explicit_gap == total:
            # Every token in the window sits behind a boundary that says we were
            # not observing. Nothing was checked, so nothing may be reported as
            # having passed -- and one failed identity read on a fresh install
            # writes exactly such a boundary, after which this line printed a
            # green PASS over 100% unattributed usage for as long as it lasted.
            skip(f"{provider} 开账后归属", detail + "（窗口内全部无观测，未验证）")
            continue
        check(f"{provider} 开账后归属", unattributed == 0, detail)


def audit_conservation(sessions, accounts) -> None:
    """Partition every token into zero, one, or multiple account claimants.

    The old check put each minute into one owner bucket and then summed all
    buckets, which equalled the source total by construction.  This version
    measures attribution coverage and separately fails on multiple claimants.

    The rewrite that said that kept the tautology alongside the real assertion:
    `unique + unassigned + duplicated == total` is still true by construction --
    the loop below adds every token to exactly one of the three -- so the
    conjunction could only ever fail on duplicate_minutes, while reading like
    two conditions. It is gone, and what remains is the one thing this can
    actually catch: a minute claimed by more than one account.

    The coverage percentage is disclosure, not a verdict. It reads 1.2% for
    Codex on the development machine and that is honest: those minutes have no
    owner, and the line that fails on unowned volume is 开账后归属, not this one.
    """
    now = datetime.now(timezone.utc)
    spans = [timedelta(hours=h) for h in (6, 18, 47, 96, 24 * 7, 24 * 30)]
    for provider in ("claude", "codex", "grok"):
        peers = [a for a in accounts if a.provider == provider]
        if not peers:
            continue
        ledger = _ledger_for(provider)
        known = set().union(*(identities(a) for a in peers)) if peers else set()
        mine = [s for s in sessions if s.provider == provider and s.timeline]
        for span in spans:
            floor = now - span
            total = 0
            unique = 0
            unassigned = 0
            duplicated = 0
            duplicate_minutes = 0
            for s in mine:
                for at, usage in s.timeline:
                    moment = parse_ts(at)
                    if moment is None or moment < floor:
                        continue
                    tokens = usage.total_tokens
                    total += tokens
                    owner = owner_of(s, moment, ledger, known)
                    claimants = [a for a in peers if owner in identities(a)] if owner else []
                    if not claimants:
                        unassigned += tokens
                    elif len(claimants) == 1:
                        unique += tokens
                    else:
                        duplicated += tokens
                        duplicate_minutes += 1
            coverage = unique / total * 100 if total else 100.0
            check(
                f"{provider} 归属分区 {int(span.total_seconds()//3600)}h",
                duplicate_minutes == 0,
                f"第一方总量 {total:,} 唯一归属 {unique:,} ({coverage:.1f}%) "
                f"未归属 {unassigned:,} 重复 {duplicated:,}/{duplicate_minutes} 分钟",
            )


def _report_accounting_snapshot(report: dict) -> dict:
    """Accounting-bearing report values, excluding timestamps and prose.

    This deliberately lives in the independent audit instead of the product's
    user-source mirror.  If a future implementation wires received evidence
    into totals, ownership, daily activity or a quota-window numerator, the
    before/after snapshots will differ even if the mirror was not updated.
    """
    keys = (
        "totals",
        "by_provider",
        "by_account",
        "by_client",
        "by_day",
        "by_provider_day",
        "unassigned_by_provider",
        "by_block",
        "accounts",
        "sessions",
        "session_count",
        "subagent_count",
    )
    def stable(value):
        if isinstance(value, dict):
            return {
                key: stable(item)
                for key, item in value.items()
                if key != "resets_in_seconds"
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    return {key: stable(report.get(key)) for key in keys}


def audit_user_source_separation(sessions, accounts) -> None:
    """Inject user evidence and measure that first-party accounting is unchanged."""
    from sandglass.report import build_report
    from sandglass.user_sources import UserSourceStore

    current = UserSourceStore().mirror()
    receipts = sum(item["receipts"] for item in current["sources"])
    probe_session = next(iter(sessions), None)
    payload = {
        "provider": getattr(probe_session, "provider", "codex") or "codex",
        "account_id": getattr(probe_session, "account_id", "audit-account")
        or "audit-account",
        "session_id": getattr(probe_session, "session_id", "audit-session")
        or "audit-session",
        "timestamp": getattr(probe_session, "ended_at", None)
        or "2026-08-30T00:00:00Z",
        "input_tokens": 987654321,
        "output_tokens": 123456789,
        "total_tokens": 1111111110,
        "calls": 1,
    }
    with tempfile.TemporaryDirectory() as tmp, patch.dict(
        os.environ, {"SANDGLASS_HOME": tmp}, clear=False
    ):
        before = _report_accounting_snapshot(
            build_report(sessions, accounts=accounts, live_quota=False)
        )
        probe_store = UserSourceStore()
        probe_store.append({"source": "sandglass.audit.isolation", "payload": payload})
        after = _report_accounting_snapshot(
            build_report(sessions, accounts=accounts, live_quota=False)
        )
    check(
        "用户来源独立覆盖",
        before == after,
        f"来源 {len(current['sources'])} 个，收件 {receipts} 条；"
        + (
            "隔离注入前后报表核算快照一致"
            if before == after
            else "隔离注入改变了报表核算快照"
        ),
    )


def audit_user_source_revision_transaction() -> None:
    """Measure preflight, atomic replacement, and rollback across all read views."""

    from pathlib import Path

    from sandglass.telemetry import TelemetryStore
    from sandglass.user_sources import UserSourceStore

    def package(revision: str, suffix: str, tokens: int) -> dict:
        return {
            "source": "audit.revisions",
            "revision": revision,
            "receipts": [{"native": suffix}],
            "accounts": [
                {"provider": "audit-provider", "account_id": f"account-{suffix}"}
            ],
            "quotas": [],
            "records": [
                {
                    "provider": "audit-provider",
                    "event_id": f"event-{suffix}",
                    "timestamp": f"2026-08-30T00:0{suffix}:00Z",
                    "account_id": f"account-{suffix}",
                    "session_id": f"session-{suffix}",
                    "input_tokens": tokens - 1,
                    "output_tokens": 1,
                    "total_tokens": tokens,
                    "calls": 1,
                }
            ],
            "expected": {
                "receipts": 1,
                "accounts": 1,
                "quotas": 0,
                "records": 1,
                "total_tokens": tokens,
            },
        }

    with tempfile.TemporaryDirectory() as tmp, patch.dict(
        os.environ, {"SANDGLASS_HOME": tmp}, clear=False
    ):
        store = UserSourceStore()
        first = package("rev-1", "1", 10)
        second = package("rev-2", "2", 20)
        preflight = store.preflight_import(first)
        no_write = not Path(tmp, "user-sources.sqlite").exists()
        store.commit_import({"package": first})
        store.commit_import(
            {
                "package": second,
                "replace": True,
                "expected_active_revision": "rev-1",
            }
        )
        replaced = (
            [row["account_id"] for row in store.accounts()],
            [row["usage"]["total_tokens"] for row in TelemetryStore().records()],
        )
        store.rollback_import(
            {
                "source": "audit.revisions",
                "target_revision": "rev-1",
                "expected_active_revision": "rev-2",
            }
        )
        restored = (
            [row["account_id"] for row in store.accounts()],
            [row["usage"]["total_tokens"] for row in TelemetryStore().records()],
        )
        revisions = store.import_status("audit.revisions")["source"]["revisions"]
    ok = (
        preflight["ready_to_commit"]
        and no_write
        and replaced == (["account-2"], [20])
        and restored == (["account-1"], [10])
        and len(revisions) == 2
    )
    check(
        "用户来源整包事务",
        ok,
        f"预检无写入 {no_write}；替换 {replaced}；回退 {restored}；保留修订 {len(revisions)}",
    )


def audit_model_api_metering_contract() -> None:
    """Measure direct Token totals, source separation, overlap, and rollback for v3."""

    from pathlib import Path

    from sandglass.user_sources import UserSourceStore

    def package(source: str, revision: str, tokens: int, authority: str) -> dict:
        return {
            "contract_version": 3,
            "source": source,
            "revision": revision,
            "receipts": [{"native_bucket": revision}],
            "entities": [
                {
                    "provider": "audit-api",
                    "entity_id": "project-1",
                    "kind": "project",
                    "parent_entity_id": "",
                    "label": "Audit project",
                    "plan": "api",
                }
            ],
            "observations": [
                {
                    "observation_id": revision,
                    "provider": "audit-api",
                    "upstream_provider": "",
                    "service": "api",
                    "kind": "usage",
                    "entity_id": "project-1",
                    "start_at": "2026-08-31T00:00:00Z",
                    "end_at": "2026-08-31T01:00:00Z",
                    "granularity": "hour",
                    "model": "audit-model",
                    "operation": "generate",
                    "authority": authority,
                    "coverage": "complete" if authority == "official_usage" else "partial",
                    "resets_at": "",
                    "dimensions": {},
                    "metrics": [
                        {"name": "gen_ai.tokens.input", "value": "100", "unit": "{token}"},
                        {"name": "gen_ai.tokens.cache_read", "value": "40", "unit": "{token}"},
                        {"name": "gen_ai.tokens.output", "value": "50", "unit": "{token}"},
                        {"name": "gen_ai.tokens.reasoning", "value": "20", "unit": "{token}"},
                        {"name": "gen_ai.tokens.total", "value": str(tokens), "unit": "{token}"},
                    ],
                }
            ],
            "expected": {
                "receipts": 1,
                "entities": 1,
                "observations": 1,
                "metrics": 5,
                "total_tokens": tokens,
            },
        }

    with tempfile.TemporaryDirectory() as tmp, patch.dict(
        os.environ, {"SANDGLASS_HOME": tmp}, clear=False
    ):
        store = UserSourceStore()
        official = package("audit.api.official", "official-1", 150, "official_usage")
        local = package("audit.api.local", "local-1", 90, "client_observed")
        preflight = store.preflight_import(official)
        no_write = not Path(tmp, "user-sources.sqlite").exists()
        store.commit_import({"package": official})
        store.commit_import({"package": local})
        view = store.metering()
        store.rollback_import(
            {
                "source": "audit.api.local",
                "target_revision": None,
                "expected_active_revision": "local-1",
            }
        )
        restored = store.metering()
    ok = (
        preflight["ready_to_commit"]
        and no_write
        and view["source_total_tokens"]
        == {"audit.api.local": 90, "audit.api.official": 150}
        and bool(view["aggregate_cross_source_overlaps"])
        and view["accounting_candidate_total_tokens"] == 0
        and not view["included_in_product_totals"]
        and restored["source_total_tokens"] == {"audit.api.official": 150}
        and restored["accounting_candidate_total_tokens"] == 150
    )
    check(
        "模型 API 计量接口",
        ok,
        f"预检无写入 {no_write}；来源总量 {view['source_total_tokens']}；"
        f"重叠 {len(view['aggregate_cross_source_overlaps'])}；"
        f"合并候选 {view['accounting_candidate_total_tokens']:,}；"
        f"撤销后 {restored['source_total_tokens']}",
    )


def audit_user_source_collision_gate() -> None:
    """Two sources claiming one unseen minute must never become two candidates."""
    from pathlib import Path

    from sandglass.models import TokenUsage
    from sandglass.telemetry import (
        TelemetryRecord,
        TelemetryStore,
        candidate_user_source_ledger,
    )

    usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
        rows = [
            TelemetryRecord(
                event_key=f"audit-{number}",
                provider="audit-provider",
                event_name="sandglass.usage",
                event_at="2026-08-30T00:00:00Z",
                received_at="2026-08-30T00:00:01Z",
                account_id="",
                session_id="shared-session",
                model="",
                source=f"user_adapter:audit.source-{number}",
                source_version="1",
                schema_version="1",
                evidence_grade="U-B",
                coverage_state="user_unassigned_missing_identity",
                usage=usage,
            )
            for number in (1, 2)
        ]
        store.append(rows)
        result = candidate_user_source_ledger([], store=store)
    ok = (
        result["candidate_only"]
        and not result["included_in_report"]
        and not result["admission_ready"]
        and result["token_candidates"] == 0
        and result["cross_source_collisions"] == 1
        and result["blocked_source_minutes"] == 2
    )
    check(
        "用户来源跨源碰撞门",
        ok,
        f"同一分钟 2 个来源；候选 {result['token_candidates']}，"
        f"跨源碰撞 {result['cross_source_collisions']}，"
        f"阻止 {result['blocked_source_minutes']} 个来源分钟；未并入总量",
    )


def audit_user_source_cross_session_gate() -> None:
    """Distinct sessions may coexist; only the same session can collide across sources."""
    from pathlib import Path

    from sandglass.models import TokenUsage
    from sandglass.telemetry import (
        TelemetryRecord,
        TelemetryStore,
        candidate_user_source_ledger,
    )

    usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
        rows = [
            TelemetryRecord(
                event_key=f"audit-cross-session-{number}",
                provider="audit-provider",
                event_name="sandglass.usage",
                event_at="2026-08-30T00:00:00Z",
                received_at="2026-08-30T00:00:01Z",
                account_id="",
                session_id=f"session-{number}",
                model="",
                source=f"user_adapter:audit.cross-session-{number}",
                source_version="1",
                schema_version="1",
                evidence_grade="U-B",
                coverage_state="user_unassigned_missing_identity",
                usage=usage,
            )
            for number in (1, 2)
        ]
        store.append(rows)
        result = candidate_user_source_ledger(
            [],
            settings={
                "audit.cross-session-1": {"totals_enabled": True},
                "audit.cross-session-2": {"totals_enabled": True},
            },
            store=store,
        )
    ok = (
        result["admission_ready"]
        and result["token_candidates"] == 2
        and result["token_enabled_tokens"] == 20
        and result["concurrent_user_provider_minutes"] == 1
        and result["cross_session_user_ambiguities"] == 0
        and result["blocked_source_minutes"] == 0
    )
    check(
        "用户来源并发会话守恒",
        ok,
        f"同一厂商分钟 2 个会话；候选 {result['token_candidates']}，"
        f"并发分钟 {result['concurrent_user_provider_minutes']}，"
        f"阻止 {result['blocked_source_minutes']} 个来源分钟；各计一次",
    )


def audit_user_identity_supplement() -> None:
    """A mapped exact match may move ownership, but never create Token."""
    from pathlib import Path

    from sandglass.models import Account, SessionRecord, TokenUsage
    from sandglass.report import build_report
    from sandglass.telemetry import (
        TelemetryRecord,
        TelemetryStore,
        apply_user_identity_evidence,
    )

    usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
    session = SessionRecord(
        provider="audit-provider",
        session_id="audit-session",
        path="official-audit.jsonl",
        usage=usage,
        timeline=[("2026-08-30T00:00:00Z", usage)],
    )
    account = Account(
        provider="audit-provider", account_id="audit-account", label="audit-account"
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
        store.append(
            [
                TelemetryRecord(
                    event_key="audit-identity-supplement",
                    provider="audit-provider",
                    event_name="sandglass.usage",
                    event_at="2026-08-30T00:00:00Z",
                    received_at="2026-08-30T00:00:01Z",
                    account_id="",
                    session_id="audit-session",
                    model="",
                    source="user_adapter:audit.identity",
                    source_version="1",
                    schema_version="1",
                    evidence_grade="U-B",
                    coverage_state="user_unassigned_missing_identity",
                    usage=usage,
                )
            ]
        )
        before = build_report([session], accounts=[account], live_quota=False)
        supplemented = apply_user_identity_evidence(
            [session],
            settings={
                "audit.identity": {
                    "mapped_provider": "audit-provider",
                    "mapped_account_id": "audit-account",
                }
            },
            store=store,
        )
        after = build_report(supplemented, accounts=[account], live_quota=False)
        reverted = build_report(
            apply_user_identity_evidence([session], settings={}, store=store),
            accounts=[account],
            live_quota=False,
        )
    totals = [
        report["totals"]["usage"]["total_tokens"]
        for report in (before, after, reverted)
    ]
    account_tokens = [
        report["accounts"][0]["local_usage"]["total_tokens"]
        for report in (before, after, reverted)
    ]
    sources = after["accounts"][0]["local_evidence_sources"]
    ok = (
        totals == [10, 10, 10]
        and account_tokens == [0, 10, 0]
        and sources == [
            "unlabeled:audit-provider",
            "user_adapter:audit.identity",
        ]
    )
    check(
        "用户来源精确补身份",
        ok,
        f"总量 {totals}；账号归属 {account_tokens}；撤销后恢复；来源 {sources}",
    )


def audit_user_token_supplement() -> None:
    """An enabled collision-free B minute is additive, labeled and reversible."""
    from pathlib import Path

    from sandglass.models import SessionRecord, TokenUsage
    from sandglass.report import build_report
    from sandglass.telemetry import (
        TelemetryRecord,
        TelemetryStore,
        apply_user_evidence,
    )

    official_usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
    added_usage = TokenUsage(input_tokens=11, output_tokens=9, calls=1)
    official = SessionRecord(
        provider="audit-provider",
        session_id="official-session",
        path="official-audit.jsonl",
        usage=official_usage,
        timeline=[("2026-08-30T00:00:00Z", official_usage)],
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
        store.append(
            [
                TelemetryRecord(
                    event_key="audit-token-supplement",
                    provider="audit-provider",
                    event_name="sandglass.usage",
                    event_at="2026-08-30T00:01:00Z",
                    received_at="2026-08-30T00:01:01Z",
                    account_id="",
                    session_id="user-session",
                    model="",
                    source="user_adapter:audit.tokens",
                    source_version="1",
                    schema_version="1",
                    evidence_grade="U-B",
                    coverage_state="user_unassigned_missing_identity",
                    usage=added_usage,
                )
            ]
        )
        before = build_report([official], accounts=[], live_quota=False)
        enabled_sessions = apply_user_evidence(
            [official],
            settings={"audit.tokens": {"totals_enabled": True}},
            store=store,
        )
        enabled = build_report(enabled_sessions, accounts=[], live_quota=False)
        reverted = build_report(
            apply_user_evidence([official], settings={}, store=store),
            accounts=[],
            live_quota=False,
        )
    totals = [
        report["totals"]["usage"]["total_tokens"]
        for report in (before, enabled, reverted)
    ]
    sources = enabled["totals"]["evidence_sources"]
    ok = (
        totals == [10, 30, 10]
        and sources == [
            "unlabeled:audit-provider",
            "user_adapter:audit.tokens",
        ]
        and len(enabled_sessions) == 2
    )
    check(
        "用户来源可逆补 Token",
        ok,
        f"总量 {totals}；开启只增加 20，关闭恢复；来源 {sources}",
    )


def audit_user_full_window_authorization() -> None:
    """Full-window permission changes no Token and remains source-labeled."""
    from pathlib import Path

    from sandglass.models import Account, TokenUsage
    from sandglass.report import build_report
    from sandglass.telemetry import (
        TelemetryRecord,
        TelemetryStore,
        apply_user_evidence,
    )

    usage = TokenUsage(input_tokens=70, output_tokens=30, calls=25)
    account = Account(
        provider="audit-provider",
        account_id="audit-account",
        label="audit-account",
        extra={
            "windows": [
                {
                    "label": "5h",
                    "used_percent": 50,
                    "window_start": "2026-08-29T23:00:00Z",
                    "resets_at": "2026-08-30T04:00:00Z",
                }
            ]
        },
    )
    base_setting = {
        "totals_enabled": True,
        "mapped_provider": "audit-provider",
        "mapped_account_id": "audit-account",
    }
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
        store.append(
            [
                TelemetryRecord(
                    event_key="audit-full-window",
                    provider="audit-provider",
                    event_name="sandglass.usage",
                    event_at="2026-08-30T00:00:00Z",
                    received_at="2026-08-30T00:00:01Z",
                    account_id="audit-account",
                    session_id="audit-user-session",
                    model="",
                    source="user_adapter:audit.window",
                    source_version="1",
                    schema_version="1",
                    evidence_grade="U-B",
                    coverage_state="user_attributed",
                    usage=usage,
                )
            ]
        )
        locked = build_report(
            apply_user_evidence(
                [], settings={"audit.window": base_setting}, store=store
            ),
            accounts=[account],
            live_quota=False,
        )
        enabled = build_report(
            apply_user_evidence(
                [],
                settings={
                    "audit.window": {
                        **base_setting,
                        "full_window_enabled": True,
                    }
                },
                store=store,
            ),
            accounts=[account],
            live_quota=False,
        )
        reverted = build_report(
            apply_user_evidence(
                [], settings={"audit.window": base_setting}, store=store
            ),
            accounts=[account],
            live_quota=False,
        )
    rows = [
        report["accounts"][0]["local_in_windows"][0]
        for report in (locked, enabled, reverted)
    ]
    totals = [row["usage"]["total_tokens"] for row in rows]
    permissions = [row["full_window_inference_allowed"] for row in rows]
    sources = [row["evidence_sources"] for row in rows]
    ok = (
        totals == [100, 100, 100]
        and permissions == [False, True, False]
        and sources
        == [["user_adapter:audit.window"]] * 3
    )
    check(
        "用户来源满窗授权",
        ok,
        f"窗口 Token {totals}；授权 {permissions}；来源始终 {sources[0]}",
    )


def audit_timeline_totals(sessions) -> None:
    """A session total and its minute slices must describe the same tokens.

    This structural invariant caught a Claude tool-use turn where requestId was
    wider than one billed provider response. It does not prove the vendor parser
    is right, but it prevents the panel total and attribution partition from
    silently using two different totals.
    """
    for provider in ("claude", "codex", "grok"):
        mine = [s for s in sessions if s.provider == provider and s.timeline is not None]
        mismatches = []
        for session in mine:
            timeline_total = sum(usage.total_tokens for _, usage in session.timeline or [])
            if timeline_total != session.usage.total_tokens:
                mismatches.append(timeline_total - session.usage.total_tokens)
        check(
            f"{provider} 会话总量 vs 分钟线",
            not mismatches,
            f"{len(mine)} 场，不一致 {len(mismatches)}，差值 {sum(mismatches):+,}",
        )


def audit_against_panel(accounts) -> None:
    """The panel's per-window totals, recomputed from scratch.

    The active account keeps working while this runs, so the panel is read once
    before and once after and the independent figure has to land between them.
    A single reading would race the data and fail on nothing but elapsed time.
    """
    snapshot = _live_snapshot(
        "面板对账", "local_windows", "quota_windows", "product_mode"
    )
    if snapshot is None:
        return
    low_value = _reading(snapshot, "local_windows")
    low = _window_totals(low_value)
    spans = _window_spans(low_value)
    low_at = _reading_at(snapshot, "local_windows")
    quota_windows = _reading(snapshot, "quota_windows") or {}
    product_mode = _reading(snapshot, "product_mode") or {}
    # Re-read the logs after that first reading, never before: sessions collected
    # earlier would be older than the number they are being compared against, and
    # the bracket would fail from the bottom for no reason but ordering.
    from sandglass.telemetry import apply_user_evidence
    from sandglass.user_sources import UserSourceStore

    sessions_read_at = datetime.now(timezone.utc)
    sessions = apply_user_evidence(
        collect_all(),
        settings=UserSourceStore().settings(),
    )

    windows = {}
    for key, bounds in quota_windows.items():
        account, _, label = str(key).partition("|")
        start, end = (list(bounds) + [None, None])[:2]
        windows[(account, label)] = (parse_ts(start), parse_ts(end))
    by_id = {a.account_id: a for a in accounts}
    ledgers = {name: _ledger_for(name) for name in ("claude", "codex", "grok")}
    known = defaultdict(set)
    peers_by_provider = defaultdict(list)
    for a in accounts:
        known[a.provider] |= identities(a)
        peers_by_provider[a.provider].append(a)
    current_by_provider = {}
    for provider, peers in peers_by_provider.items():
        current_by_provider[provider] = _audit_single_official_owner_id(peers)
    single_official = product_mode.get("attribution_mode") == "single_official"

    mine_by_key = {}
    for key in sorted(low):
        account = by_id.get(key[0])
        if account is None:
            continue
        mine = [s for s in sessions if s.provider == account.provider and s.timeline]
        # The span the panel declared, not the one the quota surface publishes.
        # When an official window has expired the report re-derives its boundary
        # from the local timeline, and reconciling across the two spans measures
        # nothing but the gap between them.
        start, end, _boundary = spans.get(key, (None, None, ""))
        if start is None:
            start, end = windows.get(key, (None, None))
        if start is None:
            continue
        mark = set(identities(account))
        # Local dates, because that is the calendar the panel buckets its days
        # into. A sum can only be compared against a sum in the same units, and
        # seven hours west of UTC most of a morning lands on the previous day.
        opened = _book_opened_at(account.provider)
        after = _local_day(opened) if opened else ""
        spent = post_open = 0
        for s in mine:
            for at, usage in s.timeline:
                moment = parse_ts(at)
                if moment is None or moment < start:
                    continue
                if end is not None and moment > end:
                    continue
                if single_official and _audit_official_builtin_only(s):
                    owned = current_by_provider.get(account.provider) == account.account_id
                else:
                    owner = owner_of(
                        s, moment, ledgers[account.provider], known[account.provider]
                    )
                    owned = owner in mark
                if owned:
                    spent += usage.total_tokens
                    if _local_day(moment) > after:
                        post_open += usage.total_tokens
        mine_by_key[key] = (account, spent, post_open)

    # A reading is only guaranteed to cover everything up to its own timestamp
    # minus the panel's payload cache, which is what that cache promises and no
    # more. Waiting only for a later reading brackets against a number that may
    # predate the recount, and heavy use during the audit then reads as the
    # panel being behind -- seen as a large positive delta against a panel that was fine.
    from sandglass.serve import _LOCAL_TTL_SECONDS

    covers = sessions_read_at + timedelta(seconds=_LOCAL_TTL_SECONDS)
    high_value = _newer_local_windows(covers.isoformat())
    if high_value is None:
        # The reconciliation needs two readings, taken either side of an
        # independent recount, or it is measuring nothing but the minutes that
        # went by while it ran. A live panel writes one every thirty seconds;
        # when none arrives the finding is that it stopped answering, and
        # reporting a token difference instead blames the panel's arithmetic
        # for the audit's own elapsed time.
        check("面板对账", False,
              f"自 {covers.isoformat()[:19]} 起没有进程写出能覆盖本次重算的读数，无法界定")
        return
    high = _window_totals(high_value)
    diagnostics = None
    for key, (account, spent, post_open) in mine_by_key.items():
        floor = low.get(key, 0)
        ceiling = max(high.get(key, floor), floor)
        name = f"{account.email or account.account_id[:8]} {key[1]}"
        mode = product_mode.get("attribution_mode") or "-"
        opened = _book_opened_at(account.provider)
        after = _local_day(opened) if opened else ""
        panel_low = _promised_days(low_value, key, after)
        panel_high = max(_promised_days(high_value, key, after), panel_low)
        # The claim is about the days the promise covers. The window total is
        # kept in view because it is what the user sees, but a difference inside
        # pre-intervention minutes is two honest methods disagreeing about a
        # minute neither can attribute, and it is not what this is asserting.
        ok = panel_low <= post_open <= panel_high
        detail = (
            f"开账后 面板 {panel_low:,}..{panel_high:,} 独立算 {post_open:,}；"
            f"开账前 面板 {floor - panel_low:,} 独立算 {spent - post_open:,}；模式 {mode}"
        )
        if not ok and diagnostics is None:
            diagnostics = _reading(snapshot, "attribution_diagnostics") or {}
        if not ok and diagnostics:
            evidence = (diagnostics.get("identity_runs") or {}).get(account.provider) or {}
            cache = diagnostics.get("local_windows_cache") or {}
            detail += (
                f"；进程启动 {diagnostics.get('process_started_at') or '-'}，"
                f"身份线 {evidence.get('count', '?')} 段 "
                f"{str(evidence.get('sha256') or '-')[:12]}，"
                f"缓存身份戳匹配 {cache.get('identity_stamp_matches_disk')}"
            )
        check(name, ok, detail if ok else detail + f"；开账后差 {post_open - panel_low:+,}")


_BOUNDARY_KINDS = {
    "official_quota_window",     # the vendor's own period, still current
    "official_reset_anchor",     # a reset we caught ourselves, inside a stale period
    "retained_provider_schedule",  # stale percentage, but the period end is still ahead
    "local_token_timeline",      # nothing official survives; rolled from our own minutes
    "official_period_rollover",  # the published period ended; the next one begins where it did
}


def _local_day(moment) -> str:
    return moment.astimezone().date().isoformat()


def _promised_days(value, key, after: str) -> int:
    """The panel's own daily totals for the days the promise covers.

    Window totals cannot answer this. Before a provider's books open there is
    no identity evidence to attribute a minute with, so the panel withholding
    those minutes is honest and a shortfall inside them proves nothing -- but a
    shortfall large enough to be pre-intervention is not the same as one that
    is, and a total cannot tell the two apart. Days can: everything dated after
    the opening is inside the promise on both sides of the comparison.
    """
    row = (value or {}).get(f"{key[0]}|{key[1]}")
    days = row.get("days") if isinstance(row, dict) else None
    return sum(int(spent) for day, spent in (days or {}).items() if str(day) > after)


def audit_window_boundaries() -> None:
    """Every window must say what span it counted, and mean it.

    Kept apart from the totals on purpose. A 5h window whose official period
    expired is re-rolled from the local timeline by design, and comparing its
    total against the expired period showed up as a 15-million-token
    discrepancy that said nothing about which of the two was wrong.

    What is not allowed is counting over one span while labelling another: when
    a window claims the vendor's own period, the span it counted has to be that
    period.

    It fetches its own snapshot rather than borrowing the panel check's. Hung
    off that one it did not merely skip when no snapshot existed -- it produced
    no line at all, which is worse than a skip: a skip is at least visible.
    """
    snapshot = _live_snapshot("配额窗口边界", "local_windows", "quota_windows")
    if snapshot is None:
        return
    spans = _window_spans(_reading(snapshot, "local_windows"))
    official = {}
    for key, bounds in (_reading(snapshot, "quota_windows") or {}).items():
        account, _, label = str(key).partition("|")
        start, end = (list(bounds) + [None, None])[:2]
        official[(account, label)] = (parse_ts(start), parse_ts(end))
    problems = []
    for key, (start, end, boundary) in sorted(spans.items()):
        name = f"{key[0][:8]} {key[1]}"
        if start is None:
            problems.append(f"{name} 未声明起算点")
            continue
        if boundary not in _BOUNDARY_KINDS:
            problems.append(f"{name} 边界来源不明：{boundary or '空'}")
        if end is not None and end <= start:
            problems.append(f"{name} 终点不晚于起点")
        if boundary == "official_quota_window":
            want_start, want_end = official.get(key, (None, None))
            if want_start is not None and start != want_start:
                problems.append(
                    f"{name} 自称官方窗口，实际从 {start.isoformat()[:19]} 起算，"
                    f"官方是 {want_start.isoformat()[:19]}"
                )
            if want_end is not None and end is not None and end != want_end:
                problems.append(f"{name} 自称官方窗口，终点与官方不一致")
    kinds = sorted({b for _, _, b in spans.values() if b})
    check("配额窗口边界", not problems,
          "；".join(problems) or f"{len(spans)} 个窗口，边界来源 {kinds}，起止自洽")


def audit_parse_layer() -> None:
    """Our per-file totals against the vendor's own per-turn figures.

    Codex writes last_token_usage on every token_count event: what that one turn
    cost, straight from the vendor. That is the baseline. The cumulative
    total_token_usage sitting next to it is not usable as one -- it restarts when
    a conversation resumes in the same file, carries a balance inherited from a
    previous file, and moves on compaction, so reconstructing turns from it
    invents differences that are not there.

    But the vendor repeats itself: some token_count events carry the previous
    event's last_token_usage unchanged, and those are not a second turn. Summing
    every one of them made this check disagree with the parse on 21 of 40 files
    by up to 15.91% -- the instrument over-counting, not the product. Measured
    2026-09-06, after f5cdf48 stopped the collector from billing them.

    A repeated payload is this side's own reason for skipping, and it is a
    different reason from the collector's: that one watches total_token_usage
    stand still. Two rules, two fields, and on all 40 files they agree to the
    byte -- which is the point of checking one against the other rather than
    running the product's rule twice.
    """
    from sandglass.collectors import _parse_codex

    files = sorted((codex_home() / "sessions").rglob("rollout-*.jsonl"),
                   key=lambda p: p.stat().st_mtime)[-40:]
    checked = off = 0
    worst = (None, 0.0)
    for path in files:
        expect = 0
        seen = False
        previous_turn: dict | None = None
        for line in path.open(encoding="utf-8", errors="replace"):
            if "last_token_usage" not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = row.get("payload") or {}
            if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            turn = (payload.get("info") or {}).get("last_token_usage") or {}
            if turn:
                seen = True
                if turn != previous_turn:
                    expect += (int(turn.get("input_tokens") or 0)
                               + int(turn.get("output_tokens") or 0))
                previous_turn = turn
        if not seen or not expect:
            continue
        record = _parse_codex(path)
        got = record.usage.total_tokens if record else 0
        checked += 1
        gap = abs(got - expect) / expect
        if gap > worst[1]:
            worst = (path.name, gap)
        if gap > 0.02:
            off += 1
    check(
        "解析层 vs 厂商每轮",
        off == 0,
        f"{checked} 个文件，超差 {off} 个，最大偏差 {worst[1]*100:.2f}%",
    )


def audit_switch_boundaries(sessions, accounts) -> None:
    """Work between one switch and the next must belong to the account switched to.

    Bounded by the following switch, not by a fixed hour: switching away and back
    inside the same hour is ordinary, and a fixed window would call the minutes
    after the second switch wrong.
    """
    for provider in ("claude", "codex", "grok"):
        runs = _ledger_rows_for(provider)
        peers = [a for a in accounts if a.provider == provider]
        if not peers:
            continue
        known = set().union(*(identities(a) for a in peers))
        ledger = Ledger(runs)
        mine = [s for s in sessions if s.provider == provider and s.timeline]
        strays = 0
        looked = 0
        stamps = sorted(p for p in (parse_ts(at) for at, _ in runs) if p)
        for at, who in runs[-8:]:
            moment = parse_ts(at)
            if moment is None or who not in known:
                continue
            after = [t for t in stamps if t > moment]
            end = min(after[0], moment + timedelta(hours=1)) if after else moment + timedelta(hours=1)
            window = (moment, end)
            for s in mine:
                for stamp, usage in s.timeline:
                    t = parse_ts(stamp)
                    if t is None or not (window[0] <= t < window[1]):
                        continue
                    looked += 1
                    if owner_of(s, t, ledger, known) != who:
                        strays += 1
        check(
            f"{provider} 切号边界",
            strays == 0,
            f"切号到下次切号之间 {looked} 个分钟，归属错的 {strays} 个",
        )


def audit_product_mode_boundary() -> None:
    """Measure the two onboarding paths against the same synthetic minute."""
    import copy

    from sandglass.models import Account, SessionRecord, TokenUsage
    from sandglass.report import build_report

    usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
    at = "2026-08-30T00:00:00Z"
    official = SessionRecord(
        provider="claude",
        session_id="mode-official",
        path="official.jsonl",
        usage=usage,
        timeline=[(at, usage)],
    )
    only = Account(provider="claude", account_id="only", label="only", active=True)
    other = Account(provider="claude", account_id="other", label="other")
    original = copy.deepcopy(official)

    single = build_report(
        [official],
        accounts=[only],
        live_quota=False,
        attribution_mode="single_official",
    )
    assisted = build_report(
        [official],
        accounts=[only],
        live_quota=False,
        attribution_mode="skill_assisted",
    )
    multi = build_report(
        [official],
        accounts=[only, other],
        live_quota=False,
        attribution_mode="single_official",
    )
    rolled_back = build_report(
        [official],
        accounts=[only, other],
        live_quota=False,
        attribution_mode="skill_assisted",
    )
    restored = build_report(
        [official],
        accounts=[only, other],
        live_quota=False,
        attribution_mode="single_official",
    )
    direct = SessionRecord(
        provider="codex",
        session_id="mode-direct",
        path="rollout.jsonl",
        usage=usage,
        timeline=[(at, usage)],
    )
    direct_account = Account(
        provider="codex", account_id="direct", email="direct@example.com", label="direct"
    )
    sibling = Account(provider="codex", account_id="sibling", label="sibling")
    with patch("sandglass.report.switch_runs_for", return_value=[(at, "direct@example.com")]):
        evidenced = build_report(
            [direct],
            accounts=[direct_account, sibling],
            live_quota=False,
            attribution_mode="skill_assisted",
        )

    values = {
        "single": single["accounts"][0]["local_usage"]["total_tokens"],
        "assisted": assisted["accounts"][0]["local_usage"]["total_tokens"],
        "multi_current": next(
            row["local_usage"]["total_tokens"]
            for row in multi["accounts"] if row["account_id"] == "only"
        ),
        "multi_old": next(
            row["local_usage"]["total_tokens"]
            for row in multi["accounts"] if row["account_id"] == "other"
        ),
        "rollback_unassigned": rolled_back["unassigned_by_provider"][0]["usage"]["total_tokens"],
        "restored_current": next(
            row["local_usage"]["total_tokens"]
            for row in restored["accounts"] if row["account_id"] == "only"
        ),
        "direct": next(
            row["local_usage"]["total_tokens"]
            for row in evidenced["accounts"]
            if row["account_id"] == "direct"
        ),
    }
    sources = single["accounts"][0]["local_evidence_sources"]
    ok = values == {
        "single": 10,
        "assisted": 0,
        "multi_current": 10,
        "multi_old": 0,
        "rollback_unassigned": 10,
        "restored_current": 10,
        "direct": 10,
    }
    ok = ok and "sandglass_policy:single_official_account" in sources
    ok = ok and official == original
    check(
        "新用户归属模式边界",
        ok,
        f"单账号 {values['single']}，Skill 账号 {values['assisted']}，"
        f"多账号当前/旧账号 {values['multi_current']}/{values['multi_old']}，"
        f"回退未归属 {values['rollback_unassigned']}，恢复 {values['restored_current']}，"
        f"多账号直接证据 {values['direct']}；会话未改写；来源 {sources}",
    )


def audit_telemetry_shadow(sessions) -> None:
    """Report whether official OTLP evidence can be joined without adding tokens."""
    from sandglass.telemetry import TelemetryStore, reconcile_telemetry_minutes

    store = TelemetryStore()
    identity_records = sum(row["attributed_records"] for row in store.status())
    if not identity_records:
        skip("OTLP 影子匹配", "尚未收到携带账号身份的官方 OTLP 事件")
        return
    result = reconcile_telemetry_minutes(sessions, store)
    check(
        "OTLP 影子匹配",
        result["account_conflicts"] == 0,
        f"身份分钟 {result['identity_groups']}，精确匹配 {result['exact_matches']}，"
        f"数值不符 {result['token_mismatches']}，无本机记录 {result['missing_local']}，"
        f"账号冲突 {result['account_conflicts']}；未并入总量",
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    audit_state_home_is_canonical()
    if _results and _results[-1][0] == FAIL:
        status, name, detail = _results[-1]
        print(f"{status} {name}: {detail}")
        print("审计已停止：状态目录本体未通过")
        return 1
    audit_state_home_singleton()
    accounts = load_accounts()
    audit_identity_ledgers_readable()
    audit_observation_accounts(accounts)
    sessions = collect_all()
    audit_runtime_identity()
    audit_observer_coverage()
    audit_identity_disk_snapshot()
    audit_parse_layer()
    audit_timeline_totals(sessions)
    audit_conservation(sessions, accounts)
    audit_post_open_attribution(sessions, accounts)
    audit_window_boundaries()
    audit_user_source_separation(sessions, accounts)
    audit_user_source_revision_transaction()
    audit_model_api_metering_contract()
    audit_user_source_collision_gate()
    audit_user_source_cross_session_gate()
    audit_user_identity_supplement()
    audit_user_token_supplement()
    audit_user_full_window_authorization()
    audit_product_mode_boundary()
    audit_switch_boundaries(sessions, accounts)
    audit_telemetry_shadow(sessions)
    audit_against_panel(accounts)

    width = max(len(name) for _, name, _ in _results)
    failed = skipped = 0
    for status, name, detail in _results:
        if status == FAIL:
            failed += 1
        elif status == SKIP:
            skipped += 1
        print(f"{status}  {name:<{width}}  {detail}")
    print()
    checked = len(_results) - skipped
    summary = f"{checked - failed}/{checked} 通过"
    if skipped:
        summary += f"，{skipped} 项跳过"
    print(summary)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
