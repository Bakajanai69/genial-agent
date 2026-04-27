"""Helpers de flow voice mode (S10 hotfix UX latence/qualité).

Regroupe :

- **A4 — `sentence_buffer`** : accumule les deltas du reformulateur
  Haiku jusqu'à un boundary voice-friendly (`. ! ?` + espace), puis
  flush en une fois. ElevenLabs reçoit des phrases complètes plutôt
  que des chunks fragmentés (137 chars / 6 chars / etc.) — élimine
  la cause racine du jitter / pop côté TTS.

- **B1 — `FILLER_PHRASES`** : voix de meubles génériques émises
  régulièrement pendant Pass 1 (main LLM + tool calls Pappers) si
  > N secondes de silence. Donne du texte à TTS-er à intervalle
  régulier pour éviter le silence prolongé sur U3.

- **B2 — `should_skip_reformulator`** : heuristic simple qui détecte
  si la réponse main est déjà voice-friendly (courte + pas de
  Markdown). Économise 1.5-2 s de latence sur les questions
  conversationnelles ("salut", "merci", "comment ça va").
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

# --------------------------------------------------------------------------- #
# A4 — Sentence buffer
# --------------------------------------------------------------------------- #

# Boundary voice-friendly : fin de phrase ponctuée + (espace ou fin de chunk).
# Pas de comma seul (trop fragmentant), pas de virgule sans espace après
# (cas pathologique des décimales "9,5 milliards"). On cherche les vraies
# fins de phrase qui font sens à l'oral.
_SENTENCE_END_RE = re.compile(r"[.!?…]+(?=\s|$)")

# Cap absolu : si on n'a pas trouvé de boundary après N chars, on flush
# de force pour ne pas avoir un buffer infini qui retarde le 1er son.
_MAX_BUFFER_CHARS = 250


async def sentence_buffer(
    deltas: AsyncIterator[str],
) -> AsyncIterator[str]:
    """Re-yield les ``deltas`` après les avoir regroupés en phrases.

    Stratégie :
    - On accumule ``deltas`` dans un buffer.
    - Dès qu'on détecte un end-of-sentence (`.`, `!`, `?`, `…` suivi
      d'espace), on flush la portion complète.
    - Si le buffer dépasse ``_MAX_BUFFER_CHARS`` sans boundary trouvé
      (cas : phrase très longue), on flush quand même pour ne pas
      bloquer le TTS.
    - À la fin du stream, on flush le reste.
    """
    buffer = ""
    async for delta in deltas:
        if not delta:
            continue
        buffer += delta

        # Cherche le DERNIER boundary dans le buffer pour flush la
        # portion la plus longue possible (multi-phrases si delta est
        # gros). Ça évite d'émettre une phrase à la fois quand le
        # reformulateur envoie un gros chunk.
        flush_until = -1
        for m in _SENTENCE_END_RE.finditer(buffer):
            # On inclut la ponctuation + l'espace qui suit (si présent).
            end = m.end()
            if end < len(buffer) and buffer[end] in " \n\t":
                end += 1
            flush_until = end

        if flush_until > 0:
            yield buffer[:flush_until]
            buffer = buffer[flush_until:]
        elif len(buffer) >= _MAX_BUFFER_CHARS:
            # Pas de boundary trouvé mais buffer plein → flush tout.
            yield buffer
            buffer = ""

    # Fin du stream : flush ce qui reste.
    if buffer:
        yield buffer


# --------------------------------------------------------------------------- #
# B1 — Filler phrases (voix de meubles)
# --------------------------------------------------------------------------- #

# Phrases neutres émises pendant Pass 1 si > FILLER_INTERVAL_S secondes
# sans aucun event tool_use ou text. Évite les silences prolongés
# (typiquement quand l'agent réfléchit avant un tool call ou pendant
# une longue génération sans tool).
#
# Ordre cyclique pour ne pas se répéter immédiatement (le user
# entendra 1, puis 2, etc., revenir à 1 après).
FILLER_PHRASES: tuple[str, ...] = (
    "Un instant…",
    "Je vérifie ça…",
    "Encore un moment…",
    "Je consulte les dernières données…",
)

FILLER_INTERVAL_S: float = 4.0


def next_filler(index: int) -> str:
    """Retourne la prochaine voix de meuble (rotation cyclique)."""
    return FILLER_PHRASES[index % len(FILLER_PHRASES)] + " "


# --------------------------------------------------------------------------- #
# B2 — Skip reformulator heuristic
# --------------------------------------------------------------------------- #

# Cap chars : sous cette taille, la réponse est probablement déjà voice-
# friendly (ex : "Salut ! Je vais bien."). Au-dessus, on lance le
# reformulateur pour synthétiser + nettoyer.
_SKIP_REFORMULATOR_CHAR_THRESHOLD = 180

# Markers Markdown qui forcent le reformulateur même si court.
_MARKDOWN_MARKERS_RE = re.compile(
    r"\*\*|^#{1,6}\s|^\s*[-*]\s|^\s*\d+\.\s|\b\d{2}/\d{2}/\d{4}\b|\b\d{9}\b",
    re.MULTILINE,
)


def should_skip_reformulator(main_text: str) -> bool:
    """Décide si le reformulateur Haiku peut être bypass (réponse déjà
    voice-friendly).

    Critères cumulatifs :
    - Texte court (< 180 chars).
    - Pas de Markdown bold / header / bullet / liste numérotée.
    - Pas de date ISO `DD/MM/YYYY`.
    - Pas de SIREN 9 chiffres.

    Si tous OK → skip = on yield le main text strippé direct, économise
    ~1.5-2 s de latence reformulateur.
    """
    if len(main_text) > _SKIP_REFORMULATOR_CHAR_THRESHOLD:
        return False
    return not _MARKDOWN_MARKERS_RE.search(main_text)
