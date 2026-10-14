const DEFAULTS = { workerUrl: "https://bindal-telegram-bot.bhikan-serein.workers.dev", token: "" };
const $ = (id) => document.getElementById(id);
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short",
                                                                     hour: "2-digit", minute: "2-digit" }) : "-");

async function show() {
  const s = await chrome.storage.local.get({ ...DEFAULTS, status: "", lastSync: null, expiresAt: null, seen: [] });
  $("workerUrl").value = s.workerUrl;
  $("token").value = s.token;
  $("status").textContent = `${s.status || "Waiting for a portal login"}\nLast sent: ${when(s.lastSync)}\n` +
                            `Login expires: ${when(s.expiresAt)}\n\nPortal cookies seen (${s.seen.length}):\n` +
                            (s.seen.join("\n") || "none");
}

$("save").onclick = async () => {
  await chrome.storage.local.set({ workerUrl: $("workerUrl").value.trim(), token: $("token").value.trim() });
  await chrome.runtime.sendMessage({ type: "sync" });
  show();
};
$("sync").onclick = async () => {
  $("status").textContent = "Sending…";
  try {
    await chrome.runtime.sendMessage({ type: "sync" });
  } catch (e) {
    await chrome.storage.local.set({ status: `Background not reachable: ${e.message}` });
  }
  show();
};
show();
