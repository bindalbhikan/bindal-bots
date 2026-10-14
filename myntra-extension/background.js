// Bindal Myntra Session: whenever the Myntra partner portal login changes in this Chrome, send its
// cookies (httpOnly ones too, which page JavaScript cannot read) to the Telegram worker, where
// the sale bot picks them up. Nothing is sent while logged out or when nothing changed.

const DEFAULTS = { workerUrl: "https://bindal-telegram-bot.bhikan-serein.workers.dev", token: "" };
const DOMAIN = "myntrainfo.com";
// Akamai bot-manager and analytics cookies change all the time and are not the login
const NOT_LOGIN = /^(_abck|ak_bmsc|bm_\w+|_ga\w*|_gid|_gcl\w*)$/;

const isLogin = (c) => c.httpOnly && !NOT_LOGIN.test(c.name);

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Every portal cookie Chrome holds: all cookie stores (normal + incognito), partitioned ones too,
// and whatever Chrome would send to the portal API itself
async function portalCookies() {
  const stores = await chrome.cookies.getAllCookieStores();
  const seen = new Map();
  for (const { id } of stores) {
    const queries = [{ domain: DOMAIN }, { domain: DOMAIN, partitionKey: {} },
                     { url: "https://partnersapi.myntrainfo.com/api/" }, { url: "https://partners.myntrainfo.com/" }];
    for (const q of queries) {
      let found = [];
      try {
        found = await chrome.cookies.getAll({ ...q, storeId: id });
      } catch (e) {
        continue;   // older Chrome: no partitionKey queries
      }
      for (const c of found) seen.set(`${c.storeId}|${c.domain}|${c.path}|${c.name}|${JSON.stringify(c.partitionKey || "")}`, c);
    }
  }
  return [...seen.values()];
}

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

async function sync(force = false) {
  const s = await chrome.storage.local.get({ ...DEFAULTS, lastHash: "" });
  const all = await portalCookies();
  const login = all.filter(isLogin);
  await chrome.storage.local.set({
    seen: all.map((c) => `${c.name}${c.httpOnly ? " (httpOnly)" : ""} @${c.domain} [store ${c.storeId}` +
                         `${c.partitionKey ? ", partitioned" : ""}]`).sort(),
  });
  // "Send now" sends whatever the portal has set: the bot's own check decides if it is a login
  if (!all.length || (!login.length && !force)) {
    badge("OFF", "#888");
    await chrome.storage.local.set({ status: "Not logged in on the Myntra partner portal" });
    return;
  }
  if (!s.token) {
    badge("SET", "#d97706");
    await chrome.storage.local.set({ status: "Enter the token in this popup, then Save" });
    return;
  }
  const hash = await sha256(JSON.stringify(login.map((c) => [c.name, c.value]).sort()));
  if (!force && hash === s.lastHash) {
    badge("OK", "#16a34a");
    return;
  }
  const ends = (login.length ? login : []).map((c) => c.expirationDate).filter(Boolean);
  const body = {
    savedAt: new Date().toISOString(),
    expiresAt: ends.length ? new Date(Math.min(...ends) * 1000).toISOString() : null,
    source: "chrome-extension",
    cookies: all.map((c) => ({ name: c.name, value: c.value, domain: c.domain, path: c.path,
                               expires: c.expirationDate || -1, httpOnly: c.httpOnly, secure: c.secure })),
  };
  try {
    const res = await fetch(`${s.workerUrl.replace(/\/$/, "")}/api/myntra-session`, {
      method: "POST",
      headers: { Authorization: `Bearer ${s.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`worker said HTTP ${res.status} ${(await res.text()).slice(0, 100)}`);
    await chrome.storage.local.set({ lastHash: hash, lastSync: body.savedAt, expiresAt: body.expiresAt,
                                     status: "Session sent to the bot" });
    badge("OK", "#16a34a");
  } catch (e) {
    await chrome.storage.local.set({ status: `Not sent: ${e.message}` });
    badge("ERR", "#dc2626");
  }
}

// A login cookie changed (log in, log out, renewal): sync once things settle down
chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (cookie.domain.endsWith(DOMAIN) && isLogin(cookie)) chrome.alarms.create("settle", { delayInMinutes: 0.5 });
});
chrome.alarms.onAlarm.addListener(() => sync());
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("check", { periodInMinutes: 360 });
  sync();
});
chrome.runtime.onStartup.addListener(() => sync());
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === "sync") sync(true).then(() => reply(true));
  return true;
});
