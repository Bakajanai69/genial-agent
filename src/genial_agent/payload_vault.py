"""Payload Vault — offload générique des tool results MCP volumineux.

Vault scoped session (jamais sur disque, jamais cross-session). Stocke
les payloads bruts > ``OFFLOAD_THRESHOLD_CHARS`` et expose un index
compact au LLM, qui peut ensuite ré-interroger via ``payload_inspect`` /
``payload_search``.

Aucune logique métier spécifique à un MCP : le module ne sait que
naviguer du JSON arbitraire (dict / list / scalaire). Il marche pour
tout serveur MCP qui retourne du JSON volumineux. Garantie maintenue
par grep dans la review S09.5 (cf. story §"Check-list spécifique").

Inspiration :

- Filesystem offload Deep Agents (LangChain) — sans la dep LangChain.
- Memory Tool Anthropic (``memory_20250818``) — sans la convention
  ``/memories/`` ni la persistence cross-session.

Cf. ``docs/stories/S09.5-mcp-payload-handling.md`` §"Approche J retenue".

Sécurité (review S09.5 phase 3) :

- ``_walk`` ne touche **jamais** au filesystem ni aux attributs Python
  d'un objet : c'est un walk déterministe sur ``dict``/``list`` parsés
  par ``json.loads``. Les segments de path qui ressemblent à
  ``__class__`` sont traités comme des clés de dict ordinaires. Si
  elles n'existent pas (ce qui est le cas sur du JSON normal), on
  retourne ``{"_error": ...}``.
- ``search`` compile un ``re.Pattern`` sans ``re.X``/``re.U``
  potentiellement permissifs et **borne** explicitement le nombre de
  matches via ``max_matches``. La protection contre le ReDoS catastrophe
  est best-effort : on borne aussi la taille de la chaîne fouillée et
  on tronque la regex utilisateur à ``MAX_REGEX_PATTERN_CHARS``.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

# --- Tunables (cf. story §"Données factuelles") -----------------------------

# Au-dessous de ce seuil, le payload passe direct (pas d'offload).
# Calibré sur le tableau §"Données factuelles" de la story S09.5 :
# un payload moyen (~19 K) doit être offloadé, un petit (~4 K) passe
# direct. 12 K = mid-point.
OFFLOAD_THRESHOLD_CHARS = 12_000

# Budget hard de l'index renvoyé au LLM (squelette + previews). Si
# l'index dépasse ce budget, on tronque les previews et on simplifie
# le squelette. Cf. test ``test_build_index_under_4kb``.
INDEX_BUDGET_CHARS = 4_000

# Cap par défaut sur ``payload_inspect.max_chars`` (l'agent peut le
# baisser, mais pas le monter au-delà de ``LOOKUP_MAX_CHARS_HARD``).
LOOKUP_MAX_CHARS_DEFAULT = 8_000
LOOKUP_MAX_CHARS_HARD = 12_000

PREVIEW_HEAD_CHARS = 400
PREVIEW_TAIL_CHARS = 400
SKELETON_MAX_DEPTH = 3
SKELETON_MAX_KEYS_PER_LEVEL = 30

# Limite sur la taille de la regex que l'agent peut envoyer. Évite des
# patterns absurdement longs (prompt injection indirecte ou bug LLM).
MAX_REGEX_PATTERN_CHARS = 200
SEARCH_DEFAULT_CONTEXT_CHARS = 200
SEARCH_DEFAULT_MAX_MATCHES = 10
SEARCH_HARD_MAX_MATCHES = 30


# --- Vault ------------------------------------------------------------------


@dataclass
class PayloadVault:
    """Vault session-scoped. Rangé sur ``ConversationState``.

    Pas cross-session : chaque session Chainlit a son propre vault, et
    le vault meurt avec elle. Cap implicite mémoire = durée de la
    session. Pas de TTL.
    """

    _store: dict[str, str] = field(default_factory=dict)

    def store(self, raw: str) -> str:
        """Range un payload brut, retourne un ``payload_id`` court."""
        pid = f"p_{secrets.token_hex(4)}"
        self._store[pid] = raw
        return pid

    def get(self, pid: str) -> str | None:
        """Lookup par ``payload_id``. ``None`` si inconnu / expiré."""
        return self._store.get(pid)

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


# --- Index ------------------------------------------------------------------


def build_index(raw: str, payload_id: str) -> dict[str, Any]:
    """Construit un index générique du payload.

    Pas de logique métier : on profile uniquement la **structure** JSON
    (squelette à profondeur N, tailles d'arrays, previews head/tail).
    Sortie compacte (< ``INDEX_BUDGET_CHARS``), prête à être injectée
    dans un ``tool_result`` Anthropic à la place du payload brut.
    """
    parsed = _safe_parse_json(raw)
    index: dict[str, Any] = {
        "_payload_id": payload_id,
        "_size_chars": len(raw),
        "_skeleton": _skeleton(parsed, depth=0),
        "_array_sizes": _collect_array_sizes(parsed),
        "_preview_head": raw[:PREVIEW_HEAD_CHARS],
        "_preview_tail": raw[-PREVIEW_TAIL_CHARS:] if len(raw) > PREVIEW_HEAD_CHARS else "",
        "_inspect_hint": (
            "Use payload_inspect(payload_id, json_path) to read a specific subtree, "
            "or payload_search(payload_id, pattern) to grep the raw JSON. "
            "Path syntax: '$.foo.bar[42].baz' or 'foo.bar[42].baz'. "
            "Indices are 0-based; use -1 for the last element."
        ),
    }
    encoded = json.dumps(index, ensure_ascii=False)
    if len(encoded) <= INDEX_BUDGET_CHARS:
        return index
    return _shrink_index(index)


def _shrink_index(index: dict[str, Any]) -> dict[str, Any]:
    """Réduit l'index si la version pleine dépasse ``INDEX_BUDGET_CHARS``.

    Étapes : (1) raccourcit les previews à 200 chars, (2) tronque le
    squelette à profondeur 2, (3) garde au plus 20 array sizes triées
    par taille décroissante. À utiliser uniquement quand le payload
    contient un squelette dense (rare : la majorité des payloads JSON
    rentrent dans 4 K avec la profondeur 3 standard).
    """
    shrunk = dict(index)
    shrunk["_preview_head"] = shrunk["_preview_head"][:200]
    shrunk["_preview_tail"] = shrunk["_preview_tail"][:200]
    shrunk["_skeleton"] = _truncate_skeleton(shrunk["_skeleton"], max_depth=2)
    sizes = shrunk.get("_array_sizes", {})
    if isinstance(sizes, dict) and len(sizes) > 20:
        kept = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:20]
        shrunk["_array_sizes"] = dict(kept)
        shrunk["_array_sizes_truncated"] = True
    return shrunk


def _truncate_skeleton(node: Any, max_depth: int, depth: int = 0) -> Any:
    if depth >= max_depth:
        return _summarize(node)
    if isinstance(node, dict):
        return {k: _truncate_skeleton(v, max_depth, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return node  # déjà résumé par ``_skeleton``
    return node


# --- Inspect & search -------------------------------------------------------


def inspect(raw_json: str, json_path: str, max_chars: int = LOOKUP_MAX_CHARS_DEFAULT) -> str:
    """Walk déterministe sur le JSON. Retourne la valeur encodée JSON.

    Tronqué à ``max_chars`` avec marker explicite si nécessaire. La
    valeur est **verbatim** (pas de reformulation), garantie par
    ``json.dumps`` sur le ``json.loads`` du payload.
    """
    capped = max(0, min(int(max_chars), LOOKUP_MAX_CHARS_HARD))
    parsed = _safe_parse_json(raw_json)
    value = _walk(parsed, json_path)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    if len(encoded) > capped:
        return encoded[:capped] + "\n…[lookup truncated]"
    return encoded


def search(
    raw_json: str,
    pattern: str,
    max_matches: int = SEARCH_DEFAULT_MAX_MATCHES,
    context_chars: int = SEARCH_DEFAULT_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Regex search avec contexte. Retourne ``[{match, position, context}]``.

    La regex est compilée avec ``IGNORECASE | MULTILINE`` (sans
    ``re.X``/``re.U`` pour rester strict). ``max_matches`` est
    explicitement borné par ``SEARCH_HARD_MAX_MATCHES`` pour limiter
    l'overhead côté LLM. Pattern tronqué à ``MAX_REGEX_PATTERN_CHARS``.
    """
    pattern = (pattern or "")[:MAX_REGEX_PATTERN_CHARS]
    capped_max = max(1, min(int(max_matches), SEARCH_HARD_MAX_MATCHES))
    try:
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        return [{"error": f"invalid regex: {exc}"}]

    out: list[dict[str, Any]] = []
    half = max(0, int(context_chars)) // 2
    for m in rx.finditer(raw_json):
        if len(out) >= capped_max:
            break
        start = max(0, m.start() - half)
        end = min(len(raw_json), m.end() + half)
        out.append(
            {
                "match": m.group(0),
                "position": m.start(),
                "context": raw_json[start:end],
            }
        )
    return out


# --- Helpers privés ---------------------------------------------------------


def _safe_parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"_raw": raw[:1000], "_note": "payload non-JSON"}


def _skeleton(node: Any, depth: int) -> Any:
    if depth >= SKELETON_MAX_DEPTH:
        return _summarize(node)
    if isinstance(node, dict):
        items = list(node.items())[:SKELETON_MAX_KEYS_PER_LEVEL]
        return {k: _skeleton(v, depth + 1) for k, v in items}
    if isinstance(node, list):
        if not node:
            return []
        # Pour un array, on résume en "<type>[N items]" plutôt que de
        # descendre — c'est ``_collect_array_sizes`` qui donne le N
        # exact. L'agent navigue ensuite via ``payload_inspect`` sur
        # ``arr[0]`` pour voir un sample, puis ``arr[N-1]`` pour la
        # queue (paterne typique : tri croissant chronologique côté
        # serveur upstream).
        return [f"<{type(node[0]).__name__}>[{len(node)} items]"]
    return _summarize(node)


def _summarize(node: Any) -> str:
    if isinstance(node, str):
        return f"<str:{len(node)}>"
    if isinstance(node, bool):
        return "<bool>"
    if isinstance(node, int):
        return "<int>"
    if isinstance(node, float):
        return "<float>"
    if node is None:
        return "<NoneType>"
    if isinstance(node, dict):
        return f"<dict:{len(node)} keys>"
    if isinstance(node, list):
        return f"<list:{len(node)}>"
    return f"<{type(node).__name__}>"


def _collect_array_sizes(
    node: Any,
    prefix: str = "$",
    out: dict[str, int] | None = None,
) -> dict[str, int]:
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            _collect_array_sizes(v, f"{prefix}.{k}", out)
    elif isinstance(node, list):
        out[prefix] = len(node)
        if node and isinstance(node[0], dict | list):
            _collect_array_sizes(node[0], f"{prefix}[*]", out)
    return out


# Path tokenizer : reconnaît ``key`` (alphanum + _ + -) et ``[N]``,
# ``[-1]`` (index entiers signés). Le preprocessing strip ``$`` et
# ``.`` initiaux pour accepter à la fois ``$.foo[0]`` et ``foo[0]``.
_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")


def _walk(node: Any, path: str) -> Any:
    """Walk ``$.foo.bar[0].baz`` ou ``foo.bar[0].baz`` ou ``arr[-1]``.

    Désambiguïsation **par contexte** : un segment numérique pur
    (``"2023"``, ``"0"``) est interprété comme

    - **clé string** si le nœud courant est un ``dict`` (utile quand
      l'upstream renvoie un mapping à clés numériques type
      ``{"2023": [...]}``),
    - **index entier** si le nœud courant est une ``list``.

    Cela évite le piège où ``$.2023[0]`` était rejeté à tort sur un
    dict avec une clé string numérique. Les tests
    ``test_walk_digit_key_in_dict`` et ``test_walk_index_on_list_*``
    figent les deux comportements.

    Sécurité : on ne touche **jamais** aux attributs Python (``__class__``,
    ``__dict__``, etc.). Tout segment est traité soit comme une clé de
    dict ordinaire, soit comme un index entier de list. Si la clé est
    introuvable ou l'index hors range → retour ``{"_error": "..."}``
    pour que le LLM puisse réajuster.
    """
    if not isinstance(path, str):
        return {"_error": f"json_path must be a string, got {type(path).__name__}"}
    cleaned = path.lstrip("$").lstrip(".")
    if not cleaned:
        return node
    segments = [m.group(1) or m.group(2) for m in _PATH_SEGMENT.finditer(cleaned)]
    if not segments:
        return {"_error": f"empty path after parsing '{path}'"}

    cur: Any = node
    for seg in segments:
        if isinstance(cur, dict):
            # Dict : segment toujours interprété comme clé string, même
            # numérique pur. Couvre les mappings à clés numériques type
            # ``{"2023": [...]}``.
            if seg in cur:
                cur = cur[seg]
            else:
                return {"_error": f"key '{seg}' missing at '{path}'"}
        elif isinstance(cur, list):
            # List : segment doit être un index entier (signé pour -1).
            if not seg.lstrip("-").isdigit():
                return {
                    "_error": (
                        f"list expects integer index, got '{seg}' at '{path}' "
                        f"(list has {len(cur)} items)"
                    )
                }
            try:
                idx = int(seg)
            except ValueError:
                return {"_error": f"invalid index '{seg}' at '{path}'"}
            if idx < 0:
                idx += len(cur)
            if idx < 0 or idx >= len(cur):
                return {"_error": f"index {seg} out of range at '{path}'"}
            cur = cur[idx]
        else:
            # Scalaire (str/int/None/...) : impossible de naviguer plus loin.
            return {
                "_error": (
                    f"cannot navigate into {type(cur).__name__} at '{path}' "
                    f"(remaining segment '{seg}')"
                )
            }
    return cur
