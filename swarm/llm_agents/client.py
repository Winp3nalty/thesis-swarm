"""
The one place that talks to the model (Ollama's native /api/chat).

Every model-driven agent calls call_structured_llm() through this module (client.call_structured_llm), so the
tests can replace it with a fake model in one place. The model name is chosen here: $env:SWARM_MODEL, then
model.txt, then qwen3:8b.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from swarm.core import paths
from swarm.core.state_machine import TaskRecord

OLLAMA_URL = "http://localhost:11434"
NUM_CTX = int(os.environ.get("SWARM_NUM_CTX", "8192"))
CALL_TIMEOUT_S = 900  # a cold model load plus generation can take minutes
CALL_STATS: list[dict] = []  # per-call timing and token counts, flushed into the audit log

def _read_model_file(path=None):
    """First non-empty, non-comment line of model.txt, or None."""
    p = Path(path) if path else paths.MODEL_FILE
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return None


# Model choice, highest priority first: $env:SWARM_MODEL (setup.ps1 -Model sets it),
# then model.txt, then the built-in default.
MODEL_NAME = os.environ.get("SWARM_MODEL") or _read_model_file() or "qwen3:8b"


class StructuredOutputError(ValueError):
    """The model answered, but the reply was cut off or unusable."""


class OllamaHTTPError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Ollama HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _post_chat(payload: dict) -> dict:
    """POST to Ollama's native /api/chat (kept separate so tests can replace it)."""
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_S) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise OllamaHTTPError(e.code, e.read().decode("utf-8", "replace"))


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
    temperature: float = 0.2,
):
    """
    One structured call through Ollama's native chat API.
    - 'format' is the Pydantic JSON schema, so the reply is constrained to valid JSON.
    - 'think': False switches off Qwen3's hidden reasoning, which otherwise burns
      thousands of tokens per call (slow, and can fill the context window).
    - num_ctx is set per request, so no environment variable is needed.
    """
    qwen3_hybrid = (MODEL_NAME.startswith("qwen3") and "instruct" not in MODEL_NAME
                    and "thinking" not in MODEL_NAME)
    if qwen3_hybrid:
        system_prompt += "\n\n/no_think"  # extra safety for older Ollama versions
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": response_model.model_json_schema(),
        "options": {"temperature": temperature, "num_ctx": NUM_CTX},
    }
    if MODEL_NAME.startswith("qwen3"):
        payload["think"] = False

    started = time.time()
    try:
        data = _post_chat(payload)
    except OllamaHTTPError as e:
        if "think" in payload and "think" in e.body.lower():
            payload.pop("think")  # this model/server does not support the switch
            data = _post_chat(payload)
        else:
            raise
    seconds = time.time() - started

    message = data.get("message") or {}
    content = message.get("content") or ""
    CALL_STATS.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "schema": response_model.__name__,
        "seconds": round(seconds, 1),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "thinking_chars": len(message.get("thinking") or ""),
        "done_reason": data.get("done_reason"),
    })

    if data.get("done_reason") == "length":
        head = content[:200].replace("\n", " ")
        raise StructuredOutputError(
            f"reply to {response_model.__name__} was cut off "
            f"(done_reason=length, num_ctx={NUM_CTX}, content starts: {head!r})"
        )
    return response_model.model_validate_json(content)  # ValidationError is retried by the loop


def _report_llm_stats(record: TaskRecord) -> None:
    """Write the per-call timing/token stats to the audit log and print a summary."""
    global CALL_STATS
    stats, CALL_STATS = CALL_STATS, []
    if not stats:
        return
    for s in stats:
        record.note("llm", json.dumps(s))
    print(f"[LLM STATS] calls={len(stats)} | llm_time={sum(s['seconds'] for s in stats):.1f}s"
          f" | prompt_tokens={sum(s['prompt_tokens'] for s in stats)}"
          f" | completion_tokens={sum(s['completion_tokens'] for s in stats)}"
          f" | thinking_chars={sum(s['thinking_chars'] for s in stats)}")
