import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from sandglass.accounts import load_accounts, note_current_identity
from sandglass.capabilities import provider_capabilities
from sandglass.collectors import collect_all
from sandglass.diagnostics import record_component_failure
from sandglass.quota import fetch_all_quotas
from sandglass.signals import QuotaSignalMonitor
from sandglass.serve import api_payload
from sandglass.telemetry import ingest_otlp_logs, telemetry_status
from sandglass.user_sources import UserSourceStore


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    out = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        stat = path.stat()
        out[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size, path.read_bytes())
    return out


def _otel_payload() -> bytes:
    request = ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    service = resource_logs.resource.attributes.add()
    service.key = "service.name"
    service.value.string_value = "claude-code"
    log = resource_logs.scope_logs.add().log_records.add()
    log.time_unix_nano = 1_787_932_800_000_000_000
    log.event_name = "claude_code.api_request"
    for key, value in {
        "request_id": "read-only-test",
        "user.account_id": "claude-1",
        "input_tokens": 1,
        "output_tokens": 1,
    }.items():
        item = log.attributes.add()
        item.key = key
        if isinstance(value, int):
            item.value.int_value = value
        else:
            item.value.string_value = value
    return request.SerializeToString()


class ReadOnlyBoundaryTests(unittest.TestCase):
    def test_normal_observation_never_changes_vendor_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            codex = root / "codex"
            grok = root / "grok"
            community_grok_app = root / "community-grok-app-accounts"
            sandglass = root / "sandglass-data"
            for directory in (claude, codex / "accounts", grok, community_grok_app / "profile-1"):
                directory.mkdir(parents=True)

            (claude / ".credentials.json").write_text(
                json.dumps({"claudeAiOauth": {"accessToken": "claude-access"}}), encoding="utf-8"
            )
            (claude / ".claude.json").write_text(
                json.dumps({"oauthAccount": {"emailAddress": "claude@example.com", "accountUuid": "claude-1"}}),
                encoding="utf-8",
            )
            (codex / "auth.json").write_text(
                json.dumps({"tokens": {"access_token": "codex-access", "account_id": "codex-1"}}),
                encoding="utf-8",
            )
            (codex / "accounts" / "registry.json").write_text(
                json.dumps(
                    {
                        "active_account_key": "account-1",
                        "active_account_activated_at_ms": 1787932800000,
                        "accounts": [
                            {
                                "account_key": "account-1",
                                "chatgpt_account_id": "codex-1",
                                "email": "codex@example.com",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            grok_auth = {
                "https://auth.x.ai::grok-1": {
                    "email": "grok@example.com",
                    "user_id": "grok-1",
                    "auth_mode": "oidc",
                    "key": "grok-access",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            }
            (grok / "auth.json").write_text(json.dumps(grok_auth), encoding="utf-8")
            (community_grok_app / "profile-1" / "auth.json").write_text(
                json.dumps(grok_auth), encoding="utf-8"
            )
            (community_grok_app / "index.json").write_text(
                json.dumps(
                    {
                        "activeId": "profile-1",
                        "profiles": [
                            {
                                "id": "profile-1",
                                "email": "grok@example.com",
                                "updatedAt": "2026-08-28T12:00:00Z",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            env = {
                "CLAUDE_CONFIG_DIR": str(claude),
                "CODEX_HOME": str(codex),
                "GROK_HOME": str(grok),
                "SANDGLASS_HOME": str(sandglass),
            }
            vendor_roots = (claude, codex, grok, community_grok_app)
            before = [_tree_snapshot(path) for path in vendor_roots]
            with patch.dict(os.environ, env, clear=False), patch(
                "sandglass.quota._get", return_value=({}, "offline")
            ):
                discovered = load_accounts()
                provider_capabilities(discovered)
                collect_all()
                note_current_identity()
                QuotaSignalMonitor().poll()
                fetch_all_quotas(force=True)
                ingest_otlp_logs(_otel_payload())
                telemetry_status()
                UserSourceStore().append(
                    {"source": "boundary.test", "payload": {"native": True}}
                )
                UserSourceStore().append_accounts(
                    {
                        "source": "boundary.test",
                        "accounts": [
                            {"provider": "codex", "account_id": "codex-1"}
                        ],
                    }
                )
                UserSourceStore().append_quotas(
                    {
                        "source": "boundary.test",
                        "quotas": [
                            {
                                "provider": "codex",
                                "account_id": "codex-1",
                                "fetched_at": "2026-08-30T00:00:00Z",
                                "windows": [{"label": "7d", "used_percent": 20}],
                            }
                        ],
                    }
                )
                UserSourceStore().configure("boundary.test", display_enabled=False)
                import_store = UserSourceStore()
                first_package = {
                    "source": "boundary.whole-import",
                    "revision": "rev-1",
                    "receipts": [{"native": "one"}],
                    "accounts": [
                        {"provider": "codex", "account_id": "adapter-codex-1"}
                    ],
                    "quotas": [],
                    "records": [
                        {
                            "provider": "codex",
                            "event_id": "boundary-event-1",
                            "timestamp": "2026-08-30T00:00:00Z",
                            "account_id": "adapter-codex-1",
                            "session_id": "boundary-session-1",
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                            "calls": 1,
                        }
                    ],
                    "expected": {
                        "receipts": 1,
                        "accounts": 1,
                        "quotas": 0,
                        "records": 1,
                        "total_tokens": 2,
                    },
                }
                second_package = json.loads(json.dumps(first_package))
                second_package["revision"] = "rev-2"
                second_package["receipts"] = [{"native": "two"}]
                second_package["records"][0]["event_id"] = "boundary-event-2"
                second_package["records"][0]["session_id"] = "boundary-session-2"
                import_store.preflight_import(first_package)
                import_store.commit_import({"package": first_package})
                import_store.commit_import(
                    {
                        "package": second_package,
                        "replace": True,
                        "expected_active_revision": "rev-1",
                    }
                )
                import_store.import_status("boundary.whole-import")
                import_store.rollback_import(
                    {
                        "source": "boundary.whole-import",
                        "target_revision": "rev-1",
                        "expected_active_revision": "rev-2",
                    }
                )
                metering_package = {
                    "contract_version": 3,
                    "source": "boundary.api-metering",
                    "revision": "meter-1",
                    "receipts": [{"native_bucket": "one"}],
                    "entities": [
                        {
                            "provider": "example-api",
                            "entity_id": "project-1",
                            "kind": "project",
                            "parent_entity_id": "",
                            "label": "Project",
                            "plan": "api",
                        }
                    ],
                    "observations": [
                        {
                            "observation_id": "bucket-1",
                            "provider": "example-api",
                            "upstream_provider": "",
                            "service": "api",
                            "kind": "usage",
                            "entity_id": "project-1",
                            "start_at": "2026-08-30T00:00:00Z",
                            "end_at": "2026-08-30T01:00:00Z",
                            "granularity": "hour",
                            "model": "model-1",
                            "operation": "generate",
                            "authority": "official_usage",
                            "coverage": "complete",
                            "resets_at": "",
                            "dimensions": {},
                            "metrics": [
                                {
                                    "name": "gen_ai.tokens.total",
                                    "value": "2",
                                    "unit": "{token}",
                                }
                            ],
                        }
                    ],
                    "expected": {
                        "receipts": 1,
                        "entities": 1,
                        "observations": 1,
                        "metrics": 1,
                        "total_tokens": 2,
                    },
                }
                import_store.preflight_import(metering_package)
                import_store.commit_import({"package": metering_package})
                import_store.metering()
                api_payload(
                    "/api/user-sources/official-minutes?provider=codex",
                    live_quota=False,
                )
                record_component_failure("native_panel", RuntimeError("synthetic component failure"))
            after = [_tree_snapshot(path) for path in vendor_roots]

            self.assertEqual(after, before)
            codex_accounts = [account for account in discovered if account.provider == "codex"]
            self.assertEqual([account.account_id for account in codex_accounts], ["codex-1"])
            self.assertTrue(codex_accounts[0].extra.get("account_source_official"))
            grok_accounts = [account for account in discovered if account.provider == "grok"]
            self.assertEqual([account.account_id for account in grok_accounts], ["grok-1"])
            self.assertTrue(all(account.extra.get("account_source_official") for account in grok_accounts))
            self.assertTrue((sandglass / "cache.sqlite").exists())
            self.assertTrue((sandglass / "quota-cache.json").exists())
            self.assertTrue((sandglass / "telemetry.sqlite").exists())
            self.assertTrue((sandglass / "runtime-diagnostics.json").exists())

    def test_public_package_has_no_private_product_source(self):
        package = Path(__file__).resolve().parents[1] / "sandglass"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
        for forbidden in (
            "GrokWorkerProvider",
            "codex-auth",
            'codex_home() / "accounts" / "registry.json"',
            "GROK_WORKER_HOMES",
            "GROK_APP_ACCOUNTS",
            "official_grok_app",
            "grok_app_accounts_dir",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
