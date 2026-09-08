"""One door to every model call.

Two backends behind one `Client.complete()`:

  * Anthropic — the Claude API, the default. The request it sends is
    byte-for-byte what the stages sent before this module existed (model,
    max_tokens, system, messages, nothing else), so every measurement in
    email_classifier.py and covers.py about thinking budgets and max_tokens
    still describes what happens.
  * OpenAICompatible — any server speaking /v1/chat/completions: vLLM first,
    but also llama.cpp, Ollama, LM Studio, or a hosted gateway. Spoken with the
    `httpx` this project already depends on rather than a second vendor SDK:
    the surface is one endpoint, and a fake server is then one MockTransport
    away (tests/test_llm.py).

Routing is by model name and has one rule (`Client.backend_for`): a name
starting with `claude-` is Anthropic's; anything else goes to
config.LLM_BASE_URL. See the LLM backend section of config.py for what that
buys and how the per-stage model variables mix the two.

Failures are sorted into two kinds the worker cares about (worker._outage):
an `Unavailable` is about the ENVIRONMENT — billing, credentials, the network,
the server down, a model name the server does not serve — and costs the job
nothing; anything else is the job's own and is charged an attempt.
`outage_reason()` is the single classifier for both backends, including the
Anthropic SDK's exceptions, which are left to propagate raw so the
credit-balance rule below can read the message.
"""

from __future__ import annotations

import json as _json
import re

import anthropic
import httpx

from . import config


class Unavailable(Exception):
    """The model could not be reached or would refuse EVERY job — pause, don't
    charge. str(exc) is the one-line reason the UI prints."""


class Refused(Exception):
    """The server refused THIS request (a 4xx about its shape or size) — the
    job's own fault, charged like any other handler failure."""


# --------------------------------------------------------------------------- backends

class AnthropicBackend:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def complete(self, *, model: str, system: str, messages: list[dict],
                 max_tokens: int, json: bool = False) -> str:
        # `json` is deliberately ignored: the Claude prompts already say
        # JSON-only and the repair retry handles the rest, and adding
        # output_config here would change the calls whose behaviour was
        # measured (see the max_tokens notes in the stages).
        resp = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text")


# A reasoning model served without --reasoning-parser puts its thinking in the
# answer as <think>…</think>; with the parser it arrives in a separate
# `reasoning` field and content is clean. Strip the first shape so both work.
_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


class OpenAICompatible:
    def __init__(self, base_url: str, api_key: str | None = None,
                 timeout: float = 600.0, extra_body: dict | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._extra = dict(extra_body or {})
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=self.base_url, headers=headers,
                                  timeout=timeout, transport=transport)

    def complete(self, *, model: str, system: str, messages: list[dict],
                 max_tokens: int, json: bool = False) -> str:
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            **self._extra,
        }
        if json:
            # Structured output: the server guarantees syntactically valid
            # JSON (vLLM's xgrammar/guidance backends; llama.cpp's grammar),
            # which small models otherwise get wrong far more often than
            # Claude. Schema validation and the repair retry stay in
            # _call_json — a valid document is not yet a correct one.
            # Temperature 0 because extraction wants the modal answer, not
            # a sample; the cover letter keeps the server's default.
            body["response_format"] = {"type": "json_object"}
            body["temperature"] = 0
        try:
            r = self._http.post("/chat/completions", json=body)
        except httpx.HTTPError as err:
            raise Unavailable(f"cannot reach {self.base_url}: {err.__class__.__name__}: {err}") from err
        if r.status_code >= 400:
            detail = _error_text(r)
            if r.status_code in (401, 403, 404, 429) or r.status_code >= 500:
                # 404 is "no such model" on every OpenAI-compatible server —
                # a config error that would fail every job identically.
                raise Unavailable(f"{self.base_url} returned {r.status_code}: {detail}")
            raise Refused(f"{self.base_url} refused the request ({r.status_code}): {detail}")
        try:
            message = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as err:
            raise Refused(f"{self.base_url} returned an unreadable completion: {err}") from err
        return _THINK_RE.sub("", message.get("content") or "", count=1)


def _error_text(r: httpx.Response) -> str:
    """The server's own sentence — OpenAI-shaped {"error": {"message": …}}
    when it is, the body otherwise, trimmed to a line."""
    try:
        err = r.json().get("error")
        text = err.get("message") if isinstance(err, dict) else err
    except ValueError:
        text = None
    text = text if isinstance(text, str) and text else r.text
    return " ".join(text.split())[:300] or r.reason_phrase


# --------------------------------------------------------------------------- client

class Client:
    """What the stages are handed. Backends are built on first use, so a Claude
    only setup never touches config.LLM_BASE_URL and a local-only setup never
    needs ANTHROPIC_API_KEY."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model_default: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url if base_url is not None else config.LLM_BASE_URL
        self._api_key = api_key if api_key is not None else config.LLM_API_KEY
        self._transport = transport
        self._anthropic: AnthropicBackend | None = None
        self._openai: OpenAICompatible | None = None

    def backend_for(self, model: str):
        if model.startswith("claude-"):
            if self._anthropic is None:
                self._anthropic = AnthropicBackend()
            return self._anthropic
        if not self._base_url:
            raise Unavailable(
                f"model {model!r} is not a Claude model and TRACKER_LLM_BASE_URL is not "
                "set — point it at an OpenAI-compatible server (vLLM: "
                "`vllm serve <model> --port 8001`, then http://127.0.0.1:8001/v1)")
        if self._openai is None:
            self._openai = OpenAICompatible(
                self._base_url, self._api_key, timeout=config.LLM_TIMEOUT_SECONDS,
                extra_body=config.LLM_EXTRA_BODY, transport=self._transport)
        return self._openai

    def complete(self, *, model: str, system: str, messages: list[dict],
                 max_tokens: int, json: bool = False) -> str:
        return self.backend_for(model).complete(
            model=model, system=system, messages=messages, max_tokens=max_tokens, json=json)


# --------------------------------------------------------------------------- outages

def _anthropic_message(exc: anthropic.APIStatusError) -> str:
    """The API's own sentence, without the SDK's `Error code: 400 - {...}`
    wrapper — this is what the Settings page and the header warning print."""
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    return err.get("message") or exc.message or str(exc)


def outage_reason(exc: BaseException) -> str | None:
    """A one-line reason when `exc` is about the ENVIRONMENT, None when it is
    about the job. The OpenAI-compatible backend has already sorted its own
    failures into Unavailable / Refused. For the Anthropic SDK the split
    follows its classes: no response at all (network), or a status the API
    attributes to the account or itself — 401, 403 (permission and
    billing_error both land here), 404 (a model id that does not exist, which
    fails every job the same way), 429, 5xx/529.

    The one case the classes cannot express: an exhausted credit balance is
    reported as a 400 `invalid_request_error`, identical in status and type to
    a malformed request, so it is told apart by its message. Measured on the
    real failure (3-7 Sep 2026, request ids req_011CepNv…): the text was
    "Your credit balance is too low to access the Anthropic API. Please go to
    Plans & Billing to upgrade or purchase credits." If Anthropic rewords it,
    this rule misses, the jobs dead-letter after MAX_ATTEMPTS, and Settings
    shows them with that new text — visible, and one requeue from recovered,
    which is the whole point of the dead-letter list."""
    if isinstance(exc, Unavailable):
        return str(exc)
    if isinstance(exc, anthropic.APIConnectionError):
        return f"cannot reach the Anthropic API ({exc})"
    if isinstance(exc, anthropic.APIStatusError):
        status, msg = exc.status_code, _anthropic_message(exc)
        if status in (401, 403, 404, 429) or status >= 500:
            return f"Anthropic API {status}: {msg}"
        if status == 400 and "credit balance" in msg.lower():
            return f"Anthropic API {status}: {msg}"
    return None
