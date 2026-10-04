"""
Everything that talks to the Claude API, in one place.

Three pipeline steps (extract, consolidate, enrich) and the optional Spanish step all ask the model
a question and expect JSON that matches a schema. They share:

  * ``get_client``            - the API client, built from the key in ``.env`` (never printed),
  * ``call_structured``       - one request with structured outputs,
  * ``cached_structured_call``- "ask once, remember forever": the answer is saved under ``cache/<step>/``
                                keyed by a hash of everything that could change it, so re-running the
                                pipeline costs nothing and the saved files double as the audit trail,
  * ``usd``                   - what a call cost.

The model *reads and summarises*; code *validates and decides*. Nothing in this module trusts the
model's output: callers validate the JSON they get back.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from common import load_env, utc_now_iso, write_json

MAX_OUT_TOKENS = 16_000

# USD per million tokens (input, output). Source: Anthropic pricing page, checked 2026-10-03.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def usd(in_tok: int, out_tok: int, model: str) -> float:
    """Cost of one call in US dollars. Unknown models are priced at the highest rate, so estimates err on the safe side."""
    pin, pout = PRICES.get(model, (10.0, 50.0))
    return in_tok / 1e6 * pin + out_tok / 1e6 * pout


# "off" = {"type": "between_tools"} (cheap, predictable); "adaptive"; "omit" (set automatically if the API rejects it).
# A dict, not a plain variable, so every module that imports it sees the same setting.
THINKING = {"mode": "off"}


def get_client():
    """Build the Anthropic client from ``ANTHROPIC_API_KEY`` (environment or ``.env``). Exits with a plain message if it cannot."""
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.exit("No API key found. Copy .env.example to .env and paste your key after ANTHROPIC_API_KEY=")
    try:
        import anthropic                      # imported late: replaying saved answers needs no SDK and no key
    except ImportError:
        sys.exit("The 'anthropic' package is missing. Run:  pip install -r requirements.txt")
    return anthropic.Anthropic(api_key=key, max_retries=6)


def call_structured(client, model: str, system: str, user_msg: str, schema: dict, max_tokens: int = MAX_OUT_TOKENS):
    """One Messages API call using structured outputs (the JSON comes back as a text block).

    Sonnet 5.5 rejects forced tool use and sampling parameters, so we send neither.
    Up-front thinking is switched off by default to keep cost predictable.
    """
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user_msg}],
    )
    if max_tokens > 16_000:
        kwargs["timeout"] = 900.0             # the SDK refuses long non-streaming requests unless a timeout is given
    mode = THINKING["mode"]
    if mode == "off":
        kwargs["thinking"] = {"type": "between_tools"}
    elif mode == "adaptive":
        kwargs["thinking"] = {"type": "adaptive"}
    try:
        return client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        if e.__class__.__name__ == "BadRequestError" and "thinking" in str(e).lower() and "thinking" in kwargs:
            THINKING["mode"] = "omit"         # the API dislikes the thinking setting: run with its default from now on
            kwargs.pop("thinking")
            return client.messages.create(**kwargs)
        raise


def strictify(node):
    """Close every object in a JSON schema the way structured outputs require.

    Every object gets ``additionalProperties: false`` and *every* property becomes required, so the model can
    neither add stray keys nor leave a field out. Works in place and returns the schema for chaining.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for v in node.values():
            strictify(v)
    elif isinstance(node, list):
        for v in node:
            strictify(v)
    return node


def cache_key(system: str, schema: dict, user: str, model: str) -> str:
    """Short hash of everything that determines the answer. Change the prompt, schema, input or model and the key changes."""
    return hashlib.sha256((system + json.dumps(schema, sort_keys=True) + user + model).encode()).hexdigest()[:12]


def response_text(resp) -> str:
    """Concatenate the text blocks of an API response."""
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")


def cached_structured_call(*, cache_dir: Path, system: str, schema: dict, user: str, model: str,
                           label: str, list_key: str, refresh: bool = False, max_tokens: int = 32_000) -> dict:
    """Ask the model one structured question, or reuse the saved answer.

    Returns the saved payload ``{model, called_at, input_tokens, output_tokens, result}``.
    ``list_key`` names the list the answer must contain (for example ``"rules"``); an answer without it is
    rejected instead of cached. Pass ``refresh=True`` to ignore the saved answer and ask again.
    """
    cache = cache_dir / f"{cache_key(system, schema, user, model)}.json"
    if cache.exists() and not refresh:
        print(f"Using the saved answer from cache/{cache_dir.name}/{cache.name} (no API call).")
        return json.loads(cache.read_text(encoding="utf-8"))
    client = get_client()
    print(f"Asking {model} to {label} ...")
    resp = call_structured(client, model, system, user, schema, max_tokens=max_tokens)
    if resp.stop_reason in ("max_tokens", "refusal"):
        sys.exit(f"The answer was not usable (stop_reason={resp.stop_reason}). Run again.")
    try:
        result = json.loads(response_text(resp))
        assert isinstance(result.get(list_key), list)
    except Exception:  # noqa: BLE001
        sys.exit("The answer was not valid JSON. Run the same command again.")
    cin, cout = resp.usage.input_tokens, resp.usage.output_tokens
    print(f"tokens in/out: {cin:,}/{cout:,}   cost: ${usd(cin, cout, model):.2f}")
    payload = {"model": model, "called_at": utc_now_iso(), "input_tokens": cin, "output_tokens": cout, "result": result}
    write_json(cache, payload)
    return payload
