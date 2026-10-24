// A standalone worker for a seller who has no Telegram bot of their own yet: the owner's plain
// messages go to the agent, its approvals come back as buttons. Setup: ultron/README.md.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_IDS,
// AGENT_TOKEN. Vars (wrangler.toml): AGENT_REPO, AGENT_ACTIONS. Bindings: AI, AGENT_KV.
import { makeAgent } from "./agent.js";

let agent;

export default {
  async fetch(request, env, ctx) {
    agent ||= makeAgent({ repo: env.AGENT_REPO });
    const path = new URL(request.url).pathname;
    const api = agent.route(request, env, path);             // its own methods (GET / PUT memory too)
    if (api) return api;
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);
    const allowed = (id) => String(env.ALLOWED_CHAT_IDS || "").split(",").map((s) => s.trim()).includes(String(id));

    const cb = update?.callback_query;
    if (cb?.message) {
      if (allowed(cb.message.chat.id) && agent.isTap(cb.data)) ctx.waitUntil(agent.decide(env, cb, String(cb.data)));
      return new Response("ok");
    }
    const msg = update?.message;
    const text = msg?.text?.trim();
    if (text && !text.startsWith("/") && allowed(msg.chat.id)) {
      ctx.waitUntil(agent.ask(env, msg.chat.id, msg.message_id, text));
    }
    return new Response("ok");
  },
};
