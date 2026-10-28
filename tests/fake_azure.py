"""A stand-in for Azure OpenAI, for testing the REAL client and the REAL Pi agent without credentials or cost.

It speaks Azure's chat-completions protocol over real HTTP (deployment in the path, api-version in the query, key in a header):
  * requests WITHOUT tools (Elephant, Goldfish) get a sensible JSON answer built from what the request contains;
  * requests WITH tools (the Pi agent, through Azure's /openai/v1 route) get scripted tool calls, then a final "done".
The intelligence is fake; the plumbing (HTTP, JSON, SSE streaming, Pi's tool schema, the guard) is real.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FakeAzureServer:
    def __init__(self, repo: Path, pi_calls: list[dict], ecp_paths: list[str], drop_claims: bool = False, key: str = "test-key"):
        self.repo, self.pi_calls, self.ecp_paths, self.calls, self.key = Path(repo), pi_calls, ecp_paths, [], key
        self.drop_claims = drop_claims
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                auth_ok = self.headers.get("api-key") == fake.key or self.headers.get("authorization") == f"Bearer {fake.key}"
                if not auth_ok:
                    return self._send({"error": {"code": "401", "message": "Access denied due to invalid subscription key."}}, 401)
                if body.get("tools"):
                    return self._pi(body)
                text = fake.answer(body, self.path)
                self._send({"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

            def _pi(self, body):
                msgs = body.get("messages", [])
                fake.calls.append({"kind": "pi", "path": self.path, "model": body.get("model"), "tools": [t["function"]["name"] for t in body["tools"]]})
                already = any(m.get("role") == "tool" for m in msgs)
                if already:
                    delta, finish = {"role": "assistant", "content": "Done."}, "stop"
                else:
                    delta = {"role": "assistant", "tool_calls": [{"index": i, "id": f"call_{i}", "type": "function", "function": {"name": c["tool"], "arguments": json.dumps(c["args"])}} for i, c in enumerate(fake.pi_calls)]}
                    finish = "tool_calls"
                if body.get("stream"):
                    self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
                    base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body.get("model")}
                    for chunk in ({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                                  {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}):
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    msg = delta if finish == "stop" else {"role": "assistant", "content": None, "tool_calls": [{k: v for k, v in tc.items() if k != "index"} for tc in delta["tool_calls"]]}
                    self._send({"id": "x", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "message": msg, "finish_reason": finish}]})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    # ------------------------------------------------------------ answers for requests without tools
    def answer(self, body: dict, path: str = "") -> str:
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        self.calls.append({"kind": "llm", "path": path, "system": system[:60]})
        if "SCOUT mode" in system:
            return json.dumps({"findings": self._scout(user)})
        if "independent reviewer with no other context" in system:
            return json.dumps({"true": not self.drop_claims, "reason": "the file supports the claim" if not self.drop_claims else "not supported"})
        if "You are Elephant, the planner" in system:
            return json.dumps(self._ecp(user))
        return "[]"  # Goldfish lenses: no findings

    def _scout(self, user: str) -> list[dict]:
        out = []
        for m in re.finditer(r"^(S-\d+) \[(\w+)/\S+ (\S+)\] (\S+):(\d+) severity=\w+\n\s+(.+)$", user, re.M):
            sid, metric, rule, f, line, msg = m.groups()
            if metric != "quality" or rule != "F401":
                continue  # this fake only reports unused imports (an easy, safe, real signal)
            quote = (self.repo / f).read_text(encoding="utf-8").splitlines()[int(line) - 1].strip()
            out.append({"title": f"Remove unused import in {f}", "metric": metric, "change_type": "refactor", "signal_ids": [sid], "file": f, "quote": quote,
                        "explanation": msg, "impact": "low", "impact_reason": "tidiness only", "fix_size": "small"})
        return out

    def _ecp(self, user: str) -> dict:
        return {"title": "Remove an unused import", "change_type": "refactor", "intent": "Delete an import that nothing uses, changing no behaviour.",
                "allowed_paths": self.ecp_paths, "forbidden_paths": [], "constraints": ["Do not change behaviour"],
                "success_criteria": ["The import is gone", "All tests still pass"], "verification": [{"description": "tests pass", "command": ""}],
                "abort_conditions": ["Any test fails"], "rollback": "Discard the branch.", "risk": "low",
                "body": "## Approach\nDelete the unused import line.\n"}
