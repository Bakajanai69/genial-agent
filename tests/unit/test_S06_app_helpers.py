"""Tests des helpers privés de ``genial_agent.app`` (S06).

Couvre les corrections review :

- ``_drain_orphan_steps`` : ferme les ``cl.Step`` laissées ouvertes
  quand le pipeline cape mid-tool (bug B9 review).
- ``_resolve_session_id`` : priorité ``cl.context.session.id`` →
  ``user_session["id"]`` → fallback UUID stable per-session (bug B7
  review : refus de partager un ``"unknown"`` global qui mêlerait les
  budgets / locks de toutes les sessions).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from genial_agent.app import _drain_orphan_steps, _resolve_session_id
from genial_agent.ui.events import TurnState


def _stub_msg() -> MagicMock:
    msg = MagicMock(name="cl.Message")
    msg.content = ""
    msg.send = AsyncMock()
    msg.stream_token = AsyncMock()
    msg.update = AsyncMock()
    msg.remove = AsyncMock()
    return msg


def _stub_step() -> MagicMock:
    step = MagicMock(name="cl.Step")
    step.is_error = False
    step.input = None
    step.output = None
    step.__aenter__ = AsyncMock(return_value=step)
    step.__aexit__ = AsyncMock(return_value=None)
    return step


# ------------------------------------------------------------------
# _drain_orphan_steps (B9)
# ------------------------------------------------------------------


async def test_drain_orphan_steps_noop_when_empty() -> None:
    state = TurnState(msg=_stub_msg())
    await _drain_orphan_steps(state)  # ne lève pas
    assert state.step_by_id == {}


async def test_drain_orphan_steps_closes_all_open_steps() -> None:
    """Régression B9 : un cap mid-tool laisse une step ouverte. On force
    son ``__aexit__`` + on la marque is_error pour signaler à Fabien
    qu'elle n'a pas eu de ``tool_result``."""
    state = TurnState(msg=_stub_msg())
    step_a, step_b = _stub_step(), _stub_step()
    state.step_by_id = {"tu_a": step_a, "tu_b": step_b}

    await _drain_orphan_steps(state)

    step_a.__aexit__.assert_awaited_once()
    step_b.__aexit__.assert_awaited_once()
    assert step_a.is_error is True
    assert step_b.is_error is True
    # Le dict est vidé pour éviter un re-drain accidentel.
    assert state.step_by_id == {}


async def test_drain_orphan_steps_tolerates_aexit_failure() -> None:
    """Si Chainlit a déjà fermé le WebSocket, ``__aexit__`` peut lever.
    On loggue best-effort et on continue plutôt que de propager."""
    state = TurnState(msg=_stub_msg())
    flaky = _stub_step()
    flaky.__aexit__ = AsyncMock(side_effect=RuntimeError("websocket closed"))
    healthy = _stub_step()
    state.step_by_id = {"tu_flaky": flaky, "tu_ok": healthy}

    # Ne doit pas lever malgré l'exception sur ``flaky``.
    await _drain_orphan_steps(state)

    healthy.__aexit__.assert_awaited_once()
    assert state.step_by_id == {}


# ------------------------------------------------------------------
# _resolve_session_id (B7)
# ------------------------------------------------------------------


@pytest.fixture
def patched_chainlit(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``cl.context.session`` et ``cl.user_session`` pour
    contrôler le résultat de ``_resolve_session_id``.

    Renvoie un handle pour mutate le mock à volonté dans chaque test.
    """
    import genial_agent.app as app_mod

    handle: dict[str, Any] = {
        "ctx_session_id": None,
        "user_session_data": {},
    }

    fake_ctx_session = MagicMock(name="cl.context.session")
    # ``id`` lu via getattr — on fait un property via PropertyMock pour
    # que ``getattr(ctx_session, "id", None)`` renvoie la valeur courante.
    type(fake_ctx_session).id = property(lambda _self: handle["ctx_session_id"])

    fake_ctx = MagicMock(name="cl.context")
    fake_ctx.session = fake_ctx_session
    monkeypatch.setattr(app_mod.cl, "context", fake_ctx)

    fake_user_session = MagicMock(name="cl.user_session")
    fake_user_session.get = MagicMock(side_effect=handle["user_session_data"].get)
    fake_user_session.set = MagicMock(
        side_effect=lambda k, v: handle["user_session_data"].__setitem__(k, v)
    )
    monkeypatch.setattr(app_mod.cl, "user_session", fake_user_session)

    return handle


def test_resolve_session_id_uses_context_session_id(
    patched_chainlit: dict[str, Any],
) -> None:
    """Priorité 1 : ``cl.context.session.id`` est l'API publique stable."""
    patched_chainlit["ctx_session_id"] = "ctx-session-abc"
    assert _resolve_session_id() == "ctx-session-abc"


def test_resolve_session_id_falls_back_to_user_session_id(
    patched_chainlit: dict[str, Any],
) -> None:
    """Priorité 2 : ``user_session["id"]`` (fallback historique Chainlit)."""
    patched_chainlit["ctx_session_id"] = None  # context.session.id absent
    patched_chainlit["user_session_data"]["id"] = "user-session-xyz"
    assert _resolve_session_id() == "user-session-xyz"


def test_resolve_session_id_generates_uuid_fallback(
    patched_chainlit: dict[str, Any],
) -> None:
    """Priorité 3 : si rien n'est dispo, on génère un UUID stable
    par session (jamais ``"unknown"`` partagé entre toutes les sessions)."""
    patched_chainlit["ctx_session_id"] = None
    # user_session vide.

    sid = _resolve_session_id()

    # Ce n'est jamais ``"unknown"`` ni ``""``.
    assert sid != "unknown"
    assert sid != ""
    # Sur un 2ème appel dans la même session, on retrouve le **même** UUID
    # (sinon le ``budget.add`` et ``budget.reset`` cibleraient des clés
    # différentes → leak mémoire + budget jamais reset).
    assert _resolve_session_id() == sid


def test_resolve_session_id_does_not_pollute_user_session_when_ctx_ok(
    patched_chainlit: dict[str, Any],
) -> None:
    """Si la priorité 1 hit, on ne pose pas de fallback UUID inutile dans
    ``user_session`` — sinon une session avec ``ctx.session.id`` qui change
    en cours de route (refresh page) ferait collision avec le fallback."""
    patched_chainlit["ctx_session_id"] = "ctx-abc"
    _resolve_session_id()
    assert "_session_id_fallback" not in patched_chainlit["user_session_data"]
