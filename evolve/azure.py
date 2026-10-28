"""Azure OpenAI connection, read from environment variables (normally the project's .env file):

  AZURE_OPENAI_BASE          https://<resource>.openai.azure.com
  AZURE_OPENAI_DEPLOYMENT    the chat deployment name
  AZURE_OPENAI_API_VERSION   e.g. 2024-10-21
  AZURE_OPENAI_API_KEY       an API key ...
  AZURE_OPENAI_TENANTID / _CLIENTID / _CLIENTSECRET   ... or leave the key empty and use a service principal
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_VERSION = "2024-10-21"
SCOPE = "https://cognitiveservices.azure.com/.default"
FIELDS = ["AZURE_OPENAI_BASE", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_API_KEY",
          "AZURE_OPENAI_TENANTID", "AZURE_OPENAI_CLIENTID", "AZURE_OPENAI_CLIENTSECRET"]
_token_cache: dict = {"key": None, "value": "", "expires": 0.0}


@dataclass
class AzureSettings:
    base: str
    deployment: str
    version: str
    key: str
    tenant: str
    client: str
    secret: str

    @property
    def auth_mode(self) -> str:
        return "key" if self.key else "service principal" if (self.tenant and self.client and self.secret) else "none"

    def missing(self) -> list[str]:
        out = []
        if not self.base:
            out.append("AZURE_OPENAI_BASE")
        if not self.deployment:
            out.append("AZURE_OPENAI_DEPLOYMENT")
        if self.auth_mode == "none":
            out.append("AZURE_OPENAI_API_KEY (or AZURE_OPENAI_TENANTID, AZURE_OPENAI_CLIENTID and AZURE_OPENAI_CLIENTSECRET)")
        return out


def settings() -> AzureSettings:
    e = lambda k: os.environ.get(k, "").strip()
    base = (e("AZURE_OPENAI_BASE") or e("AZURE_OPENAI_ENDPOINT")).rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return AzureSettings(base, e("AZURE_OPENAI_DEPLOYMENT"), e("AZURE_OPENAI_API_VERSION") or DEFAULT_VERSION, e("AZURE_OPENAI_API_KEY"),
                         e("AZURE_OPENAI_TENANTID"), e("AZURE_OPENAI_CLIENTID"), e("AZURE_OPENAI_CLIENTSECRET"))


def token(s: AzureSettings) -> str:
    """A Microsoft Entra access token for the service principal (cached until a minute before it expires)."""
    ck = (s.tenant, s.client)
    if _token_cache["key"] == ck and _token_cache["expires"] - 60 > time.time():
        return _token_cache["value"]
    host = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com").rstrip("/")
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": s.client, "client_secret": s.secret, "scope": SCOPE}).encode()
    req = urllib.request.Request(f"{host}/{s.tenant}/oauth2/v2.0/token", data=body, headers={"content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error_description", "")
        except ValueError:
            msg = ""
        raise RuntimeError(f"Microsoft sign-in with the service principal failed (HTTP {e.code}): {msg.splitlines()[0][:200] if msg else 'check tenant id, client id and client secret'}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Microsoft sign-in: {e}") from None
    _token_cache.update(key=ck, value=d["access_token"], expires=time.time() + int(d.get("expires_in", 3600)))
    return _token_cache["value"]


def auth_headers(s: AzureSettings) -> dict:
    if s.auth_mode == "key":
        return {"api-key": s.key}
    if s.auth_mode == "service principal":
        return {"authorization": f"Bearer {token(s)}"}
    raise RuntimeError("No Azure credentials: set AZURE_OPENAI_API_KEY, or the tenant, client id and client secret.")


def chat_url(s: AzureSettings, deployment: str = "") -> str:
    return f"{s.base}/openai/deployments/{urllib.parse.quote(deployment or s.deployment)}/chat/completions?api-version={urllib.parse.quote(s.version)}"


def pi_provider(s: AzureSettings, deployment: str = "") -> dict:
    """A custom provider for Pi (models.json). Azure's /openai/v1 route needs no api-version and takes the deployment as the model."""
    dep = deployment or s.deployment
    prov = {"baseUrl": f"{s.base}/openai/v1", "api": "openai-completions", "apiKey": s.key or token(s),
            "compat": {"maxTokensField": "max_completion_tokens", "supportsStore": False},
            "models": [{"id": dep, "name": dep, "contextWindow": 200000, "maxTokens": 16384}]}
    if s.auth_mode == "key":
        prov["headers"] = {"api-key": s.key}
    else:
        prov["authHeader"] = True
    return {"providers": {"azure-evolve": prov}}
