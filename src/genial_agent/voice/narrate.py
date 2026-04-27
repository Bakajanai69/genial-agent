"""Narration des étapes ``tool_use`` pour donner du texte à TTS-er.

Utilisé par ``openai_adapter.chat_completions`` : sur chaque event
``tool_use`` reçu de ``run_guarded_turn``, on émet un chunk SSE
``delta.content`` = ``narrate(tool_name) + " "`` (espace final pour que
ElevenLabs streame sur une frontière voice-friendly côté TTS).

Mapping volontairement court (≤ 6 entrées) — le mapping reste neutre,
sans entité hardcodée (cohérent avec la philosophie S09.7 "agent
adaptable, pas de logique métier dure").

Test grep enforced : ``tests/unit/test_S10_narrate.py`` échoue si une
des entités golden du jeu de tests (cf. story §"Décisions phase 1"
point 2) apparaît dans ce module. La narration doit rester générique
quel que soit le SIREN cherché.
"""

from __future__ import annotations

# Ordre figé en story phase 1 §Décisions point 2.
_TOOL_NARRATION: dict[str, str] = {
    "sirenisateur": "Je cherche le SIREN…",
    "recherche-entreprises": "Je regarde les chiffres clés…",
    "comptes-entreprise": "Je consulte les comptes…",
    "recherche-dirigeants": "Je vérifie les mandats…",
    "cartographie-entreprise": "Je trace la cartographie…",
    "payload_inspect": "Je détaille les données…",
}

_DEFAULT = "Je consulte Pappers…"


def narrate(tool_name: str) -> str:
    """Retourne la phrase narrative associée à ``tool_name``.

    Inconnu → ``_DEFAULT`` (jamais d'erreur — la narration est best-effort,
    elle ne doit pas casser le pipeline custom LLM).
    """
    return _TOOL_NARRATION.get(tool_name, _DEFAULT)
