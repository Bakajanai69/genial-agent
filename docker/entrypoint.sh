#!/bin/sh
# S09.7 hotfix volume — Railway monte les volumes en root:root par
# défaut. Le user runtime ``agent`` (uid 1000) n'a pas droit d'y
# écrire, ce qui fait échouer le bootstrap (PermissionError sur la
# copie bake → volume) et la persistance write-through du cache MCP
# (mcp_cache_persist_failed loggé en boucle).
#
# Cet entrypoint :
#   1. (en root) chown /data si présent et pas déjà à agent.
#   2. exec le CMD en tant qu'agent via setpriv (built-in util-linux,
#      pas de dep supplémentaire à installer).
#
# Si /data n'existe pas (mode dev local sans volume), le chown est
# silencieusement skippé.
set -e

if [ -d "/data" ]; then
    # ``2>/dev/null || true`` : si on est déjà non-root (rare, mais
    # tolérance), le chown échoue silencieusement plutôt que de
    # crasher le boot.
    chown -R agent:agent /data 2>/dev/null || true
fi

# setpriv est dans util-linux (présent par défaut sur slim-bookworm).
# --init-groups réinitialise les supplementary groups d'agent (sinon
# ils héritent de root, on perd le bénéfice du switch).
exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
