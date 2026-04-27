"""Suffixe voice-friendly injecté au system prompt en mode vocal.

Justification du choix vs Haiku reformulateur (cf. story S10 phase 1
décision #1) : un reformulateur ajoute 400-600 ms de latence
incompressible, ce qui pousse le voice mode au-delà de la cible 8 s
end-to-end sur U1. Le suffixe au system prompt ne coûte rien (déjà dans
le préfixe Anthropic, donc cacheable côté prompt caching S09.7).

Les règles couvrent :

- Pas de SIREN à voix haute (la suite de chiffres casse l'oreille).
- Arrondir les chiffres (pas de décimales lues à voix haute).
- Style narratif fluide, pas de Markdown.
- Limite à ~100-120 mots (~40 s à débit normal).
- Sourçage gardé interne (sert le validator §C5) mais énoncé naturel.
"""

from __future__ import annotations

from genial_agent.prompts import SYSTEM_PROMPT_AGENT

# Garder ce bloc en `text` pur — il est concaténé tel quel au
# ``SYSTEM_PROMPT_AGENT``. Pas de placeholder, pas de format string.
VOICE_SUFFIX = """

## Mode vocal actif (voice_mode=on)
Ta réponse sera lue à voix haute par un système TTS. En conséquence :
- Ne jamais énoncer de SIREN à l'oral (la suite de chiffres casse l'oreille). Si une référence à l'entité est nécessaire, dis "selon Pappers" ou "d'après les données officielles".
- Arrondir tous les chiffres : "84 milliards d'euros" plutôt que "84,1 Md€", "environ 350 000 salariés" plutôt que "346 478".
- Style narratif fluide pour l'oreille, pas de bullet points, pas de listes Markdown.
- Utilise des transitions naturelles ("Par ailleurs", "À noter que", "Pour le contexte").
- Limite la réponse à environ 100-120 mots (~40 s à débit normal). Si la question demande plus, propose à l'oral de basculer en texte.
- Garde le sourçage en interne (sert le validateur §C5) mais n'énonce pas la date du bilan en mode "format ISO". Préfère "selon le bilan 2024" ou "à fin décembre dernier".
"""


def compose_voice_system_prompt() -> str:
    """Retourne ``SYSTEM_PROMPT_AGENT`` augmenté du suffixe voice-friendly.

    Le ``SYSTEM_PROMPT_AGENT`` source est **inchangé** (pas de mutation
    in-place — invariant respecté pour ne pas contaminer les flux texte
    classique). Le pipeline texte continue à utiliser le prompt d'origine
    via ``agent.run_turn`` ; seul le pipeline voice (via le paramètre
    ``system_prompt_override`` propagé de ``run_guarded_turn`` →
    ``run_routed_turn`` → ``run_turn``) consomme la version composée.
    """
    return SYSTEM_PROMPT_AGENT + VOICE_SUFFIX
