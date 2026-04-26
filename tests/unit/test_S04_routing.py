"""Tests unitaires S04 — routing Haiku ↔ Sonnet.

Pattern ``fake AsyncAnthropic`` réutilisé depuis
``test_S03_agent_loop.py`` — 0 appel API, 0 crédit Pappers. Couvre la
plomberie :

- ``pick_initial_tier`` sur cas paramétrés FR (avec et sans accents).
- Self-escalation : Haiku appelle ``escalate_to_sonnet`` → S04 break
  → Sonnet reprend en continuation.
- Forced escalation : cap tool_calls_per_turn atteint en Haiku →
  escalade forcée.
- Capped sur Sonnet : cap atteint alors qu'on est déjà Sonnet →
  event ``capped`` émis, pas d'escalade possible.
- State integrity après escalation (I2 de S03).
- Lock released after gen.aclose().
- Events ``routing_initial`` / ``routing_done`` toujours émis.
- Multi-SIREN → Sonnet direct.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest

from genial_agent.agent import ConversationState
from genial_agent.models import MODEL_HAIKU, MODEL_SONNET, ModelTier
from genial_agent.routing import (
    ESCALATE_TOOL_SCHEMA,
    MAX_TOOL_CALLS_PER_TURN,
    REASON_CODE_CAP_TOOL_CALLS,
    REASON_CODE_CAP_WALL_CLOCK,
    REASON_CODE_SELF,
    _normalize_fr,
    pick_initial_tier,
    run_routed_turn,
)

# Fakes importés depuis le module S03 pour éviter la duplication.
# Alternative (next step) : extraire dans ``tests/_fakes_anthropic.py``.
from tests.unit.test_S03_agent_loop import (
    _install_fake_anthropic,
    _install_fake_mcp,
    _message,
    _ScriptedTurn,
    _text,
    _tool_use,
)

# ============================================================================
# Group 1 — pick_initial_tier (pur, rapide, accent-sensible)
# ============================================================================


@pytest.mark.parametrize(
    "msg,expected",
    [
        # Simple → Haiku
        ("Donne-moi la fiche de LVMH", ModelTier.HAIKU),
        ("Les dirigeants de BNP Paribas", ModelTier.HAIKU),
        ("Qui sont les dirigeants ?", ModelTier.HAIKU),
        ("Bonjour", ModelTier.HAIKU),
        # Comparaison → Sonnet
        ("Compare Carrefour et Casino", ModelTier.SONNET),
        ("Comparaison entre Renault et PSA", ModelTier.SONNET),
        ("Carrefour versus Auchan", ModelTier.SONNET),
        ("LVMH vs Kering", ModelTier.SONNET),
        ("LVMH vs. Kering", ModelTier.SONNET),
        # Dossier complet / due diligence
        ("Fais-moi un dossier complet sur Total", ModelTier.SONNET),
        ("Due diligence sur Sanofi", ModelTier.SONNET),
        ("Due dil sur BNP", ModelTier.SONNET),
        # Évolution multi-années (avec et sans accent, avec majuscules)
        ("Évolution du CA sur 3 ans", ModelTier.SONNET),
        ("Evolution du CA sur 3 ans", ModelTier.SONNET),
        ("EVOLUTION DU CA", ModelTier.SONNET),
        ("sur 5 ans", ModelTier.SONNET),
        # Pronoms interrogatifs
        ("Lequel est le plus rentable ?", ModelTier.SONNET),
        ("Laquelle a le meilleur CA ?", ModelTier.SONNET),
        ("Lesquels sont cotés ?", ModelTier.SONNET),
        # Similarité / concurrence (avec normalisation "à" → "a")
        ("Entreprises similaires à Michelin", ModelTier.SONNET),
        ("Entreprises similaires a Michelin", ModelTier.SONNET),
        ("Concurrents de Decathlon", ModelTier.SONNET),
        # Multi-SIREN
        ("Compare 123456789 et 987654321", ModelTier.SONNET),
        ("Fais la fiche de 123456789", ModelTier.HAIKU),
        # Pas de faux positifs
        ("Une solution comparable à X", ModelTier.HAIKU),
        ("Jeune entreprise avec de l'évolution", ModelTier.SONNET),
    ],
)
def test_pick_initial_tier(msg: str, expected: ModelTier) -> None:
    assert pick_initial_tier(msg) == expected


def test_normalize_fr_strips_diacritics() -> None:
    assert _normalize_fr("Évolution") == "evolution"
    assert _normalize_fr("Société Générale") == "societe generale"
    assert _normalize_fr("À propos") == "a propos"
    assert _normalize_fr("Français") == "francais"


def test_normalize_fr_drops_non_nfkd_ligatures() -> None:
    """Ligatures NON-décomposables par NFKD : ``œ`` (U+0153), ``æ``
    (U+00E6), ``ß`` (U+00DF) — NFKD ne les sépare pas et
    ``.encode("ascii", "ignore")`` les **drop** (pas de transcription
    en ``oe`` / ``ae`` / ``ss``).

    Comportement accepté pour le MVP : vocabulaire rare dans les
    requêtes entreprises FR (``compare``, ``versus``, ``évolution``
    marchent). Inscrit dans le marbre ici pour qu'un changement
    futur (remplacement explicite via ``str.maketrans`` par exemple)
    soit un choix conscient, pas un accident."""
    assert _normalize_fr("Cœur") == "cur"  # pas "coeur"
    assert _normalize_fr("Cœur Défense") == "cur defense"  # cas concret FR
    assert _normalize_fr("Straße") == "strae"  # pas "strasse"
    assert _normalize_fr("naïve") == "naive"  # diacritique simple OK
    assert _normalize_fr("curriculum vitæ") == "curriculum vit"  # æ droppé


def test_escalate_tool_schema_shape() -> None:
    # Anthropic name pattern ^[a-zA-Z0-9_-]{1,128}$
    assert re.match(r"^[a-zA-Z0-9_-]{1,128}$", ESCALATE_TOOL_SCHEMA["name"])
    assert ESCALATE_TOOL_SCHEMA["name"] == "escalate_to_sonnet"
    assert "reason" in ESCALATE_TOOL_SCHEMA["input_schema"]["properties"]
    assert ESCALATE_TOOL_SCHEMA["input_schema"]["required"] == ["reason"]


# ============================================================================
# Group 2 — run_routed_turn via fake AsyncAnthropic
# ============================================================================


async def test_simple_query_stays_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Fiche LVMH' → Haiku direct, pas d'escalade."""
    script = [
        _ScriptedTurn(
            text_chunks=["Fiche LVMH..."],
            final=_message(stop_reason="end_turn", content=[_text("Fiche LVMH...")]),
        )
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Fiche LVMH")]

    routing_initial = next(e for e in events if e["type"] == "routing_initial")
    assert routing_initial["tier"] == "haiku"
    assert routing_initial["reason"] == "default"

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["model_used"] == "haiku"
    assert routing_done["escalated"] is False

    # Pas d'event escalation ni capped
    assert not any(e["type"] == "escalation" for e in events)
    assert not any(e["type"] == "capped" for e in events)


async def test_complex_keyword_goes_sonnet_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Compare X et Y' → Sonnet direct, sans passer par Haiku."""
    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Comparaison...")],
                model=MODEL_SONNET,
            ),
        )
    ]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare LVMH et Kering")]

    routing_initial = next(e for e in events if e["type"] == "routing_initial")
    assert routing_initial["tier"] == "sonnet"
    assert routing_initial["reason"] == "keyword"

    # Un seul appel stream — pas de re-dispatch
    assert len(fake.messages.calls) == 1
    assert fake.messages.calls[0]["model"] == MODEL_SONNET
    # Pas de extra_tools escalate (on est déjà Sonnet)
    tool_names = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert "escalate_to_sonnet" not in tool_names


async def test_haiku_self_escalates_to_sonnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku appelle escalate_to_sonnet → break → Sonnet reprend en
    continuation. State intact (invariant I2)."""
    escalate_tu = _tool_use(
        "tu_esc",
        "escalate_to_sonnet",
        {"reason": "Besoin de raisonnement multi-entités"},
    )
    script = [
        # Haiku escalade directement
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        # Sonnet reprend en continuation
        _ScriptedTurn(
            text_chunks=["Analyse Sonnet..."],
            final=_message(
                stop_reason="end_turn",
                content=[_text("Analyse Sonnet...")],
                model=MODEL_SONNET,
            ),
        ),
    ]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Analyse fine de X")]

    # Event escalation émis — code stable + détail humain.
    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "self"
    assert escalation["reason_code"] == REASON_CODE_SELF
    assert "multi-entit" in escalation["reason"].lower()

    # routing_done reflète l'escalade — code + reason_detail + mode.
    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["model_used"] == "sonnet"
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "self"
    assert routing_done["escalation_reason_code"] == REASON_CODE_SELF
    assert "multi-entit" in routing_done["escalation_reason"].lower()
    # Pas de capped sur ce chemin
    assert routing_done["capped"] is False
    assert routing_done["capped_reason_code"] is None

    # 2 appels stream : Haiku + Sonnet
    assert len(fake.messages.calls) == 2
    assert fake.messages.calls[0]["model"] == MODEL_HAIKU
    assert fake.messages.calls[1]["model"] == MODEL_SONNET

    # Le 2e appel a ``messages`` partageant le state du 1er (continuation) :
    # le user_wrap initial est présent, pas dupliqué.
    user_messages = [m for m in state.messages if m.get("role") == "user"]
    wrap_count = sum(
        1
        for m in user_messages
        if isinstance(m.get("content"), str) and "<user_input>" in m["content"]
    )
    assert wrap_count == 1


async def test_haiku_self_escalate_tool_never_called_on_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le tool escalate_to_sonnet ne doit JAMAIS être envoyé à
    mcp_pappers.call_tool — S04 break avant l'exécution."""
    escalate_tu = _tool_use("tu_esc", "escalate_to_sonnet", {"reason": "x"})
    script = [
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")])),
    ]
    _install_fake_anthropic(monkeypatch, script)
    mcp_calls = _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "x"):
        pass

    # Aucun appel mcp_pappers.call_tool avec escalate_to_sonnet
    assert all(name != "escalate_to_sonnet" for name, _ in mcp_calls)


async def test_state_lock_released_after_self_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Après escalade self, state.lock doit être libre (sinon le 2e
    run_turn Sonnet deadlockerait à cause de run_turn qui re-acquiert
    state.lock)."""
    escalate_tu = _tool_use("tu_esc", "escalate_to_sonnet", {"reason": "x"})
    script = [
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")])),
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "x"):
        pass

    # Pas de deadlock (sinon le test aurait timeout) + lock libre à la fin
    assert not state.lock.locked()


async def test_forced_escalation_on_tool_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku fait 5 tool calls sans conclure → cap atteint → escalade
    forcée vers Sonnet en continuation."""
    # Haiku fait 5 tool_use successifs (chaque iter = 1 tool_use).
    haiku_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="tool_use",
                content=[_tool_use(f"t{i}", "sirenisateur", {"company_name": f"E{i}"})],
            )
        )
        for i in range(MAX_TOOL_CALLS_PER_TURN)
    ]
    # Sonnet conclut
    sonnet_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Sonnet conclut.")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, haiku_script + sonnet_script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "fiche runaway")]

    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "forced"
    assert escalation["reason_code"] == REASON_CODE_CAP_TOOL_CALLS
    # Reason humain : "5/5 tool calls" — détail, pas matching critique.
    assert str(MAX_TOOL_CALLS_PER_TURN) in escalation["reason"]

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "forced"
    assert routing_done["escalation_reason_code"] == REASON_CODE_CAP_TOOL_CALLS
    assert routing_done["model_used"] == "sonnet"
    assert routing_done["tool_calls_count"] == MAX_TOOL_CALLS_PER_TURN


async def test_capped_on_sonnet_no_further_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Query complexe → Sonnet direct. Si Sonnet lui-même atteint le cap,
    on émet un event ``capped`` mais pas d'escalation (pas de tier
    au-dessus pour MVP)."""
    # Sonnet fait 5 tool_use puis conclut
    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="tool_use",
                content=[_tool_use(f"t{i}", "sirenisateur", {"company_name": f"E{i}"})],
                model=MODEL_SONNET,
            )
        )
        for i in range(MAX_TOOL_CALLS_PER_TURN)
    ]
    script.append(
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("ok")],
                model=MODEL_SONNET,
            )
        )
    )
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare A et B")]

    capped_events = [e for e in events if e["type"] == "capped"]
    assert len(capped_events) == 1
    # Format unifié Haiku/Sonnet : reason_code stable = enum match.
    assert capped_events[0]["reason_code"] == REASON_CODE_CAP_TOOL_CALLS
    assert capped_events[0]["count"] == MAX_TOOL_CALLS_PER_TURN

    # Pas d'escalation (déjà Sonnet)
    assert not any(e["type"] == "escalation" for e in events)

    # routing_done propage aussi la cause du cap (pour S07 stats)
    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is False
    assert routing_done["capped"] is True
    assert routing_done["capped_reason_code"] == REASON_CODE_CAP_TOOL_CALLS
    assert str(MAX_TOOL_CALLS_PER_TURN) in routing_done["capped_reason"]


async def test_forced_escalation_on_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock cap atteint en Haiku → escalade forcée vers Sonnet.

    On force le budget à 0 s via monkeypatch pour que la première
    itération de la boucle détecte ``remaining <= 0`` immédiatement.
    """
    import genial_agent.routing as routing_mod

    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0)

    haiku_script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("hi")]))]
    sonnet_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Sonnet conclut.")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, haiku_script + sonnet_script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "x")]

    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "forced"
    assert escalation["reason_code"] == REASON_CODE_CAP_WALL_CLOCK

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "forced"
    assert routing_done["escalation_reason_code"] == REASON_CODE_CAP_WALL_CLOCK
    assert routing_done["model_used"] == "sonnet"


async def test_wall_clock_triggers_capped_on_sonnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier initial Sonnet (via keyword) + wall-clock hit → event
    ``capped``, pas d'escalation (pas de tier au-dessus pour MVP)."""
    import genial_agent.routing as routing_mod

    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0)

    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("start...")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare A et B")]

    capped_events = [e for e in events if e["type"] == "capped"]
    assert len(capped_events) == 1
    assert capped_events[0]["reason_code"] == REASON_CODE_CAP_WALL_CLOCK

    # Pas d'escalation (déjà Sonnet)
    assert not any(e["type"] == "escalation" for e in events)

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is False
    assert routing_done["capped"] is True
    assert routing_done["capped_reason_code"] == REASON_CODE_CAP_WALL_CLOCK


async def test_wall_clock_wait_for_timeout_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fake stream qui bloque sur le 1er ``__anext__`` plus
    longtemps que ``WALL_CLOCK_S`` doit faire lever
    ``asyncio.TimeoutError`` côté wait_for, traité comme cap hit.

    Ce test couvre le chemin ``except TimeoutError`` de la boucle — pas
    atteignable par le monkeypatch ``WALL_CLOCK_S=0`` qui hit via
    ``remaining <= 0`` en haut de boucle.
    """
    import genial_agent.routing as routing_mod

    # Budget court mais > 0 pour forcer wait_for à réellement attendre.
    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0.05)

    class _SlowStream:
        async def __aenter__(self) -> _SlowStream:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        @property
        def text_stream(self) -> AsyncIterator[str]:
            return self._iter()

        async def _iter(self) -> AsyncIterator[str]:
            # Latence >> WALL_CLOCK_S : wait_for doit timeout.
            await asyncio.sleep(1.0)
            yield "never"

        async def get_final_message(self) -> Any:
            return _message(stop_reason="end_turn", content=[_text("never")])

        @property
        def request_id(self) -> str:
            return "slow"

    class _SlowMessages:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def stream(self, **kwargs: Any) -> _SlowStream:
            self.calls.append(kwargs)
            return _SlowStream()

    class _SlowClient:
        def __init__(self) -> None:
            self.messages = _SlowMessages()

        async def __aenter__(self) -> _SlowClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

    fake = _SlowClient()
    monkeypatch.setattr("genial_agent.agent.AsyncAnthropic", lambda **_kw: fake)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events: list[dict[str, Any]] = []
    # On consomme jusqu'au 1er event escalation/capped puis on arrête
    # la démo (éviter le 2e run_turn qui dépendrait du fake).
    async for ev in run_routed_turn(state, "x"):
        events.append(ev)
        if ev["type"] in {"escalation", "capped"}:
            break

    tagged = [e for e in events if e["type"] in {"escalation", "capped"}]
    assert tagged, "aucun event cap émis malgré wait_for timeout"
    assert tagged[0]["reason_code"] == REASON_CODE_CAP_WALL_CLOCK


async def test_routing_initial_and_done_always_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quel que soit le chemin, exactement un routing_initial et un
    routing_done sont émis."""
    script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")]))]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Fiche LVMH")]

    assert sum(1 for e in events if e["type"] == "routing_initial") == 1
    assert sum(1 for e in events if e["type"] == "routing_done") == 1


async def test_extra_tools_includes_escalate_in_haiku_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")]))]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "Fiche LVMH"):
        pass

    # Haiku initial → escalate_to_sonnet injecté dans tools
    tool_names = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert "escalate_to_sonnet" in tool_names
    # Les tools Pappers sont toujours là
    assert "sirenisateur" in tool_names


async def test_simple_query_routing_done_exposes_full_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sur le chemin simple Haiku, ``routing_done`` expose tous les
    champs du contrat — même None pour escalation/capped — pour que les
    consumers S06/S07 n'aient jamais de ``KeyError``."""
    script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")]))]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Fiche LVMH")]

    routing_done = next(e for e in events if e["type"] == "routing_done")
    # Contrat complet, tous les champs présents (défense KeyError).
    expected_keys = {
        "type",
        "model_used",
        "escalated",
        "escalation_mode",
        "escalation_reason_code",
        "escalation_reason",
        "capped",
        "capped_reason_code",
        "capped_reason",
        "tool_calls_count",
        # S09.5 — compteur séparé pour les lookups Payload Vault.
        "local_lookups_count",
    }
    assert set(routing_done.keys()) == expected_keys
    assert routing_done["escalated"] is False
    assert routing_done["capped"] is False
    assert routing_done["escalation_mode"] is None
    assert routing_done["escalation_reason_code"] is None
    assert routing_done["escalation_reason"] is None
    assert routing_done["capped_reason_code"] is None
    assert routing_done["capped_reason"] is None


async def test_self_escalate_reason_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Défense en profondeur contre une ``reason`` anormalement longue
    fournie par Haiku (prompt injection indirecte possible) : S04 cap
    la longueur à ``_SELF_REASON_MAX_CHARS``.

    Ne pas matcher le nombre exact pour éviter un test fragile si on
    ajuste la constante — on vérifie juste qu'un payload >> 200 chars
    est tronqué."""
    huge = "A" * 5000
    escalate_tu = _tool_use("tu_esc", "escalate_to_sonnet", {"reason": huge})
    script = [
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")])),
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "x")]

    escalation = next(e for e in events if e["type"] == "escalation")
    # Tronqué : bien plus court que l'entrée, et contient le padding "A".
    assert len(escalation["reason"]) < len(huge)
    assert len(escalation["reason"]) <= 200
    assert escalation["reason"].startswith("A")


async def test_concurrent_routed_turn_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deux ``run_routed_turn`` concurrents sur le même ``state`` sont
    **sérialisés** par ``state.lock`` (invariant I5 de S03 propagé via
    S04). Sans ce lock, le second tour corromprait l'ordre des messages
    ``tool_use``/``tool_result`` dans ``state.messages``.

    Ce test reprend la logique de
    ``test_concurrent_run_turn_on_same_state_is_serialized`` (S03) mais
    à travers la couche S04. Il protège contre une régression qui
    acquerrait le lock trop tard (hors du 1er ``run_turn``) ou le
    libérerait trop tôt (avant le 2e ``run_turn`` post-escalade)."""
    # Chaque tour = 1 appel stream qui conclut sur end_turn.
    script = [
        _ScriptedTurn(
            text_chunks=["tour1"],
            final=_message(stop_reason="end_turn", content=[_text("tour1")]),
        ),
        _ScriptedTurn(
            text_chunks=["tour2"],
            final=_message(stop_reason="end_turn", content=[_text("tour2")]),
        ),
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()

    async def _drain(msg: str) -> list[dict[str, Any]]:
        return [ev async for ev in run_routed_turn(state, msg)]

    # Deux turns concurrents : gather les lance en parallèle ; le lock
    # S03 force une exécution séquentielle. Si ça deadlockait, le test
    # timeout pytest-asyncio couperait.
    events1, events2 = await asyncio.gather(
        _drain("Fiche LVMH"),
        _drain("Dirigeants BNP"),
    )

    # Chaque turn a son propre routing_initial / routing_done
    for events in (events1, events2):
        assert sum(1 for e in events if e["type"] == "routing_initial") == 1
        assert sum(1 for e in events if e["type"] == "routing_done") == 1

    # Lock libéré en fin de run
    assert not state.lock.locked()

    # State cohérent : 2 tours × (user + assistant) = 4 messages, dans
    # l'ordre (user1, assistant1, user2, assistant2) OU
    # (user2, assistant2, user1, assistant1) — pas d'entrelacement.
    roles = [m["role"] for m in state.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
