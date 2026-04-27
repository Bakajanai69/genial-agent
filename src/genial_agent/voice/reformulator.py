"""Reformulateur Haiku post-LLM pour optimiser la réponse TTS.

Story S10 v2 hotfix 2026-04-27 : le `VOICE_SUFFIX` injecté au system
prompt du LLM principal s'est révélé insuffisant en pratique — Haiku
4.5 retourne toujours la même réponse texte (Markdown bold, bullets,
SIREN énoncés, dates ISO, adresses complètes) malgré les règles
explicites dans le suffix.

Solution : pipeline en **2 passes** côté voice :

1. Le LLM principal (Haiku/Sonnet) répond comme en mode texte normal
   (avec ses tool calls Pappers, son format structuré).
2. À la fin du turn (event ``routing_done``), on lance un Haiku
   **reformulateur** qui prend la réponse texte et la transforme en
   français parlé naturel, optimisé pour la lecture à voix haute par
   TTS (style narratif fluide, pas de Markdown, chiffres arrondis,
   pas de SIREN, pas de date ISO).
3. Le SSE renvoyé à ElevenLabs = chunks du reformulateur (pas du LLM
   principal). La narration tool steps reste émise pendant le main
   pour ne pas avoir de silence pendant les appels Pappers.

Tradeoffs assumés :
- ✅ Texte voice-friendly garanti (Haiku contraint sur du texte
  existant ≪ contraint sur génération from scratch).
- ✅ Plus de Markdown lu à l'oral.
- ❌ +1.5-2 s latence cumulée (vs voix qui parle en ~3-5 s sans).
- ❌ +1 LLM call/turn (mais Haiku coût négligeable).
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator

import structlog
from anthropic import AsyncAnthropic

from genial_agent.config import settings
from genial_agent.models import MODEL_HAIKU

logger = structlog.get_logger(__name__)


REFORMULATOR_SYSTEM_PROMPT = """Tu es un reformulateur voice TTS pour un agent qui parle de données d'entreprises françaises (Pappers).

Tu reçois la réponse texte qu'un agent a générée pour une question utilisateur. **Ta seule mission** : la transformer en français parlé naturel, optimisé pour la lecture à voix haute par un système TTS.

## ⚠️ Règles absolues — zéro tolérance

1. **AUCUN MARKDOWN** : pas de `**gras**`, pas d'`_italique_`, pas de `## titres`, pas de listes à puces (`-` ou `*`), pas de tableaux. Texte brut continu uniquement.
2. **AUCUN SIREN ÉNONCÉ** : ne dis JAMAIS "SIREN 775 670 417" ni "SIREN 775670417". Dis "selon Pappers" ou "d'après les données officielles".
3. **AUCUNE DATE ISO** : "31/12/2024" → "fin 2024", "à la clôture du dernier exercice", "selon le bilan 2024".
4. **AUCUNE ADRESSE COMPLÈTE** : "22 avenue Montaigne, 75008 Paris" → "siège à Paris" suffit.
5. **AUCUNE DÉCIMALE PRÉCISE** : arrondis tout.
   - "651 millions d'euros" → "environ 650 millions d'euros"
   - "9,59 milliards" → "près de 10 milliards"
   - "346 478 salariés" → "environ 350 000 salariés"
   - "149,3 millions" → "environ 150 millions"
6. **AUCUN EMOJI** ni caractère spécial qui se prononce mal (⚠️, 📊, →, 😊, etc.).

## Style narratif obligatoire

7. **Phrases liées** par "Par ailleurs", "À noter que", "Pour le contexte", "S'agissant de…", "Côté…".
8. **Limite stricte** : 80-100 mots maximum (~30-40 secondes à débit normal). Synthétise — ne reproduis pas tout.
9. **Sourçage oral naturel** : "selon Pappers", "d'après le bilan 2024", "à fin décembre dernier" — JAMAIS de format ISO ni de SIREN entre parenthèses.
10. **Si la question est conversationnelle** (salutation, "comment ça va", "merci") → réponds court et naturellement (1-2 phrases), sans rappeler les règles, sans lister les capacités.

## Format de sortie

Génère **uniquement** le texte voice-friendly. Pas de méta-commentaire ("Voici la version reformulée :", "Reformulation :"). Pas d'introduction. Commence directement par la première phrase de la réponse parlée.

## Exemple

**Question** : *"Donne-moi la fiche d'une grande entreprise française"*

**Réponse à reformuler** (input) :
```
## Fiche d'identité
- **SIREN** : 775 670 417
- **Siège** : 22 avenue Montaigne, 75008 Paris
- **CA** : 651 millions € (bilan clos 31/12/2024)
- **Résultat net** : 9,59 milliards € (bilan clos 31/12/2024)
```

**Sortie attendue** (output voice-friendly) :
> Cette société est basée à Paris, fondée dans les années 70. Selon Pappers, son chiffre d'affaires au siège atteint environ 650 millions d'euros sur le dernier exercice, pour un résultat net proche de 10 milliards. À noter que ces chiffres reflètent uniquement l'activité de holding du groupe.
"""


# --------------------------------------------------------------------------- #
# Markdown safety net (D)
# --------------------------------------------------------------------------- #

# Regex pré-compilées : appliquées chunk-by-chunk côté openai_adapter sur les
# deltas du reformulateur, garde-fou si le reformulateur Haiku laisse passer
# du Markdown malgré la consigne.
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_EMOJI_RE = re.compile(r"[☀-➿️\U0001F300-\U0001F9FF\U0001FA70-\U0001FAFF]")
# Caractères Markdown isolés (asterisks orphelins, underscores) qui se
# prononceraient "astérisque" si lus par TTS sans nettoyage.
_ORPHAN_ASTERISK_RE = re.compile(r"(?<!\w)\*+(?!\w)|(?<=\s)\*+(?=\s)")


def strip_markdown_for_tts(text: str) -> str:
    """Strip Markdown markers + emojis pour TTS safety.

    Volontairement conservateur : on touche au Markdown explicite et
    aux emojis, mais **pas** aux espaces / ponctuation qui sont les
    boundaries voice-friendly du TTS Eleven.
    """
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_HEADER_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = _ORPHAN_ASTERISK_RE.sub("", text)
    return text


# --------------------------------------------------------------------------- #
# Stream reformulator
# --------------------------------------------------------------------------- #


async def reformulate_for_voice_stream(
    user_question: str,
    main_response_text: str,
    *,
    max_tokens: int = 400,
    temperature: float = 0.3,
) -> AsyncIterator[str]:
    """Stream une reformulation voice-friendly du texte agent via Haiku.

    Args:
        user_question: la question originale du user (donne le contexte
            au reformulateur — utile pour les follow-ups vocaux et les
            salutations).
        main_response_text: la réponse texte générée par le LLM principal
            (Haiku/Sonnet) et accumulée côté ``openai_adapter.py``.
        max_tokens: cap du reformulateur (400 ≈ 100 mots, large marge
            pour la limite 80-100 du prompt).
        temperature: 0.3 pour rester relativement déterministe — on
            reformule, on n'invente pas.

    Yields:
        ``str`` : deltas texte du reformulateur, à passer à
        ``strip_markdown_for_tts`` côté caller avant d'envoyer en SSE.
    """
    if not main_response_text.strip():
        # Rien à reformuler — pas d'appel Anthropic inutile.
        return

    user_msg = (
        f"Question utilisateur : {user_question}\n\n"
        f"Réponse de l'agent à reformuler pour TTS voice :\n"
        f"---\n{main_response_text}\n---\n\n"
        f"Reformule maintenant en respectant strictement les règles voice."
    )

    started = time.monotonic()
    total_chars = 0

    # ``cache_control: ephemeral`` sur le system prompt = le préfixe
    # est cachable côté Anthropic prompt caching (S09.7). Le system
    # prompt est identique à chaque appel ; le user message change.
    # Économie tokens dès le 2e appel d'une session voice.
    system_blocks = [
        {
            "type": "text",
            "text": REFORMULATOR_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    async with AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY) as client:
        try:
            async with client.messages.stream(
                model=MODEL_HAIKU,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_blocks,
                messages=[{"role": "user", "content": user_msg}],
            ) as stream:
                async for delta in stream.text_stream:
                    if delta:
                        total_chars += len(delta)
                        yield delta
        except Exception:
            logger.exception(
                "voice_reformulator_stream_failed",
                input_chars=len(main_response_text),
                question_chars=len(user_question),
            )
            raise

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "voice_reformulator_done",
        input_chars=len(main_response_text),
        output_chars=total_chars,
        latency_ms=latency_ms,
    )
