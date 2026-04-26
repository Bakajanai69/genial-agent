"""S09.6 — Bootstrap du volume Railway depuis le bake Docker.

Au premier déploiement Railway (volume neuf monté sur ``/data``), copie
le cache MCP de bootstrap (``mcp_cache.json``) depuis ``/app/data``
(baked dans l'image) vers ``/data``. Au démarrage suivant (volume
préservé), le bootstrap copie le bake **uniquement si plus récent** que
la version du volume — sinon on préserve les enrichissements runtime
(cache MCP étendu par les utilisateurs).

Review S09.6 P1-3 / P0-1 : ``cl_threads.db`` (data layer Chainlit) n'est
PAS bootstrappé. Le data layer le crée à la volée via ``IF NOT EXISTS``
et le fichier reste exclu du bake / du tracking git (PII potentielle
dans les threads utilisateur).

Review S09.6 P1-5 : la propagation bake → volume utilise désormais
``mtime`` du fichier source comme repère. Scénario nominal post-30/04 :

1. Lancelot fait ``make prewarm-comptes`` → ``data/mcp_cache.json``
   enrichi avec les 12 entrées ``comptes-entreprise``.
2. ``git commit && git push`` → Railway rebuild Docker → mtime du bake
   ``/app/data/mcp_cache.json`` est plus récent que celui du volume.
3. Boot Railway → bootstrap détecte ``source.mtime > target.mtime``,
   sauvegarde l'ancien volume en ``.runtime.bak`` et copie le bake.

Tradeoff documenté : si un visiteur a écrit dans le cache au runtime
(cache miss → ``cache.set()``) et qu'on déploie un bake plus récent,
ces ajouts runtime sont perdus (sauvegardés dans ``.runtime.bak`` pour
forensic). Acceptable car (a) on rebuild rarement, (b) les ajouts
runtime sont pour la plupart redondants avec le bake, (c) cache MCP
n'est qu'un accélérateur — un cache miss n'est jamais bloquant.

No-op en local (``/data`` n'existe pas sur la machine dev) :
``MCP_CACHE_PERSIST_PATH`` reste sur ``data/mcp_cache.json`` (relatif
au CWD) et le bake EST le runtime.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

VOLUME_DIR = Path("/data")
BAKE_DIR = Path("/app/data")
BOOTSTRAP_FILES: tuple[str, ...] = ("mcp_cache.json",)


def bootstrap_volume_from_bake() -> None:
    """Copie les fichiers bake vers le volume Railway si absents OU
    si le bake est plus récent (review P1-5).

    No-op en local (``/data`` n'existe pas, on tombe dans le early-return).
    """
    if not VOLUME_DIR.exists():
        logger.info("data_bootstrap_skip", reason="volume_dir_absent")
        return
    for fname in BOOTSTRAP_FILES:
        target = VOLUME_DIR / fname
        source = BAKE_DIR / fname
        if not source.exists():
            logger.warning("data_bootstrap_skip", file=fname, reason="bake_absent")
            continue
        # Décision copy/skip basée sur mtime (review P1-5).
        action: str
        if not target.exists():
            action = "copy_initial"
        else:
            try:
                source_mtime = source.stat().st_mtime
                target_mtime = target.stat().st_mtime
            except OSError as exc:
                logger.warning(
                    "data_bootstrap_stat_failed",
                    file=fname,
                    error_type=type(exc).__name__,
                )
                continue
            if source_mtime <= target_mtime:
                logger.info(
                    "data_bootstrap_skip",
                    file=fname,
                    reason="volume_up_to_date",
                )
                continue
            action = "copy_refresh"
            # Sauvegarde forensic de l'ancien volume avant écrasement.
            backup = target.with_suffix(target.suffix + ".runtime.bak")
            try:
                shutil.copy2(target, backup)
                logger.info("data_bootstrap_backup", file=fname, backup=str(backup))
            except OSError as exc:
                logger.warning(
                    "data_bootstrap_backup_failed",
                    file=fname,
                    error_type=type(exc).__name__,
                )
                # On copy quand même : la fraîcheur prime sur la
                # sauvegarde forensic.
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            logger.warning(
                "data_bootstrap_copy_failed",
                file=fname,
                action=action,
                error_type=type(exc).__name__,
            )
            continue
        logger.info(
            "data_bootstrap_copy",
            file=fname,
            action=action,
            size=target.stat().st_size,
        )
