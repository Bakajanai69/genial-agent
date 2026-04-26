// S09.7 hotfix — Cookie persistant ``genial_owner_id`` pour exposer
// l'historique des conversations Chainlit cross-session (style
// Claude/ChatGPT). Sans ce JS, l'``owner_id`` Chainlit était un UUID
// éphémère par-session-WebSocket → la sidebar redevenait vide à chaque
// refresh page / nouvelle conversation.
//
// Mécanique :
//   1. Au pageload, on lit ``localStorage["genial_owner_id"]``.
//   2. Si absent, on génère un UUID hex 32 chars (``crypto.randomUUID``
//      ou fallback Math.random pour les vieux navigateurs).
//   3. On le persiste dans ``localStorage`` ET dans ``document.cookie``.
//   4. Le backend (``ui/chainlit_data_layer.py:_resolve_owner_id``) lit
//      le cookie via ``cl.context.session.environ['HTTP_COOKIE']`` et
//      filtre les threads par cet owner_id dans ``list_threads`` /
//      ``get_thread``.
//
// Privacy : pas de tracking tiers, pas de PII. L'UUID est purement local
// au navigateur et permet à un visiteur de retrouver SES conversations
// après refresh / nouveau onglet — pas de cross-user data leak puisque
// chaque navigateur a son propre UUID en localStorage.
//
// Référencé via ``.chainlit/config.toml`` :
//   [UI]
//   custom_js = "/public/owner-cookie.js"
(function () {
    // S09.7 hotfix UX — anti-FOUC + anti-zigzag au tout 1er pageload.
    //
    // Symptômes observés sans ces protections :
    //   1. Flash blanc bref avant que le theme dark Chainlit s'applique.
    //   2. "Zigzag" visuel en navigation privée (pas de cache assets) :
    //      la page se rend en plusieurs étapes — Chainlit réauthentifie
    //      l'user quand il détecte un cookie changé entre la requête HTTP
    //      initiale (sans cookie) et le WebSocket (avec cookie posé par
    //      ce JS) → re-render multiple visible.
    //
    // Stratégie :
    //   A. Forcer le fond noir immédiatement (anti-flash blanc).
    //   B. Cacher le body en opacity:0 + fade-in lent → masque les
    //      re-renders intermédiaires de Chainlit pendant ~600 ms.
    //   C. Fallback timeout 2 s pour ne jamais laisser la page invisible
    //      si quelque chose foire.

    try {
        // A. Fond noir immédiat (avant React mount).
        document.documentElement.style.backgroundColor = "#0a0a0a";

        // B. Splash screen overlay full-screen — masque TOUS les
        //    re-renders Chainlit intermédiaires en navigation privée
        //    (pas de cache assets) : auth initial sans cookie →
        //    cookie posé → re-auth → React mount → premier render →
        //    sidebar inject → starters apparaissent. Sans splash,
        //    l'utilisateur voit chacune de ces étapes successivement.
        //
        //    Stratégie alternative essayée (commit f26732b, fade-in
        //    body opacity:0) : insuffisante en navigation privée car
        //    plusieurs re-renders dépassent le délai de 350 ms.
        //
        //    Splash visible avec logo + texte "Chargement…" → UX
        //    cohérente "loading state" plutôt que "page cassée".
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

        // Inject le splash dès que body est dispo. Si pas encore parsé
        // (script en defer mais body pas encore là), on attend
        // DOMContentLoaded. Sinon on append direct.
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

        // Hide le splash après ``window.load`` + 1200 ms (laisse
        // largement le temps à Chainlit de faire ses re-renders en
        // navigation privée). Fade-out 0.4 s puis remove.
        // Fallback safety net : 5 s max.
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
        setTimeout(removeSplash, 5000); // Fallback safety net
    } catch (e) {
        // No-op si DOM inaccessible (sandbox iframe extrême).
    }

    const KEY = "genial_owner_id";
    let id = null;
    try {
        id = window.localStorage.getItem(KEY);
    } catch (e) {
        // localStorage peut être bloqué (mode privé strict, quota plein).
        // On continue avec un UUID éphémère pour cette page seulement.
    }
    if (!id) {
        // crypto.randomUUID standard depuis 2022 (Chrome 92+, Firefox 95+,
        // Safari 15.4+). Fallback Math.random pour les vieux navigateurs.
        if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
            id = crypto.randomUUID().replaceAll("-", "");
        } else {
            id = "";
            for (let i = 0; i < 16; i++) {
                id += Math.floor(Math.random() * 256).toString(16).padStart(2, "0");
            }
        }
        try {
            window.localStorage.setItem(KEY, id);
        } catch (e) {
            // Idem ci-dessus — best-effort.
        }
    }
    // Pose le cookie pour que le backend Chainlit puisse le lire dans
    // les headers HTTP de la WebSocket initiale. ``max-age=31536000`` =
    // 1 an, ``SameSite=Lax`` pour cross-origin Railway/proxy.
    document.cookie = `${KEY}=${id}; path=/; max-age=31536000; SameSite=Lax`;
})();
