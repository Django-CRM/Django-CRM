"""Registry of vertical packs, built once from common/packs/manifests/*.json.

Pack ids are dict keys, never filesystem paths. A caller-supplied id can only
hit or miss this dict, so there is no traversal or SSRF surface.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from common.packs.schema import PackValidationError, validate_manifest

logger = logging.getLogger(__name__)

# Inside the packaged `common` app, so the wheel carries the manifests. A path
# outside every wheel package (they used to sit in backend/packs/) exists in a
# source checkout only; an installed copy then loads no packs at all.
PACKS_DIR = Path(__file__).resolve().parent / "manifests"

# Ids a caller's own manifest is stored under; a shipped manifest may not use them.
PRIVATE_APP_PREFIX = "x-"


@lru_cache(maxsize=1)
def get_registry() -> dict[str, dict]:
    registry: dict[str, dict] = {}
    if not PACKS_DIR.is_dir():
        # Loud, because the empty list that follows hides the starter picker on
        # org creation without any other trace.
        logger.error("Pack directory %s is missing; no vertical packs load.", PACKS_DIR)
        return registry
    for path in sorted(PACKS_DIR.glob("*.json")):
        raw = json.loads(path.read_text())
        manifest = validate_manifest(raw)
        if manifest["id"].startswith(PRIVATE_APP_PREFIX):
            raise PackValidationError(
                f"{path.name}: manifest id {manifest['id']!r} must not start with "
                f"{PRIVATE_APP_PREFIX!r}, which is reserved"
            )
        if manifest["id"] != path.stem:
            raise PackValidationError(
                f"{path.name}: manifest id {manifest['id']!r} must match the filename"
            )
        registry[manifest["id"]] = manifest
    return registry


def get_pack(pack_id: str) -> dict | None:
    """Look up a pack by id. Unknown ids, including anything path-shaped, return None."""
    return get_registry().get(pack_id)
