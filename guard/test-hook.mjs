// Loads guard.ts exactly as Pi would, hands it a stand-in for Pi's extension API,
// then fires tool calls at it. Needs `jiti` (ships with Pi). Usage: node guard/test-hook.mjs
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const require = createRequire(import.meta.url);
let jiti;
try { jiti = require("jiti"); } catch { try { jiti = require(join(process.env.PI_DIR ?? "", "node_modules", "jiti")); } catch { console.log("SKIP: jiti not found (set PI_DIR to a folder with Pi installed)"); process.exit(0); } }
const dir = mkdtempSync(join(tmpdir(), "guard-"));
process.env.ECP_SCOPE_FILE = join(dir, "scope.json");
process.env.ECP_ACTION_LOG = join(dir, "actions.jsonl");
writeFileSync(process.env.ECP_SCOPE_FILE, JSON.stringify({ allowed: ["inventory/store.py"], forbidden: [] }));
const load = (jiti.default ?? jiti)(import.meta.url, { interopDefault: true });
const mod = load(new URL("./guard.ts", import.meta.url).pathname);
let handler; (mod.default ?? mod)({ on: (name, fn) => { if (name === "tool_call") handler = fn; } });
const CWD = "/workspace";
const call = (toolName, input) => handler({ toolName, input }, { cwd: CWD });
const checks = [
  [await call("edit", { path: "inventory/store.py" }), false, "in-scope edit passes"],
  [await call("write", { path: "inventory/api.py" }), true, "out-of-scope write blocked"],
  [await call("write", { path: ".evolution/rules.md" }), true, "protected path blocked"],
  [await call("bash", { command: "rm -rf /" }), true, "bash blocked"],
  [await call("edit", { path: "/workspace/inventory/store.py" }), false, "absolute path inside the repo is understood"],
  [await call("write", { path: "/workspace/inventory/api.py" }), true, "absolute out-of-scope write blocked"],
  [await call("write", { path: "/workspace/../etc/passwd" }), true, "escape via .. blocked"],
  [await call("write", { path: "/etc/passwd" }), true, "absolute path outside the repo blocked"],
  [await call("write", { path: "~/.bashrc" }), true, "home path blocked"],
  [await call("read", { path: "anything" }), false, "reads are fine"],
];
let bad = 0;
for (const [r, shouldBlock, label] of checks) { const blocked = !!(r && r.block); if (blocked !== shouldBlock) { bad++; console.log("FAIL", label); } else console.log("ok  ", label); }
const log = readFileSync(process.env.ECP_ACTION_LOG, "utf-8").trim().split("\n");
console.log("actions logged:", log.length); if (log.length !== 9) bad++;
process.exit(bad ? 1 : 0);
