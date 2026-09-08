"""LLM backend routing and the OpenAI-compatible (vLLM) transport — no
network, no database. The fake server is an httpx.MockTransport, so every
request the backend sends is assertable data: the path, the bearer header,
the system message, the JSON-mode fields, the pass-through extra body.

Covers: routing by model name (claude-* -> Anthropic, anything else -> the
configured server, and the no-server case as an OUTAGE naming the variable);
the request shape; <think> stripping and the parsed-reasoning field; the
failure split (401/403/404/429/5xx and connection errors -> Unavailable,
400 -> Refused); outage_reason() over both backends' exceptions, including
the Anthropic credit-balance 400; and the per-stage model defaults falling
through to TRACKER_LLM_MODEL with a claude-* override winning.

Run:  python3 tests/test_llm.py
"""

import importlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
import httpx
import httpx2

from pipeline import config, email_classifier, llm

# Pin the ambient config, the way test_email_ingest pins INGEST_ALL: a
# developer's .env with a vLLM configured must not change what this suite
# means (CLAUDE.md, "the suite inherits the developer's .env").
config.LLM_BASE_URL = None
config.LLM_API_KEY = None
config.LLM_EXTRA_BODY = {}


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


USER = [{"role": "user", "content": "hi"}]


def ask(client, **kw):
    args = dict(model="local-model", system="SYS", messages=USER, max_tokens=50)
    args.update(kw)
    return client.complete(**args)


# ---------------------------------------------------------------- routing

print("routing by model name")
c = llm.Client(base_url=None)
check("claude-* goes to Anthropic",
      isinstance(c.backend_for("claude-sonnet-5"), llm.AnthropicBackend))
try:
    c.backend_for("Qwen/Qwen3-8B")
    raised = None
except llm.Unavailable as e:
    raised = str(e)
check("a non-Claude name with no server is an OUTAGE naming the variable",
      raised and "TRACKER_LLM_BASE_URL" in raised and "Qwen/Qwen3-8B" in raised, raised)
c = llm.Client(base_url="http://127.0.0.1:8001/v1")
check("...and goes to the OpenAI-compatible backend once one is configured",
      isinstance(c.backend_for("Qwen/Qwen3-8B"), llm.OpenAICompatible))
check("mixed: claude-* still goes to Anthropic beside a configured server",
      isinstance(c.backend_for("claude-haiku-4-5-20251001"), llm.AnthropicBackend))
check("one backend instance per client, not one per call",
      c.backend_for("a") is c.backend_for("b"))


# ---------------------------------------------------------------- fake server

seen: list[httpx.Request] = []


def server(reply):
    def handler(request):
        seen.append(request)
        return reply(request)
    return httpx.MockTransport(handler)


def completion(content, **message_extra):
    def reply(request):
        msg = {"role": "assistant", "content": content, **message_extra}
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "model": "local-model",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]})
    return reply


def error(code, message):
    def reply(request):
        return httpx.Response(code, json={"error": {"message": message, "type": "x"}})
    return reply


def local(reply, api_key=None):
    return llm.Client(base_url="http://fake/v1", api_key=api_key, transport=server(reply))


print("OpenAI-compatible request shape")
text = ask(local(completion('{"a": 1}'), api_key="secret"), json=True)
req = seen[-1]
body = json.loads(req.content)
check("posts to <base>/chat/completions", str(req.url) == "http://fake/v1/chat/completions", req.url)
check("bearer header carries the key", req.headers.get("authorization") == "Bearer secret")
check("system prompt travels as the first message, the user turn after it",
      body["messages"][0] == {"role": "system", "content": "SYS"}
      and body["messages"][1] == USER[0], body["messages"])
check("model and max_tokens pass through", body["model"] == "local-model" and body["max_tokens"] == 50)
check("json=True asks for a JSON object at temperature 0",
      body.get("response_format") == {"type": "json_object"} and body.get("temperature") == 0, body)
check("content returned verbatim", text == '{"a": 1}', text)

text = ask(local(completion("Dear hiring manager,")))
body = json.loads(seen[-1].content)
check("json=False sends neither response_format nor temperature",
      "response_format" not in body and "temperature" not in body, body)
check("no key configured, no Authorization header", "authorization" not in seen[-1].headers)
check("free text returned as-is", text == "Dear hiring manager,")

config.LLM_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}
ask(local(completion("x")))
check("LLM_EXTRA_BODY is merged into every request",
      json.loads(seen[-1].content)["chat_template_kwargs"] == {"enable_thinking": False})
config.LLM_EXTRA_BODY = {}

ask(llm.Client(base_url="http://fake/v1/", transport=server(completion("x"))))
check("a trailing slash on the base URL does not double up",
      str(seen[-1].url) == "http://fake/v1/chat/completions", seen[-1].url)


print("reasoning models")
check("a leaked <think> block is stripped from the answer",
      ask(local(completion('<think>\nhmm, a JSON object\n</think>\n{"ok": true}'))) == '{"ok": true}')
check("a parsed `reasoning` field is ignored and content kept",
      ask(local(completion('{"ok": true}', reasoning="hmm"))) == '{"ok": true}')
check("a <think> mid-answer is not touched (only a leading block is thinking)",
      ask(local(completion('{"note": "<think>"}'))) == '{"note": "<think>"}')
check("null content reads as empty, not a crash", ask(local(completion(None))) == "")


print("failure classes")
for code, sentence in [(401, "invalid api key"), (403, "forbidden"),
                       (404, "The model `nope` does not exist"),
                       (429, "rate limit exceeded"), (503, "engine overloaded")]:
    try:
        ask(local(error(code, sentence)))
        got = None
    except llm.Unavailable as e:
        got = str(e)
    check(f"{code} is an outage carrying the server's own sentence",
          got and sentence in got and str(code) in got and "http://fake/v1" in got, got)
    check(f"...which outage_reason repeats verbatim",
          llm.outage_reason(llm.Unavailable(got)) == got)

try:
    ask(local(error(400, "This model's maximum context length is 4096 tokens")))
    got = None
except llm.Refused as e:
    got = str(e)
check("400 is the job's own fault: Refused, not Unavailable",
      got and "maximum context length" in got, got)
check("...which outage_reason does not excuse", llm.outage_reason(llm.Refused(got)) is None)


def down(request):
    raise httpx.ConnectError("connection refused", request=request)


try:
    ask(llm.Client(base_url="http://fake/v1", transport=httpx.MockTransport(down)))
    got = None
except llm.Unavailable as e:
    got = str(e)
check("a connection failure is an outage naming the server",
      got and "http://fake/v1" in got and "ConnectError" in got, got)

try:
    ask(local(lambda request: httpx.Response(200, json={"choices": []})))
    got = None
except llm.Refused as e:
    got = str(e)
check("an unreadable completion is Refused, with the parse error", got and "unreadable" in got, got)

try:
    ask(local(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>")))
    got = None
except llm.Unavailable as e:
    got = str(e)
check("a non-JSON error body still yields a readable reason",
      got and "Bad Gateway" in got and "502" in got, got)


print("Anthropic SDK exceptions (left raw, classified by outage_reason)")
_req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def api_error(cls, status, message, err_type="invalid_request_error"):
    body = {"type": "error", "error": {"type": err_type, "message": message}}
    return cls(f"Error code: {status} - {body}",
               response=httpx2.Response(status, request=_req), body=body)


cases = [
    ("credit exhausted (a 400 told apart by its message)", anthropic.BadRequestError, 400,
     "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
     "Billing to upgrade or purchase credits.", True),
    ("malformed request", anthropic.BadRequestError, 400,
     'messages: roles must alternate between "user" and "assistant"', False),
    ("bad key", anthropic.AuthenticationError, 401, "invalid x-api-key", True),
    ("model id does not exist", anthropic.NotFoundError, 404, "model: claude-nope", True),
    ("rate limited", anthropic.RateLimitError, 429, "This request would exceed your rate limit", True),
    ("server error", anthropic.InternalServerError, 500, "Internal server error", True),
]
for label, cls, status, message, outage in cases:
    reason = llm.outage_reason(api_error(cls, status, message))
    if outage:
        check(f"{label}: outage, reason is the API's sentence",
              reason and message in reason and str(status) in reason, reason)
    else:
        check(f"{label}: the job's own fault", reason is None, reason)
reason = llm.outage_reason(anthropic.APIConnectionError(request=_req))
check("network: outage", reason and reason.startswith("cannot reach the Anthropic API"), reason)
check("an unrelated exception is the job's", llm.outage_reason(ValueError("x")) is None)


print("stage model defaults")
_saved = {k: os.environ.get(k) for k in
          ("TRACKER_LLM_MODEL", "TRACKER_CLASSIFY_MODEL", "TRACKER_EXTRACT_MODEL",
           "TRACKER_JD_MODEL", "TRACKER_COVER_MODEL")}
for k in _saved:
    os.environ.pop(k, None)


def stages():
    importlib.reload(config)
    ec = importlib.reload(email_classifier)
    return ec.CLASSIFY_MODEL, ec.EXTRACT_MODEL, config.JD_MODEL, config.COVER_MODEL


check("with nothing set every stage is a claude-* model",
      all(m.startswith("claude-") for m in stages()), stages())
os.environ["TRACKER_LLM_MODEL"] = "Qwen/Qwen3-8B"
check("TRACKER_LLM_MODEL is the default for every stage",
      stages() == ("Qwen/Qwen3-8B",) * 4, stages())
os.environ["TRACKER_COVER_MODEL"] = "claude-sonnet-5"
check("a per-stage claude-* override keeps that one stage on Anthropic",
      stages() == ("Qwen/Qwen3-8B", "Qwen/Qwen3-8B", "Qwen/Qwen3-8B", "claude-sonnet-5"), stages())
for k, v in _saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
stages()

print("\nALL LLM PATHS PASS")
