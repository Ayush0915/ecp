/**
 * Guard: a Pi extension that enforces the ECP's file scope.
 * Pi calls this before every tool use. If a write/edit targets a file outside
 * the ECP's allowed paths, the call is BLOCKED and Pi is told why.
 * Every attempt (allowed or blocked) is appended to the action log, which the
 * control plane copies into the ECP's evidence trail.
 *
 * Settings come from two environment variables set by evolve/pi_runner.py:
 *   ECP_SCOPE_FILE  JSON {"allowed": [...], "forbidden": [...]}
 *   ECP_ACTION_LOG  JSONL file to append actions to
 */
import { appendFileSync, readFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkPath } from "./guard-core.mjs";

export default function (pi: ExtensionAPI) {
  const scopeFile = process.env.ECP_SCOPE_FILE;
  const logFile = process.env.ECP_ACTION_LOG;

  pi.on("tool_call", async (event, ctx) => {
    if (!scopeFile) return { block: true, reason: "Guard has no scope file; refusing to run unguarded." }; // fail closed
    const writing = event.toolName === "write" || event.toolName === "edit";
    // Anything that can change files other than write/edit (e.g. bash) is blocked unless explicitly enabled upstream.
    if (!writing) {
      if (event.toolName === "bash") {
        record(logFile, { tool: "bash", path: String((event.input as any).command ?? ""), allowed: false, reason: "bash is not allowed inside an ECP run" });
        return { block: true, reason: "bash is not allowed inside an ECP run" };
      }
      return undefined;
    }
    const scope = JSON.parse(readFileSync(scopeFile, "utf-8"));
    const path = String((event.input as any).path ?? "");
    const [ok, reason] = checkPath(toRepoRelative(path, (ctx as any)?.cwd ?? process.cwd()), scope.allowed ?? [], scope.forbidden ?? []);
    record(logFile, { tool: event.toolName, path, allowed: ok, reason });
    return ok ? undefined : { block: true, reason: `Blocked by the ECP scope guard: ${reason}` };
  });
}

function record(file: string | undefined, entry: object) {
  if (file) appendFileSync(file, JSON.stringify(entry) + "\n");
}

/**
 * Pi may hand us absolute paths (it resolves them against its working folder).
 * Turn one that points inside the repo into a repo-relative path; leave everything
 * else as it is so the scope check rejects it (outside the repo, "~", etc.).
 */
export function toRepoRelative(p: string, cwd: string): string {
  if (p.startsWith("~")) return p; // home-folder paths are never in scope
  if (!isAbsolute(p)) return p;
  const rel = relative(resolve(cwd), resolve(p));
  return rel.startsWith("..") || isAbsolute(rel) ? p : rel;
}
