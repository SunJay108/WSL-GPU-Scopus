"""
Client for the language model server (llama.cpp llama-server, compose service
"llm").

The server holds one copy of the model and works on LLM_PARALLEL requests at
the same time (continuous batching).  A larger GPU therefore serves more
users: raise LLM_PARALLEL (and LLM_CTX, or use a larger model) in .env; this
code does not change.
"""

import json
import time
import urllib.error
import urllib.request

from core.config import LLM_REPEAT_PENALTY, LLM_TIMEOUT, LLM_TOP_K, LLM_TOP_P, LLM_URL


class LLMUnavailable(RuntimeError):
    """The language model server cannot be reached or reports an error."""


def _post(path, body, timeout):
    req = urllib.request.Request(
        LLM_URL.rstrip("/") + path, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LLMUnavailable(f"language model server error {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LLMUnavailable(f"language model server not reachable at {LLM_URL}: {exc}") from exc


def chat(messages, max_tokens=64, temperature=0.0, grammar=None, stop=None, on_text=None,
         repeat_penalty=LLM_REPEAT_PENALTY):
    """
    One chat completion; returns (text, finish_reason).

    grammar: GBNF text that constrains the reply.  on_text(text_so_far) is
    called while the reply streams; an exception raised there stops the
    generation (the server notices the closed connection).  repeat_penalty:
    1.0 for classifications (the penalty also counts the prompt's last
    tokens, e.g. the labels of few-shot examples).
    """
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": LLM_TOP_P,
        "top_k": LLM_TOP_K,
        "repeat_penalty": repeat_penalty,
        "cache_prompt": True,
    }
    if grammar:
        body["grammar"] = grammar
    if stop:
        body["stop"] = stop
    if on_text is None:
        with _post("/v1/chat/completions", body, LLM_TIMEOUT) as resp:
            choice = json.load(resp)["choices"][0]
        return (choice["message"].get("content") or "").strip(), choice.get("finish_reason")

    body["stream"] = True
    text, finish = "", None
    with _post("/v1/chat/completions", body, LLM_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            choice = json.loads(payload)["choices"][0]
            delta = (choice.get("delta") or {}).get("content") or ""
            if delta:
                text += delta
                on_text(text)
            finish = choice.get("finish_reason") or finish
    return text.strip(), finish


_context_size = None


def context_size():
    """
    Tokens of context per answer (one server slot; LLM_CTX in .env), or None
    if unknown.  Asked every time: the llm service can be restarted with
    another LLM_CTX while this app keeps running.
    """
    global _context_size
    try:
        with urllib.request.urlopen(LLM_URL.rstrip("/") + "/props", timeout=5) as resp:
            _context_size = int(json.load(resp)["default_generation_settings"]["n_ctx"]) or _context_size
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        pass
    return _context_size


def count_tokens(text):
    """Tokens of `text` for this model, or None if the server cannot say."""
    try:
        with _post("/tokenize", {"content": text}, 10) as resp:
            return len(json.load(resp)["tokens"])
    except (LLMUnavailable, ValueError, KeyError):
        return None


def healthy() -> bool:
    try:
        with urllib.request.urlopen(LLM_URL.rstrip("/") + "/health", timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_until_ready(timeout=600) -> bool:
    """Wait for the server to finish loading the model (first start)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy():
            return True
        time.sleep(2)
    return False
