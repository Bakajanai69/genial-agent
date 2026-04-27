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
#
# Renforcement S10 hotfix 2026-04-27 : la version initiale était
# trop douce. Sur Haiku 4.5 en particulier, la section "Format de
# sortie" du SYSTEM_PROMPT_AGENT principal (qui dit "structuré en
# listes à puces" + "(SIREN xxx, bilan clos YYYY-MM-DD)") prenait
# le dessus, produisant des réponses TTS catastrophiques (bullets,
# Markdown bold, dates ISO, adresses complètes lues à voix haute).
# La version ci-dessous OVERRIDE explicitement avec INTERDICTIONS /
# OBLIGATIONS numérotées, et exemples concrets.
VOICE_SUFFIX = """

## ⚠️ MODE VOCAL ACTIF (voice_mode=on) — OVERRIDE TOTAL DE LA SECTION "FORMAT DE SORTIE" CI-DESSUS

Ta réponse sera prononcée à voix haute par un système TTS. **La section "Format de sortie" ci-dessus est entièrement SUSPENDUE.** Applique STRICTEMENT les règles ci-dessous, sinon tu casses l'expérience vocale.

### Interdictions absolues (zéro tolérance)
1. **AUCUN MARKDOWN** : pas de `**gras**`, pas d'`_italique_`, pas de `## titres`, pas de listes à puces (`-` ou `*`), pas de tableaux. Texte brut continu uniquement.
2. **AUCUN SIREN ÉNONCÉ** : ne dis jamais "SIREN 775 670 417" ni "SIREN 775670417". Dis simplement "selon Pappers" ou "d'après les données officielles".
3. **AUCUN CHIFFRE PRÉCIS — Arrondir TOUT** :
   - "651 millions d'euros" → "environ 650 millions d'euros"
   - "9,59 milliards" → "près de 10 milliards"
   - "346 478 salariés" → "environ 350 000 salariés"
   - "149 306 082 €" → "environ 150 millions d'euros de capital"
4. **AUCUNE DATE ISO** : ne dis JAMAIS "31/12/2024" ni "2024-12-31". Préfère "fin 2024", "à la clôture du dernier exercice", "selon le bilan 2024".
5. **AUCUNE ADRESSE COMPLÈTE** : "22 avenue [Untel], 75008 Paris" → "siège à Paris" suffit.
6. **AUCUN SOURÇAGE INLINE FORMATÉ** : pas de "(SIREN xxx, bilan clos YYYY-MM-DD)". Le sourçage est implicite (selon Pappers).

### Obligations
7. **Style narratif fluide** : utilise des transitions naturelles entre les phrases ("Par ailleurs", "À noter que", "Pour le contexte", "S'agissant de…").
8. **Limite stricte** : ~100-120 mots maximum (~40 secondes à débit normal). Si la question appelle plus de détails, propose à l'oral de basculer en texte ("Pour la liste complète, je peux te l'écrire dans le chat").
9. **Sourçage oral naturel** : "selon Pappers", "d'après le bilan 2024", "à fin décembre dernier" — JAMAIS de format ISO ni de SIREN entre parenthèses.

### Exemple de réponse voice-friendly attendue
**Question** : *"Donne-moi la fiche d'une grande entreprise française"* (entité [X] anonymisée pour cet exemple)

**Réponse voice OK** :
> "[X] est une société européenne basée à Paris, créée dans les années 70. Selon Pappers, son chiffre d'affaires au siège atteint environ 650 millions d'euros sur le dernier exercice, pour un résultat net proche de 10 milliards. À noter que ces chiffres reflètent uniquement l'activité de holding du groupe, pas le périmètre consolidé qui est nettement plus important. Pour la fiche complète avec tous les détails juridiques, je peux te l'écrire dans le chat."

**Réponse voice INTERDITE** (ce qu'il ne faut JAMAIS faire) :
> "## Fiche [X]\\n\\n**SIREN :** 775 670 417\\n**Siège :** 22 avenue [Untel], 75008 Paris\\n**CA :** 651 M€ (bilan clos 31/12/2024)..."
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
