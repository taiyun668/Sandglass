from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from sandglass.paths import meter_home


SINGLE_OFFICIAL = "single_official"
SKILL_ASSISTED = "skill_assisted"
MODES = (SINGLE_OFFICIAL, SKILL_ASSISTED)

_LOCK = threading.RLock()


def product_mode_path() -> Path:
    return meter_home() / "product-mode.json"


def _load_mode() -> str:
    """Read the saved choice. A missing file is unselected; an unreadable one is not.

    FileNotFoundError is the first run. Any other OSError means the file is
    there and we could not read it -- treating that as unselected showed the
    chooser over a choice that was still on disk, and the next save replaced
    the file.
    """
    try:
        text = product_mode_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    payload = json.loads(text)
    mode = str(payload.get("attribution_mode") or "") if isinstance(payload, dict) else ""
    return mode if mode in MODES else ""


def attribution_mode() -> str:
    """Return the user's explicit attribution path, or empty before onboarding.

    An unreadable file is empty for the merge -- do not guess
    ``single_official`` -- and is not an API payload that says the user has
    not chosen.
    """
    with _LOCK:
        try:
            return _load_mode()
        except (OSError, ValueError, TypeError) as exc:
            from sandglass.diagnostics import record_component_failure

            record_component_failure("product_mode_write", exc)
            return ""


def set_attribution_mode(mode: str) -> dict[str, object]:
    """Persist one explicit product path under SANDGLASS_HOME, atomically."""
    selected = str(mode or "").strip()
    if selected not in MODES:
        raise ValueError("attribution_mode must be single_official or skill_assisted")
    payload = {"attribution_mode": selected}
    path = product_mode_path()
    temporary: Path | None = None
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                temporary = Path(handle.name)
            temporary.replace(path)
        except OSError as exc:
            from sandglass.diagnostics import record_component_failure

            record_component_failure("product_mode_write", exc)
            raise
        else:
            from sandglass.diagnostics import clear_component_failure

            clear_component_failure("product_mode_write")
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
    return mode_payload()


def mode_payload() -> dict[str, object]:
    with _LOCK:
        try:
            mode = _load_mode()
        except (OSError, ValueError, TypeError) as exc:
            from sandglass.diagnostics import record_component_failure

            record_component_failure("product_mode_write", exc)
            raise
    from sandglass.diagnostics import clear_component_failure

    clear_component_failure("product_mode_write")
    return {
        "selected": bool(mode),
        "attribution_mode": mode,
        "single_account_official_fallback": mode == SINGLE_OFFICIAL,
        "skill_required": mode == SKILL_ASSISTED,
    }
