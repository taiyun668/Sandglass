"""Canonical application resource resolution.

Tests and runtime code use these resolvers together so they cannot silently
validate one copy while the desktop loads another.
"""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"


def source_native_bridge_path() -> Path | None:
    """Return the editable bridge source only when running from a source tree."""

    candidate = SOURCE_ROOT / "native" / "SandglassShell" / "PanelShell.js"
    return candidate if candidate.is_file() else None
