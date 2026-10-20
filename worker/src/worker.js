// Telegram webhook -> GitHub Actions workflow_dispatch for the Bindal report bots.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_IDS
// ALLOWED_CHAT_IDS + EXTRA_CHAT_IDS (both secrets: the repo is public) are comma-separated chat ids;
// messages and button taps from any other chat are ignored. Each run's report goes to the chat
// that asked for it.
//
// Every reply is written for a phone: a bold heading line, a blank line, short lines below,
// and at most two short buttons per row.

// All bots live in one repo; each command starts one of its workflows.
const OWNER = "bindalbhikan";
const REPO = "bindal-bots";
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// Each command maps to one workflow. `inputs` turns the command's arguments into
// workflow_dispatch inputs, or returns a string error to show the user. `minutes` is the
// usual run time, used until the workflow has enough runs of its own to measure.
const COMMANDS = {
  sale: {
    workflow: "sale.yml",
    icon: "📈",
    name: "Daily Sale",
    examples: ["/sale", "/sale today", "/sale 2026-10-01 2026-10-07"],
    minutes: 1,
    inputs: (args) =>
      args.length === 1 && args[0].toLowerCase() === "today" ? { today: "1" } : dateRange(args),
  },
  vms: {
    workflow: "vms.yml",
    icon: "📹",
    name: "Myntra VMS",
    examples: ["/vms", "/vms 2026-10-01 2026-10-07", "/vms erp"],
    minutes: 2,
    inputs: (args) => (args.length === 1 && args[0].toLowerCase() === "erp" ? { erp_only: "1" } : dateRange(args)),
  },
  ajiotat: {
    workflow: "ajio-tat.yml",
    icon: "📦",
    name: "AJIO TAT",
    examples: ["/ajiotat", "/ajiotat 3"],
    minutes: 2,
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && /^\d{1,2}$/.test(args[0])) return { days: args[0] };
      return "Days must be a number, e.g. /ajiotat 3";
    },
  },
  ajiospf: {
    workflow: "ajio-spf.yml",
    icon: "🧾",
    name: "AJIO SPF",
    examples: ["/ajiospf dry", "/ajiospf"],
    minutes: 13,
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && args[0].toLowerCase() === "dry") return { dry_run: "1" };
      return "Use /ajiospf or /ajiospf dry";
    },
  },
  ajioads: {
    workflow: "ajio-ads.yml",
    icon: "📣",
    name: "AJIO Ads / TOT gap",
    examples: ["/ajioads"],
    minutes: 3,
    inputs: (args) => (args.length === 0 ? {} : "Use /ajioads"),
  },
  myntraspf: {
    workflow: "myntra-spf.yml",
    icon: "🧾",
    name: "Myntra SPF",
    examples: ["/myntraspf"],
    minutes: 30,
    inputs: (args) => (args.length === 0 ? {} : "Use /myntraspf"),
  },
  returns: {
    workflow: "returns.yml",
    icon: "↩️",
    name: "Return Analysis",
    examples: ["/returns", "/returns 2026-09"],
    minutes: 25,
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && /^\d{4}-\d{2}$/.test(args[0])) return { month: args[0] };
      return "Month must be YYYY-MM, e.g. /returns 2026-09";
    },
  },
  // Pictures of a month sheet (the workbook is not changed); same workflows, so left out of
  // /status and of the measured run time (LibreOffice makes them slower than a report).
  salepic: {
    workflow: "sale.yml",
    icon: "🖼",
    name: "Sale sheet photo",
    examples: ["/salepic", "/salepic 2026-09"],
    picture: true,
    minutes: 4,
    inputs: monthArg,
  },
  vmspic: {
    workflow: "vms.yml",
    icon: "🖼",
    name: "VMS sheet photo",
    examples: ["/vmspic", "/vmspic 2026-09"],
    picture: true,
    minutes: 4,
    inputs: monthArg,
  },
};

function monthArg(args) {
  if (args.length === 0) return { snapshot: "current" };
  if (args.length === 1 && /^\d{4}-\d{2}$/.test(args[0])) return { snapshot: args[0] };
  return "Month must be YYYY-MM, e.g. 2026-09";
}

// Daily automatic runs, in IST. Cloudflare cron is UTC (IST = UTC + 5:30), so each `cron`
// below must also be listed under [triggers] in wrangler.toml. GitHub's own `schedule:`
// is not used: it skipped runs.
const SCHEDULE = [
  // 7 AM: Myntra report requests - they queue on the portal for a while. The same Myntra report
  // twice within 15 minutes is refused, so the SPF request leaves the order report to 9 AM.
  { ist: "7:00 AM daily", cron: "30 1 * * *", cmd: "sale", label: "SJIT report request", inputs: { sjit_request: "1" } },
  { ist: "7:00 AM daily", cron: "30 1 * * *", cmd: "myntraspf", label: "Myntra SPF report request",
    inputs: { request: "1" } },
  { ist: "7:00 AM on the 15th", cron: "30 1 * * *", day: 15, cmd: "returns", label: "Return analysis report request",
    inputs: { request: "1" } },
  // 9 AM: the SPF masters. AJIO tickets and replies only on Wed + Sat, the other days the master only.
  { ist: "9:00 AM daily", cron: "30 3 * * *", cmd: "myntraspf" },
  { ist: "9:00 AM Wed, Sat", cron: "30 3 * * *", weekdays: [3, 6], cmd: "ajiospf" },
  { ist: "9:00 AM other days", cron: "30 3 * * *", weekdays: [0, 1, 2, 4, 5], cmd: "ajiospf",
    label: "AJIO SPF master update", inputs: { master_only: "1" } },
  // AJIO ads / TOT gap: the 3rd, and the 5th only if the 3rd did not save the report
  { ist: "9:00 AM on the 3rd", cron: "30 3 * * *", day: 3, cmd: "ajioads" },
  { ist: "9:00 AM on the 5th", cron: "30 3 * * *", day: 5, cmd: "ajioads", label: "AJIO Ads / TOT gap (if missed)",
    inputs: { if_missing: "1" } },
  { ist: "2:00 PM on the 15th", cron: "30 8 * * *", day: 15, cmd: "returns" },
  { ist: "3:30 PM daily", cron: "0 10 * * *", cmd: "sale" },
  { ist: "5:30 PM daily", cron: "0 12 * * *", cmd: "vms" },
  { ist: "5:30 PM daily", cron: "0 12 * * *", cmd: "ajiotat" },
];

// The button menu. Every screen edits the same message. Button data:
//   "m:<screen>" open a screen · "r:<command line>" start a run · "c:<command line>" ask first
//   "st" status · "hp" help. Telegram allows 64 bytes of data per button.
const BACK = [["⬅️ Back", "m:main"]];
const SCREENS = {
  main: {
    title: "📊 <b>Bindal Exports</b>\n\nChoose a report 👇",
    rows: [
      [["📈 Sale", "m:sale"], ["📹 VMS", "m:vms"]],
      [["📦 AJIO TAT", "m:tat"], ["🧾 AJIO SPF", "m:spf"]],
      [["↩️ Returns", "m:returns"], ["🧾 Myntra SPF", "r:myntraspf"]],
      [["📣 AJIO Ads gap", "r:ajioads"]],
      [["📋 Status", "st"], ["🖥 Dashboard", "db"]],
      [["❓ Help", "hp"]],
    ],
  },
  sale: {
    title: "📈 <b>Daily Sale</b>\n<i>OMSGuru + FBF + SJIT</i>\n\nWhich days?",
    rows: [
      [["Yesterday", "r:sale"], ["Today", "r:sale today"]],
      [["Last 7 days", "r:sale @week"], ["This month", "r:sale @month"]],
      [["🖼 Sheet photo", "r:salepic"]],
      BACK,
    ],
  },
  vms: {
    title: "📹 <b>Myntra VMS</b>\n<i>Packing video check</i>\n\nWhich SLA dates?",
    rows: [
      [["Today", "r:vms"], ["Last 7 days", "r:vms @week"]],
      [["This month", "r:vms @month"], ["🖼 Sheet photo", "r:vmspic"]],
      [["🏭 ERP vs VMS", "r:vms erp"]],
      BACK,
    ],
  },
  tat: {
    title: "📦 <b>AJIO TAT</b>\n<i>Dispatch TAT + courier pickup</i>\n\nWhich order dates?",
    rows: [
      [["Today", "r:ajiotat"], ["Last 3 days", "r:ajiotat 3"]],
      [["Last 7 days", "r:ajiotat 7"]],
      BACK,
    ],
  },
  returns: {
    title: "↩️ <b>Return Analysis</b>\n<i>Myntra x3 + Ajio + Flipkart, Drive + Telegram</i>\n\n" +
      "Last month: sale 1st-last day, returns up to the 14th. Runs by itself on the 15th, 2 PM.",
    rows: [
      [["Last month", "r:returns"]],
      BACK,
    ],
  },
  spf: {
    title: "🧾 <b>AJIO SPF</b>\n<i>SPF tickets + follow-ups</i>\n\n🧪 Dry = only lists\n⚠️ LIVE = posts on AJIO",
    rows: [
      [["🧪 Dry run", "r:ajiospf dry"], ["⚠️ LIVE", "c:ajiospf"]],
      BACK,
    ],
  },
};
// Always-visible buttons under the chat box; their text arrives as a normal message.
const KEYBOARD_MENU = "📋 Menu";
const KEYBOARD_STATUS = "📊 Status";
const REPLY_KEYBOARD = {
  keyboard: [[{ text: KEYBOARD_MENU }, { text: KEYBOARD_STATUS }]],
  resize_keyboard: true,
  is_persistent: true,
};

function dateRange(args) {
  if (args.length === 0) return {};
  if (args.length > 2 || !args.every((a) => DATE.test(a))) return "Dates must be YYYY-MM-DD";
  return args.length === 2 ? { from: args[0], to: args[1] } : { from: args[0] };
}

// "@week" / "@month" from the buttons become dates, in IST. Sale reports end yesterday,
// the VMS report (by SLA date) ends today.
function expandPeriod(name, args) {
  if (args.length !== 1 || !args[0].startsWith("@")) return args;
  const ist = (d) => new Date(d.getTime() + 330 * 60000).toISOString().slice(0, 10);
  const today = new Date();
  const end = name === "sale" ? new Date(today.getTime() - 86400000) : today;
  const from = args[0] === "@week" ? ist(new Date(end.getTime() - 6 * 86400000)) : `${ist(end).slice(0, 8)}01`;
  return [from, ist(end)];
}

function helpText() {
  const lines = Object.values(COMMANDS).flatMap((c) => [
    `${c.icon} <b>${c.name}</b>`, ...c.examples.map((e) => `<code>${e}</code>`), "",
  ]);
  return [
    "❓ <b>Help</b>",
    "",
    `Tap <b>${KEYBOARD_MENU}</b> below, or type:`,
    "",
    ...lines,
    "🖥 <b>Dashboard</b>",
    "<code>/dashboard</code> — sign-in link, valid 10 min",
    "",
    "⏰ <b>Auto runs (IST)</b>",
    ...SCHEDULE.map((s) => `• ${s.label || COMMANDS[s.cmd].name}: ${s.ist}`),
  ].join("\n");
}

function allowedChat(env, chatId) {
  const allowed = `${env.ALLOWED_CHAT_IDS || ""},${env.EXTRA_CHAT_IDS || ""}`.split(",").map((s) => s.trim()).filter(Boolean);
  return allowed.includes(String(chatId));
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    const path = new URL(request.url).pathname;
    if (path.startsWith("/api/push/")) return pushDashboard(request, env, path.slice("/api/push/".length));
    if (path === "/api/myntra-session") return saveMyntraSession(request, env);
    if (path === "/api/myntra-session/read") return readMyntraSession(request, env);
    if (path === "/api/ai") return runAI(request, env);
    if (path === "/api/approval") return saveApproval(request, env);
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);

    // A tap on a menu button
    const cb = update?.callback_query;
    if (cb?.message) {
      if (!allowedChat(env, cb.message.chat.id)) {
        console.log(`ignored button ${cb.data} from chat ${cb.message.chat.id} user ${cb.from?.id}`);
        return new Response("ok");
      }
      ctx.waitUntil(handleTap(env, cb));
      return new Response("ok");
    }

    const msg = update?.message;
    let text = msg?.text?.trim();
    if (text === KEYBOARD_MENU) text = "/menu";
    if (text === KEYBOARD_STATUS) text = "/status";
    if (!text) return new Response("ok");
    if (!text.startsWith("/")) {
      // plain text: a question or a request for the ops agent (bindal/agent/chat.py)
      if (!allowedChat(env, msg.chat.id)) return new Response("ok");
      ctx.waitUntil(askAgent(env, msg.chat.id, msg.message_id, text));
      return new Response("ok");
    }
    if (!allowedChat(env, msg.chat.id)) {
      console.log(`ignored ${text} from chat ${msg.chat.id} (${msg.chat.type} ${msg.chat.title || ""}) user ${msg.from?.id} ${msg.from?.first_name || ""}`);
      return new Response("ok");
    }
    // Reply after the 200 so Telegram never retries a slow GitHub call.
    ctx.waitUntil(handle(env, msg.chat.id, msg.message_id, text));
    return new Response("ok");
  },

  // Cron trigger: start the reports scheduled for this time. With no chat_id input the
  // workflow sends its report to its default TELEGRAM_CHAT_ID, same as a scheduled run.
  async scheduled(event, env, ctx) {
    // Matched on minute + hour only: Cloudflare may hand back the day-of-week
    // field in another form ("WED,SAT" vs "4,7"), and the times are unique.
    const at = (c) => String(c).trim().split(/\s+/).slice(0, 2).join(" ");
    // `day` = IST day of the month, `weekdays` = IST weekdays (0 Sunday .. 6 Saturday): the free
    // plan allows 5 cron triggers, so monthly / weekly runs ride on daily ones
    const ist = new Date(event.scheduledTime + 5.5 * 3600 * 1000);
    const due = SCHEDULE.filter((s) => at(s.cron) === at(event.cron) && (!s.day || s.day === ist.getUTCDate())
      && (!s.weekdays || s.weekdays.includes(ist.getUTCDay())));
    ctx.waitUntil(
      Promise.all(
        due.map(async (s) => {
          const cmd = COMMANDS[s.cmd];
          try {
            const res = await dispatch(env, cmd, s.inputs || {});
            console.log(`cron ${event.cron} -> ${cmd.workflow} ${res.status}`);
            if (res.status !== 204) throw new Error(`GitHub said ${res.status}: ${(await res.text()).slice(0, 200)}`);
          } catch (e) {
            console.log(`cron ${s.cmd} failed: ${e.stack || e}`);
            const chat = String(env.ALLOWED_CHAT_IDS || "").split(",")[0].trim();
            if (chat) {
              await sendMessage(env, chat, `❌ <b>${s.label || cmd.name} did not start</b>\n<i>auto run, ${s.ist}</i>\n\n<code>${esc(e.message)}</code>`);
            }
          }
        })
      )
    );
  },
};

// The bots' latest numbers for the dashboard, stored in KV under their key.
const DASH_KEYS = new Set(["sale", "sale_today", "vms", "vms_erp", "ajio_tat", "ajio_spf",
                           "returns", "returns_year", "myntra_spf", "ajio_ads", "run_sale", "run_vms", "run_ajio_tat", "run_ajio_spf", "run_returns", "run_myntra_spf", "run_ajio_ads"]);

async function pushDashboard(request, env, key) {
  const token = env.DASHBOARD_TOKEN || "";
  if (!token || request.headers.get("Authorization") !== `Bearer ${token}`) return new Response("forbidden", { status: 403 });
  if (!DASH_KEYS.has(key)) return new Response("unknown key", { status: 404 });
  const body = await request.text();
  if (body.length > 1_000_000) return new Response("too large", { status: 413 });
  try {
    JSON.parse(body);
  } catch {
    return new Response("not JSON", { status: 400 });
  }
  await env.DASH.put(key, body);
  return new Response("stored");
}

// Myntra partner portal login cookies, one per seller account (?account=main|dbcc|fmcc; main =
// the SJIT report's account). Saved by scripts/myntra_session.py when someone pastes a new login
// (announced on Telegram) and by the bot's runs with the cookies the portal renewed (quiet).
// Same DASHBOARD_TOKEN; kept in KV under keys the dashboard never reads.
const MYNTRA_ACCOUNTS = { main: "myntra_session", dbcc: "myntra_session_dbcc", fmcc: "myntra_session_fmcc" };
const myntraAccount = (request) => new URL(request.url).searchParams.get("account") || "main";

function authorised(request, env) {
  const token = env.DASHBOARD_TOKEN || "";
  return token && request.headers.get("Authorization") === `Bearer ${token}`;
}

async function saveMyntraSession(request, env) {
  if (!authorised(request, env)) return new Response("forbidden", { status: 403 });
  const account = myntraAccount(request);
  const key = MYNTRA_ACCOUNTS[account];
  if (!key) return new Response("unknown account", { status: 404 });
  const body = await request.text();
  if (body.length > 100_000) return new Response("too large", { status: 413 });
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    return new Response("not JSON", { status: 400 });
  }
  if (!Array.isArray(data.cookies) || !data.cookies.length) return new Response("no cookies", { status: 400 });
  await env.DASH.put(key, body);
  const chat = String(env.ALLOWED_CHAT_IDS || "").split(",")[0].trim();
  if (chat && data.source !== "bot") {   // the bot saving renewed cookies after a run stays quiet
    const ends = data.expiresAt ? new Date(data.expiresAt).toLocaleString("en-IN", { timeZone: "Asia/Kolkata",
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "not given";
    await sendMessage(env, chat, `🔐 <b>Myntra ${account.toUpperCase()} session saved</b>\n<i>new partner portal login</i>\n\n` +
      `Cookies: ${data.cookies.length}\nLogin expires: ${esc(ends)}`);
  }
  return new Response("stored");
}

// ------------------------------------------------------------------ the ops agent
// Plain text in an allowed chat goes to the agent-chat workflow (bindal/agent/chat.py), which
// answers in the same chat. The agent can only read; anything it wants done it proposes with
// POST /api/approval, and only an Approve tap here starts it - one of AGENT_ACTIONS, with only
// the inputs listed there.
const AGENT_ACTIONS = {
  sale: { workflow: "sale.yml", inputs: ["from", "to", "today"] },
  vms: { workflow: "vms.yml", inputs: ["from", "to"] },
  ajiotat: { workflow: "ajio-tat.yml", inputs: ["days", "orders"] },
  ajiospf: { workflow: "ajio-spf.yml", inputs: ["dry_run"] },
  myntraspf: { workflow: "myntra-spf.yml", inputs: [] },
  invpush: { workflow: "inv-push.yml", inputs: ["dry_run"] },
  morning: { workflow: "morning.yml", inputs: ["day"] },
  sla: { workflow: "sla.yml", inputs: [] },
  labels: { workflow: "labels.yml", inputs: [] },
  stock: { workflow: "stock.yml", inputs: [] },
  evening: { workflow: "evening.yml", inputs: [] },
  weekly: { workflow: "weekly.yml", inputs: [] },
  deadstock: { workflow: "deadstock.yml", inputs: [] },
  listings: { workflow: "listings.yml", inputs: [] },
};
const APPROVAL_HOURS = 24;

async function askAgent(env, chatId, messageId, text) {
  try {
    const res = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/agent-chat.yml/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { text: text.slice(0, 1500), chat_id: String(chatId),
                                                    reply_to: String(messageId) } }),
    });
    if (res.status !== 204) throw new Error(`GitHub said ${res.status}: ${(await res.text()).slice(0, 200)}`);
    await sendMessage(env, chatId, "🤖 On it… (about 1-2 minutes)", messageId);
  } catch (e) {
    await sendMessage(env, chatId, `❌ <b>The agent did not start</b>\n<code>${esc(e.message)}</code>`, messageId);
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
  const action = AGENT_ACTIONS[req.action];
  if (!action) return Response.json({ error: `unknown action ${req.action}` }, { status: 400 });
  const inputs = {};
  for (const [k, v] of Object.entries(req.inputs || {})) {
    if (!action.inputs.includes(k)) return Response.json({ error: `input ${k} not allowed for ${req.action}` }, { status: 400 });
    inputs[k] = String(v).slice(0, 1000);
  }
  const chat = String(req.chat_id || "");
  if (!allowedChat(env, chat)) return Response.json({ error: "chat not allowed" }, { status: 400 });
  const id = crypto.randomUUID().slice(0, 12);
  await env.DASH.put(`approval:${id}`, JSON.stringify({ action: req.action, inputs, chat }),
                     { expirationTtl: APPROVAL_HOURS * 3600 });
  const args = Object.entries(inputs).map(([k, v]) => `${k} = ${v}`).join("\n") || "(no inputs)";
  await sendMessage(env, chat,
    `🙋 <b>Approve this?</b>\n\n${esc(String(req.summary || req.action).slice(0, 600))}\n\n` +
    `<b>Run:</b> ${esc(action.workflow)}\n<code>${esc(args)}</code>\n\n<i>Expires in ${APPROVAL_HOURS} hours.</i>`,
    undefined, keyboard([[["✅ Approve", `ap:${id}`], ["✖️ Reject", `rj:${id}`]]]));
  return Response.json({ id });
}

async function decideApproval(env, cb, data) {
  const id = data.slice(3);
  const key = `approval:${id}`;
  const saved = await env.DASH.get(key, "json");
  const edit = (text) => telegram(env, "editMessageText", { chat_id: cb.message.chat.id, message_id: cb.message.message_id,
                                                           text, parse_mode: "HTML", disable_web_page_preview: true });
  if (!saved) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Expired or already decided" });
    return edit(`${cb.message.text ? esc(cb.message.text) : ""}\n\n⌛ <i>Expired or already decided.</i>`);
  }
  await env.DASH.delete(key);                     // one tap decides it, a second does nothing
  const who = esc(cb.from?.first_name || "someone");
  if (data.startsWith("rj:")) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Rejected" });
    return edit(`${esc(cb.message.text || "")}\n\n✖️ <b>Rejected</b> by ${who}.`);
  }
  const action = AGENT_ACTIONS[saved.action];
  const res = await dispatch(env, action, saved.inputs);
  await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: res.status === 204 ? "Started" : "Failed" });
  return edit(`${esc(cb.message.text || "")}\n\n` + (res.status === 204
    ? `✅ <b>Approved</b> by ${who}: ${esc(action.workflow)} started.`
    : `❌ Approved by ${who}, but GitHub said ${res.status}: ${esc((await res.text()).slice(0, 200))}`));
}

// Workers AI for the ops agent (bindal/agent): one chat-completions call (OpenAI shape: messages,
// tools, tool_calls) on the account's free daily allocation (10,000 neurons). Same DASHBOARD_TOKEN;
// only these models, which the Workers Free plan serves with tool calling (20 Oct 2026).
const AI_MODELS = new Set(["@cf/openai/gpt-oss-120b", "@cf/qwen/qwen3.8-27b", "@cf/nvidia/nemotron-3-120b-a12b",
                           "@cf/openai/gpt-oss-20b"]);

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
  if (!AI_MODELS.has(model)) return new Response("model not allowed", { status: 400 });
  try {
    return Response.json(await env.AI.run(model, { ...input, stream: false }));
  } catch (e) {
    // the free allocation used up, or the model failing: the caller reports it
    return Response.json({ error: String(e && e.message || e) }, { status: 502 });
  }
}

async function readMyntraSession(request, env) {
  if (!authorised(request, env)) return new Response("forbidden", { status: 403 });
  const key = MYNTRA_ACCOUNTS[myntraAccount(request)];
  if (!key) return new Response("unknown account", { status: 404 });
  const body = await env.DASH.get(key);
  return body ? new Response(body, { headers: { "Content-Type": "application/json" } }) : new Response("none", { status: 404 });
}

// Dashboard sign-in: a link carrying a token signed with SESSION_KEY (shared with the dashboard
// worker), valid 10 minutes and usable once. Only allowed chats ever reach this.
async function sendDashboardLink(env, chatId, replyTo) {
  if (!env.SESSION_KEY || !env.DASHBOARD_PAGE) {
    return sendMessage(env, chatId, "🖥 <b>Dashboard</b>\n\nSign-in is not set up yet.", replyTo);
  }
  const claim = { kind: "login", chat: String(chatId), nonce: crypto.randomUUID(), exp: Date.now() + 10 * 60000 };
  const body = b64url(new TextEncoder().encode(JSON.stringify(claim)));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SESSION_KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body)));
  const link = `${env.DASHBOARD_PAGE.replace(/\/$/, "")}/login?t=${body}.${b64url(sig)}`;
  return sendMessage(env, chatId, "🖥 <b>Dashboard</b>\n\nTap below to sign in on this device.\n<i>Link works once, for 10 minutes.</i>", replyTo,
                     { inline_keyboard: [[{ text: "Open dashboard", url: link }]] });
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function dispatch(env, cmd, inputs) {
  return github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${cmd.workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main", inputs }),
  });
}

function keyboard(rows) {
  return { inline_keyboard: rows.map((row) => row.map(([text, data]) => ({ text, callback_data: data }))) };
}

async function handleTap(env, cb) {
  const chatId = cb.message.chat.id;
  const messageId = cb.message.message_id;
  const data = String(cb.data || "");
  const show = (text, rows) =>
    telegram(env, "editMessageText", { chat_id: chatId, message_id: messageId, text, parse_mode: "HTML",
                                       disable_web_page_preview: true, reply_markup: keyboard(rows) });

  if (data.startsWith("m:")) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
    const screen = SCREENS[data.slice(2)] || SCREENS.main;
    return show(screen.title, screen.rows);
  }
  if (data === "st") {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Checking…" });
    return show(await statusText(env), [[["🔄 Refresh", "st"], BACK[0]]]);
  }
  if (data === "db") {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
    return sendDashboardLink(env, chatId, undefined);
  }
  if (data === "hp") {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
    return show(helpText(), [BACK]);
  }
  if (data.startsWith("c:")) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
    return show("⚠️ <b>AJIO SPF · LIVE</b>\n\nRaises tickets and posts replies on AJIO.\nStart it?",
                [[["✅ Yes, start", `r:${data.slice(2)}`], ["✖️ Cancel", "m:spf"]]]);
  }
  if (data.startsWith("ap:") || data.startsWith("rj:")) return decideApproval(env, cb, data);
  if (data.startsWith("r:")) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Starting…" });
    await show(SCREENS.main.title, SCREENS.main.rows);   // menu stays ready for the next tap
    return handle(env, chatId, undefined, `/${data.slice(2)}`);
  }
  return telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
}

async function handle(env, chatId, replyTo, text) {
  const [head, ...rawArgs] = text.split(/\s+/);
  const name = head.slice(1).split("@")[0].toLowerCase();
  const reply = (t, markup) => sendMessage(env, chatId, t, replyTo, markup);

  try {
    if (name === "start") {
      await reply("👋 <b>Bindal Exports</b>\n\nUse the buttons below the chat box any time.", REPLY_KEYBOARD);
      return await reply(SCREENS.main.title, keyboard(SCREENS.main.rows));
    }
    if (name === "help") return await reply(helpText());
    if (name === "dashboard") return await sendDashboardLink(env, chatId, replyTo);
    if (name === "menu") return await reply(SCREENS.main.title, keyboard(SCREENS.main.rows));
    if (name === "status") return await reply(await statusText(env), keyboard([[["🔄 Refresh", "st"], ["📋 Menu", "m:main"]]]));

    const cmd = COMMANDS[name];
    if (!cmd) return await reply(`🤔 <b>Unknown command</b>\n\n<code>/${esc(name)}</code>\nTap <b>${KEYBOARD_MENU}</b> or send /help.`);

    const args = expandPeriod(name, rawArgs);
    const inputs = cmd.inputs(args);
    if (typeof inputs === "string") {
      return await reply(`⚠️ <b>${cmd.name}</b>\n\n${esc(inputs)}\n\nExamples:\n${cmd.examples.map((e) => `<code>${e}</code>`).join("\n")}`);
    }

    // Measured before dispatching, so the new run is not counted as "already running".
    const eta = await estimate(env, cmd, inputs);
    // The run's report goes back to the chat that asked for it.
    const res = await dispatch(env, cmd, { ...inputs, chat_id: String(chatId) });
    console.log(`dispatch ${cmd.workflow} ${JSON.stringify(inputs)} -> ${res.status}`);
    if (res.status !== 204) {
      return await reply(`❌ <b>${cmd.name} did not start</b>\n\nGitHub said ${res.status}\n<code>${esc((await res.text()).slice(0, 200))}</code>`);
    }
    const what = describe(name, inputs);
    await reply([
      `⏳ <b>${cmd.name} started</b>`,
      "",
      ...(what ? [`📅 ${esc(what)}`] : []),
      eta,
      "",
      "<i>Report will come here.</i>",
    ].join("\n"));
  } catch (e) {
    console.log(`handle /${name} failed: ${e.stack || e}`);
    await reply(`❌ <b>Error</b>\n\n<code>${esc(e.message)}</code>`);
  }
}

function describe(name, inputs) {
  const d = (s) => new Date(`${s}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
  if (inputs.today) return "Today so far";
  if (inputs.erp_only) return "ERP vs VMS · yesterday and today";
  if (inputs.snapshot) return inputs.snapshot === "current" ? "This month" : inputs.snapshot;
  if (inputs.dry_run) return "Dry run · nothing posted";
  if (name === "returns") return inputs.month ? `Month ${inputs.month}` : "Last month";
  if (name === "ajiospf") return "LIVE · posts on AJIO";
  if (inputs.days) return `Today + ${inputs.days} days back`;
  if (inputs.from) return inputs.to && inputs.to !== inputs.from ? `${d(inputs.from)} → ${d(inputs.to)}` : d(inputs.from);
  return "";
}

// How long the run will take: the median of this workflow's recent successful runs
// (command to finish, so GitHub's queue time is included), plus any run already in progress.
async function estimate(env, cmd, inputs) {
  let minutes = cmd.minutes;
  let waiting = "";
  try {
    const res = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${cmd.workflow}/runs?per_page=20`);
    if (res.ok) {
      const runs = (await res.json()).workflow_runs || [];
      const done = runs
        .filter((r) => r.status === "completed" && r.conclusion === "success")
        .map((r) => (Date.parse(r.updated_at) - Date.parse(r.created_at)) / 60000)
        .slice(0, 15)
        .sort((a, b) => a - b);
      if (!cmd.picture && done.length >= 3) minutes = done[Math.floor(done.length / 2)];
      const busy = runs.filter((r) => r.status !== "completed");
      if (busy.length) {
        const ranFor = (Date.now() - Date.parse(busy[0].created_at)) / 60000;
        const left = Math.max(1, Math.ceil(minutes - ranFor));
        minutes += left;
        waiting = `\n🚦 Waits ~${left} min for a run in progress`;
      }
    }
  } catch (e) {
    console.log(`estimate ${cmd.workflow} failed: ${e}`);
  }
  const more = inputs.from && inputs.to && inputs.from !== inputs.to ? "+" : "";
  const finish = new Date(Date.now() + minutes * 60000).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit",
  });
  return `⏱ ~${fmt(minutes)}${more} · ready by <b>${finish}</b>${waiting}`;
}

function fmt(minutes) {
  return minutes < 1.5 ? "1 min" : `${Math.round(minutes)} min`;
}

async function statusText(env) {
  const rows = await Promise.all(
    Object.entries(COMMANDS).filter(([, c]) => !c.picture).map(async ([key, c]) => {
      const head = `${c.icon} <b>${c.name}</b>`;
      const auto = SCHEDULE.filter((s) => s.cmd === key).map((s) => `⏰ ${s.ist}`);
      const res = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${c.workflow}/runs?per_page=1`);
      if (!res.ok) return [head, `⚠️ GitHub ${res.status}`].join("\n");
      const run = (await res.json()).workflow_runs?.[0];
      if (!run) return [head, "No runs yet", ...auto].join("\n");
      const when = new Date(run.created_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
      });
      const took = ((run.status === "completed" ? Date.parse(run.updated_at) : Date.now()) - Date.parse(run.created_at)) / 60000;
      const state = run.status !== "completed"
        ? `⏳ Running · ${fmt(took)}`
        : run.conclusion === "success" ? `✅ Done · ${fmt(took)}` : `❌ ${run.conclusion}`;
      return [head, state, `🕒 ${when}`, ...auto].join("\n");
    })
  );
  const now = new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit" });
  return [`📋 <b>Status</b> · ${now}`, "", rows.join("\n\n")].join("\n");
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function github(env, path, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "bindal-telegram-command-bot",
    },
  });
}

async function telegram(env, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) console.log(`${method} ${res.status}: ${await res.text()}`);
  return res;
}

function sendMessage(env, chatId, text, replyTo, markup) {
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    ...(replyTo ? { reply_parameters: { message_id: replyTo, allow_sending_without_reply: true } } : {}),
    disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });
}
