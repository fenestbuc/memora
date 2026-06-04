"""Single source of truth for the Memora version."""

from __future__ import annotations

try:
    from importlib.metadata import version as _get_version
except ImportError:  # pragma: no cover
    from importlib_metadata import version as _get_version  # type: ignore[no-redef]

try:
    __version__ = _get_version("memora")
except Exception:  # pragma: no cover — fallback during editable installs
    __version__ = "0.4.0"
