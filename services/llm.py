"""Shared configuration and text completion for the two LiteLLM consumers."""
import os
from pathlib import Path

from dotenv import dotenv_values


def configuration():
    config = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    return ((config.get("AGENT_NAME") or "").strip(),
            (config.get("AGENT_API_KEY") or "").strip())


def complete_text(messages, *, max_tokens=None):
    model, api_key = configuration()
    if not model or not api_key:
        raise ValueError("Set AGENT_NAME and AGENT_API_KEY in .env.")
    import litellm

    kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
    response = litellm.completion(
        model=model, api_key=api_key, messages=messages,
        timeout=60, num_retries=0, **kwargs,
    )
    if not response.choices:
        raise ValueError("The provider returned no choices.")
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The provider returned no text.")
    return content.strip()
