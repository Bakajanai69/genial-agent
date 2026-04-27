"""Tests unit pour ``genial_agent.ui.thread_resume``.

Couvre les cas critiques pour ne pas casser le format Anthropic
``MessageParam`` quand on reconstruit ``state.messages`` depuis les
steps stockés en SQLite par le data layer Chainlit.

Garanties testées :

1. Empty / None / malformed steps → ``ConversationState`` neuf valide.
2. Alternance stricte user/assistant.
3. Skip des steps non-message (``tool``, ``run``, ``system_message``).
4. Steps assistant orphelins en tête → poppés.
5. Cap ``MAX_RESUMED_MESSAGES`` respecté.
6. Format Anthropic correct (1er message ``role="user"``, content
   wrappé pour les user messages, content texte pour les assistant).
7. Robustesse face à des entries non-dict dans la liste.
8. Tri par ``createdAt`` même si l'ordre d'entrée est cassé.
"""

from __future__ import annotations

from genial_agent.agent import ConversationState
from genial_agent.ui.thread_resume import (
    MAX_RESUMED_MESSAGES,
    reconstruct_state_from_steps,
)

# ─── 1. Inputs vides / nuls / malformés ───────────────────────────────


def test_none_steps_returns_empty_state() -> None:
    state = reconstruct_state_from_steps(None)
    assert isinstance(state, ConversationState)
    assert state.messages == []


def test_empty_list_returns_empty_state() -> None:
    state = reconstruct_state_from_steps([])
    assert state.messages == []


def test_non_list_returns_empty_state() -> None:
    """API qui passerait un dict ou une str par erreur — pas de crash."""
    state = reconstruct_state_from_steps("not a list")  # type: ignore[arg-type]
    assert state.messages == []


def test_only_non_dict_entries_returns_empty_state() -> None:
    state = reconstruct_state_from_steps([None, "string", 42, []])  # type: ignore[list-item]
    assert state.messages == []


# ─── 2. Alternance user/assistant ────────────────────────────────────


def test_simple_user_assistant_pair() -> None:
    steps = [
        {"type": "user_message", "output": "Bonjour"},
        {"type": "assistant_message", "output": "Bonjour ! Comment t'aider ?"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2
    assert state.messages[0]["role"] == "user"
    assert state.messages[1]["role"] == "assistant"


def test_multiple_pairs_preserved() -> None:
    steps = [
        {"type": "user_message", "output": "Q1"},
        {"type": "assistant_message", "output": "A1"},
        {"type": "user_message", "output": "Q2"},
        {"type": "assistant_message", "output": "A2"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 4
    assert [m["role"] for m in state.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_consecutive_user_steps_dedups_keeps_first() -> None:
    """Deux user d'affilée sans assistant entre eux → on garde le 1er."""
    steps = [
        {"type": "user_message", "output": "Q1"},
        {"type": "user_message", "output": "Q2"},
        {"type": "assistant_message", "output": "A"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2
    # Le 1er user est conservé (Q1).
    assert "Q1" in state.messages[0]["content"]
    assert "Q2" not in state.messages[0]["content"]


def test_consecutive_assistant_steps_dedups_keeps_first() -> None:
    steps = [
        {"type": "user_message", "output": "Q"},
        {"type": "assistant_message", "output": "A1"},
        {"type": "assistant_message", "output": "A2"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2
    assert state.messages[1]["content"] == "A1"


# ─── 3. Skip des steps non-message ────────────────────────────────────


def test_tool_steps_are_skipped() -> None:
    steps = [
        {"type": "user_message", "output": "fiche LVMH"},
        {"type": "tool", "name": "sirenisateur", "output": '{"siren": "775670417"}'},
        {"type": "assistant_message", "output": "LVMH SIREN 775670417"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2
    assert state.messages[0]["role"] == "user"
    assert state.messages[1]["role"] == "assistant"


def test_run_and_system_steps_are_skipped() -> None:
    steps = [
        {"type": "run", "output": "agent run started"},
        {"type": "user_message", "output": "Q"},
        {"type": "system_message", "output": "Pappers indispo"},
        {"type": "assistant_message", "output": "A"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2


# ─── 4. Orphan assistant en tête ─────────────────────────────────────


def test_orphan_assistant_at_head_is_dropped() -> None:
    """Anthropic exige role='user' en 1er → on drop l'assistant orphelin."""
    steps = [
        {"type": "assistant_message", "output": "Bienvenue"},
        {"type": "user_message", "output": "Q"},
        {"type": "assistant_message", "output": "A"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert state.messages[0]["role"] == "user"
    # L'assistant "Bienvenue" doit avoir été retiré.
    assert all("Bienvenue" not in str(m.get("content", "")) for m in state.messages)


def test_only_assistant_steps_returns_empty() -> None:
    steps = [
        {"type": "assistant_message", "output": "A1"},
        {"type": "assistant_message", "output": "A2"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert state.messages == []


# ─── 5. Cap MAX_RESUMED_MESSAGES ─────────────────────────────────────


def test_cap_max_resumed_messages() -> None:
    steps: list[dict[str, object]] = []
    for i in range(MAX_RESUMED_MESSAGES + 10):
        steps.append({"type": "user_message", "output": f"Q{i}"})
        steps.append({"type": "assistant_message", "output": f"A{i}"})
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) <= MAX_RESUMED_MESSAGES
    # 1er message reste user (cap respecte la contrainte Anthropic).
    assert state.messages[0]["role"] == "user"


def test_cap_keeps_most_recent() -> None:
    """Quand on cap, on garde les messages **les plus récents**."""
    steps: list[dict[str, object]] = []
    # 30 paires (Q,A) → cap à MAX_RESUMED_MESSAGES.
    for i in range(30):
        steps.append({"type": "user_message", "output": f"Q{i}"})
        steps.append({"type": "assistant_message", "output": f"A{i}"})
    state = reconstruct_state_from_steps(steps)
    # Le dernier message doit être A29 (le plus récent).
    last = state.messages[-1]
    assert last["role"] == "assistant"
    assert "A29" in last["content"]


# ─── 6. Format Anthropic ─────────────────────────────────────────────


def test_user_content_is_wrapped() -> None:
    """Les user messages sont wrappés avec ``wrap_user_input`` (tags
    ``<user_input>`` pour défense anti-injection)."""
    from genial_agent.agent import wrap_user_input

    steps = [
        {"type": "user_message", "output": "fiche LVMH"},
        {"type": "assistant_message", "output": "OK"},
    ]
    state = reconstruct_state_from_steps(steps)
    user_content = state.messages[0]["content"]
    assert isinstance(user_content, str)
    expected = wrap_user_input("fiche LVMH")
    assert user_content == expected


def test_assistant_content_is_plain_string() -> None:
    """Les assistant messages sont posés en str pure (Anthropic accepte
    str ou list[ContentBlock])."""
    steps = [
        {"type": "user_message", "output": "Q"},
        {"type": "assistant_message", "output": "Bonjour"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert state.messages[1]["content"] == "Bonjour"


def test_empty_output_skipped() -> None:
    steps = [
        {"type": "user_message", "output": ""},
        {"type": "assistant_message", "output": "A"},
    ]
    state = reconstruct_state_from_steps(steps)
    # Le user vide est skippé, donc l'assistant n'a plus de paire user
    # devant lui → tout est skippé (1er message non-user).
    assert state.messages == []


# ─── 7. Robustesse mixte ─────────────────────────────────────────────


def test_mixed_valid_and_malformed_entries() -> None:
    steps = [
        None,
        {"type": "user_message", "output": "Q"},
        "not a dict",
        {"type": "assistant_message", "output": "A"},
        42,
        {},  # dict vide
        {"type": "user_message"},  # pas d'output
    ]
    state = reconstruct_state_from_steps(steps)  # type: ignore[arg-type]
    assert len(state.messages) == 2
    assert state.messages[0]["role"] == "user"
    assert state.messages[1]["role"] == "assistant"


# ─── 8. Tri par createdAt ─────────────────────────────────────────────


def test_steps_sorted_by_created_at() -> None:
    """Si la liste arrive dans le désordre, on trie par createdAt avant
    le filtrage alternance.

    Steps fournis dans le désordre :
      Q2 (10:03), A2 (10:02), Q1 (10:00), A1 (10:01)

    Après tri temporel : Q1, A1, A2, Q2.
    Après dedup consécutifs : Q1, A1, Q2 (A2 skippé car consécutif à A1).
    """
    steps = [
        {"type": "user_message", "output": "Q2", "createdAt": "2026-04-27T10:03:00Z"},
        {"type": "assistant_message", "output": "A2", "createdAt": "2026-04-27T10:02:00Z"},
        {"type": "user_message", "output": "Q1", "createdAt": "2026-04-27T10:00:00Z"},
        {"type": "assistant_message", "output": "A1", "createdAt": "2026-04-27T10:01:00Z"},
    ]
    state = reconstruct_state_from_steps(steps)
    contents = [str(m["content"]) for m in state.messages]
    # Q1 (le plus ancien) doit venir AVANT Q2 (le plus récent).
    q1_idx = next(i for i, c in enumerate(contents) if "Q1" in c)
    a1_idx = next(i for i, c in enumerate(contents) if "A1" in c)
    q2_idx = next(i for i, c in enumerate(contents) if "Q2" in c)
    assert q1_idx < a1_idx < q2_idx
    # A2 a été dedupé (consécutif à A1) — pas dans le résultat.
    assert all("A2" not in c for c in contents)


def test_supports_snake_case_created_at() -> None:
    """Le data layer SQLite stocke ``created_at`` (snake_case) ; le
    ThreadDict Chainlit utilise ``createdAt`` (camelCase) — on accepte
    les deux."""
    steps = [
        {"type": "user_message", "output": "Q1", "created_at": "2026-04-27T10:00:00Z"},
        {"type": "assistant_message", "output": "A1", "created_at": "2026-04-27T10:01:00Z"},
    ]
    state = reconstruct_state_from_steps(steps)
    assert len(state.messages) == 2


# ─── 9. Smoke E2E sur un thread réaliste ─────────────────────────────


def test_realistic_thread_with_tool_calls_in_middle() -> None:
    """Pattern typique : user → tool calls multiples → assistant final."""
    steps = [
        {"type": "user_message", "output": "fiche LVMH", "createdAt": "2026-04-27T10:00:00Z"},
        {
            "type": "tool",
            "name": "sirenisateur",
            "output": '{"siren":"775670417"}',
            "createdAt": "2026-04-27T10:00:01Z",
        },
        {
            "type": "tool",
            "name": "recherche-entreprises",
            "output": '{"name":"LVMH"}',
            "createdAt": "2026-04-27T10:00:02Z",
        },
        {
            "type": "assistant_message",
            "output": "LVMH (SIREN 775670417), siège à Paris…",
            "createdAt": "2026-04-27T10:00:03Z",
        },
        {"type": "user_message", "output": "et son CA ?", "createdAt": "2026-04-27T10:01:00Z"},
        {
            "type": "tool",
            "name": "comptes-entreprise",
            "output": '{"ca":"86000000000"}',
            "createdAt": "2026-04-27T10:01:01Z",
        },
        {
            "type": "assistant_message",
            "output": "CA 2024 : 86 Md€",
            "createdAt": "2026-04-27T10:01:02Z",
        },
    ]
    state = reconstruct_state_from_steps(steps)
    # 4 messages : Q1, A1, Q2, A2 (les 3 tool steps sont skippés).
    assert len(state.messages) == 4
    assert state.messages[0]["role"] == "user"
    assert state.messages[1]["role"] == "assistant"
    assert state.messages[2]["role"] == "user"
    assert state.messages[3]["role"] == "assistant"
    # Pas de SIREN dans le user wrappé (juste la phrase originale).
    assert "fiche LVMH" in str(state.messages[0]["content"])
    # SIREN dans la réponse assistant historique.
    assert "775670417" in str(state.messages[1]["content"])
