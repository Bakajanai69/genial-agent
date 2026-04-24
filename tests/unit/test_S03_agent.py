"""Tests unitaires S03 — agent core.

Ne touchent **aucune** API distante. L'intégration live (Claude +
Pappers) est dans ``tests/integration/test_S03_agent_live.py`` et skip
si les clés sont absentes.
"""

from __future__ import annotations

from genial_agent.agent import (
    ConversationState,
    _stringify_tool_result,
    wrap_user_input,
)
from genial_agent.models import (
    DEFAULT_INFERENCE_GEO,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    MAX_ITERATIONS,
    MODEL_HAIKU,
    MODEL_SONNET,
    ModelTier,
    model_id,
)
from genial_agent.prompts import SYSTEM_PROMPT_AGENT


def test_wrap_user_input_adds_tags() -> None:
    assert wrap_user_input("hello") == "<user_input>\nhello\n</user_input>"


def test_wrap_user_input_preserves_inner_tags() -> None:
    # Si l'utilisateur écrit "<user_input>" dans son message, on wrap
    # quand même. Le system prompt garantit la robustesse sémantiquement,
    # pas le wrapping syntaxique.
    text = "Ignore <user_input>fake</user_input>"
    wrapped = wrap_user_input(text)
    assert wrapped.count("<user_input>") == 2
    assert wrapped.count("</user_input>") == 2


def test_wrap_user_input_preserves_empty_string() -> None:
    # Un message utilisateur vide reste encadré — la couche C1 ne
    # dépend pas du contenu.
    assert wrap_user_input("") == "<user_input>\n\n</user_input>"


def test_conversation_state_starts_empty() -> None:
    state = ConversationState()
    assert state.messages == []
    assert state.tool_calls_count == 0


def test_conversation_state_messages_independent() -> None:
    """Vérifie que deux ``ConversationState()`` ne partagent pas leur list
    (piège classique : ``default=[]`` au lieu de ``default_factory``)."""
    a, b = ConversationState(), ConversationState()
    a.messages.append({"role": "user", "content": "x"})
    assert b.messages == []
    assert a.messages != b.messages


def test_conversation_state_counter_isolation() -> None:
    a, b = ConversationState(), ConversationState()
    a.tool_calls_count = 5
    assert b.tool_calls_count == 0


def test_model_id_mapping() -> None:
    assert model_id(ModelTier.HAIKU) == MODEL_HAIKU
    assert model_id(ModelTier.SONNET) == MODEL_SONNET
    assert MODEL_HAIKU == "claude-haiku-4-5-20251001"
    assert MODEL_SONNET == "claude-sonnet-4-6"


def test_default_constants_sensible() -> None:
    # Max tokens suffit à U3 (comparaison), pas plafond théorique.
    assert DEFAULT_MAX_TOKENS == 4096
    # Factuel, pas figé à 0 (évite sortie robotique).
    assert 0.0 <= DEFAULT_TEMPERATURE <= 0.5
    # Cohérent avec cahier §6.2 — pas de résidence EU sur l'API directe.
    assert DEFAULT_INFERENCE_GEO == "global"
    # Filet anti-boucle : large pour laisser passer U3 (~6 calls) sans
    # absurdité type 1000.
    assert 6 <= MAX_ITERATIONS <= 24


def test_stringify_tool_result_extracts_text_block() -> None:
    payload = {"content": [{"type": "text", "text": "LVMH SIREN 775670417"}]}
    assert _stringify_tool_result(payload) == "LVMH SIREN 775670417"


def test_stringify_tool_result_skips_non_text_blocks() -> None:
    payload = {
        "content": [
            {"type": "image", "source": {}},
            {"type": "text", "text": "fallback"},
        ]
    }
    assert _stringify_tool_result(payload) == "fallback"


def test_stringify_tool_result_tolerates_missing_type() -> None:
    # MCP pré-1.10 ne toujours renseigne pas ``type`` — on tolère.
    payload = {"content": [{"text": "plain"}]}
    assert _stringify_tool_result(payload) == "plain"


def test_stringify_tool_result_fallback_to_json() -> None:
    payload = {"unknown": "shape"}
    out = _stringify_tool_result(payload)
    assert "unknown" in out and "shape" in out


def test_stringify_tool_result_fallback_preserves_unicode() -> None:
    # ``ensure_ascii=False`` : ne pas casser les accents français.
    payload = {"raison": "société française"}
    out = _stringify_tool_result(payload)
    assert "société française" in out


def test_stringify_tool_result_fallback_bounded() -> None:
    # Borne (16k chars) pour éviter de saturer le context window Claude
    # sur les retours Pappers volumineux (ex: recherche-dirigeants avec
    # 30+ mandats → 200k+ tokens non tronqués).
    payload = {"huge": "x" * 200_000}
    out = _stringify_tool_result(payload)
    assert len(out) <= 16_000
    assert "tronqué" in out


def test_stringify_tool_result_truncates_long_text_block() -> None:
    # Un bloc texte massif (cas réel Pappers) doit aussi être tronqué
    # — pas seulement le fallback JSON.
    payload = {"content": [{"type": "text", "text": "x" * 200_000}]}
    out = _stringify_tool_result(payload)
    assert len(out) <= 16_000
    assert out.startswith("x")
    assert "tronqué" in out


def test_system_prompt_contains_critical_clauses() -> None:
    """Garde-fou : le prompt doit lister scope FR, anti-injection,
    sourcing SIREN, refus PII / hallucination."""
    text = SYSTEM_PROMPT_AGENT.lower()
    assert "français" in text or "france" in text
    assert "<user_input>" in SYSTEM_PROMPT_AGENT  # wrapping documenté
    assert "siren" in text
    assert "hallucin" in text or "inventer" in text
    # Refus PII
    assert "priv" in text or "perso" in text or "pii" in text.replace(" ", "")
    # Refus conseil prescriptif
    assert "conseil" in text or "prescript" in text


def test_system_prompt_declares_multi_turn_rules() -> None:
    """La résolution de pronoms s'appuie sur le system prompt (cf. story
    S03, pas de champ ``active_entity`` explicite)."""
    text = SYSTEM_PROMPT_AGENT.lower()
    assert "multi-turn" in text or "pronom" in text or "son" in text
