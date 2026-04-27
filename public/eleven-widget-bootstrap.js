// S10 voice mode — bootstrap qui :
//   1. Préserve intégralement le comportement de `public/owner-cookie.js`
//      (cookie d'isolation des conversations + splash anti-FOUC).
//      Chainlit n'autorise qu'un seul ``custom_js`` (string, pas array)
//      → on fusionne les deux scripts dans ce fichier unique.
//   2. Si voice mode est activé côté serveur (route ``/voice-meta.html``
//      montée → renvoie un meta tag ``genial-voice-mode=true`` + agent-id),
//      injecte ``<elevenlabs-convai>`` + le bundle CDN du widget.
//
// Si ``ENABLE_VOICE_MODE=false`` côté serveur, ``/voice-meta.html`` n'est
// pas montée (cf. ``voice/mount.py``) → fetch renvoie 404 → on skippe
// silencieusement l'injection. Le chat texte reste 100 % fonctionnel.

(function () {
    // ───────────────────────────────────────────────────────────────
    // Section 1 — owner-cookie.js verbatim (S09.7)
    // ───────────────────────────────────────────────────────────────
    try {
        document.documentElement.style.backgroundColor = "#0a0a0a";

        const splash = document.createElement("div");
        splash.id = "__genial_splash__";
        splash.style.cssText = [
            "position:fixed",
            "top:0",
            "left:0",
            "width:100vw",
            "height:100vh",
            "background:#0a0a0a",
            "display:flex",
            "flex-direction:column",
            "justify-content:center",
            "align-items:center",
            "z-index:99999",
            "transition:opacity 0.4s ease-out",
        ].join(";");
        splash.innerHTML = [
            '<img src="/public/favicon.png" alt="Genial" ',
            'style="width:96px;height:96px;object-fit:contain" />',
            '<div style="margin-top:20px;color:#888;font-family:system-ui,sans-serif;',
            'font-size:14px;letter-spacing:0.5px">Chargement…</div>',
        ].join("");

        const injectSplash = function () {
            if (document.body && !document.getElementById("__genial_splash__")) {
                document.body.appendChild(splash);
            }
        };
        if (document.body) {
            injectSplash();
        } else {
            document.addEventListener("DOMContentLoaded", injectSplash);
        }

        const removeSplash = function () {
            const el = document.getElementById("__genial_splash__");
            if (!el) return;
            el.style.opacity = "0";
            setTimeout(function () {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, 400);
        };
        window.addEventListener("load", function () {
            setTimeout(removeSplash, 1200);
        });
        setTimeout(removeSplash, 5000);
    } catch (e) {
        // No-op si DOM inaccessible.
    }

    const KEY = "genial_owner_id";
    let id = null;
    try {
        id = window.localStorage.getItem(KEY);
    } catch (e) {
        // localStorage bloqué — UUID éphémère pour cette page.
    }
    // Fallback cookie : si localStorage est vide mais que le serveur a
    // déjà posé le cookie ``genial_owner_id`` (cf. middleware backend
    // ``auth/middleware.py:ensure_owner_cookie_dispatch``), on adopte
    // cette valeur côté localStorage. Évite de regénérer un UUID qui
    // écraserait le cookie posé par le serveur.
    if (!id) {
        try {
            const cookieMatch = document.cookie.match(/(?:^|;\s*)genial_owner_id=([A-Za-z0-9-]{8,64})/);
            if (cookieMatch) {
                id = cookieMatch[1];
            }
        } catch (e) {
            // Pas d'accès à document.cookie (sandbox extrême).
        }
    }
    if (!id) {
        if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
            id = crypto.randomUUID().replaceAll("-", "");
        } else {
            id = "";
            for (let i = 0; i < 16; i++) {
                id += Math.floor(Math.random() * 256).toString(16).padStart(2, "0");
            }
        }
    }
    try {
        window.localStorage.setItem(KEY, id);
    } catch (e) {
        // Best-effort.
    }
    const secureFlag = (typeof location !== "undefined" && location.protocol === "https:") ? "; Secure" : "";
    document.cookie = `${KEY}=${id}; path=/; max-age=31536000; SameSite=Lax${secureFlag}`;

    // ───────────────────────────────────────────────────────────────
    // Section 2 — Voice mode widget bootstrap (S10)
    // ───────────────────────────────────────────────────────────────

    // Évite double-injection si le bootstrap est inclus deux fois (cf.
    // recharge à chaud Chainlit, hot reload dev, etc.).
    if (window.__genialVoiceBootstrapDone) {
        return;
    }
    window.__genialVoiceBootstrapDone = true;

    function injectElevenWidget(agentId) {
        if (!agentId) return;
        if (document.querySelector("elevenlabs-convai")) return;

        const widget = document.createElement("elevenlabs-convai");
        widget.setAttribute("agent-id", agentId);
        widget.setAttribute("override-language", "fr");
        widget.setAttribute("placement", "bottom-right");
        widget.setAttribute("avatar-orb-color-1", "#6040C0");
        widget.setAttribute("avatar-orb-color-2", "#9080E0");
        widget.setAttribute("start-call-text", "Parler à l'agent");
        widget.setAttribute("listening-text", "J'écoute…");
        widget.setAttribute("speaking-text", "Je réponds…");
        document.body.appendChild(widget);

        const script = document.createElement("script");
        // S10 phase 2 hotfix 2026-04-27 : bump 0.5.4 → 0.11.6.
        // 0.5.4 (figé en story phase 1) ne capturait pas l'audio mic
        // côté prod — l'ASR ElevenLabs recevait du silence. Le format
        // audio / protocol WebSocket a changé entre 0.5.x et 0.11.x.
        // Validé live : ASR FR fonctionne avec 0.11.6.
        script.src = "https://unpkg.com/@elevenlabs/convai-widget-embed@0.11.6";
        script.async = true;
        document.body.appendChild(script);
    }

    function bootstrapVoiceWidget() {
        // Fetch le meta endpoint côté serveur. Si ``ENABLE_VOICE_MODE=false``,
        // la route n'est pas montée → 404 → on skippe.
        fetch("/voice-meta.html", { credentials: "same-origin", cache: "no-store" })
            .then(function (resp) {
                if (!resp.ok) return null;
                return resp.text();
            })
            .then(function (html) {
                if (!html) return;
                // Parse le meta tag. DOMParser est dispo partout (IE10+).
                const doc = new DOMParser().parseFromString(html, "text/html");
                const meta = doc.querySelector('meta[name="genial-voice-mode"]');
                if (!meta) return;
                if (meta.getAttribute("content") !== "true") return;
                const agentId = meta.getAttribute("data-agent-id");
                if (!agentId) {
                    console.warn("[genial] voice-mode enabled but data-agent-id missing");
                    return;
                }
                if (document.body) {
                    injectElevenWidget(agentId);
                } else {
                    document.addEventListener("DOMContentLoaded", function () {
                        injectElevenWidget(agentId);
                    });
                }
            })
            .catch(function (err) {
                // Réseau / parsing : le mode texte reste 100% fonctionnel.
                console.warn("[genial] voice widget bootstrap skipped:", err);
            });
    }

    bootstrapVoiceWidget();
})();
