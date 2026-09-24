"""TriggerBOFF property plugin.

The 13 Sydney property tool modules in ``$HERMES_HOME/tools/`` are written as
Hermes *registry* modules: each ends with a guarded ``from tools.registry import
registry; registry.register(...)`` block. Outside a Hermes process that import
fails and the module silently registers nothing — which is exactly why, sitting
in ``$HERMES_HOME/tools/``, they were never visible to any session.

This plugin bridges them into the supported plugin API. It imports each module
with a *capture shim* standing in for the registry, collects the schema/handler
each module already declares, and re-registers every tool through
``ctx.register_tool``. Schemas and handlers are therefore reused verbatim: this
file contains no duplicated tool definitions and cannot drift from the modules.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The tool modules shipped by start.sh into $HERMES_HOME/tools/
MODULES = (
    "domain_property_tool",
    "nsw_property_sales_tool",
    "abs_suburb_tool",
    "geocode_tool",
    "nsw_planning_tool",
    "rental_yield_tool",
    "auction_history_tool",
    "nsw_land_value_tool",
    "nsw_overlays_tool",
    "school_catchment_tool",
    "strata_tool",
    "transport_tool",
    "bp_inspection_tool",
)

DEFAULT_TOOLSET = "property_data"


def _tools_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "tools"


def _capture_registrations(tools_dir: Path) -> list[dict]:
    """Import every tool module with a shim registry and collect its registrations.

    Each module binds ``registry`` at import time via ``from tools.registry import
    registry``, so the shim must be installed *before* the import and the modules
    must be imported fresh (purged from ``sys.modules`` first), otherwise a module
    imported earlier would bind the real registry and register nothing here.
    """
    import tools.registry as registry_module  # noqa: PLC0415 — must be inside Hermes

    real_registry = registry_module.registry
    captured: list[dict] = []

    class _CaptureShim:
        """Records register() calls; delegates anything else to the real registry."""

        def register(self, **kwargs) -> None:
            captured.append(kwargs)

        def __getattr__(self, name):
            return getattr(real_registry, name)

    registry_module.registry = _CaptureShim()
    sys.path.insert(0, str(tools_dir))
    try:
        for module_name in MODULES:
            sys.modules.pop(module_name, None)
            try:
                importlib.import_module(module_name)
            except Exception:  # noqa: BLE001 — one bad module must not kill the plugin
                logger.exception("triggerboff-property: failed to import %s", module_name)
    finally:
        registry_module.registry = real_registry
    return captured


def register(ctx) -> None:
    """Import the property tool modules and re-register their tools via the plugin API."""
    tools_dir = _tools_dir()
    if not tools_dir.is_dir():
        logger.error("triggerboff-property: tools dir not found: %s", tools_dir)
        return

    captured = _capture_registrations(tools_dir)
    if not captured:
        logger.error("triggerboff-property: no tools captured from %s", tools_dir)
        return

    registered = 0
    for kwargs in captured:
        name = kwargs.get("name")
        schema = kwargs.get("schema")
        handler = kwargs.get("handler")
        if not (name and schema and handler):
            logger.warning("triggerboff-property: skipping incomplete registration %r", name)
            continue
        try:
            ctx.register_tool(
                name=name,
                toolset=kwargs.get("toolset") or DEFAULT_TOOLSET,
                schema=schema,
                handler=handler,
                check_fn=kwargs.get("check_fn"),
                requires_env=kwargs.get("requires_env") or None,
            )
            registered += 1
        except Exception:  # noqa: BLE001
            logger.exception("triggerboff-property: failed to register %s", name)

    logger.info("triggerboff-property: registered %d/%d tools", registered, len(captured))
