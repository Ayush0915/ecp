// Same scope rules as evolve/scope.py (both tested against tests/scope_cases.json).
const ALWAYS_PROTECTED = [".git/**", ".evolution/**"];

function globToRegex(pattern) {
  let p = pattern.trim().replace(/\\/g, "/");
  if (p.endsWith("/")) p += "**";
  let out = "";
  for (let i = 0; i < p.length; ) {
    if (p.startsWith("**/", i)) { out += "(?:.*/)?"; i += 3; }
    else if (p.startsWith("**", i)) { out += ".*"; i += 2; }
    else if (p[i] === "*") { out += "[^/]*"; i += 1; }
    else if (p[i] === "?") { out += "[^/]"; i += 1; }
    else { out += p[i].replace(/[.+^${}()|[\]\\]/g, "\\$&"); i += 1; }
  }
  return new RegExp("^" + out + "$");
}

export function normalize(path) {
  let p = path.trim().replace(/\\/g, "/");
  if (p.startsWith("/") || /^[A-Za-z]:\//.test(p)) return null;
  const parts = [];
  for (const seg of p.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") { if (!parts.length) return null; parts.pop(); } else parts.push(seg);
  }
  return parts.join("/") || ".";
}

const matches = (p, pats) => pats.some((g) => globToRegex(g).test(p));

export function checkPath(path, allowed, forbidden) {
  const p = normalize(path);
  if (p === null) return [false, `'${path}' is outside the repository`];
  if (matches(p, ALWAYS_PROTECTED)) return [false, `'${p}' is a protected location (.git / .evolution)`];
  if (matches(p, forbidden)) return [false, `'${p}' is listed as forbidden in the ECP`];
  if (!matches(p, allowed)) return [false, `'${p}' is not in the ECP's allowed paths`];
  return [true, "ok"];
}
