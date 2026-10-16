/* Session credentials stay in memory, never in URLs or browser storage. */
(function () {
  "use strict";
  let sessionPromise = null;

  function session() {
    if (!sessionPromise) {
      sessionPromise = fetch("/api/session", {
        cache: "no-store", credentials: "same-origin", redirect: "error",
      }).then(async function (response) {
        if (!response.ok) throw new Error("Não foi possível iniciar a sessão local.");
        const data = await response.json();
        if (!data || typeof data.session_token !== "string" || data.session_token.length < 32) {
          throw new Error("Sessão local inválida.");
        }
        return data.session_token;
      }).catch(function (error) {
        sessionPromise = null;
        throw error;
      });
    }
    return sessionPromise;
  }

  async function apiFetch(input, options) {
    const url = new URL(input, location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      throw new Error("Destino fora da API local.");
    }
    const pendingSession = session();
    const token = await pendingSession;
    const headers = new Headers(options && options.headers);
    headers.set("X-Zeus-Session", token);
    const response = await fetch(url.href, Object.assign({}, options, {
      headers: headers, cache: "no-store", credentials: "same-origin", redirect: "error",
    }));
    // Never replay a mutation automatically, including after a server restart.
    if (response.status === 403 && sessionPromise === pendingSession) sessionPromise = null;
    return response;
  }

  globalThis.zeusAPI = { fetch: apiFetch };
})();
