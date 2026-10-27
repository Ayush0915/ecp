"""The AI model connection. One tiny interface so Elephant and Goldfish don't care
which AI is behind it.

  complete(role, system, user) -> text

`role` is a label such as "elephant:draft" or "goldfish:intent_fit". Real models
ignore it; the Mock model uses it to play back a prepared scenario (so the whole
system can be demonstrated and tested with no API key and no cost).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

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
            raise RuntimeError(f"AI service returned HTTP {e.code}: {body}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                _sleep(min(2 ** attempt * 2, 30))
                continue
            raise RuntimeError(f"Could not reach the AI service: {e}") from None
    raise RuntimeError("unreachable")


class AnthropicLLM:
    def __init__(self, default_model: str, api_key_env: str = "ANTHROPIC_API_KEY"):
        self.key = os.environ.get(api_key_env or "ANTHROPIC_API_KEY", "")
        if not self.key:
            raise RuntimeError(f"Set the {api_key_env or 'ANTHROPIC_API_KEY'} environment variable to use provider=anthropic.")
        self.default_model = default_model

    def complete(self, role, system, user, model=None):
        out = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            {"model": model or self.default_model, "max_tokens": 4096, "system": system, "messages": [{"role": "user", "content": user}]},
        )
        return "".join(b.get("text", "") for b in out["content"])


class OpenAICompatLLM:
    """Any OpenAI-style chat API (Groq, OpenRouter, local servers...)."""

    def __init__(self, default_model: str, base_url: str, api_key_env: str):
        self.key = os.environ.get(api_key_env, "")
        if not self.key:
            raise RuntimeError(f"Set the {api_key_env} environment variable to use provider=openai.")
        self.base, self.default_model = base_url.rstrip("/"), default_model

    def complete(self, role, system, user, model=None):
        out = _post(
            f"{self.base}/chat/completions",
            {"authorization": f"Bearer {self.key}"},
            {"model": model or self.default_model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        return out["choices"][0]["message"]["content"]


class AzureOpenAILLM:
    """Azure OpenAI (e.g. a GPT-5.5 deployment). The 'model' is your DEPLOYMENT name.

    Needs two environment variables (names are configurable in config.yaml):
      AZURE_OPENAI_API_KEY   and   AZURE_OPENAI_ENDPOINT  (https://<resource>.openai.azure.com)
    """

    def __init__(self, default_model: str, endpoint_env: str, key_env: str):
        self.key = os.environ.get(key_env, "")
        ep = os.environ.get(endpoint_env, "") or os.environ.get("AZURE_OPENAI_BASE_URL", "")  # BASE_URL is Pi's name for it
        if not ep and os.environ.get("AZURE_OPENAI_RESOURCE_NAME"):
            ep = f"https://{os.environ['AZURE_OPENAI_RESOURCE_NAME']}.openai.azure.com"
        self.endpoint = ep.rstrip("/")
        for suffix in ("/openai/v1", "/openai"):
            if self.endpoint.endswith(suffix):
                self.endpoint = self.endpoint[: -len(suffix)]
        if not self.key or not self.endpoint:
            raise RuntimeError(f"Set {key_env} and {endpoint_env} to use provider=azure.")
        self.default_model = default_model

    def complete(self, role, system, user, model=None):
        out = _post(
            f"{self.endpoint}/openai/v1/chat/completions",
            {"api-key": self.key},
            {"model": model or self.default_model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=300,
        )
        return out["choices"][0]["message"]["content"]


class MockLLM:
    """Plays back a prepared scenario. See demo/scenarios/*.json.

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
    p = cfg.models.provider
    if p == "mock":
        if scenario is None:
            raise RuntimeError("provider is 'mock' but no scenario was given (use --scenario).")
        return MockLLM(scenario)
    if p == "azure":
        m = cfg.models
        return AzureOpenAILLM(m.elephant, m.azure_endpoint_env, m.api_key_env or "AZURE_OPENAI_API_KEY")
    if p == "anthropic":
        return AnthropicLLM(cfg.models.elephant, cfg.models.api_key_env)
    if p == "openai":
        return OpenAICompatLLM(cfg.models.elephant, cfg.models.base_url, cfg.models.api_key_env)
    raise ValueError(f"unknown provider {p}")
