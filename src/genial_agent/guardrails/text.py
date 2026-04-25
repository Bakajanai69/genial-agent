"""Normalisation FR — mutualisé entre ``routing.py`` (S04) et ``input_gate.py`` (S05).

``normalize_fr`` strip les diacritiques (NFKD), retire les caractères
de format/contrôle invisibles (catégories Unicode ``Cf`` et ``Cc``)
puis met en minuscule pour un matching regex accent-insensible et
résistant aux obfuscations zero-width.

**Pourquoi remplacer Cf par un espace** : un attaquant peut insérer
ZWSP (U+200B), ZWNJ (U+200C), ZWJ (U+200D), BOM (U+FEFF) ou autres
formatters Unicode (catégorie ``Cf``) entre les mots d'un prompt
d'injection. Sans traitement explicite, ``NFKD + encode("ascii",
"ignore")`` **drop** ces caractères (non-ASCII), ce qui **fusionne**
les mots autour (``ignore<ZWSP>previous instructions`` →
``ignoreprevious instructions``) et casse les ``\\s+`` des patterns
d'injection. En les remplaçant par un espace ASCII **avant** l'encode,
on préserve leur rôle de séparateur visuel et les patterns matchent
l'attaque.

On ne touche **pas** la catégorie ``Cc`` (control) : ``\\n``, ``\\r``,
``\\t`` y sont, et le pattern de fake-turn ``^\\s*(system|assistant):``
en mode ``re.MULTILINE`` dépend du ``\\n`` pour s'aligner. Côté
sécurité, les caractères ``Cc`` non whitespace (NULL, BEL, BS, …)
sortent de toute façon en non-ASCII via ``encode("ascii", "ignore")``
si leur point ne tient pas en ASCII printable, et n'ont pas le rôle
de « séparateur invisible » exploité par les attaques zero-width.

Le texte normalisé n'est jamais renvoyé à Claude — c'est une vue
*detection-only* du contenu utilisateur. La transformation peut donc
être agressive sans risque côté UX.

**Limite connue (héritée S04 phase 1)** : les ligatures qui n'ont pas de
décomposition NFKD canonique en ASCII sont **perdues** plutôt que
transcrites (ex : ``ß`` → drop, ``œ`` reste dans la sortie unicode mais
son ``c`` suivi du digraphe ``oe`` fusionné n'est pas reconstruit).
Acceptable : aucun pattern d'injection OWASP LLM01 ou d'advisory
financier n'en dépend.

S04 garde pour l'instant son alias privé ``_normalize_fr`` pour zéro
risque de régression (5 lignes dupliquées) — dédup prévue en S09 polish.
"""

from __future__ import annotations

import unicodedata


def normalize_fr(text: str) -> str:
    """Strip diacritiques + Cf/Cc + lowercase.

    Pipeline :

    1. NFKD : décompose les caractères composés (``é`` → ``e`` + diacritique).
    2. Remplace la catégorie Unicode ``Cf`` (formatters invisibles :
       ZWSP, ZWNJ, ZWJ, BOM, soft-hyphen, LRM/RLM, …) par un espace
       ASCII. ``Cc`` (control) est volontairement préservé pour que
       ``\\n`` reste un séparateur de ligne exploité par le pattern
       fake-turn en ``re.MULTILINE``.
    3. ``encode("ascii", "ignore")`` : drop tout résidu non-ASCII
       (diacritiques décomposés, ligatures sans décomposition canonique).
    4. ``lower()`` : matching accent-insensible sans ``re.IGNORECASE``.

    Exemples :
        >>> normalize_fr("Évolution")
        'evolution'
        >>> normalize_fr("Société Générale")
        'societe generale'
        >>> # ZWSP entre les mots → remplacé par espace, pattern matche :
        >>> normalize_fr("Ignore\\u200Bprevious")
        'ignore previous'
    """
    nfkd = unicodedata.normalize("NFKD", text)
    cleaned = "".join(" " if unicodedata.category(ch) == "Cf" else ch for ch in nfkd)
    stripped = cleaned.encode("ascii", "ignore").decode("ascii")
    return stripped.lower()
