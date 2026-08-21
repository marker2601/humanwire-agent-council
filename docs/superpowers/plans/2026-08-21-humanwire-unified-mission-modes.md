# HumanWire Unified Mission Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect HumanWire's Gemini Agent Council, Demo run stakeholders, organization subjects, and real consented communication behind one durable mission workflow.

**Architecture:** Add a small mission domain and repository that bind mode, identity, participants, council output, outreach state, and public events. Keep model reasoning in the existing council runtime, keep provider authority behind a narrow transport adapter, and expose one tenant-bound DecisionOS API and workspace projection. Demo mode resolves only AI stakeholders and cannot reach a provider; connected mode resolves only activated organization humans and fails closed without an exact consented route.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, Firestore/in-memory repositories, existing HumanWire council/workflow contracts, vanilla JavaScript/CSS, pytest, Node hostile harness.

**Spec:** `docs/superpowers/specs/2026-08-20-humanwire-unified-mission-modes-design.md`

## Global Constraints

- The browser labels the modes exactly `Demo run` and `Connected organization`.
- Ordinary interface copy does not use `fabricated`; demo actors remain visibly labeled AI.
- AI specialists participate in both modes but cannot create destinations, authorize sends, approve decisions, or mark deliveries successful.
- Demo run performs zero provider calls.
- Connected organization never falls back to demo actors or simulated responses.
- Connected sends require an activated human, a server-resolved consented route, and a configured transport.
- Missing readiness produces a stable blocked reason; it never produces a fake delivery.
- All mission mutations preserve Firebase authentication, App Check, CSRF, body caps, exact tenant/workspace binding, canonical response boundaries, and fixed public errors.
- Existing organization, activation, council, gateway, studio, synthetic, and workflow behavior remains compatible.
- Do not make a live-provider claim until a consented operator-controlled route is verified end to end.

---

### Task 1: Mission domain and exact in-memory persistence

**Files:**
- Create: `src/humanwire/mission_models.py`
- Create: `src/humanwire/mission_store.py`
- Create: `tests/humanwire/test_mission_models.py`
- Create: `tests/humanwire/test_mission_store.py`
- Create: `tests/humanwire/test_mission_firestore.py`

**Interfaces:**
- Consumes: `DecisionOSContext`, `DecisionWorkspace`, and existing safe ID conventions.
- Produces: `MissionMode`, `MissionActorType`, `MissionState`, `MissionBlockedReason`, `MissionRequest`, `MissionParticipant`, `MissionEvent`, `MissionSnapshot`, `MissionRepository`, `InMemoryMissionRepository`, and `FirestoreMissionRepository`.

- [ ] **Step 1: Write failing model tests**

```python
def test_connected_mission_cannot_contain_demo_stakeholders() -> None:
    with pytest.raises(ValueError, match="participant mode"):
        MissionSnapshot(
            schema_version="humanwire.mission/v1",
            mission_id=MISSION,
            organization_id=ORG,
            workspace_id=WORKSPACE,
            mode=MissionMode.CONNECTED_ORGANIZATION,
            state=MissionState.READY,
            objective="Approve the launch decision with current evidence.",
            participants=(demo_participant(),),
            events=(),
            blocked_reason=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_demo_request_rejects_browser_supplied_subject_ids() -> None:
    with pytest.raises(ValidationError):
        MissionRequest.model_validate(
            {
                "mode": "demo_run",
                "objective": "Approve the launch decision with current evidence.",
                "subject_ids": [SUBJECT],
            }
        )
```

- [ ] **Step 2: Run model tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_models.py -v`

Expected: collection fails because `humanwire.mission_models` does not exist.

- [ ] **Step 3: Implement strict mission models**

```python
class MissionMode(StrEnum):
    DEMO_RUN = "demo_run"
    CONNECTED_ORGANIZATION = "connected_organization"


class MissionActorType(StrEnum):
    AI_SPECIALIST = "ai_specialist"
    DEMO_STAKEHOLDER = "demo_stakeholder"
    HUMAN_MEMBER = "human_member"


class MissionState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"


class MissionBlockedReason(StrEnum):
    ORGANIZATION_NOT_READY = "organization_not_ready"
    NO_ELIGIBLE_PARTICIPANT = "no_eligible_participant"
    NO_CONSENTED_ROUTE = "no_consented_route"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_STATE_UNKNOWN = "delivery_state_unknown"


class MissionRequest(_MissionModel):
    mode: MissionMode = Field(strict=False)
    objective: str = Field(min_length=12, max_length=1000)
    urgency: Literal["standard", "urgent"] = "standard"
    include_conflict: bool = True
```

The snapshot validator must enforce exact built-in values, unique IDs, tenant/workspace binding, event ordinals, aware timestamps, mode-specific actor types, blocked-reason/state consistency, and immutable mode after creation.

- [ ] **Step 4: Write failing repository tests**

```python
def test_repository_compare_and_swap_rejects_stale_version() -> None:
    repository = InMemoryMissionRepository()
    saved = repository.create(context(), workspace(), request())
    repository.update(context(), saved, expected_version=1)
    with pytest.raises(MissionUnavailable):
        repository.update(context(), saved, expected_version=1)


def test_repository_never_returns_cross_tenant_snapshot() -> None:
    repository = InMemoryMissionRepository()
    saved = repository.create(context(), workspace(), request())
    with pytest.raises(MissionUnavailable):
        repository.load(other_context(), saved.mission_id)
```

- [ ] **Step 5: Run repository tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_store.py -v`

Expected: collection fails because `humanwire.mission_store` does not exist.

- [ ] **Step 6: Implement canonical repository operations**

```python
class MissionRepository(Protocol):
    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot: ...

    def load(self, context: DecisionOSContext, mission_id: str) -> MissionSnapshot: ...

    def update(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        *,
        expected_version: int,
    ) -> MissionSnapshot: ...
```

Use detached canonical reconstruction before storage and return. Keep IDs cryptographically random and reject stale/cross-tenant updates without exposing the supplied value.

- [ ] **Step 7: Run Task 1 tests**

Add fake-Firestore tests for atomic create, compare-and-swap update, restart load, malformed stored shapes, duplicate current versions, timestamp normalization, cross-tenant reads, and provider exceptions. Firestore document paths are organization/workspace/mission bound, and a transaction must compare the stored integer version before publishing one replacement snapshot.

Run: `python -m pytest tests/humanwire/test_mission_models.py tests/humanwire/test_mission_store.py tests/humanwire/test_mission_firestore.py -q`

Expected: all pass.

- [ ] **Step 8: Commit Task 1**

```powershell
git add -- src/humanwire/mission_models.py src/humanwire/mission_store.py tests/humanwire/test_mission_models.py tests/humanwire/test_mission_store.py tests/humanwire/test_mission_firestore.py
git commit -m "feat: add durable HumanWire missions"
```

---

### Task 2: Participant resolution and transport authority

**Files:**
- Create: `src/humanwire/mission_participants.py`
- Create: `src/humanwire/mission_transport.py`
- Create: `tests/humanwire/test_mission_participants.py`
- Create: `tests/humanwire/test_mission_transport.py`

**Interfaces:**
- Consumes: `OrganizationGraphRepository.load_graph`, `OrganizationSubject`, `SubjectLifecycle`, `MissionMode`, and `MissionParticipant`.
- Produces: `MissionParticipantResolver`, `MissionRouteRegistry`, `MissionTransport`, `PreparedMissionOutreach`, `MissionInboundResponse`, `ConnectedMissionDispatcher`, and `CaspianMissionTransport`.

- [ ] **Step 1: Write failing participant-resolution tests**

```python
def test_demo_resolution_returns_only_ai_specialists_and_demo_stakeholders() -> None:
    participants = resolver().resolve(context(), request(mode="demo_run"))
    assert {item.actor_type for item in participants} == {
        MissionActorType.AI_SPECIALIST,
        MissionActorType.DEMO_STAKEHOLDER,
    }


def test_connected_resolution_uses_only_active_humans_and_ai_specialists() -> None:
    participants = resolver(graph=graph_with_active_and_directory_people()).resolve(
        context(), request(mode="connected_organization")
    )
    assert {item.actor_type for item in participants} == {
        MissionActorType.AI_SPECIALIST,
        MissionActorType.HUMAN_MEMBER,
    }
    assert DIRECTORY_ONLY not in {item.subject_id for item in participants}
```

- [ ] **Step 2: Run participant tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_participants.py -v`

Expected: collection fails because `humanwire.mission_participants` does not exist.

- [ ] **Step 3: Implement mode-specific resolution**

```python
class MissionParticipantResolver:
    def resolve(
        self,
        context: DecisionOSContext,
        request: MissionRequest,
    ) -> tuple[MissionParticipant, ...]:
        if request.mode is MissionMode.DEMO_RUN:
            return self._demo_catalog()
        graph = self._graph_repository.load_graph(context)
        return self._connected_participants(graph)
```

Connected participants must be exact canonical graph records. Include active `AI_SPECIALIST` subjects and active human subjects with `member_uid`; exclude external, directory-only, invited, needs-review, suspended, and cross-workspace authority. Never put `member_uid`, source identity, or contact values in `MissionParticipant`.

- [ ] **Step 4: Write failing transport tests**

```python
def test_demo_dispatch_performs_zero_transport_calls() -> None:
    transport = RecordingMissionTransport()
    dispatcher = ConnectedMissionDispatcher(routes=routes(), transport=transport)
    outcome = dispatcher.dispatch(demo_snapshot(), participant=demo_person())
    assert outcome.code == "demo_inert"
    assert transport.calls == []


def test_connected_dispatch_requires_exact_consented_route() -> None:
    dispatcher = ConnectedMissionDispatcher(routes=empty_routes(), transport=transport())
    outcome = dispatcher.dispatch(connected_snapshot(), participant=active_human())
    assert outcome.code == "no_consented_route"
    assert transport.calls == []


def test_connected_dispatch_records_real_adapter_result() -> None:
    transport = RecordingMissionTransport(delivery_id="delivery_01")
    outcome = ConnectedMissionDispatcher(routes=routes(), transport=transport).dispatch(
        connected_snapshot(), participant=active_human()
    )
    assert outcome.code == "delivered"
    assert outcome.delivery_id == "delivery_01"
    assert len(transport.calls) == 1
```

- [ ] **Step 5: Run transport tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_transport.py -v`

Expected: collection fails because `humanwire.mission_transport` does not exist.

- [ ] **Step 6: Implement fail-closed dispatch**

```python
class MissionTransport(Protocol):
    route_id: str

    def deliver(self, outreach: PreparedMissionOutreach) -> MissionDeliveryReceipt: ...


class MissionRouteRegistry(Protocol):
    def consented_routes(
        self, context: DecisionOSContext, subject_id: str
    ) -> tuple[MissionRoute, ...]: ...
```

`ConnectedMissionDispatcher` must validate mode, actor type, organization, route ownership, consent, provider readiness, and exact return type before publishing delivery state. Transport exceptions yield `delivery_state_unknown` and retain no private traceback value in public frames.

`CaspianMissionTransport` must convert only a server-prepared outreach into the existing `DeliveryInstruction` vocabulary and dispatch it through the existing `CaspianGateway`; it must not instantiate a second provider handler. Its inbound adapter accepts the existing gateway's normalized `IncomingMessage`, resolves the saved mission/participant correlation, and returns a canonical `MissionInboundResponse` without exposing provider IDs or addresses.

- [ ] **Step 7: Run Task 2 tests**

Run: `python -m pytest tests/humanwire/test_mission_participants.py tests/humanwire/test_mission_transport.py -q`

Expected: all pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add -- src/humanwire/mission_participants.py src/humanwire/mission_transport.py tests/humanwire/test_mission_participants.py tests/humanwire/test_mission_transport.py
git commit -m "feat: resolve mission participants and routes"
```

---

### Task 3: Mission coordinator and shared event projection

**Files:**
- Create: `src/humanwire/mission_service.py`
- Create: `src/humanwire/mission_projection.py`
- Create: `tests/humanwire/test_mission_service.py`
- Create: `tests/humanwire/test_mission_projection.py`

**Interfaces:**
- Consumes: `MissionRepository`, `MissionParticipantResolver`, `ConnectedMissionDispatcher`, and `DecisionOSCouncilRuntime.run`.
- Produces: `MissionService.create`, `MissionService.run`, `MissionService.load`, and `build_mission_projection`.

- [ ] **Step 1: Write failing service tests**

```python
def test_demo_run_executes_council_and_never_dispatches_provider() -> None:
    service, council, dispatcher = service_fixture()
    created = service.create(context(), workspace(), request(mode="demo_run"))
    completed = service.run(context(), workspace(), created.mission_id)
    assert completed.state is MissionState.COMPLETE
    assert council.calls == [created.objective]
    assert dispatcher.calls == []


def test_connected_run_blocks_before_council_when_provider_is_not_ready() -> None:
    service, council, _ = service_fixture(provider_ready=False)
    created = service.create(
        context(), workspace(), request(mode="connected_organization")
    )
    blocked = service.run(context(), workspace(), created.mission_id)
    assert blocked.state is MissionState.BLOCKED
    assert blocked.blocked_reason is MissionBlockedReason.PROVIDER_NOT_CONFIGURED
    assert council.calls == []


def test_authenticated_reply_updates_the_exact_connected_assignment() -> None:
    service, _, _ = service_fixture(provider_ready=True)
    running = start_connected_mission(service)
    updated = service.record_response(
        context(),
        workspace(),
        MissionInboundResponse(
            mission_id=running.mission_id,
            participant_id=HUMAN_PARTICIPANT,
            response_kind="fact",
            safe_summary="Launch dependency confirmed.",
            received_at=NOW,
        ),
    )
    assert updated.events[-1].kind == "response.recorded"
    assert updated.events[-1].participant_id == HUMAN_PARTICIPANT
```

- [ ] **Step 2: Run service tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_service.py -v`

Expected: collection fails because `humanwire.mission_service` does not exist.

- [ ] **Step 3: Implement deterministic coordinator order**

```python
class MissionService:
    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot: ...

    def run(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
        *,
        cancellation: threading.Event,
        on_event: Callable[[MissionEvent], None] | None = None,
    ) -> MissionSnapshot: ...

    def record_response(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        response: MissionInboundResponse,
    ) -> MissionSnapshot: ...
```

Order: load and bind mission; resolve participants; validate readiness; publish `mission.started`; run council and project its safe stage events; in connected mode prepare and dispatch the minimum required outreach; publish exact delivery status; wait in the durable `awaiting_response` state; ingest authenticated normalized responses through `record_response`; then continue synthesis, approval gating, availability, and meeting-package projection through the existing workflow state. Every response must match the saved mission, participant, active route class, and outstanding assignment before it can affect evidence.

- [ ] **Step 4: Write failing projection tests**

```python
def test_projection_contains_mode_and_safe_actor_labels() -> None:
    projection = build_mission_projection(completed_demo_snapshot())
    assert projection.mode_label == "Demo run"
    assert all("fabricated" not in item.display_name.casefold() for item in projection.participants)
    assert {item.actor_label for item in projection.participants} >= {
        "AI specialist",
        "AI stakeholder",
    }


def test_projection_rejects_private_route_values() -> None:
    poisoned = completed_connected_snapshot().model_copy(
        update={"events": (event(summary="alice@example.invalid"),)}
    )
    with pytest.raises(MissionProjectionUnavailable):
        build_mission_projection(poisoned)
```

- [ ] **Step 5: Run projection tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_mission_projection.py -v`

Expected: collection fails because `humanwire.mission_projection` does not exist.

- [ ] **Step 6: Implement allowlisted browser projection**

The projection exposes mission ID, objective, mode label, state, stage, safe participant labels, ordered events, next action, council recommendation summary, delivery status code, and blocked reason. Reject unknown event kinds, raw addresses, provider payload keys, tokens, conversation IDs, source identities, member UIDs, and traceback markers.

- [ ] **Step 7: Run Task 3 tests**

Run: `python -m pytest tests/humanwire/test_mission_service.py tests/humanwire/test_mission_projection.py -q`

Expected: all pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add -- src/humanwire/mission_service.py src/humanwire/mission_projection.py tests/humanwire/test_mission_service.py tests/humanwire/test_mission_projection.py
git commit -m "feat: coordinate HumanWire missions"
```

---

### Task 4: Tenant-bound DecisionOS mission API

**Files:**
- Modify: `src/humanwire/decisionos_app.py`
- Create: `tests/humanwire/test_decisionos_mission_app.py`

**Interfaces:**
- Consumes: `MissionService`, `MissionRequest`, and `build_mission_projection`.
- Produces: optional `mission_features_enabled`/`mission_service` dependencies and exact create/load/run routes.

- [ ] **Step 1: Write failing route tests**

```python
def test_create_demo_mission_returns_canonical_projection(client) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/workspaces/{WORKSPACE}/missions",
        json={
            "mode": "demo_run",
            "objective": "Approve the launch decision with current evidence.",
            "urgency": "standard",
            "include_conflict": True,
        },
        headers=mutation_headers(),
    )
    assert response.status_code == 201
    assert response.json()["mission"]["mode_label"] == "Demo run"


def test_connected_route_never_accepts_browser_contact_destination(client) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/workspaces/{WORKSPACE}/missions",
        json={
            "mode": "connected_organization",
            "objective": "Approve the launch decision with current evidence.",
            "recipient": "alice@example.invalid",
        },
        headers=mutation_headers(),
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
```

- [ ] **Step 2: Run route tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_decisionos_mission_app.py -v`

Expected: create route returns 404.

- [ ] **Step 3: Add optional mission dependencies and exact profiles**

Add `mission_features_enabled: bool = False` and `mission_service: MissionService | None = None`. Validate exact dependency pairing. Add the mission path patterns to the same authenticated host/origin/App Check/CSRF/body-limit middleware profile as council mutations without changing disabled behavior.

- [ ] **Step 4: Implement create, load, and run routes**

```python
@app.post("/api/organizations/{organization_id}/workspaces/{workspace_id}/missions")
async def create_mission(...): ...

@app.api_route(
    "/api/organizations/{organization_id}/workspaces/{workspace_id}/missions/{mission_id}",
    methods=["GET", "HEAD"],
)
def load_mission(...): ...

@app.post(
    "/api/organizations/{organization_id}/workspaces/{workspace_id}/missions/{mission_id}/run"
)
async def run_mission(...): ...
```

Create returns 201. Load returns a canonical projection. Run streams NDJSON `started`, ordered `activity`, then `complete`, `awaiting_response`, or `blocked`. Worker exceptions emit only `mission_unavailable`. The worker is joined before terminal EOF. Provider response ingestion is not exposed as a browser POST; the configured gateway calls `MissionService.record_response` after provider authentication and normalization.

- [ ] **Step 5: Add hostile boundary cases**

Cover cross-tenant/workspace/mission IDs, missing auth, missing App Check, missing CSRF, invalid origin, query strings, duplicate JSON keys, over-cap/deep bodies, enum subclasses, injected model methods/private fields, transport exceptions, disconnect cancellation, nonterminal EOF, and disabled-mode compatibility.

- [ ] **Step 6: Run Task 4 tests**

Run: `python -m pytest tests/humanwire/test_decisionos_mission_app.py tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_auth.py -q`

Expected: all pass.

- [ ] **Step 7: Commit Task 4**

```powershell
git add -- src/humanwire/decisionos_app.py tests/humanwire/test_decisionos_mission_app.py
git commit -m "feat: expose tenant-bound mission API"
```

---

### Task 5: Mission composer and live workspace

**Files:**
- Modify: `src/humanwire/templates/decisionos_shell.html`
- Modify: `src/humanwire/decisionos_static/decisionos-app.js`
- Modify: `src/humanwire/decisionos_static/decisionos.css`
- Modify: `tests/humanwire/test_decisionos_frontend.py`
- Modify: `tests/humanwire/decisionos_frontend_harness.js`

**Interfaces:**
- Consumes: mission API projections and stream envelopes from Task 4.
- Produces: mode selector, readiness panel, participant list, live event timeline, blocked state, and result panel.

- [ ] **Step 1: Add failing frontend contract tests**

```python
assert "Demo run" in html
assert "Connected organization" in html
assert "Synthetic demo evidence" not in html
assert "fabricated" not in html.casefold()
assert 'data-mission-mode="demo_run"' in html
assert 'data-mission-mode="connected_organization"' in html
```

The Node harness must submit both modes, assert connected mode does not include browser-supplied contacts, consume running/activity/blocked/complete streams, keep the selected mode after recoverable errors, abort an old stream on New mission, and show the terminal state after refresh.

- [ ] **Step 2: Run frontend tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_decisionos_frontend.py -v`

Expected: assertions fail because the mission composer does not exist and old synthetic copy remains.

- [ ] **Step 3: Add semantic mission composer markup**

Create a fieldset with two real radio controls, a short persistent truth line, objective/urgency/conflict controls, readiness output, and one Start mission button. Add a mission workspace with status, current stage, agent/participant cards, ordered timeline, next action, blocked-reason explanation, and final recommendation. Retain existing organization onboarding, evidence, council, and team views.

- [ ] **Step 4: Implement stream controller**

Add `createMission`, `runMission`, `consumeMissionStream`, `renderMission`, and `resetMission` functions. Abort obsolete readers, reject nonterminal EOF, render fixed public errors in the visible mission panel, and never convert a connected failure into demo success.

- [ ] **Step 5: Implement responsive styling**

Use existing DecisionOS tokens. Ensure 44 px controls, 14 px minimum text, keyboard-visible mode cards, reduced-motion-safe status updates, no page horizontal overflow at 390x844, and no overlap between timeline/result/sticky controls.

- [ ] **Step 6: Run frontend tests**

Run: `python -m pytest tests/humanwire/test_decisionos_frontend.py -q`

Run: `node --check src/humanwire/decisionos_static/decisionos-app.js`

Run: `node tests/humanwire/decisionos_frontend_harness.js`

Expected: all pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add -- src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/decisionos-app.js src/humanwire/decisionos_static/decisionos.css tests/humanwire/test_decisionos_frontend.py tests/humanwire/decisionos_frontend_harness.js
git commit -m "feat: add HumanWire mission workspace"
```

---

### Task 6: Cloud wiring, compatibility, and product proof

**Files:**
- Modify: `src/humanwire/google_config.py`
- Modify: `src/humanwire/google_submission_app.py`
- Modify: `tests/humanwire/test_google_submission_app.py`
- Modify: `tests/humanwire/test_google_deployment_contract.py`
- Modify: `README.md`
- Create: `docs/humanwire-mission-modes.md`

**Interfaces:**
- Consumes: verified Firestore repositories, council runtime, organization graph repository, and configured mission route/transport registries.
- Produces: disabled-by-default production wiring, readiness diagnostics, deployment documentation, and reproducible local verification.

- [ ] **Step 1: Write failing cloud-wiring tests**

Assert that mission features remain disabled unless every dependency is present; Demo run can be enabled with no provider; Connected organization readiness reports `provider_not_configured` without credentials; server configuration never serializes secrets; and no ambient Caspian key silently enables a route outside the explicit feature configuration.

- [ ] **Step 2: Run cloud tests and confirm RED**

Run: `python -m pytest tests/humanwire/test_google_submission_app.py tests/humanwire/test_google_deployment_contract.py -v`

Expected: mission dependency assertions fail because cloud wiring is absent.

- [ ] **Step 3: Add explicit cloud dependency wiring**

Construct the Firestore mission repository and mission service only under an exact feature flag. Register a real transport only when its server route registry and provider configuration are both valid. Keep provider destinations outside public environment/config JSON. Do not make a live communication claim from configuration alone.

- [ ] **Step 4: Document both modes and setup**

Document Demo run, Connected organization, initial email/Telegram capability, consent requirements, provider readiness, replay/testing commands, and explicit deferred channels. Replace current public copy that describes the Agent Council and coordination engine as unrelated experiences.

- [ ] **Step 5: Run focused and compatibility gates**

Run:

```powershell
python -m pytest tests/humanwire/test_mission_models.py tests/humanwire/test_mission_store.py tests/humanwire/test_mission_participants.py tests/humanwire/test_mission_transport.py tests/humanwire/test_mission_service.py tests/humanwire/test_mission_projection.py tests/humanwire/test_decisionos_mission_app.py tests/humanwire/test_decisionos_frontend.py -q
python -m pytest tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_auth.py tests/humanwire/test_decisionos_council_app.py tests/humanwire/test_decisionos_organization_app.py tests/humanwire/test_organization_activation.py tests/humanwire/test_caspian_gateway.py tests/humanwire/test_studio_e2e.py tests/humanwire/test_workflow.py -q
ruff check src tests
git diff --check
```

Expected: zero failures; environment-dependent emulator/provider tests are explicit skips only.

- [ ] **Step 6: Run browser acceptance**

Verify signed-in desktop and 390x844 mobile states for:

1. Demo run selection, AI actor labels, full stream, and zero provider readiness claim.
2. Connected organization selection with missing configuration and exact blocked explanation.
3. Connected organization with an injected consented test route, visible real-delivery receipt status, and no exposed destination.
4. An authenticated injected gateway reply changes the exact participant from waiting to responded, appends evidence, and advances the shared mission timeline.
5. Refresh/resume, stream failure, keyboard focus, no overflow, and clean console.

- [ ] **Step 7: Run full repository gate**

Run: `python -m pytest -q`

Expected: zero failures; only documented environment skips.

- [ ] **Step 8: Commit Task 6**

```powershell
git add -- src/humanwire/google_config.py src/humanwire/google_submission_app.py tests/humanwire/test_google_submission_app.py tests/humanwire/test_google_deployment_contract.py README.md docs/humanwire-mission-modes.md
git commit -m "feat: wire HumanWire mission modes"
```
