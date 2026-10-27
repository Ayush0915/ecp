"""One self-contained HTML page per ECP: the story of a change, for showing people.
Built only from the ECP file and its evidence trail, so it can be regenerated any time."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .ecp import ECP, Evidence

CSS = """
:root{--bg:#0b1220;--card:#121b30;--ink:#e8eefb;--mut:#8ea2c6;--line:#26365a;--gf:#3cc8f5;--ok:#4cd19a;--bad:#ff6b6b;--warn:#f5b93c;--blue:#5b8cff}
@media (prefers-color-scheme: light){:root{--bg:#f6f8fc;--card:#fff;--ink:#0f1a2e;--mut:#566582;--line:#d5deee;--gf:#0b7fa6;--ok:#17855a;--bad:#c93a3a;--warn:#a86f00;--blue:#2456c9}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
.step{display:flex;gap:10px;align-items:center;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin-bottom:6px}
.pill{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}
.accepted{background:var(--ok);color:#06251a}.rolled_back,.rejected{background:var(--bad);color:#2a0606}
.blocker{color:var(--bad)}.major{color:var(--warn)}.nit{color:var(--mut)}
.gf{border-left:3px solid var(--gf)}.f{padding:6px 0;border-top:1px solid var(--line)}.f:first-child{border-top:0}
code,pre{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;max-height:340px}
.add{color:var(--ok)}.del{color:var(--bad)}.blk{color:var(--bad);font-weight:600}.mut{color:var(--mut)}ul{margin:6px 0;padding-left:20px}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _findings(fs: list[dict]) -> str:
    if not fs:
        return '<div class="mut">No findings.</div>'
    return "".join(f'<div class="f"><b class="{_e(f["severity"])}">{_e(f["severity"]).upper()}</b> · <span class="mut">{_e(f["reviewer"])}</span> · {_e(f.get("location",""))}<br>{_e(f["claim"])}'
                   + (f'<br><span class="mut">Suggested: {_e(f["suggested_fix"])}</span>' if f.get("suggested_fix") else "") + "</div>" for f in fs)


def _diff(d: str) -> str:
    out = []
    for line in d.splitlines():
        cls = "add" if line.startswith("+") and not line.startswith("+++") else "del" if line.startswith("-") and not line.startswith("---") else "mut" if line.startswith(("diff", "index", "@@", "---", "+++")) else ""
        out.append(f'<span class="{cls}">{_e(line)}</span>')
    return "\n".join(out)


def render(ecp: ECP, entries: list[dict], chain_ok: bool) -> str:
    by = lambda k: [e for e in entries if e["kind"] == k]
    request = (by("request") or [{"data": {"text": ""}}])[0]["data"]["text"]
    decision = (by("decision") or [{"data": {"outcome": "in progress", "reasons": []}}])[-1]["data"]
    parts = [f'<h1>{_e(ecp.id)} · {_e(ecp.title)}</h1><div class="sub">{_e(ecp.change_type)} · risk {_e(ecp.risk)} · plan lock <code>{_e(ecp.plan_hash or "not locked")}</code> · evidence chain {"intact" if chain_ok else "BROKEN"}</div>',
             f'<div class="card"><div class="step">Outcome</div><span class="pill {_e(decision["outcome"])}">{_e(decision["outcome"]).upper().replace("_"," ")}</span>'
             + "".join(f"<div>• {_e(r)}</div>" for r in decision.get("reasons", [])) + "</div>",
             f'<div class="card"><div class="step">Request</div>{_e(request)}</div>',
             f'<div class="card"><div class="step">1 · Plan (Elephant) → the ECP</div><b>Intent.</b> {_e(ecp.intent)}<br><b>May edit:</b> <code>{_e(", ".join(ecp.allowed_paths))}</code><br><b>Must not touch:</b> <code>{_e(", ".join(ecp.forbidden_paths) or "—")}</code>'
             + "<br><b>Success criteria</b><ul>" + "".join(f"<li>{_e(c)}</li>" for c in ecp.success_criteria) + "</ul><b>Constraints</b><ul>" + "".join(f"<li>{_e(c)}</li>" for c in ecp.constraints) + "</ul></div>"]
    for e in entries:  # chronological, so each revision sits between the review rounds it belongs to
        d = e["data"]
        if e["kind"] == "review_before":
            parts.append(f'<div class="card gf"><div class="step">2 · Review (Goldfish) · round {d["round"]}</div>{_findings(d["findings"])}</div>')
        elif e["kind"] == "revised":
            parts.append(f'<div class="card"><div class="step">Elephant revised the plan after round {d["round"]}</div><span class="mut">new allowed paths:</span> <code>{_e(", ".join(d["allowed_paths"]))}</code></div>')
    acts = by("pi_action")
    if acts:
        rows = "".join(f'<div>{"✔" if a["data"]["allowed"] else "<span class=blk>✖ BLOCKED</span>"} {_e(a["data"]["tool"])} <code>{_e(a["data"]["path"])}</code>' + ("" if a["data"]["allowed"] else f' <span class="mut">— {_e(a["data"]["reason"])}</span>') + "</div>" for a in acts)
        parts.append(f'<div class="card"><div class="step">3 · Build (Pi) · every action logged by the guard</div>{rows}</div>')
    for e in by("verify"):
        d = e["data"]
        parts.append(f'<div class="card"><div class="step">4 · Verify (tests + scope check)</div>{_e(d["summary"])}<br><span class="mut">files changed: {_e(", ".join(d["changed_files"]) or "none")}</span>'
                     + (f'<pre>{_diff(d["diff"])}</pre>' if d.get("diff") else "") + "</div>")
    for e in by("review_after"):
        parts.append(f'<div class="card gf"><div class="step">5 · Check the result (Goldfish)</div>{_findings(e["data"]["findings"])}</div>')
    tl = "".join(f'<div class="mut">{_e(e["time"])} · {_e(e["kind"])}{(" · " + _e(e["data"].get("from","")) + " → " + _e(e["data"].get("to",""))) if e["kind"]=="status" else ""}</div>' for e in entries)
    parts.append(f'<div class="card"><div class="step">Evidence trail (append-only, hash-chained)</div>{tl}</div>')
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(ecp.id)} report</title><style>{CSS}</style></head><body><main>{"".join(parts)}</main></body></html>'


def write_report(repo: Path, ecp_id: str) -> Path:
    repo = Path(repo)
    ecp = ECP.load(repo / ".evolution" / "ecps" / f"{ecp_id}.md")
    ev = Evidence(repo, ecp_id)
    out = repo / ".evolution" / "reports" / f"{ecp_id}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ecp, ev.entries(), ev.chain_intact()), encoding="utf-8")
    return out
