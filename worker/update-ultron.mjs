// Fetches Ultron's worker routes (worker/agent.js of github.com/bindalbhikan/Ultron) at the release
// pinned below into src/ultron-agent.js, which src/worker.js imports. `npm run deploy` runs it first.
// (npm cannot install a package into this Google Drive folder: it extracts empty files.)
import { writeFileSync } from "node:fs";

const VERSION = "v0.1.0";
const url = `https://raw.githubusercontent.com/bindalbhikan/Ultron/${VERSION}/worker/agent.js`;

const res = await fetch(url);
if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
const code = await res.text();
if (!code.includes("export function makeAgent")) throw new Error(`${url} is not Ultron's agent.js`);
writeFileSync(new URL("./src/ultron-agent.js", import.meta.url),
              `// Ultron ${VERSION} worker/agent.js, fetched by update-ultron.mjs: do not edit here.\n${code}`);
console.log(`src/ultron-agent.js: Ultron ${VERSION}`);
