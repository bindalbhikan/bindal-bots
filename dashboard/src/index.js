// Bindal Exports dashboard: one page showing every bot's latest numbers.
//
// The bots push their numbers to the Telegram worker, which keeps them in the DASH KV
// namespace; this worker only reads them. The whole workers.dev URL sits behind Cloudflare
// Access (email one-time code). The worker checks Access's signed token itself and refuses
// everything while ACCESS_TEAM_DOMAIN / ACCESS_AUD are not set, so the data is never public
// by accident.

import PAGE from "./page.html";

const KEYS = ["sale", "sale_today", "vms", "ajio_tat", "ajio_spf",
              "run_sale", "run_vms", "run_ajio_tat", "run_ajio_spf"];

export default {
  async fetch(request, env) {
    const denied = await checkAccess(request, env);
    if (denied) return new Response(denied, { status: 403, headers: { "Content-Type": "text/plain; charset=utf-8" } });

    const { pathname } = new URL(request.url);
    if (pathname === "/api/data") {
      const values = await Promise.all(KEYS.map((k) => env.DASH.get(k, "json")));
      const data = Object.fromEntries(KEYS.map((k, i) => [k, values[i]]));
      return Response.json(data, { headers: { "Cache-Control": "no-store" } });
    }
    if (pathname === "/" || pathname === "/index.html") {
      return new Response(PAGE, {
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store",
          "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:",
          "X-Frame-Options": "DENY",
        },
      });
    }
    return new Response("Not found", { status: 404 });
  },
};

// ------------------------------------------------------------------ Cloudflare Access

let certCache = { at: 0, keys: [] };

async function checkAccess(request, env) {
  const team = (env.ACCESS_TEAM_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
  const aud = env.ACCESS_AUD || "";
  if (!team || !aud) return "Login is not set up yet (Cloudflare Access).";
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!jwt) return "Please sign in.";
  try {
    const [h, p, s] = jwt.split(".");
    const header = JSON.parse(b64text(h));
    const claims = JSON.parse(b64text(p));
    const key = await accessKey(team, header.kid);
    if (!key) return "Sign-in could not be verified.";
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64bytes(s), new TextEncoder().encode(`${h}.${p}`));
    const audOk = Array.isArray(claims.aud) ? claims.aud.includes(aud) : claims.aud === aud;
    if (!ok || !audOk || claims.iss !== `https://${team}` || claims.exp * 1000 < Date.now()) {
      return "Sign-in could not be verified.";
    }
    return "";
  } catch (e) {
    console.log(`access check failed: ${e}`);
    return "Sign-in could not be verified.";
  }
}

async function accessKey(team, kid) {
  if (Date.now() - certCache.at > 3600_000 || !certCache.keys.some((k) => k.kid === kid)) {
    const res = await fetch(`https://${team}/cdn-cgi/access/certs`);
    certCache = { at: Date.now(), keys: (await res.json()).keys || [] };
  }
  const jwk = certCache.keys.find((k) => k.kid === kid);
  if (!jwk) return null;
  return crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
}

function b64bytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "="));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function b64text(s) {
  return new TextDecoder().decode(b64bytes(s));
}
