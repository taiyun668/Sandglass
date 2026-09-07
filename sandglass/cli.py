from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sandglass import __version__
from sandglass.accounts import load_accounts
from sandglass.cache import SessionCache
from sandglass.collectors import collect_all
from sandglass.diagnostics import runtime_diagnostics
from sandglass.paths import (
    StateHomeAttestationError,
    cache_db,
    claude_home,
    claude_projects,
    codex_home,
    grok_home,
    require_canonical_state_home,
)
from sandglass.report import build_report, parse_since
from sandglass.serve import loopback_host
from sandglass.telemetry import apply_user_evidence
from sandglass.product_mode import attribution_mode
from sandglass.user_sources import MAX_USER_SOURCE_IMPORT_BODY, UserSourceStore


def main(argv: list[str] | None = None) -> int:
    # Windows may give redirected Python output a legacy code page. The CLI's
    # user-facing notes are Chinese, so make piping and capture deterministic.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="sandglass",
        description="Monitor Codex, Claude, and Grok usage written on this computer.",
    )
    parser.add_argument("--version", action="version", version=f"sandglass {__version__}")
    parser.add_argument("--since", help="Limit local usage to a window, e.g. 7d, 24h, or 2026-08-01")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--rebuild-cache", action="store_true", help="Ignore the session cache and re-read every log")
    parser.add_argument("--offline", action="store_true", help="Skip live quota API calls; use local cached observations only")
    parser.add_argument(
        "--refresh-quota",
        action="store_true",
        help="Bypass the quota cache (Claude 180s; Codex/Grok 90s)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("summary", help="This-machine totals (default)")
    sub.add_parser("daily", help="Usage by local calendar day")
    sub.add_parser("accounts", help="Known local accounts, with per-account local usage")
    sub.add_parser("sessions", help="Recent local sessions")
    sub.add_parser("clients", help="Usage grouped by the client that wrote the log")
    sub.add_parser("quota", help="Live account quota bars (official 5h/7d/weekly)")
    sub.add_parser("blocks", help="Local usage in 5-hour blocks")
    serve = sub.add_parser("serve", help="Open a local dashboard")
    serve.add_argument("--host", default="127.0.0.1", type=loopback_host)
    serve.add_argument("--port", type=int, default=7740)
    serve.add_argument("--no-browser", action="store_true")
    sub.add_parser("doctor", help="Show which local data sources were found")
    user_source = sub.add_parser(
        "user-source", help="Preflight, commit, inspect, or roll back one adapter import"
    )
    user_actions = user_source.add_subparsers(dest="user_source_action", required=True)
    preflight = user_actions.add_parser("preflight", help="Validate one whole package without writes")
    preflight.add_argument("package", type=Path)
    commit = user_actions.add_parser("commit", help="Atomically commit one whole package")
    commit.add_argument("package", type=Path)
    commit.add_argument("--replace", action="store_true")
    commit.add_argument("--expected-active-revision")
    status = user_actions.add_parser("status", help="Show retained and active revisions")
    status.add_argument("--source", default="")
    rollback = user_actions.add_parser("rollback", help="Activate a retained revision or deactivate")
    rollback.add_argument("--source", required=True)
    rollback.add_argument("--expected-active-revision", required=True)
    target = rollback.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-revision")
    target.add_argument("--deactivate", action="store_true")
    args = parser.parse_args(argv)
    command = args.command or "summary"

    # argparse has already handled --version/--help above. Every ordinary
    # command below may read or write Sandglass state, so attest first.
    try:
        require_canonical_state_home()
    except StateHomeAttestationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if command == "doctor":
        return _doctor(args.json)
    if command == "user-source":
        return _user_source_command(args)
    if command == "serve":
        from sandglass.serve import serve as serve_dashboard

        return serve_dashboard(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            since=args.since,
            live_quota=not args.offline,
        )
    if command == "quota":
        return _print_quota(as_json=args.json, offline=args.offline, force=args.refresh_quota)

    report = _load_report(since=args.since, rebuild=args.rebuild_cache, live_quota=not args.offline, force_quota=args.refresh_quota)
    if args.json or command == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if command == "summary":
        _print_summary(report)
    elif command == "daily":
        _print_table(
            ["Day", "Sessions", "Tokens", "Providers"],
            [
                [row["key"], row["sessions"], _fmt_int(row["usage"]["total_tokens"]), ",".join(row["providers"])]
                for row in report["by_day"][-31:]
            ],
        )
    elif command == "accounts":
        _print_accounts(report)
    elif command == "sessions":
        _print_table(
            ["When", "Provider", "Client", "Account", "Tokens"],
            [
                [
                    (row.get("ended_at") or row.get("started_at") or "")[:19].replace("T", " "),
                    row["provider"],
                    row["client"],
                    row["account_label"] or "unassigned",
                    _fmt_int(row["usage"]["total_tokens"]),
                ]
                for row in report["sessions"][:40]
            ],
        )
    elif command == "clients":
        _print_table(
            ["Client", "Sessions", "Tokens", "Providers"],
            [
                [row["key"], row["sessions"], _fmt_int(row["usage"]["total_tokens"]), ",".join(row["providers"])]
                for row in report["by_client"]
            ],
        )
    elif command == "blocks":
        _print_table(
            ["Block (UTC)", "Now", "Sessions", "Tokens", "Providers"],
            [
                [
                    (row.get("start") or "")[:16].replace("T", " "),
                    "*" if row.get("current") else "",
                    row["sessions"],
                    _fmt_int(row["usage"]["total_tokens"]),
                    ",".join(row["providers"]),
                ]
                for row in report.get("by_block") or []
            ],
        )
    return 0


def _read_import_package(path: Path) -> object:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read import package: {exc}") from exc
    if len(payload) > MAX_USER_SOURCE_IMPORT_BODY:
        raise ValueError("user source import package is too large")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("import package must be UTF-8 JSON") from exc


def _user_source_command(args: argparse.Namespace) -> int:
    store = UserSourceStore()
    try:
        if args.user_source_action == "preflight":
            result = store.preflight_import(_read_import_package(args.package))
        elif args.user_source_action == "commit":
            envelope: dict[str, Any] = {
                "package": _read_import_package(args.package),
                "replace": bool(args.replace),
            }
            if args.expected_active_revision is not None:
                envelope["expected_active_revision"] = args.expected_active_revision
            result = store.commit_import(envelope)
        elif args.user_source_action == "status":
            result = store.import_status(args.source)
        else:
            result = store.rollback_import(
                {
                    "source": args.source,
                    "target_revision": None
                    if args.deactivate
                    else args.target_revision,
                    "expected_active_revision": args.expected_active_revision,
                }
            )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _load_report(since: str | None = None, rebuild: bool = False, live_quota: bool = True, force_quota: bool = False) -> dict[str, Any]:
    cache = SessionCache()
    if rebuild:
        cache._conn.execute("DELETE FROM sessions")
        cache.commit()
    last = ["", 0, 0]

    def progress(provider: str, index: int, total: int) -> None:
        if total <= 0:
            return
        if index == total or index == 1 or index % 25 == 0:
            msg = f"\rindexing {provider} {index}/{total}"
            sys.stderr.write(msg.ljust(60))
            sys.stderr.flush()
            last[0] = provider

    sessions = collect_all(cache=cache, progress=progress)
    if last[0]:
        sys.stderr.write("\r" + " " * 60 + "\r")
        sys.stderr.flush()
        sys.stdout.flush()
    cache.close()
    sessions = apply_user_evidence(
        sessions,
        settings=UserSourceStore().settings(),
    )
    if live_quota and force_quota:
        from sandglass.quota import attach_live_quota
        from sandglass.accounts import load_accounts as _load_accounts

        return build_report(
            sessions,
            since=parse_since(since),
            accounts=attach_live_quota(_load_accounts(), force=True),
            live_quota=False,
            attribution_mode=attribution_mode(),
        )
    return build_report(
        sessions,
        since=parse_since(since),
        live_quota=live_quota,
        attribution_mode=attribution_mode(),
    )


def _print_summary(report: dict[str, Any]) -> None:
    totals = report["totals"]
    usage = totals["usage"]
    new_tokens = int(usage["input_tokens"]) + int(usage["output_tokens"])
    print("sandglass  ·  this machine only")
    subagents = int(totals.get("subagents") or report.get("subagent_count") or 0)
    extra = f"  ·  subagents {subagents}" if subagents else ""
    print(
        f"Sessions {totals['sessions']}{extra}  ·  new {_fmt_int(new_tokens)}  ·  cache {_fmt_int(usage['cache_read_tokens'])}  ·  all {_fmt_int(usage['total_tokens'])}"
    )
    print()
    _print_table(
        ["Provider", "Sessions", "Tokens", "Input", "Output", "Cache read"],
        [
            [
                row["key"],
                row["sessions"],
                _fmt_int(row["usage"]["total_tokens"]),
                _fmt_int(row["usage"]["input_tokens"]),
                _fmt_int(row["usage"]["output_tokens"]),
                _fmt_int(row["usage"]["cache_read_tokens"]),
            ]
            for row in report["by_provider"]
        ],
    )
    print()
    print("By client (whatever wrote a local log)")
    _print_table(
        ["Client", "Sessions", "Tokens"],
        [
            [row["key"], row["sessions"], _fmt_int(row["usage"]["total_tokens"])]
            for row in report["by_client"]
        ],
    )
    print()
    print("By account  (quota bar = that account's current plan usage)")
    _print_accounts(report)
    print()
    print("Notes")
    for note in report["notes"]:
        print(f"  - {note}")


def _print_accounts(report: dict[str, Any]) -> None:
    rows = []
    for account in report["accounts"]:
        windows = account.get("current_usage") or account.get("windows") or []
        rows.append(
            [
                account["provider"],
                ("*" if account.get("active") else " ") + (account.get("label") or account.get("account_id")),
                account.get("plan") or "",
                _fmt_window(windows, ("5h", "primary")),
                _fmt_window(windows, ("7d", "weekly", "30d")),
                account.get("quota_source") or "",
                _fmt_int((account.get("local_usage") or {}).get("total_tokens") or 0),
            ]
        )
    _print_table(["Provider", "Account", "Plan", "5h used", "7d used", "Source", "Current log tokens"], rows)


def _print_quota(as_json: bool, offline: bool, force: bool) -> int:
    from sandglass.accounts import load_accounts
    from sandglass.quota import attach_live_quota, fetch_all_quotas

    if as_json:
        json.dump(fetch_all_quotas(force=force) if not offline else {}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    accounts = load_accounts() if offline else attach_live_quota(load_accounts(), force=force)
    rows = []
    for account in accounts:
        windows = account.extra.get("windows") or []
        from sandglass.pace import annotate_windows

        windows = annotate_windows(windows)
        rows.append(
            [
                account.provider,
                ("*" if account.active else " ") + (account.label or account.account_id),
                account.plan or "",
                _fmt_window(windows, ("5h", "primary")),
                _fmt_window(windows, ("7d", "weekly", "30d")),
                _fmt_reset(windows),
                account.extra.get("quota_source") or ("cached" if windows else "—"),
            ]
        )
    print("sandglass quota  ·  official plan windows")
    _print_table(["Provider", "Account", "Plan", "5h", "7d", "Resets", "Source"], rows)
    return 0


def _doctor(as_json: bool) -> int:
    info = {
        "claude_home": str(claude_home()),
        "claude_projects": str(claude_projects()),
        "claude_projects_exists": claude_projects().exists(),
        "codex_home": str(codex_home()),
        "codex_sessions_exists": (codex_home() / "sessions").exists(),
        "grok_home": str(grok_home()),
        "grok_sessions_exists": (grok_home() / "sessions").exists(),
        "cache": str(cache_db()),
        "runtime_diagnostics": runtime_diagnostics(),
        "accounts": [
            {
                "provider": a.provider,
                "label": a.label,
                "plan": a.plan,
                "active": a.active,
                "auth_mode": a.auth_mode,
            }
            for a in load_accounts()
        ],
    }
    if as_json:
        json.dump(info, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print("sandglass doctor")
    for key, value in info.items():
        if key == "accounts":
            continue
        print(f"  {key}: {value}")
    print("  accounts:")
    if not info["accounts"]:
        print("    (none found)")
    for account in info["accounts"]:
        mark = "*" if account["active"] else " "
        print(f"   {mark}{account['provider']:6}  {account['label']}  plan={account['plan'] or '-'}  auth={account['auth_mode']}")
    return 0


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    str_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    if not str_rows:
        print("(none)")
        return
    for row in str_rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def _fmt_reset(windows: list[dict[str, Any]]) -> str:
    future = [w for w in windows if w.get("resets_in") and w.get("resets_in") != "now"]
    pool = future or windows
    for window in pool:
        label = str(window.get("label") or "")
        if label in {"5h", "primary"} and window.get("resets_in"):
            return f"5h {window['resets_in']}"
    for window in pool:
        if window.get("resets_in"):
            return f"{window.get('label')} {window['resets_in']}"
    return "—"


def _fmt_window(windows: list[dict[str, Any]], labels: tuple[str, ...] = (), extra: bool = False) -> str:
    known = {"5h", "primary", "7d", "weekly"}
    for window in windows:
        label = str(window.get("label") or "")
        pct = window.get("used_percent")
        if not isinstance(pct, (int, float)):
            continue
        if extra:
            if label in known:
                continue
            return f"{label}:{int(pct)}%"
        if label in labels:
            return f"{int(pct)}%"
    return "—"


def _fmt_int(value: int) -> str:
    return f"{int(value):,}"


if __name__ == "__main__":
    raise SystemExit(main())
