// Runs the shared scope test cases against the JS rules. Usage: node guard/test-core.mjs
import { readFileSync } from "node:fs";
import { checkPath } from "./guard-core.mjs";
const cases = JSON.parse(readFileSync(new URL("../tests/scope_cases.json", import.meta.url), "utf-8"));
let bad = 0;
for (const c of cases) {
  const [ok] = checkPath(c.path, c.allowed, c.forbidden);
  if (ok !== c.ok) { bad++; console.log("MISMATCH", JSON.stringify(c), "got", ok); }
}
console.log(bad ? `${bad} mismatches` : `guard-core: all ${cases.length} cases match`);
process.exit(bad ? 1 : 0);
