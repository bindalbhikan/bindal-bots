// Telegram webhook -> GitHub Actions workflow_dispatch for the Bindal report bots.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_IDS
// ALLOWED_CHAT_IDS (secret) + EXTRA_CHAT_IDS (wrangler.toml var) are comma-separated chat ids;
// messages and menu taps from any other chat are ignored. Each run's report goes to the chat
// that asked for it.

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
    label: "Daily sale report",
    usage: "/sale today  ·  /sale  ·  /sale 2026-10-01  ·  /sale 2026-10-01 2026-10-06",
    minutes: 1,
    inputs: (args) =>
      args.length === 1 && args[0].toLowerCase() === "today" ? { today: "1" } : dateRange(args),
  },
  vms: {
    workflow: "vms.yml",
    label: "Myntra VMS report",
    usage: "/vms  ·  /vms 2026-10-01  ·  /vms 2026-10-01 2026-10-06",
    minutes: 2,
    inputs: dateRange,
  },
  ajiotat: {
    workflow: "ajio-tat.yml",
    label: "AJIO TAT update",
    usage: "/ajiotat  ·  /ajiotat 3  (today + 3 previous days)",
    minutes: 2,
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && /^\d{1,2}$/.test(args[0])) return { days: args[0] };
      return "days must be a number, e.g. /ajiotat 3";
    },
  },
  ajiospf: {
    workflow: "ajio-spf.yml",
    label: "AJIO SPF claims",
    usage: "/ajiospf  ·  /ajiospf dry  (dry = only list what would be raised / replied)",
    minutes: 13,
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && args[0].toLowerCase() === "dry") return { dry_run: "1" };
      return "use /ajiospf or /ajiospf dry";
    },
  },
  // Pictures of a month sheet (the workbook is not changed); same workflows, so left out of
  // /status and of the measured run time (LibreOffice makes them slower than a report).
  salepic: {
    workflow: "sale.yml",
    label: "Sale sheet picture",
    usage: "/salepic  ·  /salepic 2026-09",
    picture: true,
    minutes: 4,
    inputs: monthArg,
  },
  vmspic: {
    workflow: "vms.yml",
    label: "VMS sheet picture",
    usage: "/vmspic  ·  /vmspic 2026-09",
    picture: true,
    minutes: 4,
    inputs: monthArg,
  },
};

function monthArg(args) {
  if (args.length === 0) return { snapshot: "current" };
  if (args.length === 1 && /^\d{4}-\d{2}$/.test(args[0])) return { snapshot: args[0] };
  return "month must be YYYY-MM, e.g. 2026-09";
}

// Daily automatic runs, in IST. Cloudflare cron is UTC (IST = UTC + 5:30), so each `cron`
// below must also be listed under [triggers] in wrangler.toml. GitHub's own `schedule:`
// is not used: it skipped runs.
const SCHEDULE = [
  { ist: "11:30 AM Wed + Sat", cron: "0 6 * * WED,SAT", cmd: "ajiospf" },
  { ist: "3:30 PM daily", cron: "0 10 * * *", cmd: "sale" },
  { ist: "5:30 PM daily", cron: "0 12 * * *", cmd: "vms" },
  { ist: "5:30 PM daily", cron: "0 12 * * *", cmd: "ajiotat" },
];

// /menu buttons. callback_data is "<command> <args>" (Telegram allows 64 bytes).
// AJIO SPF live posts to AJIO, so its button asks for a second tap first.
const MENU = [
  [["📈 Sale (yesterday)", "sale"], ["📊 Sale today", "sale today"]],
  [["📹 Myntra VMS", "vms"], ["📦 AJIO TAT", "ajiotat"]],
  [["🧾 AJIO SPF (dry)", "ajiospf dry"], ["⚠️ AJIO SPF LIVE", "confirm ajiospf"]],
  [["🖼 Sale sheet", "salepic"], ["🖼 VMS sheet", "vmspic"]],
  [["📋 Status", "status"], ["❓ Help", "help"]],
];

function dateRange(args) {
  if (args.length === 0) return {};
  if (args.length > 2 || !args.every((a) => DATE.test(a))) return "dates must be YYYY-MM-DD";
  return args.length === 2 ? { from: args[0], to: args[1] } : { from: args[0] };
}

function helpText() {
  const lines = Object.entries(COMMANDS).map(([, c]) => `• ${c.label}\n   ${c.usage}`);
  return ["Commands:", ...lines, "", "/menu — buttons for every report", "/status — last run of each report"].join("\n");
}

function allowedChat(env, chatId) {
  const allowed = `${env.ALLOWED_CHAT_IDS || ""},${env.EXTRA_CHAT_IDS || ""}`.split(",").map((s) => s.trim()).filter(Boolean);
  return allowed.includes(String(chatId));
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);

    // A tap on a /menu button
    const cb = update?.callback_query;
    if (cb?.message) {
      if (!allowedChat(env, cb.message.chat.id)) {
        console.log(`ignored menu tap ${cb.data} from chat ${cb.message.chat.id} user ${cb.from?.id}`);
        return new Response("ok");
      }
      ctx.waitUntil(handleTap(env, cb));
      return new Response("ok");
    }

    const msg = update?.message;
    const text = msg?.text?.trim();
    if (!text || !text.startsWith("/")) return new Response("ok");
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
    const due = SCHEDULE.filter((s) => at(s.cron) === at(event.cron));
    ctx.waitUntil(
      Promise.all(
        due.map(async (s) => {
          const cmd = COMMANDS[s.cmd];
          try {
            const res = await dispatch(env, cmd, {});
            console.log(`cron ${event.cron} -> ${cmd.workflow} ${res.status}`);
            if (res.status !== 204) throw new Error(`GitHub said ${res.status}: ${(await res.text()).slice(0, 200)}`);
          } catch (e) {
            console.log(`cron ${s.cmd} failed: ${e.stack || e}`);
            const chat = String(env.ALLOWED_CHAT_IDS || "").split(",")[0].trim();
            if (chat) await sendMessage(env, chat, `❌ ${cmd.label} (auto ${s.ist}) could not start: ${e.message}`);
          }
        })
      )
    );
  },
};

function dispatch(env, cmd, inputs) {
  return github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${cmd.workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main", inputs }),
  });
}

async function handleTap(env, cb) {
  const chatId = cb.message.chat.id;
  const data = String(cb.data || "");
  await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
  if (data === "cancel") {
    return telegram(env, "editMessageText", { chat_id: chatId, message_id: cb.message.message_id, text: "Cancelled." });
  }
  if (data === "confirm ajiospf") {
    return sendMessage(env, chatId, "⚠️ AJIO SPF LIVE raises tickets and posts replies on AJIO. Start it?", undefined, {
      inline_keyboard: [[{ text: "✅ Yes, run LIVE", callback_data: "ajiospf" }, { text: "✖️ Cancel", callback_data: "cancel" }]],
    });
  }
  return handle(env, chatId, cb.message.message_id, `/${data}`, cb.from?.first_name);
}

async function handle(env, chatId, replyTo, text, who) {
  const [head, ...args] = text.split(/\s+/);
  const name = head.slice(1).split("@")[0].toLowerCase();
  const reply = (t, markup) => sendMessage(env, chatId, t, replyTo, markup);

  try {
    if (name === "start" || name === "help") return await reply(helpText());
    if (name === "menu") return await reply("Choose a report:", menuMarkup());
    if (name === "status") return await reply(await statusText(env));

    const cmd = COMMANDS[name];
    if (!cmd) return await reply(`Unknown command /${name}\n\n${helpText()}`);

    const inputs = cmd.inputs(args);
    if (typeof inputs === "string") return await reply(`${inputs}\nUsage: ${cmd.usage}`);

    // Measured before dispatching, so the new run is not counted as "already running".
    const eta = await estimate(env, cmd, inputs);
    // The run's report goes back to the chat that asked for it.
    const res = await dispatch(env, cmd, { ...inputs, chat_id: String(chatId) });
    console.log(`dispatch ${cmd.workflow} ${JSON.stringify(inputs)} -> ${res.status}`);
    if (res.status !== 204) {
      return await reply(`❌ ${cmd.label}: GitHub said ${res.status}\n${(await res.text()).slice(0, 300)}`);
    }
    const what = describe(name, inputs);
    await reply(`⏳ ${cmd.label}${what ? ` (${what})` : ""} started${who ? ` by ${who}` : ""}\n${eta}\nThe report comes here when it is done.`);
  } catch (e) {
    console.log(`handle /${name} failed: ${e.stack || e}`);
    await reply(`❌ Error: ${e.message}`);
  }
}

function describe(name, inputs) {
  if (inputs.today) return "today so far";
  if (inputs.snapshot) return inputs.snapshot === "current" ? "this month" : inputs.snapshot;
  if (inputs.dry_run) return "dry run, nothing posted";
  if (name === "ajiospf") return "LIVE";
  if (inputs.days) return `today + ${inputs.days} previous days`;
  if (inputs.from) return `${inputs.from} to ${inputs.to || (name === "sale" ? "yesterday" : "today")}`;
  return "";
}

// How long the run will take: the median of this workflow's recent successful runs
// (command to finish, so GitHub's queue time is included), plus any run already in progress.
async function estimate(env, cmd, inputs) {
  let minutes = cmd.minutes;
  let basis = "usual time";
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
      if (!cmd.picture && done.length >= 3) {
        minutes = done[Math.floor(done.length / 2)];
        basis = `median of the last ${done.length} runs`;
      }
      const busy = runs.filter((r) => r.status !== "completed");
      if (busy.length) {
        const ranFor = (Date.now() - Date.parse(busy[0].created_at)) / 60000;
        const left = Math.max(1, Math.ceil(minutes - ranFor));
        minutes += left;
        waiting = `\n${busy.length} run already in progress — this one starts after it (~${left} min).`;
      }
    }
  } catch (e) {
    console.log(`estimate ${cmd.workflow} failed: ${e}`);
  }
  if (inputs.from && inputs.to) basis += "; a back-fill of several days takes longer";
  const finish = new Date(Date.now() + minutes * 60000).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit",
  });
  return `Expected: ~${fmt(minutes)} (${basis}), ready around ${finish}${waiting}`;
}

function fmt(minutes) {
  return minutes < 1.5 ? "1 min" : `${Math.round(minutes)} min`;
}

function menuMarkup() {
  return { inline_keyboard: MENU.map((row) => row.map(([text, data]) => ({ text, callback_data: data }))) };
}

async function statusText(env) {
  const rows = await Promise.all(
    Object.entries(COMMANDS).filter(([, c]) => !c.picture).map(async ([name, c]) => {
      const auto = SCHEDULE.filter((s) => s.cmd === name).map((s) => s.ist).join(", ");
      const res = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${c.workflow}/runs?per_page=1`);
      if (!res.ok) return `• ${c.label}: GitHub ${res.status}`;
      const run = (await res.json()).workflow_runs?.[0];
      if (!run) return `• ${c.label}: no runs yet${auto ? `\n   auto: ${auto}` : ""}`;
      const when = new Date(run.created_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "numeric", minute: "2-digit",
      });
      const took = ((run.status === "completed" ? Date.parse(run.updated_at) : Date.now()) - Date.parse(run.created_at)) / 60000;
      const state = run.status === "completed"
        ? `${run.conclusion === "success" ? "✅" : "❌"} ${run.conclusion}, took ${fmt(took)}`
        : `⏳ ${run.status} for ${fmt(took)}`;
      return `• ${c.label}: ${state} — ${when}${auto ? `\n   auto: ${auto}` : ""}`;
    })
  );
  return rows.join("\n");
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
    ...(replyTo ? { reply_parameters: { message_id: replyTo, allow_sending_without_reply: true } } : {}),
    disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });
}
