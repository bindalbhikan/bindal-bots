// Bindal Exports dashboard: one page showing every bot's latest numbers.
//
// The bots push their numbers to the Telegram worker, which keeps them in the DASH KV
// namespace; this worker only reads them.
//
// Sign-in goes through the Telegram bot, which already obeys only the allowed chats:
//   /dashboard in Telegram -> a link with a token signed by SESSION_KEY, valid 10 minutes, once
//   /login?t=<token>       -> a "Continue" button (link-preview fetchers never press it)
//   POST /login            -> checks the token, burns it in KV, sets a 30-day signed cookie
// Every other page needs that cookie. Without SESSION_KEY nothing is served at all.

import PAGE from "./page.html";

const KEYS = ["sale", "sale_today", "vms", "vms_erp", "ajio_tat", "ajio_spf",
              "returns", "returns_year", "myntra_spf", "ajio_ads",
              "run_sale", "run_vms", "run_ajio_tat", "run_ajio_spf", "run_returns", "run_myntra_spf", "run_ajio_ads"];
const COOKIE = "bd_session";
const SESSION_DAYS = 30;

export default {
  async fetch(request, env) {
    if (!env.SESSION_KEY) return text("Dashboard sign-in is not set up yet.", 503);
    const url = new URL(request.url);

    if (url.pathname === "/login") return login(request, env, url);
    if (url.pathname === "/logout") {
      return new Response(null, { status: 303, headers: { Location: "/", "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` } });
    }

    const session = await readSigned(env, cookie(request, COOKIE));
    if (!session || session.kind !== "session") return signInPage();

    if (url.pathname === "/api/data") {
      const values = await Promise.all(KEYS.map((k) => env.DASH.get(k, "json")));
      return Response.json(Object.fromEntries(KEYS.map((k, i) => [k, values[i]])), { headers: { "Cache-Control": "no-store" } });
    }
    if (url.pathname === "/" || url.pathname === "/index.html") return html(PAGE);
    return text("Not found", 404);
  },
};

// ------------------------------------------------------------------ sign-in

async function login(request, env, url) {
  if (request.method === "GET") {
    const t = url.searchParams.get("t") || "";
    const ok = await readSigned(env, t);
    if (!ok || ok.kind !== "login") return signInPage("This sign-in link has expired. Ask the bot for a new one with /dashboard.");
    return html(shell("Sign in", `<p>Signing in to the Bindal Exports dashboard on this device.</p>
      <form method="post" action="/login"><input type="hidden" name="t" value="${esc(t)}">
      <button type="submit">Continue</button></form>`));
  }
  if (request.method !== "POST") return text("Method not allowed", 405);
  const t = String((await request.formData()).get("t") || "");
  const claim = await readSigned(env, t);
  if (!claim || claim.kind !== "login") return signInPage("This sign-in link has expired. Ask the bot for a new one with /dashboard.");
  const used = `used:${claim.nonce}`;
  if (await env.DASH.get(used)) return signInPage("This sign-in link was already used. Ask the bot for a new one with /dashboard.");
  await env.DASH.put(used, "1", { expirationTtl: 3600 });
  const session = await sign(env, { kind: "session", chat: claim.chat, exp: Date.now() + SESSION_DAYS * 86400000 });
  return new Response(null, {
    status: 303,
    headers: { Location: "/", "Set-Cookie": `${COOKIE}=${session}; Path=/; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Lax` },
  });
}

function signInPage(message) {
  return html(shell("Sign in", `<p>${esc(message || "Open the Bindal Exports bot in Telegram and send /dashboard (or tap 🖥 Dashboard in the menu). It sends a sign-in link for this page.")}</p>`), 401);
}

// Tokens: base64url(JSON) "." base64url(HMAC-SHA256), signed with SESSION_KEY (shared with the
// Telegram worker, which signs the login links). Expired or tampered tokens read as null.
async function hmacKey(env) {
  return crypto.subtle.importKey("raw", new TextEncoder().encode(env.SESSION_KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

async function sign(env, claim) {
  const body = b64url(new TextEncoder().encode(JSON.stringify(claim)));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(env), new TextEncoder().encode(body));
  return `${body}.${b64url(new Uint8Array(sig))}`;
}

async function readSigned(env, token) {
  try {
    const [body, sig] = String(token || "").split(".");
    if (!body || !sig) return null;
    const ok = await crypto.subtle.verify("HMAC", await hmacKey(env), unb64url(sig), new TextEncoder().encode(body));
    if (!ok) return null;
    const claim = JSON.parse(new TextDecoder().decode(unb64url(body)));
    return claim.exp > Date.now() ? claim : null;
  } catch {
    return null;
  }
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "="));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function cookie(request, name) {
  const m = (request.headers.get("Cookie") || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : "";
}

// ------------------------------------------------------------------ responses

const HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
};

function html(body, status = 200) {
  return new Response(body, { status, headers: HEADERS });
}

function text(body, status) {
  return new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function shell(title, inner) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bindal Dashboard · ${esc(title)}</title><style>
:root{color-scheme:light;--page:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--ring:rgba(11,11,11,.1);--btn:#0b0b0b;--btnink:#fff}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--ring:rgba(255,255,255,.1);--btn:#f0efec;--btnink:#0d0d0d}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}
main{max-width:380px;width:100%;background:var(--card);border:1px solid var(--ring);border-radius:16px;padding:24px}
h1{font-size:20px;margin:0 0 8px}p{color:var(--ink2);margin:0 0 16px}
button{width:100%;min-height:44px;border:0;border-radius:12px;background:var(--btn);color:var(--btnink);font:inherit;font-weight:600;cursor:pointer}
</style></head><body><main><h1>📊 Bindal Exports</h1>${inner}</main></body></html>`;
}
