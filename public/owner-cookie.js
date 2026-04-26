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

        // B. Inject un style inline qui hide le body avec fade-in.
        //    Le custom CSS (footer.css) charge en parallèle du bundle
        //    Chainlit, parfois trop tard pour le 1er paint. Inject
        //    inline ici garantit que c'est appliqué AVANT le 1er paint.
        const foucGuard = document.createElement("style");
        foucGuard.id = "__genial_fouc_guard__";
        foucGuard.textContent = `
            html, body { background-color: #0a0a0a !important; }
            body { opacity: 0; transition: opacity 0.45s ease-in; }
            body.genial-revealed { opacity: 1; }
        `;
        if (document.head) {
            document.head.appendChild(foucGuard);
        }

        // C. Reveal stratégie : après ``load`` complet + 350 ms (laisse
        //    Chainlit React monter et faire son premier render stable).
        //    Fallback : 2 s max au cas où ``load`` ne fire jamais.
        const reveal = function () {
            if (document.body && !document.body.classList.contains("genial-revealed")) {
                document.body.classList.add("genial-revealed");
            }
        };
        window.addEventListener("load", function () {
            setTimeout(reveal, 350);
        });
        setTimeout(reveal, 2000); // Fallback safety net
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
