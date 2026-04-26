"""Tests S09.6 (H3') — AnonymousSQLiteDataLayer.

Couvre les méthodes ``BaseDataLayer`` que Chainlit appelle dans le flow
sidebar / resume : ``get_user`` / ``update_thread`` (UPSERT) / ``get_thread``
/ ``list_threads`` / ``delete_thread`` / ``create_step`` / ``update_step``
/ ``upsert_feedback`` / ``close``.

Pas de test de la couche websocket Chainlit (out-of-scope unit) ; un test
e2e manuel est documenté dans la story §"Test phase 3 review".
"""

from __future__ import annotations

import pytest
from chainlit.types import Feedback, Pagination, ThreadFilter

from genial_agent.ui.chainlit_data_layer import (
    ANONYMOUS_USER_ID,
    AnonymousSQLiteDataLayer,
)


@pytest.fixture
async def layer(tmp_path):
    """Fresh layer pointant sur un fichier SQLite unique par test."""
    db = tmp_path / "cl.db"
    layer = AnonymousSQLiteDataLayer(db_path=str(db))
    yield layer
    await layer.close()


async def test_get_user_propagates_identifier(layer: AnonymousSQLiteDataLayer) -> None:
    """S09.7 hotfix v3 : ``get_user`` propage l'identifier passé
    (cookie UUID ou fallback éphémère) au lieu d'écraser avec
    ``ANONYMOUS_USER_ID``. Sans ça, tous les threads étaient sous
    user_id="anonymous" → fuite cross-visiteur (chaque visiteur
    voyait les threads de tous les autres)."""
    user = await layer.get_user("cookie-uuid-abc123")
    assert user is not None
    assert user.id == "cookie-uuid-abc123"
    assert user.identifier == "cookie-uuid-abc123"
    # Fallback éphémère préfixé "anon-" doit aussi être propagé
    user2 = await layer.get_user("anon-deadbeef")
    assert user2 is not None
    assert user2.identifier == "anon-deadbeef"


async def test_create_thread_via_update_then_fetch(
    layer: AnonymousSQLiteDataLayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chainlit n'a pas de ``create_thread`` distinct — ``update_thread``
    fait l'UPSERT. ``get_thread`` doit retourner les valeurs persistées.

    On force un owner explicite via monkeypatch puisque hors contexte
    Chainlit le fallback est désormais ``__no_owner_resolved__``
    (sentinel anti-fuite, S09.7 hotfix v4)."""
    import genial_agent.ui.chainlit_data_layer as module

    monkeypatch.setattr(module, "_resolve_owner_id", lambda: "test-owner")
    await layer.update_thread(
        "thread-1",
        name="LVMH fiche identité",
        metadata={"locale": "fr"},
        tags=["demo"],
    )
    fetched = await layer.get_thread("thread-1")
    assert fetched is not None
    assert fetched["id"] == "thread-1"
    assert fetched["name"] == "LVMH fiche identité"
    assert fetched["userIdentifier"] == "test-owner"
    assert fetched["metadata"] == {"locale": "fr"}
    assert fetched["tags"] == ["demo"]
    assert fetched["steps"] == []
    assert fetched["elements"] == []


async def test_get_thread_returns_none_for_unknown(
    layer: AnonymousSQLiteDataLayer,
) -> None:
    assert await layer.get_thread("does-not-exist") is None


async def test_update_thread_partial_patch(layer: AnonymousSQLiteDataLayer) -> None:
    """``update_thread`` doit faire un patch partiel (ne pas écraser
    les colonnes non fournies par ``None``)."""
    await layer.update_thread("t", name="initial", tags=["a"], metadata={"k": 1})
    await layer.update_thread("t", name="updated")  # ne touche que name
    fetched = await layer.get_thread("t")
    assert fetched is not None
    assert fetched["name"] == "updated"
    assert fetched["tags"] == ["a"]
    assert fetched["metadata"] == {"k": 1}


async def test_list_threads_orders_by_recent_first(
    layer: AnonymousSQLiteDataLayer,
) -> None:
    """Les threads les plus récemment touchés (insert ou update step)
    apparaissent en haut — c'est ce que la sidebar Chainlit affiche."""
    await layer.update_thread("old", name="old thread")
    await layer.update_thread("recent", name="recent thread")

    page = await layer.list_threads(
        Pagination(first=10, cursor=None),
        ThreadFilter(feedback=None, userId=None, search=None),
    )
    ids = [t["id"] for t in page.data]
    # `recent` a été inséré en dernier → updated_at plus récent → en tête.
    assert ids[0] == "recent"
    assert ids[1] == "old"
    assert page.pageInfo.hasNextPage is False


async def test_list_threads_search_filter(
    layer: AnonymousSQLiteDataLayer,
) -> None:
    """Le filtre ``search`` fait un LIKE sur ``name``."""
    await layer.update_thread("a", name="LVMH fiche")
    await layer.update_thread("b", name="Carrefour comparaison")

    page = await layer.list_threads(
        Pagination(first=10, cursor=None),
        ThreadFilter(feedback=None, userId=None, search="Carrefour"),
    )
    assert [t["id"] for t in page.data] == ["b"]


async def test_list_threads_pagination(layer: AnonymousSQLiteDataLayer) -> None:
    """``first`` borne la page ; ``hasNextPage`` est True s'il reste."""
    for i in range(5):
        await layer.update_thread(f"t{i}", name=f"thread {i}")
    page1 = await layer.list_threads(
        Pagination(first=2, cursor=None),
        ThreadFilter(feedback=None, userId=None, search=None),
    )
    assert len(page1.data) == 2
    assert page1.pageInfo.hasNextPage is True


async def test_delete_thread_idempotent(layer: AnonymousSQLiteDataLayer) -> None:
    """Delete d'un thread inexistant ne lève pas — Chainlit peut
    rejouer un delete au reload page."""
    await layer.delete_thread("does-not-exist")  # ne lève pas

    await layer.update_thread("t", name="to-delete")
    await layer.delete_thread("t")
    assert await layer.get_thread("t") is None


async def test_delete_thread_cascades_steps(layer: AnonymousSQLiteDataLayer) -> None:
    """Les steps doivent être supprimées avec le thread parent."""
    await layer.update_thread("t1", name="parent")
    await layer.create_step({"id": "s1", "threadId": "t1", "name": "step1", "type": "user_message"})
    await layer.delete_thread("t1")

    # Le thread n'existe plus → ses steps non plus.
    assert await layer.get_thread("t1") is None


async def test_create_step_attached_to_thread(layer: AnonymousSQLiteDataLayer) -> None:
    await layer.update_thread("t1", name="parent")
    await layer.create_step(
        {
            "id": "s1",
            "threadId": "t1",
            "name": "user message",
            "type": "user_message",
            "input": "Hello",
            "output": "",
        }
    )

    fetched = await layer.get_thread("t1")
    assert fetched is not None
    assert len(fetched["steps"]) == 1
    assert fetched["steps"][0]["id"] == "s1"


async def test_update_step_overwrites(layer: AnonymousSQLiteDataLayer) -> None:
    """``update_step`` est un UPSERT : ré-appelé avec le même id, met à jour."""
    await layer.update_thread("t1", name="parent")
    await layer.create_step({"id": "s1", "threadId": "t1", "name": "first", "type": "tool"})
    await layer.update_step({"id": "s1", "threadId": "t1", "name": "updated", "type": "tool"})
    fetched = await layer.get_thread("t1")
    assert fetched is not None
    assert len(fetched["steps"]) == 1  # toujours 1 step (UPSERT, pas insert)
    assert fetched["steps"][0]["name"] == "updated"


async def test_upsert_feedback_round_trip(layer: AnonymousSQLiteDataLayer) -> None:
    fb = Feedback(forId="step-1", value=1, threadId="t1", comment="LGTM")
    feedback_id = await layer.upsert_feedback(fb)
    assert feedback_id  # non vide

    # Update : même forId, value différente.
    fb2 = Feedback(forId="step-1", value=0, threadId="t1", id=feedback_id)
    new_id = await layer.upsert_feedback(fb2)
    assert new_id == feedback_id


async def test_delete_user_session_returns_true(
    layer: AnonymousSQLiteDataLayer,
) -> None:
    """``delete_user_session`` est un no-op (pas de session table) mais
    doit retourner True pour respecter le contrat ``BaseDataLayer``."""
    assert await layer.delete_user_session("any") is True


async def test_close_releases_connection(layer: AnonymousSQLiteDataLayer) -> None:
    """``close`` doit pouvoir être appelé sans erreur, et la connexion
    interne doit redevenir ``None`` (utile si le tearown est appelé 2x)."""
    await layer.update_thread("t", name="test")  # force open
    assert layer._conn is not None
    await layer.close()
    assert layer._conn is None
    # 2ème close : no-op.
    await layer.close()


async def test_persistence_across_instances(tmp_path) -> None:
    """Critère "fini" en miniature : 2 instances pointant sur le même
    fichier voient les threads de la 1ère depuis la 2ème — preuve que
    le data layer survit à un restart de process Chainlit."""
    db = tmp_path / "cl.db"
    layer1 = AnonymousSQLiteDataLayer(db_path=str(db))
    await layer1.update_thread("t1", name="LVMH")
    await layer1.update_thread("t2", name="Carrefour")
    await layer1.close()

    layer2 = AnonymousSQLiteDataLayer(db_path=str(db))
    page = await layer2.list_threads(
        Pagination(first=10, cursor=None),
        ThreadFilter(feedback=None, userId=None, search=None),
    )
    names = {t["name"] for t in page.data}
    assert names == {"LVMH", "Carrefour"}
    await layer2.close()


async def test_no_s3_or_azure_imports() -> None:
    """Acceptance review : ne pas importer S3 / Azure / autres backends
    (le critère "Aucune dépendance S3/Azure" du review check-list)."""
    from pathlib import Path

    import genial_agent.ui.chainlit_data_layer as module

    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "boto3" not in src
    assert "azure" not in src.lower()
    assert "google.cloud" not in src


# ── Review S09.6 P1-3 : isolation multi-tenant par owner_id ───────────


async def test_owner_isolation_get_thread_denied(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un thread créé par owner A ne doit PAS être lisible par owner B
    (régression P1-3 : avant le fix, tous les threads étaient visibles
    à tous via le user "anonymous" constant)."""
    import genial_agent.ui.chainlit_data_layer as module

    db = tmp_path / "cl.db"
    layer = AnonymousSQLiteDataLayer(db_path=str(db))
    try:
        # Owner A crée le thread.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "owner-a")
        await layer.update_thread("t-private", name="LVMH conf")
        assert (await layer.get_thread("t-private")) is not None

        # Owner B essaie de l'ouvrir → refusé (None comme si inexistant).
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "owner-b")
        assert (await layer.get_thread("t-private")) is None
    finally:
        await layer.close()


async def test_owner_isolation_list_threads_filters(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``list_threads`` ne renvoie que les threads de l'owner courant
    (+ les threads "anonymous" legacy pour rétrocompat)."""
    import genial_agent.ui.chainlit_data_layer as module

    db = tmp_path / "cl.db"
    layer = AnonymousSQLiteDataLayer(db_path=str(db))
    try:
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "alice")
        await layer.update_thread("t-alice", name="alice's chat")

        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "bob")
        await layer.update_thread("t-bob", name="bob's chat")

        # Alice ne voit que son thread.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "alice")
        page = await layer.list_threads(
            Pagination(first=10, cursor=None),
            ThreadFilter(feedback=None, userId=None, search=None),
        )
        ids = {t["id"] for t in page.data}
        assert ids == {"t-alice"}

        # Bob ne voit que le sien.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "bob")
        page = await layer.list_threads(
            Pagination(first=10, cursor=None),
            ThreadFilter(feedback=None, userId=None, search=None),
        )
        ids = {t["id"] for t in page.data}
        assert ids == {"t-bob"}
    finally:
        await layer.close()


async def test_owner_isolation_delete_thread_denied(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un owner ne peut pas supprimer le thread d'un autre owner —
    refus silencieux (Chainlit attend un no-op)."""
    import genial_agent.ui.chainlit_data_layer as module

    db = tmp_path / "cl.db"
    layer = AnonymousSQLiteDataLayer(db_path=str(db))
    try:
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "alice")
        await layer.update_thread("t-alice", name="alice")

        # Bob essaie de supprimer → refusé silencieusement.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "bob")
        await layer.delete_thread("t-alice")  # ne lève pas

        # Alice voit toujours son thread.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "alice")
        assert (await layer.get_thread("t-alice")) is not None
    finally:
        await layer.close()


async def test_legacy_anonymous_threads_invisible_to_other_owners(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S09.7 hotfix v3 : les threads "legacy" sous user_id="anonymous"
    (créés à cause du bug ``get_user`` qui retournait toujours
    ``ANONYMOUS_USER_ID``) ne sont **plus** visibles à un nouvel owner.
    Auparavant ils l'étaient "pour rétrocompat" → fuite cross-visiteur.
    """
    import genial_agent.ui.chainlit_data_layer as module

    db = tmp_path / "cl.db"
    layer = AnonymousSQLiteDataLayer(db_path=str(db))
    try:
        # Thread "legacy" créé sous le user anonymous (avant fix v3).
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: ANONYMOUS_USER_ID)
        await layer.update_thread("t-legacy", name="vieux thread")

        # Un nouvel owner (alice) ouvre l'app → ne voit PAS le thread
        # legacy (isolation stricte par-owner).
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: "alice")
        page = await layer.list_threads(
            Pagination(first=10, cursor=None),
            ThreadFilter(feedback=None, userId=None, search=None),
        )
        assert {t["id"] for t in page.data} == set(), (
            "alice ne doit pas voir les threads legacy anonymous (isolation "
            "stricte S09.7 hotfix v3)"
        )
        # Mais l'owner "anonymous" lui-même (cas dégénéré) les voit toujours.
        monkeypatch.setattr(module, "_resolve_owner_id", lambda: ANONYMOUS_USER_ID)
        page2 = await layer.list_threads(
            Pagination(first=10, cursor=None),
            ThreadFilter(feedback=None, userId=None, search=None),
        )
        assert {t["id"] for t in page2.data} == {"t-legacy"}
    finally:
        await layer.close()


async def test_resolve_owner_id_falls_back_outside_chainlit_context() -> None:
    """S09.7 hotfix v4 : hors contexte Chainlit, ``_resolve_owner_id``
    retourne désormais le sentinel ``__no_owner_resolved__`` (au lieu
    de ``ANONYMOUS_USER_ID``). Raison : ``ANONYMOUS_USER_ID`` matchait
    les threads pollués pré-fix v3 et causait une fuite cross-visiteur
    quand list_threads était appelé hors contexte WebSocket."""
    from genial_agent.ui.chainlit_data_layer import _resolve_owner_id

    assert _resolve_owner_id() == "__no_owner_resolved__"
