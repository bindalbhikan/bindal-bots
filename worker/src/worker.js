// Telegram webhook -> GitHub Actions workflow_dispatch for the Bindal report bots.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_IDS
// ALLOWED_CHAT_IDS (secret) + EXTRA_CHAT_IDS (wrangler.toml var) are comma-separated chat ids;
// messages from any other chat are ignored. Each run's report goes to the chat that sent the command.

// All bots live in one repo; each command starts one of its workflows.
const OWNER = "bindalbhikan";
const REPO = "bindal-bots";
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// Each command maps to one workflow. `inputs` turns the command's arguments
// into workflow_dispatch inputs, or returns a string error to show the user.
const COMMANDS = {
  sale: {
    workflow: "sale.yml",
    label: "Daily sale report",
    usage: "/sale today  ·  /sale  ·  /sale 2026-10-01  ·  /sale 2026-10-01 2026-10-06",
    inputs: (args) =>
      args.length === 1 && args[0].toLowerCase() === "today" ? { today: "1" } : dateRange(args),
  },
  vms: {
    workflow: "vms.yml",
    label: "Myntra VMS report",
    usage: "/vms  ·  /vms 2026-10-01  ·  /vms 2026-10-01 2026-10-06",
    inputs: dateRange,
  },
  ajiotat: {
    workflow: "ajio-tat.yml",
    label: "AJIO TAT update",
    usage: "/ajiotat  ·  /ajiotat 3  (today + 3 previous days)",
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
    inputs: (args) => {
      if (args.length === 0) return {};
      if (args.length === 1 && args[0].toLowerCase() === "dry") return { dry_run: "1" };
      return "use /ajiospf or /ajiospf dry";
    },
  },
  // Pictures of a month sheet (the workbook is not changed); same workflows, so left out of /status.
  salepic: {
    workflow: "sale.yml",
    label: "Sale sheet picture",
    usage: "/salepic  ·  /salepic 2026-09",
    picture: true,
    inputs: monthArg,
  },
  vmspic: {
    workflow: "vms.yml",
    label: "VMS sheet picture",
    usage: "/vmspic  ·  /vmspic 2026-09",
    picture: true,
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
  { ist: "3:30 PM", cron: "0 10 * * *", cmd: "sale" },
  { ist: "5:30 PM", cron: "0 12 * * *", cmd: "vms" },
  { ist: "5:30 PM", cron: "0 12 * * *", cmd: "ajiotat" },
];

function dateRange(args) {
  if (args.length === 0) return {};
  if (args.length > 2 || !args.every((a) => DATE.test(a))) return "dates must be YYYY-MM-DD";
  return args.length === 2 ? { from: args[0], to: args[1] } : { from: args[0] };
}

function helpText() {
  const lines = Object.entries(COMMANDS).map(([, c]) => `• ${c.label}\n   ${c.usage}`);
  return ["Commands:", ...lines, "", "/status — last run of each report"].join("\n");
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    const update = await request.json().catch(() => null);
    const msg = update?.message;
    const text = msg?.text?.trim();
    if (!text || !text.startsWith("/")) return new Response("ok");

    const allowed = `${env.ALLOWED_CHAT_IDS || ""},${env.EXTRA_CHAT_IDS || ""}`.split(",").map((s) => s.trim()).filter(Boolean);
    if (!allowed.includes(String(msg.chat.id))) {
      console.log(`ignored ${text} from chat ${msg.chat.id} (${msg.chat.type} ${msg.chat.title || ""}) user ${msg.from?.id} ${msg.from?.first_name || ""}`);
      return new Response("ok");
    }

    // Reply after the 200 so Telegram never retries a slow GitHub call.
    ctx.waitUntil(handle(env, msg, text));
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

async function handle(env, msg, text) {
  const [head, ...args] = text.split(/\s+/);
  const name = head.slice(1).split("@")[0].toLowerCase();
  const reply = (t) => sendMessage(env, msg.chat.id, t, msg.message_id);

  try {
    if (name === "start" || name === "help") return await reply(helpText());
    if (name === "status") return await reply(await statusText(env));

    const cmd = COMMANDS[name];
    if (!cmd) return await reply(`Unknown command /${name}\n\n${helpText()}`);

    const inputs = cmd.inputs(args);
    if (typeof inputs === "string") return await reply(`${inputs}\nUsage: ${cmd.usage}`);

    // The run's report goes back to the chat that asked for it.
    const res = await dispatch(env, cmd, { ...inputs, chat_id: String(msg.chat.id) });
    console.log(`dispatch ${cmd.workflow} -> ${res.status}`);
    if (res.status !== 204) {
      return await reply(`❌ ${cmd.label}: GitHub said ${res.status}\n${(await res.text()).slice(0, 300)}`);
    }
    await reply("⏳ Working… please wait 2–3 min");
  } catch (e) {
    console.log(`handle /${name} failed: ${e.stack || e}`);
    await reply(`❌ Error: ${e.message}`);
  }
}

async function statusText(env) {
  const rows = await Promise.all(
    Object.values(COMMANDS).filter((c) => !c.picture).map(async (c) => {
      const res = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${c.workflow}/runs?per_page=1`);
      if (!res.ok) return `• ${c.label}: GitHub ${res.status}`;
      const run = (await res.json()).workflow_runs?.[0];
      if (!run) return `• ${c.label}: no runs yet`;
      const when = new Date(run.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
      const state = run.status === "completed" ? run.conclusion : run.status;
      return `• ${c.label}: ${state} — ${when}`;
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

async function sendMessage(env, chatId, text, replyTo) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      reply_to_message_id: replyTo,
      disable_web_page_preview: true,
    }),
  });
  if (!res.ok) console.log(`sendMessage ${res.status}: ${await res.text()}`);
  return res;
}
