"""S09.6 — Bootstrap du volume Railway depuis le bake Docker.

Au premier déploiement Railway (volume neuf monté sur ``/data``), copie
les fichiers de bootstrap (``mcp_cache.json``, ``cl_threads.db``) depuis
``/app/data`` (baked dans l'image) vers ``/data``. Au démarrage suivant
(volume préservé), le bootstrap est no-op : le code lit/écrit toujours
sur ``/data``, donc le volume accumule les enrichissements runtime.

Trade-off documenté (cf. story §"Architecture phase 1 — Axe 3 révisé") :
la propagation bake → volume après un ``make prewarm-comptes`` n'est
pas automatique (sinon on écraserait les ajouts runtime). Pour forcer
la reprise du bake, supprimer manuellement le fichier sur le volume.

No-op en local (``/data`` n'existe pas sur la machine dev) : ``MCP_CACHE_PERSIST_PATH``
reste sur ``data/mcp_cache.json`` (relatif au CWD) et le bake EST le
runtime.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

VOLUME_DIR = Path("/data")
BAKE_DIR = Path("/app/data")
BOOTSTRAP_FILES: tuple[str, ...] = ("mcp_cache.json", "cl_threads.db")


def bootstrap_volume_from_bake() -> None:
    """Copie les fichiers bake vers le volume Railway si absents.

    No-op en local (``/data`` n'existe pas, on tombe dans le early-return).
    No-op si le volume contient déjà les fichiers (cas standard runtime,
    on préserve l'état accumulé).
    """
    if not VOLUME_DIR.exists():
        logger.info("data_bootstrap_skip", reason="volume_dir_absent")
        return
    for fname in BOOTSTRAP_FILES:
        target = VOLUME_DIR / fname
        source = BAKE_DIR / fname
        if target.exists():
            logger.info("data_bootstrap_skip", file=fname, reason="volume_has_file")
            continue
        if not source.exists():
            logger.warning("data_bootstrap_skip", file=fname, reason="bake_absent")
            continue
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            logger.warning(
                "data_bootstrap_copy_failed",
                file=fname,
                error_type=type(exc).__name__,
            )
            continue
        logger.info("data_bootstrap_copy", file=fname, size=target.stat().st_size)
