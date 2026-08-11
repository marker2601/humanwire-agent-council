"""Small, safe client for Featherless JSON-only completions."""

from __future__ import annotations

import json
from typing import Protocol

import httpx


class ModelFailure(RuntimeError):
    """A model failure with a safe, machine-readable reason only."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class JsonModelClient(Protocol):
    """Produces a JSON object from trusted instructions and untrusted content."""

    def complete_json(self, system: str, user: str) -> dict: ...


class FeatherlessJsonClient:
    """OpenAI-compatible Featherless client that accepts object-shaped JSON only."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        base_url: str = "https://api.featherless.ai/v1",
        max_tokens: int = 900,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=15.0)
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens

    def complete_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"UNTRUSTED_CONTENT_START\n{user}\nUNTRUSTED_CONTENT_END",
                },
            ],
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "HTTP-Referer": "https://secondsignal.vercel.app",
                    "X-Title": "HumanWire",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ModelFailure("timeout") from error
        except httpx.HTTPError as error:
            raise ModelFailure("network_error") from error

        if response.status_code >= 400:
            raise ModelFailure(f"http_{response.status_code}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ModelFailure("invalid_response") from error
        if not isinstance(content, str):
            raise ModelFailure("invalid_response")

        try:
            result = json.loads(content)
        except (json.JSONDecodeError, TypeError) as error:
            raise ModelFailure("invalid_json") from error
        if not isinstance(result, dict):
            raise ModelFailure("invalid_schema")
        return result
