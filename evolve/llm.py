"""The AI model connection. One tiny interface so Elephant and Goldfish don't care
which AI is behind it.

  complete(role, system, user) -> text

`role` is a label such as "elephant:draft" or "goldfish:intent_fit". Real models
ignore it; the Mock model uses it to play back a prepared scenario (so the whole
system can be tested with no API key and no cost; real use is Azure OpenAI only).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

from . import azure
from .config import Config


class LLM(Protocol):
    def complete(self, role: str, system: str, user: str, model: str | None = None) -> str: ...


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (models often wrap it in prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # try whichever bracket comes first, so a JSON array is not mistaken for the object inside it
    pairs = sorted((("{", "}"), ("[", "]")), key=lambda p: text.find(p[0]) if text.find(p[0]) != -1 else 10**9)
    for opener, closer in pairs:
        s, e = text.find(opener), text.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model reply")


def _sleep(seconds: float) -> None:  # separate function so tests can skip the wait
    time.sleep(seconds)


def _friendly(code: int, body: str) -> str:
    """Turn an AI-service error into a sentence a person can act on."""
    msg = body
    try:
        j = json.loads(body)
        msg = (j.get("error") or {}).get("message", body) if isinstance(j.get("error"), dict) else str(j.get("error", body))
    except (ValueError, AttributeError):
        pass
    hint = {401: "Azure rejected the credentials: check AZURE_OPENAI_API_KEY (or the service principal)", 403: "these credentials are not allowed to use this deployment",
            404: "the deployment was not found: check AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_BASE", 429: "rate limited: the request will be retried, or lower the load"}.get(code, "")
    return f"AI service returned HTTP {code}: {str(msg)[:300]}" + (f"  ({hint})" if hint else "")


def _post(url: str, headers: dict, payload: dict, timeout: int = 180, retries: int = 4) -> dict:
    """POST JSON. Retries on rate limits / temporary server errors, with growing waits."""
    data = json.dumps(payload).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                _sleep(min(2 ** attempt * 2, 30))
                continue
            raise RuntimeError(_friendly(e.code, body)) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                _sleep(min(2 ** attempt * 2, 30))
                continue
            raise RuntimeError(f"Could not reach the AI service: {e}") from None
    raise RuntimeError("unreachable")


class AzureOpenAILLM:
    """Azure OpenAI chat. Every role uses AZURE_OPENAI_DEPLOYMENT unless a role has its own deployment name in config."""

    def __init__(self):
        self.s = azure.settings()
        miss = self.s.missing()
        if miss:
            raise RuntimeError("Azure is not set up. Missing: " + ", ".join(miss) + ". Put them in the .env file or in Settings.")

    def complete(self, role, system, user, model=None):
        out = _post(azure.chat_url(self.s, model or ""), azure.auth_headers(self.s),
                    {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout=300)
        if "choices" not in out:
            raise RuntimeError(f"Azure answered without a result: {json.dumps(out)[:300]}")
        ch = out["choices"][0]
        content = (ch.get("message") or {}).get("content")
        if not content:
            why = ch.get("finish_reason", "")
            raise RuntimeError("Azure returned an empty answer" + (" (blocked by the content filter)" if why == "content_filter" else f" (finish reason: {why})" if why else ""))
        return content


class MockLLM:
    """Plays back a prepared scenario. See tests/fixtures/scenarios/*.json.

    scenario["elephant"]  = list of ECP drafts; call 1 = first draft, call 2 = first revision, ...
    scenario["goldfish"]  = list of review rounds; each round maps lens name -> list of findings.
                            Lenses not mentioned return "no findings".
    Every call is recorded in self.calls so tests can inspect exactly what each agent was shown.
    """

    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.calls: list[dict] = []
        self._elephant_n = 0
        self._rounds: dict[str, int] = {}

    def complete(self, role, system, user, model=None):
        self.calls.append({"role": role, "system": system, "user": user, "model": model})
        kind, _, name = role.partition(":")
        if kind == "elephant" and name == "scout":
            return json.dumps(self.scenario["scout"])
        if kind == "goldfish" and name == "claim":
            verdicts = self.scenario.get("claim_checks", {})
            for key, v in verdicts.items():  # match on a word from the claim
                if key in user:
                    return json.dumps(v)
            return json.dumps({"true": True, "reason": "supported by the file"})
        if kind == "elephant":
            drafts = self.scenario["elephant"]
            d = drafts[min(self._elephant_n, len(drafts) - 1)]
            self._elephant_n += 1
            return json.dumps(d)
        if kind == "goldfish":
            n = self._rounds.get(name, 0)
            self._rounds[name] = n + 1
            rounds = self.scenario.get("goldfish", [])
            # phase-aware: "post:" lenses live in scenario["goldfish_post"]
            if name in ("conformance", "correctness", "security", "performance"):
                rounds = self.scenario.get("goldfish_post", [])
            round_ = rounds[min(n, len(rounds) - 1)] if rounds else {}
            return json.dumps(round_.get(name, []))
        raise ValueError(f"MockLLM: unknown role {role}")


def make_llm(cfg: Config, scenario: dict | None = None) -> LLM:
    if cfg.models.provider == "mock":
        if scenario is None:
            raise RuntimeError("provider is 'mock' but no scenario was given.")
        return MockLLM(scenario)
    if cfg.models.provider == "azure":
        return AzureOpenAILLM()
    raise ValueError(f"unknown provider {cfg.models.provider}")
