"""Disposable subprocess probe for hard persona-decision timeout enforcement."""

from __future__ import annotations

import json
import multiprocessing
import sys
import threading
import time
from pathlib import Path

import humanwire.synthetic as synthetic_module
from humanwire.domain import Channel
from humanwire.persona_runtime import PersonaDecision, SyntheticIntent, SyntheticProvenance
from humanwire.synthetic import SyntheticPersona, SyntheticScenario, generate_scenario


class IgnoringDeadlineDecisionEngine:
    """Accept the cooperative contract, then deliberately ignore it forever."""

    model_identifier = "fixture/ignores-deadline"

    def decide(
        self,
        profile,
        context,
        *,
        deadline=None,
        cancellation=None,
    ) -> PersonaDecision:
        del profile, context, deadline, cancellation
        while True:
            time.sleep(60)


class IgnoringDeadlineDecisionEngineFactory:
    """Spawn-safe factory with a legacy adapter that exposes the current hang."""

    model_identifier = "fixture/ignores-deadline"

    def build(self) -> IgnoringDeadlineDecisionEngine:
        return IgnoringDeadlineDecisionEngine()

    def decide(self, profile, context, *, deadline=None, cancellation=None) -> PersonaDecision:
        return self.build().decide(
            profile,
            context,
            deadline=deadline,
            cancellation=cancellation,
        )


def _scenario() -> SyntheticScenario:
    return SyntheticScenario(
        schema_version="humanwire.synthetic/v1",
        scenario_id="timeout-probe",
        identity_seed=0,
        identity_generator_version="humanwire.synthetic-identities/v1",
        personas=[
            SyntheticPersona(
                persona_id="synthetic-manager",
                display_name="Manager Persona",
                role="Simulation manager",
                email="manager@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
            SyntheticPersona(
                persona_id="beta",
                display_name="Beta Persona",
                role="Beta owner",
                email="beta@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            ),
        ],
        provenance=SyntheticProvenance(
            proof_class="synthetic_multi_persona",
            actor_type="simulated_persona",
            identity_source="synthetic_fixture",
            transport="fake_caspian",
            human_attested=False,
            live_provider_verified=False,
        ),
    )


def main(run_root: Path) -> int:
    synthetic_module.MODEL_DECISION_TIMEOUT_SECONDS = 0.1
    synthetic_module.MODEL_DECISION_CANCELLATION_GRACE_SECONDS = 0.1
    started = time.monotonic()
    result = generate_scenario(
        _scenario(),
        run_root / "transcript.json",
        run_root,
        decision_engine=IgnoringDeadlineDecisionEngineFactory(),
    )
    payload = {
        "elapsed_seconds": time.monotonic() - started,
        "actions": [
            {"intent": action.intent.value, "content": action.content}
            for action in result.transcript.actions
        ],
        "inbound_count": len(result.inbound_envelopes),
        "live_children": [
            process.pid for process in multiprocessing.active_children() if process.is_alive()
        ],
        "live_workers": [
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("humanwire-persona")
        ],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
