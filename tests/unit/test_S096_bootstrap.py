"""Tests S09.6 (review P1-5 + P0-1) — bootstrap_volume_from_bake.

Couvre les 4 chemins :

1. Volume absent (cas dev local) → no-op.
2. Bake source absent → log warning, no-op.
3. Volume vide → copie initiale (action=copy_initial).
4. Volume plus ancien que bake → backup + copie refresh (action=copy_refresh).
5. Volume plus récent que bake → préservé (action=skip volume_up_to_date).

Le module utilise des constantes ``VOLUME_DIR`` / ``BAKE_DIR`` à
patcher via monkeypatch — pas besoin de mocker shutil.
"""

from __future__ import annotations

import pytest

import genial_agent.data_bootstrap as bootstrap


def test_bootstrap_no_volume_dir_is_noop(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cas dev local : ``/data`` n'existe pas → early return."""
    monkeypatch.setattr(bootstrap, "VOLUME_DIR", tmp_path / "absent")
    monkeypatch.setattr(bootstrap, "BAKE_DIR", tmp_path / "bake")
    bootstrap.bootstrap_volume_from_bake()  # ne lève pas


def test_bootstrap_bake_absent_logs_warning(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bake source absent → on log warning, on continue (no-op pour
    ce fichier). Cas typique : le dev a oublié de pré-warmer le cache."""
    vol = tmp_path / "data"
    vol.mkdir()
    bake = tmp_path / "bake"
    bake.mkdir()
    monkeypatch.setattr(bootstrap, "VOLUME_DIR", vol)
    monkeypatch.setattr(bootstrap, "BAKE_DIR", bake)
    bootstrap.bootstrap_volume_from_bake()  # ne lève pas
    assert not (vol / "mcp_cache.json").exists()


def test_bootstrap_initial_copy_when_volume_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Volume neuf : la 1ère copie peuple le volume depuis le bake."""
    vol = tmp_path / "data"
    vol.mkdir()
    bake = tmp_path / "bake"
    bake.mkdir()
    (bake / "mcp_cache.json").write_text('{"hello": "bake"}')
    monkeypatch.setattr(bootstrap, "VOLUME_DIR", vol)
    monkeypatch.setattr(bootstrap, "BAKE_DIR", bake)

    bootstrap.bootstrap_volume_from_bake()

    target = vol / "mcp_cache.json"
    assert target.exists()
    assert target.read_text() == '{"hello": "bake"}'


def test_bootstrap_refresh_when_bake_is_newer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review P1-5 — le scénario nominal post-30/04 :

    1. Volume contient une vieille version du cache.
    2. Lancelot fait ``make prewarm-comptes`` + commit + push.
    3. Boot Railway → le bake est plus récent → on l'applique.
    4. Backup forensic ``.runtime.bak`` créé pour récupération.
    """
    import os

    vol = tmp_path / "data"
    vol.mkdir()
    bake = tmp_path / "bake"
    bake.mkdir()

    target = vol / "mcp_cache.json"
    source = bake / "mcp_cache.json"
    target.write_text('{"v": "old runtime"}')
    source.write_text('{"v": "fresh bake"}')
    # Force mtime : bake plus récent que volume.
    os.utime(target, (1_000_000, 1_000_000))
    os.utime(source, (2_000_000, 2_000_000))

    monkeypatch.setattr(bootstrap, "VOLUME_DIR", vol)
    monkeypatch.setattr(bootstrap, "BAKE_DIR", bake)

    bootstrap.bootstrap_volume_from_bake()

    # Bake fresh appliqué.
    assert target.read_text() == '{"v": "fresh bake"}'
    # Backup créé avec l'ancienne version.
    backup = vol / "mcp_cache.json.runtime.bak"
    assert backup.exists()
    assert backup.read_text() == '{"v": "old runtime"}'


def test_bootstrap_skips_when_volume_is_up_to_date(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si le volume est plus récent que le bake (cas standard runtime
    après accumulation d'écritures), on préserve."""
    import os

    vol = tmp_path / "data"
    vol.mkdir()
    bake = tmp_path / "bake"
    bake.mkdir()

    target = vol / "mcp_cache.json"
    source = bake / "mcp_cache.json"
    target.write_text('{"v": "runtime accumulated"}')
    source.write_text('{"v": "old bake"}')
    # Force mtime : volume plus récent que bake.
    os.utime(source, (1_000_000, 1_000_000))
    os.utime(target, (2_000_000, 2_000_000))

    monkeypatch.setattr(bootstrap, "VOLUME_DIR", vol)
    monkeypatch.setattr(bootstrap, "BAKE_DIR", bake)

    bootstrap.bootstrap_volume_from_bake()

    # Volume préservé, pas de backup créé.
    assert target.read_text() == '{"v": "runtime accumulated"}'
    assert not (vol / "mcp_cache.json.runtime.bak").exists()


def test_bootstrap_does_not_handle_cl_threads_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review P0-1 : ``cl_threads.db`` n'est plus dans BOOTSTRAP_FILES
    (il contient des PII utilisateur, créé à la volée par le data layer
    via IF NOT EXISTS)."""
    assert "cl_threads.db" not in bootstrap.BOOTSTRAP_FILES
    assert bootstrap.BOOTSTRAP_FILES == ("mcp_cache.json",)


def test_bootstrap_data_layer_creates_db_lazily(tmp_path) -> None:
    """Review P0-1 : confirme que le data layer crée le DB SQLite
    automatiquement au premier _get_conn(), sans dépendre du bootstrap."""
    import asyncio

    from genial_agent.ui.chainlit_data_layer import AnonymousSQLiteDataLayer

    db_path = tmp_path / "fresh.db"
    assert not db_path.exists()

    async def _create() -> None:
        layer = AnonymousSQLiteDataLayer(db_path=str(db_path))
        await layer._get_conn()
        await layer.close()

    asyncio.run(_create())
    assert db_path.exists()
    # Schéma créé : la taille est non-nulle (header SQLite ≥ 100 octets).
    assert db_path.stat().st_size > 0
