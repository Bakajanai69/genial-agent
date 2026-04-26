"""S09.7 Axe 1 A4 — Messages d'erreur enrichis avec ``_available_keys``.

Quand ``_walk_simple`` retourne ``_error: "key 'X' missing at '...'"``,
on ajoute la liste des clés disponibles au nœud parent (jusqu'à 10) pour
que le LLM puisse corriger sans relancer un ``payload_inspect`` à
l'aveugle.
"""

from __future__ import annotations

import json

from genial_agent.payload_vault import inspect


def test_missing_key_lists_available_keys() -> None:
    raw = json.dumps({"siren": "x", "denomination": "y", "siege": {}})
    out = inspect(raw, "$.unknown")
    parsed = json.loads(out)
    assert "_error" in parsed
    assert "_available_keys" in parsed
    assert set(parsed["_available_keys"]) >= {"siren", "denomination", "siege"}


def test_missing_key_caps_available_keys_to_10() -> None:
    """Si le dict a 50 clés, on n'en expose que 10 dans la liste — évite
    un message d'erreur qui exploserait le contexte LLM."""
    big = {f"key_{i}": i for i in range(50)}
    raw = json.dumps(big)
    out = inspect(raw, "$.unknown")
    parsed = json.loads(out)
    assert "_error" in parsed
    assert "_available_keys" in parsed
    assert len(parsed["_available_keys"]) <= 10


def test_missing_key_no_available_keys_for_list_error() -> None:
    """L'erreur "list expects integer index" garde son message dédié,
    pas besoin d'ajouter ``_available_keys`` (les indices sont implicites)."""
    raw = json.dumps({"items": ["a", "b"]})
    out = inspect(raw, "$.items.foo")
    parsed = json.loads(out)
    assert "_error" in parsed
    # Pas de _available_keys nécessaire ici — l'erreur dit déjà "list has 2 items"
    # mais ne casse pas si on ne l'ajoute pas. Ce test ne fait que documenter.
    assert "list expects integer index" in parsed["_error"]


# ---- S09.7 amélioration 3 : footer _remaining_siblings ----


def test_indexed_inspect_appends_remaining_siblings_footer() -> None:
    """``$.items[2]`` sur un array de 5 → footer indique 2 items restants
    après l'index, suggère ``[*]`` pour tout récupérer.

    Règle l'anti-pattern où l'agent regarde ``[0]`` et ``[2]`` puis
    s'arrête sans réaliser qu'il y a 36 autres items à explorer."""
    raw = json.dumps({"items": [{"x": i} for i in range(5)]})
    out = inspect(raw, "$.items[2]")
    # La valeur extraite est présente
    assert '"x": 2' in out
    # Le footer signale les remaining
    assert "_meta:" in out
    assert "2 more siblings" in out
    assert "[*]" in out


def test_indexed_inspect_no_footer_on_last_element() -> None:
    """``$.items[-1]`` ou ``$.items[4]`` sur array de 5 → pas de footer
    (pas de remaining)."""
    raw = json.dumps({"items": [{"x": i} for i in range(5)]})
    out_neg = inspect(raw, "$.items[-1]")
    assert "_meta:" not in out_neg
    out_last = inspect(raw, "$.items[4]")
    assert "_meta:" not in out_last


def test_indexed_inspect_no_footer_on_dict_path() -> None:
    """Path qui se termine sur une clé dict (pas un index) → pas de footer."""
    raw = json.dumps({"siege": {"ville": "PARIS"}})
    out = inspect(raw, "$.siege.ville")
    assert "_meta:" not in out


def test_wildcard_path_no_remaining_footer() -> None:
    """Wildcard ``[*]`` → pas de footer remaining (couvert par
    amélioration 2 enveloppe)."""
    raw = json.dumps({"items": [{"x": i} for i in range(5)]})
    out = inspect(raw, "$.items[*].x")
    assert "more siblings" not in out
