// Ultron routes for a seller's Cloudflare worker (the same for every seller):
//
//   POST /api/ai        one chat-completions call to Workers AI for ultron.core.llm
//   POST /api/approval  ultron.agents.chat proposes an action: saved in KV, Approve / Reject buttons sent
//   GET / PUT /api/memory  the agent's memory (ultron.core.memory), one versioned KV document
//   plain text          an owner's message starts the agent-chat workflow, which answers in the chat
//   ap:<id> / rj:<id>   a tap on Approve / Reject: only Approve starts the saved action
//
// The seller's setup comes from the worker's env:
//   AGENT_ACTIONS  (wrangler.toml [vars.AGENT_ACTIONS]) {name: {workflow, inputs: [..]}}: the only
//                  workflows and inputs an approval can start; the same list as agent.toml [actions]
//   GITHUB_TOKEN, TELEGRAM_BOT_TOKEN (secrets), the [ai] binding AI, the KV namespace passed as `kv`.
//
// Use: const agent = makeAgent({ repo: "owner/name", token: (env) => env.AGENT_TOKEN, allowedChat, kv: "DASH" });
//      agent.route(request, env, path) -> Response | null;  agent.ask(env, chatId, messageId, text);
//      agent.isTap(data) / agent.decide(env, cb, data)

export const FREE_MODELS = ["@cf/openai/gpt-oss-120b", "@cf/qwen/qwen3.8-27b", "@cf/nvidia/nemotron-3-120b-a12b",
                            "@cf/openai/gpt-oss-20b"];

export function makeAgent({
  repo,                                  // "owner/name" of the seller's bots repo
  token = (env) => env.AGENT_TOKEN,      // bearer token the Python side sends
  allowedChat = (env, chatId) => String(env.ALLOWED_CHAT_IDS || "").split(",").map((s) => s.trim()).includes(String(chatId)),
  kv = "AGENT_KV",                       // KV binding name for pending approvals
  chatWorkflow = "agent-chat.yml",
  ref = "main",
  models = FREE_MODELS,                  // Workers AI models allowed on /api/ai
  approvalHours = 24,
  userAgent = "ultron-worker",
} = {}) {
  const allowedModels = new Set(models);

  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const github = (env, path, init = {}) => fetch(`https://api.github.com${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": userAgent },
  });

  const telegram = async (env, method, body) => {
    const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) console.log(`${method} ${res.status}: ${await res.text()}`);
    return res;
  };

  const send = (env, chatId, text, replyTo, markup) => telegram(env, "sendMessage", {
    chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true,
    ...(replyTo ? { reply_parameters: { message_id: replyTo, allow_sending_without_reply: true } } : {}),
    ...(markup ? { reply_markup: markup } : {}),
  });

  const authorised = (request, env) => {
    const t = token(env) || "";
    return t && request.headers.get("Authorization") === `Bearer ${t}`;
  };

  const actions = (env) => {
    const a = env.AGENT_ACTIONS;
    return typeof a === "string" ? JSON.parse(a) : (a || {});
  };

  const dispatch = (env, workflow, inputs) => github(env, `/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
    method: "POST", body: JSON.stringify({ ref, inputs }),
  });

  async function ask(env, chatId, messageId, text) {
    try {
      const res = await dispatch(env, chatWorkflow, { text: text.slice(0, 1500), chat_id: String(chatId),
                                                      reply_to: String(messageId) });
      if (res.status !== 204) throw new Error(`GitHub said ${res.status}: ${(await res.text()).slice(0, 200)}`);
      await send(env, chatId, "🤖 On it… (about 1-2 minutes)", messageId);
    } catch (e) {
      await send(env, chatId, `❌ <b>The agent did not start</b>\n<code>${esc(e.message)}</code>`, messageId);
    }
  }

  async function saveApproval(request, env) {
    if (!authorised(request, env)) return new Response("forbidden", { status: 403 });
    let req;
    try {
      req = JSON.parse(await request.text());
    } catch {
      return new Response("not JSON", { status: 400 });
    }
    const action = actions(env)[req.action];
    if (!action) return Response.json({ error: `unknown action ${req.action}` }, { status: 400 });
    const inputs = {};
    for (const [k, v] of Object.entries(req.inputs || {})) {
      if (!(action.inputs || []).includes(k)) return Response.json({ error: `input ${k} not allowed for ${req.action}` }, { status: 400 });
      inputs[k] = String(v).slice(0, 1000);
    }
    const chat = String(req.chat_id || "");
    if (!allowedChat(env, chat)) return Response.json({ error: "chat not allowed" }, { status: 400 });
    const id = crypto.randomUUID().slice(0, 12);
    await env[kv].put(`approval:${id}`, JSON.stringify({ action: req.action, inputs, chat }),
                      { expirationTtl: approvalHours * 3600 });
    const args = Object.entries(inputs).map(([k, v]) => `${k} = ${v}`).join("\n") || "(no inputs)";
    await send(env, chat,
      `🙋 <b>Approve this?</b>\n\n${esc(String(req.summary || req.action).slice(0, 600))}\n\n` +
      `<b>Run:</b> ${esc(action.workflow)}\n<code>${esc(args)}</code>\n\n<i>Expires in ${approvalHours} hours.</i>`,
      undefined, { inline_keyboard: [[{ text: "✅ Approve", callback_data: `ap:${id}` }, { text: "✖️ Reject", callback_data: `rj:${id}` }]] });
    return Response.json({ id });
  }

  const isTap = (data) => /^(ap|rj):/.test(String(data || ""));

  async function decide(env, cb, data) {
    const key = `approval:${data.slice(3)}`;
    const saved = await env[kv].get(key, "json");
    const edit = (text) => telegram(env, "editMessageText", { chat_id: cb.message.chat.id, message_id: cb.message.message_id,
                                                             text, parse_mode: "HTML", disable_web_page_preview: true });
    if (!saved) {
      await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Expired or already decided" });
      return edit(`${cb.message.text ? esc(cb.message.text) : ""}\n\n⌛ <i>Expired or already decided.</i>`);
    }
    await env[kv].delete(key);                     // one tap decides it, a second does nothing
    const who = esc(cb.from?.first_name || "someone");
    if (data.startsWith("rj:")) {
      await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Rejected" });
      return edit(`${esc(cb.message.text || "")}\n\n✖️ <b>Rejected</b> by ${who}.`);
    }
    const action = actions(env)[saved.action];
    if (!action) {
      await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Action no longer allowed" });
      return edit(`${esc(cb.message.text || "")}\n\n❌ <i>${esc(saved.action)} is no longer in AGENT_ACTIONS.</i>`);
    }
    const res = await dispatch(env, action.workflow, saved.inputs);
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: res.status === 204 ? "Started" : "Failed" });
    return edit(`${esc(cb.message.text || "")}\n\n` + (res.status === 204
      ? `✅ <b>Approved</b> by ${who}: ${esc(action.workflow)} started.`
      : `❌ Approved by ${who}, but GitHub said ${res.status}: ${esc((await res.text()).slice(0, 200))}`));
  }

  // Workers AI on the account's free daily allocation: one chat-completions call (OpenAI shape:
  // messages, tools, tool_calls), only the allowed models.
  async function runAI(request, env) {
    if (!authorised(request, env)) return new Response("forbidden", { status: 403 });
    const body = await request.text();
    if (body.length > 1_500_000) return new Response("too large", { status: 413 });
    let req;
    try {
      req = JSON.parse(body);
    } catch {
      return new Response("not JSON", { status: 400 });
    }
    const { model, ...input } = req;
    if (!allowedModels.has(model)) return new Response("model not allowed", { status: 400 });
    try {
      return Response.json(await env.AI.run(model, { ...input, stream: false }));
    } catch (e) {
      // the free allocation used up, or the model failing: the caller reports it
      return Response.json({ error: String(e && e.message || e) }, { status: 502 });
    }
  }

  // The agent's memory (ultron.core.memory, WorkerStore): one JSON document in KV with a version
  // number. GET returns {version, data}; PUT {version, data} is refused (409) when another run wrote
  // a newer version first, and the agent then reads again and redoes its change.
  const MEMORY_KEY = "agent:memory";
  async function memory(request, env) {
    if (!authorised(request, env)) return new Response("forbidden", { status: 403 });
    const saved = await env[kv].get(MEMORY_KEY, "json");
    if (request.method === "GET") {
      return saved ? Response.json(saved) : new Response("none", { status: 404 });
    }
    if (request.method !== "PUT") return new Response("method not allowed", { status: 405 });
    const body = await request.text();
    if (body.length > 5_000_000) return new Response("too large", { status: 413 });
    let req;
    try {
      req = JSON.parse(body);
    } catch {
      return new Response("not JSON", { status: 400 });
    }
    const current = saved ? saved.version : 0;
    if (Number(req.version) !== current) return Response.json({ error: "stale", version: current }, { status: 409 });
    if (!req.data || typeof req.data !== "object") return new Response("no data", { status: 400 });
    const next = { version: current + 1, data: req.data, at: new Date().toISOString() };
    await env[kv].put(MEMORY_KEY, JSON.stringify(next));
    return Response.json({ version: next.version });
  }

  function route(request, env, path) {
    if (path === "/api/ai") return runAI(request, env);
    if (path === "/api/approval") return saveApproval(request, env);
    if (path === "/api/memory") return memory(request, env);
    return null;
  }

  return { route, ask, isTap, decide, runAI, saveApproval, memory };
}
