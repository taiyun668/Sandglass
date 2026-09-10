"""Desktop shell: a floating orb on the desktop, the panel behind it, a tray icon.

Optional. Needs pywebview + pystray + pillow. The CLI, the collectors and the
plain `serve` command do not import this module and stay dependency-free.

The orb is a Win32 layered window (see sandglass.orb) rather than a webview:
pywebview clamps windows to a 200px minimum width and drops its transparent
layer on resize, neither of which a 56px circle survives. The panel is a
pywebview window, which is what pywebview is actually good at.

The orb remains visible while the panel is open and acts as its toggle and
movement handle. Closing the panel leaves only the tray icon. A later tray
click parks the orb at the primary work-area corner and opens the panel from
there -- the same place a first launch uses, not the last drag. Closing the UI
does not stop the independent Observer; the tray exposes a separate explicit
"stop monitoring and exit" action for that boundary.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
from ctypes import wintypes
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from sandglass.orb import (DOCK_PEEK, EdgeDock, Orb, activate_existing_orb,
                           animate_window_reveal,
                           clamp_to_screen, clear_window_region, default_orb_pos,
                           drag_window,
                           hide_from_taskbar, keep_on_screen, monitor_scale, monitor_work_area,
                           place_beside, primary_work_area, render, set_window_icon,
                           set_window_reveal, system_scale, window_rect,
                           window_scale)
from sandglass.diagnostics import clear_component_failure, record_component_failure
from sandglass.native_panel import (
    NativePanel,
    load_native_bridge,
    native_shell_path,
)
from sandglass import live_snapshot
from sandglass.accounts import state_file_lock
from sandglass.paths import StateHomeAttestationError, meter_home, require_canonical_state_home
from sandglass.product_mode import set_attribution_mode
from sandglass.serve import (
    WEB_DIR,
    Handler,
    OtlpHandler,
    api_payload,
    configure_user_source,
    _attribution_self_check_watch_context,
)
from sandglass.resources import SOURCE_ROOT
from sandglass.runtime_provenance import (
    build_runtime_identity,
    build_web_runtime_identity,
    record_runtime_identity,
    runtime_identity_matches,
    runtime_provenance,
)

ORB = 56                      # logical px, the size the user picked
PANEL_W, PANEL_H = 380, 1040  # enlarged month heatmap stays fully visible
HOST = "127.0.0.1"
PREFERRED_PORT = 7740
NATIVE_APP_URL = "https://sandglass.local/index.html"
INSTANCE_MUTEX_NAME = r"Local\Sandglass.Desktop.SingleInstance"
APP_USER_MODEL_ID = "Ayun.Sandglass.Desktop"
ERROR_ALREADY_EXISTS = 183
TELEMETRY_PROVIDERS = ("claude", "codex", "grok")

kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]

DESKTOP_TEXT = {
    "zh-CN": {
        "open_panel": "打开面板",
        "toggle_orb": "显示/隐藏悬浮球",
        "autostart": "开机启动",
        "quit": "关闭界面",
        "stop_monitoring": "停止监测并退出",
    },
    "zh-TW": {
        "open_panel": "開啟面板",
        "toggle_orb": "顯示/隱藏懸浮球",
        "autostart": "登入時啟動",
        "quit": "關閉介面",
        "stop_monitoring": "停止監測並結束",
    },
    "en-US": {
        "open_panel": "Open panel",
        "toggle_orb": "Show/hide floating orb",
        "autostart": "Start at login",
        "quit": "Close interface",
        "stop_monitoring": "Stop monitoring and exit",
    },
    "es-ES": {
        "open_panel": "Abrir panel",
        "toggle_orb": "Mostrar/ocultar esfera",
        "autostart": "Iniciar al entrar",
        "quit": "Cerrar interfaz",
        "stop_monitoring": "Detener monitoreo y salir",
    },
    "fr-FR": {
        "open_panel": "Ouvrir le panneau",
        "toggle_orb": "Afficher/masquer l’orbe",
        "autostart": "Démarrer à la connexion",
        "quit": "Fermer l’interface",
        "stop_monitoring": "Arrêter le suivi et quitter",
    },
    "de-DE": {
        "open_panel": "Panel öffnen",
        "toggle_orb": "Kugel ein-/ausblenden",
        "autostart": "Bei Anmeldung starten",
        "quit": "Oberfläche schließen",
        "stop_monitoring": "Überwachung stoppen und beenden",
    },
    "pt-BR": {
        "open_panel": "Abrir painel",
        "toggle_orb": "Mostrar/ocultar esfera",
        "autostart": "Iniciar ao entrar",
        "quit": "Fechar interface",
        "stop_monitoring": "Parar monitoramento e sair",
    },
    "ru-RU": {
        "open_panel": "Открыть панель",
        "toggle_orb": "Показать/скрыть сферу",
        "autostart": "Запускать при входе",
        "quit": "Закрыть интерфейс",
        "stop_monitoring": "Остановить мониторинг и выйти",
    },
    "ja-JP": {
        "open_panel": "パネルを開く",
        "toggle_orb": "フローティングオーブを表示/非表示",
        "autostart": "ログイン時に起動",
        "quit": "画面を閉じる",
        "stop_monitoring": "監視を停止して終了",
    },
    "ko-KR": {
        "open_panel": "패널 열기",
        "toggle_orb": "플로팅 오브 표시/숨기기",
        "autostart": "로그인 시 시작",
        "quit": "인터페이스 닫기",
        "stop_monitoring": "모니터링 중지 후 종료",
    },
}


def _desktop_locale(value: object) -> str:
    locale = str(value or "").replace("_", "-")
    if locale in DESKTOP_TEXT:
        return locale
    language = locale.split("-", 1)[0].lower()
    return {
        "zh": "zh-CN",
        "en": "en-US",
        "es": "es-ES",
        "fr": "fr-FR",
        "de": "de-DE",
        "pt": "pt-BR",
        "ru": "ru-RU",
        "ja": "ja-JP",
        "ko": "ko-KR",
    }.get(language, "zh-CN")


def _state_path() -> Path:
    return meter_home() / "desktop.json"


def _webview_start_options() -> dict[str, object]:
    """Keep panel preferences between launches, inside Sandglass's own home."""
    return {
        "gui": "edgechromium",
        "private_mode": False,
        "storage_path": str(meter_home() / "webview"),
    }


def _load_state() -> dict:
    """Read desktop.json. A missing file is empty; an unreadable one is not.

    FileNotFoundError is the first save. Any other OSError means the file is
    there and we could not read it -- treating that as `{}` made the next
    save write only the new keys over the top, wiping position, size and
    which panel was open, and the write itself succeeded so nothing recorded
    it. Invalid JSON and a non-object stay empty: those were never a prior
    save of ours.
    """
    try:
        text = _state_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _save_state(**changes) -> None:
    path = _state_path()
    temporary: Path | None = None
    try:
        with state_file_lock(path):
            data = _load_state()
            data.update(changes)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        # Not raised: callers save window geometry and panel state
        # opportunistically, and a failed save must not take the panel down
        # with it. But it is our own write, and `pass` alone meant a state
        # directory that had gone read-only, a locked file or a full disk
        # silently discarded every change the user made -- position, size,
        # the panel they had open -- with nothing anywhere saying so. The
        # same split the identity ledgers and the diagnostics file already
        # have. ValueError is a file we could not parse: overwriting it
        # with one new key is a wipe, not a save.
        record_component_failure("desktop_state_write", exc)
    else:
        clear_component_failure("desktop_state_write")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _wait_for_orb(orb: Orb) -> bool:
    """Record an orb startup timeout while allowing the tray to continue."""
    if orb.wait_ready():
        clear_component_failure("orb")
        return True
    record_component_failure("orb", TimeoutError("the floating orb did not start"))
    return False


def _claim_single_instance() -> int | None:
    """Return the owned named-mutex handle, or None for a second instance."""
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(kernel32.GetLastError())
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_single_instance(handle: int) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def _set_app_user_model_id() -> None:
    """Give every source and packaged desktop launch one stable Windows identity."""
    result = int(shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID))
    if result != 0:
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: 0x{result & 0xFFFFFFFF:08X}")


class TelemetryReceiver:
    """User-controlled OTLP listener; never serves dashboard or account data."""

    def __init__(self, port: int = PREFERRED_PORT,
                 enabled_providers: object = None) -> None:
        self._port = port
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._error = ""
        if isinstance(enabled_providers, dict):
            selected = {
                provider for provider, enabled in enabled_providers.items()
                if enabled is True
            }
        elif isinstance(enabled_providers, (set, frozenset, list, tuple)):
            selected = set(enabled_providers)
        else:
            selected = set()
        self._enabled_providers = set(TELEMETRY_PROVIDERS).intersection(selected)

    def status(self) -> dict[str, object]:
        with self._lock:
            state = "ready" if self._server else ("error" if self._error else "disabled")
            port = self._server.server_port if self._server else self._port
            return {
                "state": state,
                "endpoint": f"http://{HOST}:{port}/v1/logs",
                "user_source_endpoint": f"http://{HOST}:{port}/v1/user-sources",
                "protocol": "otlp_http_protobuf",
                "manageable": True,
                "error": self._error,
                "providers": {
                    provider: provider in self._enabled_providers
                    for provider in TELEMETRY_PROVIDERS
                },
            }

    def provider_enabled(self, provider: str) -> bool:
        with self._lock:
            return provider in self._enabled_providers

    def enabled_providers(self) -> dict[str, bool]:
        with self._lock:
            return {
                provider: provider in self._enabled_providers
                for provider in TELEMETRY_PROVIDERS
            }

    def set_provider_enabled(self, provider: str, enabled: bool) -> None:
        if provider not in TELEMETRY_PROVIDERS:
            raise ValueError("unknown telemetry provider")
        with self._lock:
            if enabled:
                self._enabled_providers.add(provider)
            else:
                self._enabled_providers.discard(provider)

    def enable(self) -> bool:
        with self._lock:
            if self._server:
                return True
            try:
                server = ThreadingHTTPServer(
                    (HOST, self._port),
                    partial(
                        OtlpHandler,
                        since=None,
                        live_quota=False,
                        telemetry_provider_enabled=self.provider_enabled,
                    ),
                )
            except OSError as exc:
                self._error = str(exc)
                return False
            thread = threading.Thread(
                target=server.serve_forever,
                name="sandglass-otlp",
                daemon=True,
            )
            self._server, self._thread, self._error = server, thread, ""
            thread.start()
            return True

    def close(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)

    def disable(self) -> None:
        self.close()
        with self._lock:
            self._error = ""


def _bind_dashboard_server(receiver: TelemetryReceiver, shell=None) -> tuple[ThreadingHTTPServer, int]:
    """Fallback-only dashboard; port 7740 remains reserved for the OTLP receiver."""
    handler = partial(
        Handler,
        since=None,
        live_quota=False,
        allow_otlp=False,
        telemetry_receiver=receiver.status,
        telemetry_apply=partial(apply_telemetry_receiver, receiver),
        update_apply=partial(apply_update_request, shell),
    )
    httpd = ThreadingHTTPServer((HOST, 0), handler)
    return httpd, httpd.server_address[1]


def apply_telemetry_receiver(receiver: TelemetryReceiver, body: str) -> dict:
    """Turn the receiver on or off. One implementation, both panel transports.

    This used to live inside _desktop_api and so existed only on the native
    bridge. The fallback panel reports the receiver as manageable, draws the
    switch, and posts here over HTTP -- where there was no route, so every
    click answered 404 and the frontend's `if (!response.ok) return` swallowed
    it. The control looked live and did nothing.
    """
    try:
        envelope = json.loads(body)
        enabled = envelope.get("enabled")
        provider = envelope.get("provider")
    except (AttributeError, TypeError, ValueError):
        enabled = None
        provider = None
    if not isinstance(enabled, bool):
        raise ValueError("receiver state requires an explicit boolean")
    if provider is not None:
        if not isinstance(provider, str) or provider not in TELEMETRY_PROVIDERS:
            raise ValueError("unknown telemetry provider")
        if enabled and not receiver.enable():
            return {
                "ok": False,
                "provider": provider,
                "enabled": receiver.provider_enabled(provider),
                "receiver": receiver.status(),
            }
        receiver.set_provider_enabled(provider, enabled)
        _save_state(
            telemetry_receiver_enabled=True,
            telemetry_providers_enabled=receiver.enabled_providers(),
        )
        return {
            "ok": True,
            "provider": provider,
            "enabled": enabled,
            "receiver": receiver.status(),
        }
    if enabled:
        ok = receiver.enable()
    else:
        receiver.disable()
        ok = True
    if ok:
        _save_state(telemetry_receiver_enabled=enabled)
    return {"ok": ok, "receiver": receiver.status()}


def apply_update_request(shell, body: str) -> dict:
    """Verify and hand over to the installer, then close the panel.

    The order is deliberate. Downloading and checking can fail and the user has
    to be told, so that happens while the panel is still up. Only once the file
    is proven does this start the installer and quit -- the installer refuses to
    write over a running copy, which is what makes that ordering necessary
    rather than merely tidy.
    """
    from sandglass.update import apply_update, available_update

    try:
        envelope = json.loads(body) if body else {}
    except (TypeError, ValueError):
        envelope = {}
    requested_version = envelope.get("version") if isinstance(envelope, dict) else None
    # Older panels sent {offer: {version: ...}}. Keep accepting that shape for
    # compatibility, but never accept any other client-supplied offer field.
    if not isinstance(requested_version, str):
        legacy_offer = envelope.get("offer") if isinstance(envelope, dict) else None
        requested_version = legacy_offer.get("version") if isinstance(legacy_offer, dict) else None
    if not isinstance(requested_version, str) or not requested_version.strip():
        return {"ok": False, "error": "invalid_update_request"}
    try:
        # The displayed offer may be stale or have been tampered with in the
        # request. Re-read the release, checksum manifest, and signature now.
        offer = available_update(force=True)
    except Exception as exc:  # noqa: BLE001 - fail closed while panel remains up
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
    if not isinstance(offer, dict) or not offer:
        return {"ok": False, "error": "no_update"}
    if offer.get("version") != requested_version:
        return {"ok": False, "error": "stale_update"}
    try:
        result = apply_update(offer)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        from sandglass.update import UpdateBusyError

        if isinstance(exc, UpdateBusyError):
            return {"ok": False, "error": "update_busy"}
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
    if not isinstance(result, dict) or result.get("ok") is not True:
        return result if isinstance(result, dict) else {"ok": False, "error": "apply_failed"}
    if shell is not None and shell.quit() is not True:
        from sandglass.update import cancel_update_handoff

        cancelled = cancel_update_handoff(result)
        return {
            "ok": False,
            "error": "desktop_quit_failed" if cancelled else "update_cancel_failed",
        }
    from sandglass.update import public_update_result

    return public_update_result(result)


def _desktop_api(receiver: TelemetryReceiver, method: str,
                 target: str, body: str = "", shell=None) -> object:
    parsed = urlparse(target)
    if method == "GET":
        return api_payload(
            target,
            live_quota=False,
            telemetry_receiver=receiver.status(),
            apply_supported=True,
        )
    if method == "POST" and parsed.path == "/api/update/apply":
        return apply_update_request(shell, body)
    if method == "POST" and parsed.path == "/api/update/announcement/dismiss":
        from sandglass.update import dismiss_update_announcement

        try:
            envelope = json.loads(body) if body else {}
        except (TypeError, ValueError):
            envelope = {}
        if not isinstance(envelope, dict) or not isinstance(envelope.get("version"), str):
            raise ValueError("announcement dismissal requires a version")
        return dismiss_update_announcement(envelope["version"])
    if method == "POST" and parsed.path == "/api/telemetry-receiver":
        return apply_telemetry_receiver(receiver, body)
    if method == "POST" and parsed.path == "/api/user-sources/configure":
        try:
            envelope = json.loads(body)
        except (TypeError, ValueError):
            raise ValueError("user source configuration must be JSON") from None
        return configure_user_source(envelope)
    if method == "POST" and parsed.path == "/api/product-mode":
        try:
            envelope = json.loads(body)
        except (TypeError, ValueError):
            raise ValueError("product mode configuration must be JSON") from None
        return set_attribution_mode(str(envelope.get("attribution_mode") or ""))
    raise KeyError(parsed.path)


def _default_orb_pos(side: int) -> tuple[int, int]:
    """Bottom-right of the primary work area, in physical pixels."""
    return default_orb_pos(side)


def _initial_orb_geometry(_state: dict) -> tuple[int, int, int]:
    """Primary work-area bottom-right. A saved drag is not the launch position."""
    _left, _top, right, bottom = primary_work_area()
    scale = monitor_scale(right - 1, bottom - 1)
    side = max(1, round(ORB * scale))
    x, y = _default_orb_pos(side)
    x, y = clamp_to_screen(x, y, side)
    return x, y, side


def _panel_usable_height(hwnd: int | None, orb: Orb | None) -> int:
    """Return a logical-height cap from the panel's own monitor work area."""
    try:
        if hwnd:
            x, y, width, height = window_rect(hwnd)
            scale = window_scale(hwnd)
        elif orb:
            x, y = orb.x, orb.y
            width, height = orb.image.size
            scale = monitor_scale(x, y, width, height)
        else:
            return 900
        _, top, _, bottom = monitor_work_area(x, y, width, height)
        return max(240, int((bottom - top) / max(scale, 0.1)) - 40)
    except Exception:
        return 900


AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "sandglass"
UPDATE_READY_ARG = "/UPDATE_TOKEN="
_UPDATE_READY_TOKEN_RE = re.compile(r"[0-9a-f]{32}")
_UPDATE_READY_LOCK = threading.Lock()
_UPDATE_READY_SENT = False


def _update_ready_token() -> str | None:
    """Return only the strict, installer-created named-event token."""
    prefix = UPDATE_READY_ARG.casefold()
    for argument in sys.argv[1:]:
        if not isinstance(argument, str) or not argument.casefold().startswith(prefix):
            continue
        value = argument[len(UPDATE_READY_ARG):].strip()
        if not _UPDATE_READY_TOKEN_RE.fullmatch(value):
            return None
        return value
    return None


def _signal_update_ready() -> None:
    """Signal the one-shot named event after the real panel is usable.

    The event is created by the installer.  This process only opens that
    exact event and sets it; it never treats a command-line value as a path.
    """
    global _UPDATE_READY_SENT
    with _UPDATE_READY_LOCK:
        if _UPDATE_READY_SENT:
            return
        token = _update_ready_token()
        if token is None or os.name != "nt":
            return
        name = f"Local\\Sandglass.UpdateReady.{token}"
        handle = None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.OpenEventW.restype = wintypes.HANDLE
            kernel32.SetEvent.argtypes = [wintypes.HANDLE]
            kernel32.SetEvent.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.SetEvent(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except (OSError, AttributeError) as exc:
            # The update installer will time out and roll back.  Startup itself
            # must not fail merely because an optional handoff observer vanished.
            print(f"could not signal update readiness: {exc}", file=sys.stderr)
        else:
            _UPDATE_READY_SENT = True
        finally:
            if handle:
                kernel32.CloseHandle(handle)


def _signal_update_ready_if_window_visible(hwnd: int) -> bool:
    """Signal only after Windows confirms the fallback panel is visible."""
    if not hwnd:
        return False
    try:
        visible = bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except (AttributeError, OSError):
        return False
    if not visible:
        return False
    _signal_update_ready()
    return True


def _launcher_command(*, background: bool = False) -> str:
    """What to run at login: pythonw plus the launcher beside the package.

    A Run entry carries no working directory, so `-m sandglass.desktop` would
    only work if the package were installed. The launcher puts its own directory
    on the path, which makes one absolute path enough either way.
    """
    exe = Path(sys.executable)
    # In a packaged desktop build sys.executable is Sandglass.exe itself.  It
    # is already the launcher; treating it as Python and appending ``-m`` would
    # leave a misleading Run entry and make future packaging changes fragile.
    if getattr(sys, "frozen", False):
        command = f'"{exe}"'
    else:
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            exe = quiet
        launcher = Path(__file__).resolve().parent.parent / "sandglass-desktop.pyw"
        if launcher.exists():
            command = f'"{exe}" "{launcher}"'
        else:
            command = f'"{exe}" -m sandglass.desktop'
    return f"{command} --background" if background else command


def _logical_panel_origin(state: dict) -> dict[str, int]:
    """The stored physical panel origin, as the logical pair pywebview wants."""
    x, y = state.get("panel_x"), state.get("panel_y")
    if x is None or y is None:
        return {"x": x, "y": y}
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return {"x": None, "y": None}
    scale = monitor_scale(x, y) or 1.0
    return {"x": round(x / scale), "y": round(y / scale)}


def _packaged_self_test() -> int:
    """Fail fast when a Windows bundle omitted a desktop runtime component."""
    try:
        require_canonical_state_home()
    except StateHomeAttestationError:
        return 5
    required = (
        WEB_DIR / "index.html",
        WEB_DIR / "assets" / "logo-mark.png",
        WEB_DIR / "assets" / "orb.ico",
    )
    if any(not path.is_file() for path in required):
        return 2
    runtime = native_shell_path()
    if runtime is None:
        return 3
    try:
        bridge, mode, content = load_native_bridge(runtime)
        build_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
            native_runtime=runtime,
            panel_bridge=bridge,
            bridge_content=content,
            bridge_mode=mode,
        )
        import clr  # noqa: F401
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        import webview  # noqa: F401
    except Exception:
        return 4
    return 0


def _expected_runtime_identity() -> dict[str, object]:
    runtime = native_shell_path()
    if runtime is None:
        return {}
    bridge, mode, content = load_native_bridge(runtime)
    return build_runtime_identity(
        source_root=SOURCE_ROOT,
        web_index=WEB_DIR / "index.html",
        native_runtime=runtime,
        panel_bridge=bridge,
        bridge_content=content,
        bridge_mode=mode,
    )


def _warn_different_runtime() -> None:
    ctypes.windll.user32.MessageBoxW(
        None,
        "另一个不同版本的 Sandglass 正在运行。请先从托盘退出旧版本，再启动当前版本。\n\n"
        "A different Sandglass build is already running. Quit it from the tray before opening this build.",
        "Sandglass",
        0x30,
    )


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            return bool(winreg.QueryValueEx(key, AUTOSTART_NAME)[0])
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    """Add or remove this user's login entry after an explicit desktop action."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
        if on:
            winreg.SetValueEx(
                key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                _launcher_command(background=True),
            )
            return
        try:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        except FileNotFoundError:
            pass


# Injected into the dashboard, which knows nothing about running in a shell.
# Keeping it here means index.html stays byte-identical to the browser version.
PANEL_CHROME = r"""
(function () {
  if (document.getElementById('shell-bar')) return;
  var css = document.createElement('style');
  css.textContent =
    // The page draws a card floating on a background. In a window the window IS
    // the card, so the background, the margins and the card's own rounded corners
    // and shadow all have to go -- otherwise the shell shows a strip of someone
    // else's desktop around the edges.
    'html,body{min-height:0!important;background:transparent!important;' +
    'overflow-y:auto!important;scrollbar-width:none!important;' +
    '-ms-overflow-style:none!important}' +
    'html::-webkit-scrollbar,body::-webkit-scrollbar{' +
    'width:0!important;height:0!important;display:none!important}' +
    'html.overview-mode,html.overview-mode body{overflow-y:hidden!important}' +
    '.popover{width:100%!important;margin:0!important;padding-top:0!important;' +
    'border-radius:0!important;box-shadow:none!important}' +
    'html.overview-mode .popover{max-height:calc(100vh - 32px)!important}' +
    '.skip{display:none!important}' +
    '.app-menu-shell{position:fixed!important;top:0!important;left:6px!important;' +
    'height:32px!important;display:flex!important;align-items:center!important;' +
    'z-index:10000!important}' +
    '.app-menu-panel{top:32px!important}' +
    '#shell-bar{position:fixed;top:0;left:0;right:0;height:32px;display:flex;' +
    'align-items:center;justify-content:flex-end;z-index:9999;' +
    'padding-right:6px;background:var(--card)}' +
    '.shell-window-button{width:26px;height:26px;border:0;' +
    'background:transparent;color:currentColor;opacity:.45;font-size:17px;' +
    'line-height:1;cursor:pointer;border-radius:7px}' +
    '.shell-window-button:hover{opacity:1;background:rgba(127,127,127,.18)}' +
    '#shell-minimize{font-size:19px;padding-bottom:7px}' +
    'body{padding-top:32px}';
  document.head.appendChild(css);
  var bar = document.createElement('div');
  bar.id = 'shell-bar';
  bar.innerHTML =
    '<button id="shell-minimize" class="shell-window-button" ' +
    'aria-label="缩回悬浮球" title="缩回悬浮球">−</button>' +
    '<button id="shell-close" class="shell-window-button" ' +
    'aria-label="缩回托盘" title="缩回托盘">×</button>';
  document.body.appendChild(bar);
  var shellLabels = {
    'zh-CN': {minimize: '缩回悬浮球', close: '缩回托盘'},
    'zh-TW': {minimize: '縮回懸浮球', close: '縮回系統匣'},
    'en-US': {minimize: 'Minimize to floating orb', close: 'Close to system tray'},
    'es-ES': {minimize: 'Minimizar a la esfera', close: 'Cerrar a la bandeja'},
    'fr-FR': {minimize: 'Réduire vers l’orbe', close: 'Fermer dans la zone de notification'},
    'de-DE': {minimize: 'Zur Kugel minimieren', close: 'In die Taskleiste schließen'},
    'pt-BR': {minimize: 'Minimizar para a esfera', close: 'Fechar para a bandeja'},
    'ru-RU': {minimize: 'Свернуть в сферу', close: 'Закрыть в область уведомлений'},
    'ja-JP': {minimize: 'フローティングオーブに戻す', close: 'システムトレイに格納'},
    'ko-KR': {minimize: '플로팅 오브로 최소화', close: '시스템 트레이로 닫기'}
  };
  var syncShellLocale = function () {
    var locale = document.documentElement.lang || 'zh-CN';
    var copy = shellLabels[locale] || shellLabels['zh-CN'];
    var minimize = document.getElementById('shell-minimize');
    var close = document.getElementById('shell-close');
    minimize.setAttribute('aria-label', copy.minimize);
    minimize.title = copy.minimize;
    close.setAttribute('aria-label', copy.close);
    close.title = copy.close;
    try { window.pywebview.api.set_locale(locale); } catch (_) {}
  };
  new MutationObserver(syncShellLocale).observe(
    document.documentElement, {attributes: true, attributeFilter: ['lang']}
  );
  syncShellLocale();
  document.getElementById('shell-minimize').onclick = function () {
    window.pywebview.api.minimize_panel();
  };
  document.getElementById('shell-close').onclick = function () {
    window.pywebview.api.close_panel();
  };

  // Drag from anywhere that is not something you can click. -webkit-app-region
  // would be smoother, but it swallows the mouseup, and the end of the drag is
  // exactly when the shell has to decide whether the window landed on an edge.
  var HOT = 'button,a,input,select,textarea,label,[role="button"],[onclick],[tabindex]';
  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    if (e.target.closest && e.target.closest(HOT)) return;
    e.preventDefault();
    window.pywebview.api.begin_drag();
  });

  // Grow the window to the content instead of making the reader scroll. In the
  // overview the card itself is capped to the current viewport, so reconstruct
  // the height wanted by its inner account list. Otherwise measuring the capped
  // card feeds the old short window back into fit_panel and it can never grow.
  var card = document.querySelector('.popover');
  if (!card) return;
  var last = 0;
  var displayed = 0;
  var resizeFrame = 0;
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  var sendHeight = function (height) {
    displayed = height;
    window.pywebview.api.fit_panel(height);
  };
  var easeOut = function (progress) {
    return 1 - Math.pow(1 - progress, 3);
  };
  var animateHeight = function (target) {
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    if (!displayed || reducedMotion.matches || Math.abs(target - displayed) < 4) {
      sendHeight(target);
      return;
    }
    var start = displayed;
    var started = performance.now();
    var duration = 180;
    var step = function (now) {
      var progress = Math.min(1, (now - started) / duration);
      sendHeight(Math.round(start + (target - start) * easeOut(progress)));
      if (progress < 1) resizeFrame = requestAnimationFrame(step);
      else resizeFrame = 0;
    };
    resizeFrame = requestAnimationFrame(step);
  };
  var tell = function () {
    var cardHeight = card.getBoundingClientRect().height;
    var h = Math.ceil(cardHeight) + 32;
    if (document.documentElement.classList.contains('overview-mode')) {
      var accounts = card.querySelector('.overview-cards');
      if (accounts) {
        var rows = accounts.querySelectorAll('.ov');
        var wanted = accounts.scrollHeight;
        if (rows.length > 5) {
          var box = accounts.getBoundingClientRect();
          var fifth = rows[4].getBoundingClientRect();
          wanted = Math.ceil(fifth.bottom - box.top + accounts.scrollTop);
        }
        var fixed = Math.max(0, cardHeight - accounts.clientHeight);
        h = Math.ceil(fixed + wanted) + 32;
      }
      // Even one selected account must leave enough room to open and use the
      // account picker instead of collapsing into the tiny clipped panel.
      h = Math.max(h, 560);
    }
    if (Math.abs(h - last) < 2) return;
    last = h;
    animateHeight(h);
  };
  new ResizeObserver(tell).observe(card);
  new MutationObserver(function () { setTimeout(tell, 0); }).observe(
    card, {childList: true, subtree: true}
  );
  setTimeout(tell, 60);
})();
"""


class PanelApi:
    """What the page may call. Deliberately tiny.

    pywebview introspects the js_api object's public members, so handing it the
    Shell exposes `panel`/`orb` too -- and repr-ing a pywebview Window walks into
    .native.AccessibilityObject and recurses until it dies. One private
    attribute, one method, no surface.
    """

    def __init__(self, shell: "Shell") -> None:
        self._shell = shell

    def close_panel(self) -> None:
        self._shell.hide_panel(show_orb=False)

    def minimize_panel(self) -> None:
        self._shell.hide_panel(show_orb=True)

    def set_locale(self, locale: str) -> None:
        self._shell.set_locale(locale)

    def autostart_status(self) -> bool:
        return autostart_enabled()

    def enable_autostart(self) -> bool:
        """The page calls this only from the user's explicit Enable click."""
        try:
            set_autostart(True)
        except OSError:
            return False
        if self._shell.icon:
            try:
                self._shell.icon.update_menu()
            except Exception:
                pass
        return autostart_enabled()

    def fit_panel(self, height: float) -> None:
        self._shell.fit_panel(height)

    def begin_drag(self) -> None:
        self._shell.begin_panel_drag()


class Shell:
    """Owns the panel, the orb and the tray."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.panel = None
        self.native_panel: NativePanel | None = None
        self.orb: Orb | None = None
        self.icon = None
        self.mark = None          # the round image, reused for every icon
        self.locale = "zh-CN"
        self._placed = False      # the panel is parked once per open, then grows down
        self._panel_open = False  # resizing a hidden pywebview window un-hides it
        self._in_tray = False     # close-to-tray; next reveal starts at the corner
        self._wanted_height = PANEL_H
        self._quitting = False
        self._transition = threading.Lock()
        self._activation_lock = threading.Lock()
        self._activation_pending = False
        self._done = threading.Event()
        self.panel_dock = EdgeDock(self._panel_hwnd)
        self._panel_state_lock = threading.Lock()
        self._pending_panel_position: tuple[int, int] | None = None
        self._panel_state_timer: threading.Timer | None = None
        self._panel_state_generation = 0
        self._dock_work_area: tuple[int, int, int, int] | None = None

    def text(self, key: str) -> str:
        return DESKTOP_TEXT[self.locale][key]

    def set_locale(self, locale: object) -> None:
        selected = _desktop_locale(locale)
        if selected == self.locale:
            return
        self.locale = selected
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def _orb_pos(self) -> tuple[int, int]:
        """Where to remember the orb. A tucked orb is mostly off screen, and
        restoring it there would look like it never came back."""
        orb = self.orb
        if not orb:
            return 0, 0
        if orb.dock.tucked:
            return orb.dock.flush_position(orb.x, orb.y, *orb.image.size)
        return orb.x, orb.y

    def _panel_hwnd(self) -> int:
        """The panel's native handle. .Handle is a .NET IntPtr, not an int."""
        try:
            return int(self.panel.native.Handle.ToInt64())
        except Exception:
            return 0

    def _orb_rect(self) -> tuple[int, int, int, int] | None:
        if not self.orb:
            return None
        side = self.orb.image.size[0]
        return self.orb.x, self.orb.y, side, side

    def _park_orb_at_corner(self) -> tuple[int, int, int]:
        """Put the orb on the primary work-area corner, in physical pixels.

        Written onto the Python object before the Win32 move is posted so a
        following native show() reads the corner, not the last drag.
        """
        if not self.orb:
            return 0, 0, ORB
        side = self.orb.image.size[0]
        x, y = clamp_to_screen(*_default_orb_pos(side), side)
        self.orb.dock.release()
        self.orb.x, self.orb.y = x, y
        self.orb.post_move(x, y)
        return x, y, side

    def native_opened(self) -> None:
        self._panel_open = True
        self._in_tray = False
        if self.orb:
            # Re-show after a hide_to_tray that ran between post_show and _show.
            self.orb.post_show()

    def native_ready(self) -> None:
        """Report native readiness after the actual open transition completes."""
        _signal_update_ready()

    def native_minimized(self) -> None:
        self._panel_open = False
        self._in_tray = False
        if self.orb:
            self.orb.post_show()

    def native_closed(self) -> None:
        self._panel_open = False
        self._in_tray = True
        if self.orb:
            self.orb.dock.release()
            self.orb.post_hide()

    def orb_drag_started(self) -> None:
        """Detach a docked panel before the user carries its orb elsewhere."""
        if not self.orb:
            return
        side = self.orb.image.size[0]
        if self.native_panel and self.native_panel.is_docked():
            # A minimized native panel keeps its last dock edge.  The orb can
            # still be dragged while that panel is hidden, so release the stale
            # edge before the next click or show() will reopen on the old
            # monitor and pull the orb back there.
            self.native_panel.detach_to_orb(self.orb.x, self.orb.y, side)
            return
        if not self._panel_open:
            return
        if self.panel_dock.edge:
            self.panel_dock.release()
            self.hide_panel(show_orb=True)

    def orb_moved(self, x: int, y: int) -> None:
        """Keep an open, free panel attached to the orb during a drag."""
        if not self._panel_open or not self.orb:
            return
        side = self.orb.image.size[0]
        if self.native_panel:
            self.native_panel.follow_orb(x, y, side)
            return
        hwnd = self._panel_hwnd()
        if hwnd and not self.panel_dock.edge:
            place_beside(hwnd, x, y, side)

    def native_panel_docked(self, edge: str | None, tucked: bool,
                            rect: tuple[int, int, int, int]) -> None:
        """Park the orb at one stable edge point while the panel peeks/tucks."""
        if not self.orb:
            return
        if not edge:
            self._dock_work_area = None
            self._move_orb_beside_panel(rect, animate=True)
            return
        x, y, width, height = rect
        side = self.orb.image.size[0]
        gap = max(6, round(10 * system_scale()))
        # Same trap EdgeDock already latched: a tucked rect is 8px on the
        # docked monitor and the rest on the neighbour, so MonitorFromRect
        # selects the neighbour and the orb jumps screens. Latch from the
        # flush rectangle.
        if not tucked:
            self._dock_work_area = monitor_work_area(x, y, width, height)
        work_left, work_top, work_right, _work_bottom = (
            self._dock_work_area or monitor_work_area(x, y, width, height)
        )
        if edge == "left":
            orb_x, orb_y = work_left + DOCK_PEEK + gap, y + height - side
        elif edge == "right":
            orb_x = work_right - side - DOCK_PEEK - gap
            orb_y = y + height - side
        else:
            orb_x, orb_y = x + width - side, work_top + DOCK_PEEK + gap
        self.orb.post_move(orb_x, orb_y, animate=True)

    def native_panel_moved(self, rect: tuple[int, int, int, int]) -> None:
        """Keep the persistent orb beside a panel the user is dragging."""
        x, y, _width, _height = rect
        self._queue_panel_position(x, y)
        self._move_orb_beside_panel(rect)

    def panel_moved(self, x: int, y: int) -> None:
        """Remember the fallback panel's position, in the unit the other one uses.

        pywebview reports logical pixels; the native shell reports physical ones
        from GetWindowRect, and both wrote panel_x. On a 150% display the same
        window is physical (1200, 300) and logical (800, 200) -- measured here --
        so a position written by one and read by the other lands 600px away. One
        name, two quantities.

        Physical is what gets stored: it is absolute, and the same number means
        the same place whichever screen the window is later restored on.
        """
        try:
            x, y = int(x), int(y)
        except (TypeError, ValueError):
            return
        scale = monitor_scale(round(x), round(y))
        self._queue_panel_position(round(x * scale), round(y * scale))

    def _queue_panel_position(self, x: int, y: int) -> None:
        """Coalesce move events; persist the final position after the burst."""
        with self._panel_state_lock:
            self._pending_panel_position = (int(x), int(y))
            self._panel_state_generation += 1
            generation = self._panel_state_generation
            timer = self._panel_state_timer
            if timer is not None:
                timer.cancel()
            timer = threading.Timer(
                0.25, self._flush_panel_position, args=(generation,)
            )
            timer.daemon = True
            self._panel_state_timer = timer
        timer.start()

    def _flush_panel_position(self, generation: int | None = None) -> None:
        with self._panel_state_lock:
            if (generation is not None
                    and generation != self._panel_state_generation):
                return
            timer = self._panel_state_timer
            self._panel_state_timer = None
            position = self._pending_panel_position
            self._pending_panel_position = None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()
        if position is not None:
            _save_state(panel_x=position[0], panel_y=position[1])

    def _move_orb_beside_panel(self, rect: tuple[int, int, int, int], *,
                               animate: bool = False) -> None:
        if not self.orb:
            return
        x, y, width, height = rect
        side = self.orb.image.size[0]
        gap = max(6, round(10 * system_scale()))
        work_left, _work_top, work_right, _work_bottom = monitor_work_area(
            x, y, width, height)
        orb_x = x + width + gap
        if orb_x + side > work_right:
            orb_x = x - side - gap
        orb_x = max(work_left, min(orb_x, work_right - side))
        orb_y = y + height - side
        self.orb.post_move(orb_x, orb_y, animate=animate)

    # -- called from the orb thread and the tray thread -------------------
    def activate(self) -> None:
        """Reveal this instance, or remember the request until its panel is ready."""
        with self._activation_lock:
            if not self.native_panel and not self.panel:
                self._activation_pending = True
                return
            self._activation_pending = False
        self.show_panel()

    def activate_when_ready(self, *_) -> None:
        with self._activation_lock:
            if not self._activation_pending:
                return
            self._activation_pending = False
        self.show_panel()

    def toggle_panel(self) -> None:
        """Use the persistent orb as the panel's open/close switch."""
        if self._panel_open:
            self.hide_panel(show_orb=True)
        else:
            self.show_panel()

    def show_panel(self) -> None:
        with self._transition:
            if self._panel_open:
                self._raise_panel()
                return
            if self._in_tray:
                self._park_orb_at_corner()
                self._in_tray = False
            if self.native_panel:
                if not self.orb:
                    return
                _save_state(**dict(zip(("orb_x", "orb_y"), self._orb_pos())))
                side = self.orb.image.size[0]
                self.orb.post_show()
                # After the show, not before. The orb is the only switch and
                # toggle_panel reads this flag, so marking it open first turned a
                # single failed show into a dead switch for the rest of the
                # session: every later click took the hide branch, minimize() on
                # a panel that was never shown fires no on_minimized, and the
                # flag stayed True with nothing on screen. The orb swallows
                # whatever this raises, so nothing said so either -- hence the
                # recorded failure, which outlives the click.
                try:
                    opened = self.native_panel.show(
                        self.orb.x, self.orb.y, side, self._wanted_height,
                    )
                except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
                    record_component_failure("native_panel_show", exc)
                    raise
                clear_component_failure("native_panel_show")
                # A deferred show queues until the page measures. Treating that
                # as open made hide() a no-op -- native close requires self.open
                # -- so the orb stayed a dead switch until fit arrived, and if
                # fit never came, for the rest of the session.
                if opened:
                    self._panel_open = True
                return
            if not self.panel:
                return
            anchor = None
            orb_rect = None
            orb_visible = False
            if self.orb:
                _save_state(**dict(zip(("orb_x", "orb_y"), self._orb_pos())))
                anchor = (self.orb.x, self.orb.y, self.orb.image.size[0])
                orb_rect = self._orb_rect()
                self.orb.post_show()
                orb_visible = True
            hwnd = self._panel_hwnd()
            if hwnd:
                hide_from_taskbar(hwnd)
            if hwnd and orb_visible and orb_rect:
                # Mask before show so no full-sized frame flashes before the
                # reveal has been positioned beside the orb.
                set_window_reveal(hwnd, orb_rect, 0.0)
            try:
                self.panel.show()
                # Catch up on whatever the page measured while it was hidden,
                # before placement, so the transition ends at the real size.
                self.panel.resize(PANEL_W, self._wanted_height)
            except Exception as exc:  # noqa: BLE001 - recorded, then return
                # The native path records and re-raises because the orb swallows
                # it. This path already returns, so the flag stays closed and
                # the next click retries -- but the swallow used to look like a
                # quiet click with nothing in diagnostics.
                record_component_failure("fallback_panel_show", exc)
                if hwnd:
                    clear_window_region(hwnd)
                return
            clear_component_failure("fallback_panel_show")
            hwnd = self._panel_hwnd()
            if not hwnd or (os.name == "nt" and not ctypes.windll.user32.IsWindowVisible(hwnd)):
                record_component_failure(
                    "fallback_panel_show",
                    RuntimeError("fallback panel did not provide a visible HWND"),
                )
                if hwnd:
                    clear_window_region(hwnd)
                return
            if hwnd and self.mark is not None:
                set_window_icon(hwnd, self.mark)
            self.panel_dock.start()
            if self.panel_dock.edge:
                # Preserve a user-selected edge position. Its own peek animation
                # remains the direct transition when the panel is edge-docked.
                self._placed = True
                self.panel_dock.peek()
            elif hwnd and anchor:
                place_beside(hwnd, *anchor)
                self._placed = True
                if orb_visible and orb_rect:
                    animate_window_reveal(hwnd, orb_rect, opening=True)
            self._panel_open = True
            # pywebview's loaded event has already fired before this method is
            # called. The OS-visible HWND, rather than show() returning, is the
            # direct proof the installer needs before retiring its rollback.
            _signal_update_ready_if_window_visible(hwnd)

    def _raise_panel(self) -> None:
        """Bring an already-open panel forward. Tray click is not a toggle.

        A failed raise used to leave `_panel_open` True. The orb's toggle
        hides first and recovers; the tray only calls `show_panel`, so every
        later click took this branch and did nothing.
        """
        if self.native_panel:
            try:
                self.native_panel.activate()
            except Exception as exc:  # noqa: BLE001
                record_component_failure("native_panel_show", exc)
                self._panel_open = False
                return
            clear_component_failure("native_panel_show")
            return
        if self.panel:
            try:
                self.panel.show()
            except Exception as exc:  # noqa: BLE001
                record_component_failure("fallback_panel_show", exc)
                self._panel_open = False
                return
            clear_component_failure("fallback_panel_show")

    def begin_panel_drag(self) -> None:
        hwnd = self._panel_hwnd()
        if not hwnd:
            return

        def run() -> None:
            if drag_window(hwnd):
                self.panel_dock.settle()

        threading.Thread(target=run, name="sandglass-panel-drag", daemon=True).start()

    def fit_panel(self, height: float) -> None:
        """Match the window to the card, capped so a tall tab cannot fill the screen."""
        if not self.panel:
            return
        hwnd = self._panel_hwnd()
        usable = _panel_usable_height(hwnd, self.orb)
        self._wanted_height = max(240, min(int(height), usable))
        if not self._panel_open:
            # The page loads while the window is still hidden, so its first
            # measurement lands here at startup. pywebview's resize() shows a
            # hidden window, which is how an empty panel used to appear next to
            # the orb a couple of seconds after launch. Remember it instead.
            return
        try:
            self.panel.resize(PANEL_W, self._wanted_height)
        except Exception:
            return
        hwnd = self._panel_hwnd()
        if not hwnd:
            return
        if self._placed:
            # Every later change extends the bottom. Re-anchoring here would pin
            # the bottom instead, and the whole window would jump upward on each
            # tab switch -- the top edge is what the reader is looking at.
            if not self.panel_dock.edge:
                keep_on_screen(hwnd)
            return
        if self.orb:
            # First fit only: now that the real height is known, park the window
            # against the orb.
            place_beside(hwnd, self.orb.x, self.orb.y, self.orb.image.size[0])
        self._placed = True

    def hide_panel(self, *, show_orb: bool = True) -> None:
        with self._transition:
            self._panel_open = False
            self._in_tray = not show_orb
            if not show_orb:
                self.panel_dock.release()
            if self.native_panel:
                if show_orb:
                    self.native_panel.minimize()
                else:
                    self.native_panel.hide_to_tray()
                return
            hwnd = self._panel_hwnd()
            orb_rect = self._orb_rect()
            if self.orb and show_orb:
                # The orb is already present under the final shrinking frames,
                # so the panel visibly lands in it rather than vanishing first.
                self.orb.post_show()
            if hwnd and orb_rect and show_orb:
                animate_window_reveal(hwnd, orb_rect, opening=False)
            if self.panel:
                try:
                    self.panel.hide()
                except Exception:
                    pass
            if hwnd:
                clear_window_region(hwnd)
            if self.orb and not show_orb:
                self.orb.post_hide()

    def toggle_orb(self) -> None:
        if not self.orb:
            return
        if self.orb.visible:
            if self._panel_open:
                # The orb is the panel's toggle and drag handle. Hiding it
                # while the panel stays up leaves a window with no way back
                # except the tray.
                return
            _save_state(**dict(zip(("orb_x", "orb_y"), self._orb_pos())))
            self.orb.post_hide()
            self._in_tray = True
            return
        if self._in_tray:
            self._park_orb_at_corner()
            self._in_tray = False
        self.orb.post_show()

    def quit(self) -> bool:
        if self._quitting:
            return True
        self._quitting = True
        self._flush_panel_position()
        if self.orb:
            _save_state(**dict(zip(("orb_x", "orb_y"), self._orb_pos())))
        if self.native_panel:
            if self.native_panel.quit() is not True:
                self._quitting = False
                return False
            self._done.set()
            return True
        if self.panel:
            try:
                self.panel.destroy()
            except Exception:
                self._quitting = False
                return False
        return True

    def stop_monitoring(self) -> None:
        from sandglass.observer import request_observer_stop, stop_supervising_observer

        # Order matters: the watchdog has to be told first, or it restarts the
        # observer the user just asked to stop.
        stop_supervising_observer()
        request_observer_stop()
        self.quit()

    def stop_auxiliary(self) -> None:
        """Stop non-WebView surfaces after the GUI main loop has returned."""
        if self.native_panel:
            self.native_panel.quit()
        if self.orb:
            self.orb.post_quit()
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

    def toggle_autostart(self) -> None:
        set_autostart(not autostart_enabled())
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def dress_panel(self, *_) -> None:
        try:
            self.panel.evaluate_js(PANEL_CHROME)
        except Exception:
            pass


# NotifyIcon on Vista+ delivers a left click as NIN_SELECT (WM_USER), not
# only WM_LBUTTONUP. pystray only matches the mouse-up, so a tray click on
# current Windows can do nothing.
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
WM_LBUTTONUP = 0x0202


def _tray(shell: Shell):
    import pystray

    class TrayIcon(pystray.Icon):
        def _on_notify(self, wparam, lparam):
            if lparam in (WM_LBUTTONUP, NIN_SELECT, NIN_KEYSELECT):
                self()
                return
            super()._on_notify(wparam, lparam)

    # The same round mark as the orb, not the square app icon, so the tray, the
    # taskbar and the thing on the desktop all read as one object.
    menu = pystray.Menu(
        pystray.MenuItem(lambda item: shell.text("open_panel"),
                         lambda icon, item: shell.show_panel(), default=True),
        pystray.MenuItem(lambda item: shell.text("toggle_orb"),
                         lambda icon, item: shell.toggle_orb()),
        pystray.MenuItem(lambda item: shell.text("autostart"),
                         lambda icon, item: shell.toggle_autostart(),
                         checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: shell.text("quit"),
                         lambda icon, item: shell.quit()),
        pystray.MenuItem(lambda item: shell.text("stop_monitoring"),
                         lambda icon, item: shell.stop_monitoring()),
    )
    return TrayIcon("sandglass", shell.mark, "sandglass", menu)


def _handle_native_panel_failure(shell: Shell, exc: Exception) -> None:
    """Stop a failed native panel before switching to the fallback transport."""
    panel = shell.native_panel
    if panel is not None:
        try:
            panel.quit()
        except Exception:
            pass
    record_component_failure("native_panel", exc)
    print(f"native panel unavailable, using pywebview: {exc}", file=sys.stderr)
    shell.native_panel = None


def _record_fallback_runtime_identity() -> None:
    """Replace native startup evidence once the fallback dashboard is bound."""
    record_runtime_identity(
        build_web_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
        )
    )


def main() -> int:
    # Uninstall must run before state-home attestation: removal is precisely
    # the recovery path for a damaged or unavailable state directory.
    if "--uninstall" in sys.argv:
        from sandglass.uninstall import main as uninstall_main

        return uninstall_main(quiet="--quiet" in sys.argv)
    # This launcher has no argument parser: every invocation, including
    # --help/--version probes, must prove state ownership before dispatch.
    try:
        require_canonical_state_home()
    except StateHomeAttestationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if "--observer" in sys.argv:
        from sandglass.observer import main as observer_main

        return observer_main()
    if "--stop" in sys.argv:
        # The installer calls this before overwriting or deleting the program
        # directory. The observer is a second detached copy of this executable:
        # it holds the files being replaced, and it keeps observing after an
        # uninstall that never told it to stop.
        # Answer whether it let go, not merely that it was asked. The caller is
        # about to overwrite the files this process maps, and it has no other way
        # to see the observer: the desktop mutex it can query says nothing about
        # a detached observer still holding them.
        from sandglass.observer import stop_and_wait

        return 0 if stop_and_wait() else 1
    if "--self-test" in sys.argv:
        return _packaged_self_test()
    _set_app_user_model_id()
    lock = _claim_single_instance()
    if lock is None:
        try:
            matches = runtime_identity_matches(
                _expected_runtime_identity(), runtime_provenance()
            )
        except Exception:
            matches = False
        if matches:
            if "--background" not in sys.argv:
                activate_existing_orb()
            return 0
        _warn_different_runtime()
        return 5

    state = _load_state()
    live_snapshot.set_role("panel")
    from sandglass.accounts import note_current_identity
    from sandglass.observer import ensure_observer_running, supervise_observer

    # Open the books here, not only from the observer's loop. On a first install
    # the observer is the one component most likely to be stopped from starting
    # -- application control blocks an unsigned detached process, and it dies
    # without a word -- and until something records the current identity, every
    # minute the panel shows is unattributed. The promise is that from the
    # moment Sandglass runs, usage is written down; that has to hold whether or
    # not the background process survived its first second.
    try:
        note_current_identity(observe_codex=False)
    except Exception as exc:  # noqa: BLE001 - never block startup over bookkeeping
        record_component_failure("identity_observation", exc)
    else:
        clear_component_failure("identity_observation")
    ensure_observer_running()
    supervise_observer()
    configured_providers = state.get("telemetry_providers_enabled")
    receiver = TelemetryReceiver(
        enabled_providers=(
            [provider for provider in TELEMETRY_PROVIDERS
             if isinstance(configured_providers, dict)
             and configured_providers.get(provider) is True]
            if isinstance(configured_providers, dict)
            else []
        )
    )
    if state.get("telemetry_receiver_enabled") is True:
        receiver.enable()
    shell = Shell(NATIVE_APP_URL)
    start_visible = "--background" not in sys.argv
    if start_visible:
        # A user launching Sandglass expects the product window. Login startup
        # stays quiet behind the explicit --background argument written only by
        # the user's autostart action.
        shell.activate()
    else:
        shell._in_tray = True

    x, y, side = _initial_orb_geometry(state)
    mark = WEB_DIR / "assets" / "logo-mark.png"
    shell.mark = render(256, mark)          # icons scale down from one master
    shell.orb = Orb(render(side, mark), x, y,
                    on_click=shell.toggle_panel, on_activate=shell.activate,
                    on_uninstall=shell.quit,
                    on_menu=shell.toggle_orb,
                    on_drag_start=shell.orb_drag_started,
                    on_move=shell.orb_moved,
                    master_image=shell.mark, logical_size=ORB,
                    start_visible=start_visible)
    threading.Thread(target=shell.orb.run, name="sandglass-orb", daemon=True).start()
    _wait_for_orb(shell.orb)

    shell.icon = _tray(shell)
    shell.icon.run_detached()

    native = native_shell_path()
    if native is not None:
        shell.url = NATIVE_APP_URL
        shell.native_panel = NativePanel(
            shell.url,
            mark,
            _launcher_command(background=True),
            window_icon=WEB_DIR / "assets" / "orb.ico",
            on_minimized=shell.native_minimized,
            on_opened=shell.native_opened,
            on_ready=shell.native_ready,
            on_closed=shell.native_closed,
            on_locale=shell.set_locale,
            on_dock=shell.native_panel_docked,
            on_move=shell.native_panel_moved,
            web_root=WEB_DIR,
            api_request=lambda method, target, body: _desktop_api(
                receiver, method, target, body, shell=shell,
            ),
        )
        try:
            shell.native_panel.start()
        except Exception as exc:
            _handle_native_panel_failure(shell, exc)
        else:
            clear_component_failure("native_panel")
            shell.activate_when_ready()
            with _attribution_self_check_watch_context():
                try:
                    shell.native_panel.run()
                finally:
                    shell.stop_auxiliary()
                    receiver.close()
                    _release_single_instance(lock)
            return 0
    else:
        clear_component_failure("native_panel")

    import webview

    httpd, port = _bind_dashboard_server(receiver, shell)
    _record_fallback_runtime_identity()
    threading.Thread(
        target=httpd.serve_forever,
        name="sandglass-dashboard",
        daemon=True,
    ).start()
    shell.url = f"http://{HOST}:{port}/"

    # Created hidden: webview.start() needs a window, but the orb shows first.
    shell.panel = webview.create_window(
        "sandglass", url=shell.url,
        width=PANEL_W, height=PANEL_H,
        # Stored physical; pywebview places by logical. See panel_moved.
        **_logical_panel_origin(state),
        frameless=True, easy_drag=False, on_top=True,
        resizable=True, hidden=True, js_api=PanelApi(shell),
    )
    shell.panel.events.loaded += shell.dress_panel
    shell.panel.events.loaded += shell.activate_when_ready
    shell.panel.events.moved += shell.panel_moved

    clear_component_failure("fallback_panel")
    try:
        with _attribution_self_check_watch_context():
            webview.start(**_webview_start_options())
    except Exception as exc:
        record_component_failure("fallback_panel", exc)
        raise
    finally:
        shell.stop_auxiliary()
        httpd.shutdown()
        httpd.server_close()
        receiver.close()
        _release_single_instance(lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
