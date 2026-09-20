"""An LLM judge for the research-only tier, over the DESY LocalAI gateway.

Kept in its own module because it is the only part of this card that needs a
network, a credential, and a model - everything else is deterministic and
runs offline. If this file cannot do its job the card still produces its
verdict; the judged tier simply reports why it was skipped.

The gateway is OpenAI-compatible but needs its own `x-bf-vk` virtual-key
header alongside the bearer token, which is why this is hand-rolled on urllib
rather than handed to an SDK.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("LLM_BASE_URL", "https://localai.desy.de/v1").rstrip("/")


class JudgeUnavailable(RuntimeError):
    """Raised when no judge can be reached - never fatal to the card."""


def _model_name() -> str:
    """The gateway namespaces its models; accept either spelling."""
    raw = (os.environ.get("DEEPEVAL_JUDGE_MODEL") or "").strip()
    if not raw:
        raise JudgeUnavailable("DEEPEVAL_JUDGE_MODEL unset")
    return raw if "/" in raw else f"vllm/{raw}"


def chat(prompt: str, *, max_tokens: int = 1200, temperature: float = 0.0) -> dict:
    """One completion. Returns the text and what the gateway actually served."""
    key = (os.environ.get("LLM_API_KEY") or "").strip()
    if not key:
        raise JudgeUnavailable("LLM_API_KEY unset")

    body = json.dumps({
        "model": _model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    request = urllib.request.Request(f"{BASE}/chat/completions", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("x-bf-vk", key)
    request.add_header("Authorization", f"Bearer {key}")

    try:
        with urllib.request.urlopen(request, timeout=120) as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise JudgeUnavailable(f"HTTP {exc.code}: {exc.read().decode('utf-8')[:200]}") from exc
    except OSError as exc:
        raise JudgeUnavailable(str(exc)) from exc

    choice = (payload.get("choices") or [{}])[0]
    return {
        "text": (choice.get("message") or {}).get("content") or "",
        # Recorded because the gateway may route a request to a different
        # model than the one asked for - which would otherwise be invisible.
        "model_requested": _model_name(),
        "model_served": payload.get("model"),
        "usage": payload.get("usage") or {},
    }


RUBRIC = """\
You are grading whether a metadata value was faithfully carried over.

FIELD: {field}
REFERENCE (the published record, authoritative): {expected}
CANDIDATE (produced by an extraction pipeline):  {actual}

Decide whether the candidate preserves the meaning of the reference for this
field. Differences of formatting, punctuation, date precision or word order
are ACCEPTABLE. Dropping, adding or altering information that changes what a
reader would understand is NOT acceptable.

Answer with one JSON object and nothing else:
{{"score": <0.0-1.0>, "verdict": "<equivalent|degraded|wrong>", "reason": "<one sentence>"}}
"""


def judge_field(field: str, expected, actual) -> dict:
    """Score one field. Any failure is returned, never raised."""
    prompt = RUBRIC.format(field=field, expected=expected, actual=actual)
    try:
        result = chat(prompt)
    except JudgeUnavailable as exc:
        return {"skipped": True, "reason": str(exc)}

    text = (result["text"] or "").strip()
    parsed, parse_error = None, None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    else:
        parse_error = "no JSON object in reply"

    entry = {
        "skipped": False,
        "model_requested": result["model_requested"],
        "model_served": result["model_served"],
        "usage": result["usage"],
        "raw": text[:400],
    }
    if parsed is None:
        entry.update({"usable": False, "parse_error": parse_error})
        return entry
    entry.update({
        "usable": True,
        "score": parsed.get("score"),
        "verdict": parsed.get("verdict"),
        "reason": parsed.get("reason"),
    })
    return entry
