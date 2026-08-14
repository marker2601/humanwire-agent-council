"""Spawn-safe persona engine factories used by synthetic runtime tests."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import PersonaDecision, SyntheticIntent


class _LegacyEngine:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def decide(self, profile, context) -> PersonaDecision:
        del profile, context
        Path(self._marker).write_text("called", encoding="utf-8")
        raise AssertionError("legacy engine must not be called")


class _FixtureEngine:
    def __init__(
        self,
        *,
        mode: str,
        value: str,
        delay_seconds: float,
        concurrency_target: int,
    ) -> None:
        self.model_identifier = "fixture/child-engine"
        self._mode = mode
        self._value = value
        self._delay_seconds = delay_seconds
        self._concurrency_target = concurrency_target
        self._condition = threading.Condition()
        self._active = 0
        self._maximum_active = 0

    def decide(
        self,
        profile,
        context,
        *,
        deadline=None,
        cancellation=None,
    ) -> PersonaDecision:
        if self._mode == "failure":
            raise ModelFailure(self._value)
        if self._mode == "cooperative_late":
            if cancellation is None:
                time.sleep(60)
            else:
                cancellation.wait(timeout=60)
            return PersonaDecision(
                time_offset_seconds=1,
                intent=SyntheticIntent.ACKNOWLEDGE,
                content="Late acknowledgement must be discarded.",
            )
        if self._mode == "partial_timeout" and profile.role == "Alpha owner":
            if cancellation is None:
                time.sleep(60)
            else:
                cancellation.wait(timeout=60)
            return PersonaDecision(
                time_offset_seconds=1,
                intent=SyntheticIntent.ACKNOWLEDGE,
                content="Late alpha acknowledgement must be discarded.",
            )
        if self._mode == "disallowed":
            return PersonaDecision(
                time_offset_seconds=1,
                intent=SyntheticIntent.CHANGE,
                content="Forged change.",
            )
        if self._mode == "unsafe":
            return PersonaDecision(
                time_offset_seconds=1,
                intent=SyntheticIntent.ACKNOWLEDGE,
                content=self._value,
            )

        observed_concurrency = 0
        if self._mode == "concurrency_probe":
            with self._condition:
                self._active += 1
                self._maximum_active = max(self._maximum_active, self._active)
                self._condition.notify_all()
                self._condition.wait_for(
                    lambda: self._maximum_active >= self._concurrency_target,
                    timeout=self._delay_seconds,
                )
                observed_concurrency = self._maximum_active
                self._active -= 1
        elif self._delay_seconds:
            time.sleep(self._delay_seconds)

        prompt = context.delivered_message.casefold()
        if self._mode in {"workflow", "tracking"}:
            if prompt.startswith("question "):
                intent = SyntheticIntent.ANSWER
                content = "Launch date is 2026-09-01."
            elif "evidence confirmation" in prompt:
                intent = SyntheticIntent.CONFIRM_EVIDENCE
                content = "Confirmed."
            else:
                intent = SyntheticIntent.ACKNOWLEDGE
                content = "Acknowledged."
        else:
            intent = SyntheticIntent.ACKNOWLEDGE
            content = (
                f"Observed concurrency {observed_concurrency}."
                if self._mode == "concurrency_probe"
                else f"Context time {context.virtual_time.isoformat()}"
                if self._mode == "zero"
                else "Acknowledged."
            )
        return PersonaDecision(
            time_offset_seconds=0 if self._mode == "zero" else 1,
            intent=intent,
            content=content,
        )


@dataclass(frozen=True)
class FixtureDecisionEngineFactory:
    """Primitive-only spec that constructs all mutable engine state in the child."""

    mode: str = "ack"
    value: str = ""
    delay_seconds: float = 0.0
    concurrency_target: int = 1
    model_identifier: str = "fixture/immediate"

    def build(self):
        if self.mode == "legacy":
            return _LegacyEngine(self.value)
        return _FixtureEngine(
            mode=self.mode,
            value=self.value,
            delay_seconds=self.delay_seconds,
            concurrency_target=self.concurrency_target,
        )
