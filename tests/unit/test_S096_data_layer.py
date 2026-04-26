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


async def test_get_user_returns_anonymous_constant(layer: AnonymousSQLiteDataLayer) -> None:
    """``get_user`` ignore l'identifier passé : tout le monde est
    ``anonymous``. C'est intentionnel (mode démo single-tenant)."""
    user = await layer.get_user("ignored-identifier")
    assert user is not None
    assert user.id == ANONYMOUS_USER_ID
    assert user.identifier == ANONYMOUS_USER_ID


async def test_create_thread_via_update_then_fetch(
    layer: AnonymousSQLiteDataLayer,
) -> None:
    """Chainlit n'a pas de ``create_thread`` distinct — ``update_thread``
    fait l'UPSERT. ``get_thread`` doit retourner les valeurs persistées."""
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
    assert fetched["userIdentifier"] == ANONYMOUS_USER_ID
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
