# HumanWire Commercial Growth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add measurable activation, referrals, entitlements, billing readiness, notifications, retention controls, and enterprise identity without weakening HumanWire authority or privacy.

**Architecture:** Product events are emitted from authoritative server transitions into a content-free analytics contract and exported to BigQuery. Entitlements are resolved server-side from a billing-provider-neutral account record; invitations and sanitized share packets create growth loops, while enterprise Identity Platform features remain gated by plan and explicit configuration.

**Tech Stack:** Firebase/Identity Platform, Firestore, Cloud Run, Pub/Sub, BigQuery, Firebase Cloud Messaging or email adapter, provider-neutral billing interface, pytest.

**Spec:** docs/superpowers/specs/2026-08-17-humanwire-decisionos-design.md

## Global Constraints

- Billing state cannot grant organization authority; membership and RBAC remain separate.
- Analytics contain opaque IDs, event names, timestamps, numeric measures, and plan identifiers only.
- No prompt, document text, evidence excerpt, email, name, route, token, artifact body, or private decision content enters analytics.
- No user is contacted without a consented notification route and preference.
- Share links expose only immutable sanitized artifacts and are revocable.
- Pricing values remain configuration until validated; UI cannot claim savings, ROI, or outcomes without evidence.
- Enterprise SSO/MFA is enabled through Identity Platform only after tenant and recovery testing.

---

### Task 1: Entitlement and usage model

**Files:**
- Create: src/humanwire/entitlements.py
- Test: tests/humanwire/test_entitlements.py

**Interfaces:**
- Consumes: organization ID, plan record, usage counters, and current clock.
- Produces: ProductPlan, Entitlement, UsageWindow, EntitlementDecision, and require_entitlement.

- [ ] **Step 1: Write entitlement RED tests**

~~~python
def test_free_plan_blocks_second_active_workspace() -> None:
    decision = evaluate_entitlement(
        plan=free_plan(),
        usage=usage(active_workspaces=1),
        action=EntitledAction.CREATE_WORKSPACE,
    )
    assert decision.allowed is False
    assert decision.reason == "workspace_limit"


def test_billing_plan_never_changes_user_role() -> None:
    decision = evaluate_entitlement(
        plan=enterprise_plan(),
        usage=usage(),
        action=EntitledAction.APPROVE_DECISION,
    )
    assert decision.allowed is True
    assert not hasattr(decision, "role")
~~~

Cover expired trial, clock boundary, duplicate usage event, partial provider outage, downgrade with active work, fundraising sprint expiry, and owner/admin-only billing access.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_entitlements.py -v
Expected: missing-module collection failure.

- [ ] **Step 3: Implement pure policy evaluation**

~~~python
def evaluate_entitlement(
    *,
    plan: OrganizationPlan,
    usage: UsageWindow,
    action: EntitledAction,
) -> EntitlementDecision:
    limit = plan.limit_for(action)
    if limit is None:
        return EntitlementDecision(allowed=True)
    return EntitlementDecision(
        allowed=usage.value_for(action) < limit,
        reason=None if usage.value_for(action) < limit else limit.reason,
    )
~~~

Initial configurable plans are free, founder, team, fundraising_sprint, investor, and enterprise. No price is hardcoded into authority code.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_entitlements.py -v
python -m ruff check src/humanwire/entitlements.py tests/humanwire/test_entitlements.py
git add src/humanwire/entitlements.py tests/humanwire/test_entitlements.py
git commit -m "feat: define DecisionOS entitlements"
~~~

### Task 2: Provider-neutral billing boundary

**Files:**
- Create: src/humanwire/billing.py
- Modify: src/humanwire/decisionos_app.py
- Test: tests/humanwire/test_billing.py
- Test: tests/humanwire/test_decisionos_app.py

**Interfaces:**
- Consumes: organization owner/admin context and an injected BillingProvider.
- Produces: checkout request, customer portal request, signed webhook normalization, and canonical OrganizationPlan updates.

- [ ] **Step 1: Write webhook and authority RED tests**

~~~python
def test_webhook_updates_plan_once_after_signature_verification(service) -> None:
    first = service.handle_webhook(valid_signed_subscription_event())
    second = service.handle_webhook(valid_signed_subscription_event())
    assert first == "applied"
    assert second == "duplicate"


def test_billing_customer_id_is_not_an_organization_authority(service, attacker) -> None:
    with pytest.raises(BillingUnavailable):
        service.create_portal(attacker, customer_id="customer_from_other_org")
~~~

Cover replay, timestamp expiry, invalid signature, reordered JSON, provider outage, unknown product mapping, downgrade, cancellation, refund, private exception graphs, and raw webhook logging.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_billing.py -v.

- [ ] **Step 3: Implement the narrow interface**

~~~python
class BillingProvider(Protocol):
    def create_checkout(self, request: CheckoutRequest) -> CheckoutRedirect:
        raise NotImplementedError

    def create_portal(self, customer_reference: str, return_url: str) -> PortalRedirect:
        raise NotImplementedError

    def verify_webhook(self, body: bytes, signature: str) -> BillingEvent:
        raise NotImplementedError
~~~

Select the actual payment provider in a separate reviewed implementation. The HumanWire repository stores an opaque provider customer reference and canonical plan state, never payment-card data.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_billing.py tests/humanwire/test_decisionos_app.py -q
git add src/humanwire/billing.py src/humanwire/decisionos_app.py tests/humanwire/test_billing.py tests/humanwire/test_decisionos_app.py
git commit -m "feat: add provider-neutral billing boundary"
~~~

### Task 3: Privacy-safe product analytics and BigQuery export

**Files:**
- Create: src/humanwire/product_analytics.py
- Create: src/humanwire/analytics_export.py
- Create: infra/google/bigquery/decisionos_events.json
- Test: tests/humanwire/test_product_analytics.py
- Test: tests/humanwire/test_analytics_export.py

**Interfaces:**
- Consumes: authoritative server transitions and injected publisher/BigQuery writer.
- Produces: ProductEvent schema v1 and aggregate activation, retention, cost, and conversion queries.

- [ ] **Step 1: Write privacy and idempotency RED tests**

~~~python
def test_product_event_rejects_customer_content() -> None:
    with pytest.raises(ValidationError):
        ProductEvent(
            event_name="council_completed",
            organization_ref="opaque_org",
            properties={"objective": "Private launch objective"},
        )


def test_duplicate_event_id_is_inserted_once(exporter) -> None:
    exporter.write(product_event(event_id="event_01"))
    exporter.write(product_event(event_id="event_01"))
    assert exporter.accepted_count == 1
~~~

Allow only enumerated event names and properties. Cover user email/name, source text, run alias, contact route, prompt, model output, artifact body, arbitrary keys, NaN/Infinity, and oversized dimensions.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_product_analytics.py tests/humanwire/test_analytics_export.py -v.

- [ ] **Step 3: Implement schema and export**

~~~python
class ProductEvent(_AnalyticsModel):
    schema_version: Literal[1] = 1
    event_id: str
    event_name: ProductEventName
    organization_ref: str
    workspace_ref: str | None = None
    occurred_at: datetime
    properties: ProductEventProperties
~~~

Emit signup completion, organization creation, first upload, first council, first completed decision, invitation acceptance, playbook reuse, approval cycle, artifact share, entitlement block, checkout start, and plan activation. Pseudonymous refs are keyed hashes rotated under a documented retention policy.

- [ ] **Step 4: Add aggregate query fixtures**

Queries calculate activation funnel, time to value, weekly active decision owners, playbook retention, share conversion, partial/failed rate, latency, and cost per completed decision. They never join customer content.

- [ ] **Step 5: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_product_analytics.py tests/humanwire/test_analytics_export.py -q
git add src/humanwire/product_analytics.py src/humanwire/analytics_export.py infra/google/bigquery tests/humanwire/test_product_analytics.py tests/humanwire/test_analytics_export.py
git commit -m "feat: measure DecisionOS growth safely"
~~~

### Task 4: Invitation, sharing, and referral loops

**Files:**
- Create: src/humanwire/growth_loops.py
- Modify: src/humanwire/decisionos_app.py
- Modify: src/humanwire/templates/decisionos_shell.html
- Modify: src/humanwire/decisionos_static/decisionos-app.js
- Test: tests/humanwire/test_growth_loops.py
- Modify: tests/humanwire/decisionos_frontend_harness.js

**Interfaces:**
- Consumes: invitations, sanitized ShareGrant, organization plan, and product events.
- Produces: invite collaborator, request approval, share decision packet, and create-my-workspace conversion actions.

- [ ] **Step 1: Write RED tests**

~~~python
def test_shared_packet_cannot_reveal_private_workspace(service) -> None:
    packet = service.load_packet(valid_share_token())
    assert packet.organization_id is None
    assert packet.private_evidence == ()
    assert packet.create_workspace_cta == "Create your own workspace"


def test_referral_attribution_cannot_grant_entitlement(service) -> None:
    service.record_referral(fake_referral())
    assert service.organization_plan == ProductPlan.FREE
~~~

Cover token leakage in referrer/analytics, cross-tenant invitation, revoked shares, stale digests, role escalation, invitation spam caps, and malicious display names.

- [ ] **Step 2: Implement explicit loops**

The product may invite an approver/advisor, share a sanitized packet, and offer the recipient an independent workspace. It must not upload contacts, scrape address books, or send invitations without the owner's action.

- [ ] **Step 3: Run GREEN, browser QA, and commit**

~~~bash
python -m pytest tests/humanwire/test_growth_loops.py -v
node tests/humanwire/decisionos_frontend_harness.js
git add src/humanwire/growth_loops.py src/humanwire/decisionos_app.py src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/decisionos-app.js tests/humanwire/test_growth_loops.py tests/humanwire/decisionos_frontend_harness.js
git commit -m "feat: add DecisionOS collaboration growth loops"
~~~

### Task 5: Consent-bound notifications

**Files:**
- Create: src/humanwire/notifications.py
- Create: src/humanwire/notification_worker.py
- Test: tests/humanwire/test_notifications.py
- Test: tests/humanwire/test_notification_worker.py

**Interfaces:**
- Consumes: member notification preference, required-action event, and injected email/FCM provider.
- Produces: NotificationIntent, deduplicated delivery job, unsubscribe/preferences, and safe status.

- [ ] **Step 1: Write consent and privacy RED tests**

~~~python
def test_no_route_means_no_delivery(notification_service) -> None:
    result = notification_service.request(required_approval_event(), member_without_route())
    assert result.status == "not_configured"
    assert provider.call_count == 0


def test_notification_contains_no_private_decision_content(notification_service) -> None:
    intent = notification_service.request(private_decision_event(), opted_in_member())
    assert intent.body == "A HumanWire decision needs your review."
    assert "PRIVATE" not in intent.model_dump_json()
~~~

Cover duplicate events, unsubscribe races, revoked membership, wrong tenant, provider failure, route privacy, retry cap, and quiet hours.

- [ ] **Step 2: Implement provider-neutral delivery**

Only safe fixed templates are allowed initially. Deep links require authentication and do not contain invitation or decision secrets in query parameters.

- [ ] **Step 3: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_notifications.py tests/humanwire/test_notification_worker.py -q
git add src/humanwire/notifications.py src/humanwire/notification_worker.py tests/humanwire/test_notifications.py tests/humanwire/test_notification_worker.py
git commit -m "feat: notify DecisionOS members safely"
~~~

### Task 6: Enterprise identity and retention controls

**Files:**
- Create: src/humanwire/enterprise_identity.py
- Create: src/humanwire/data_governance.py
- Modify: src/humanwire/decisionos_app.py
- Test: tests/humanwire/test_enterprise_identity.py
- Test: tests/humanwire/test_data_governance.py
- Modify: infra/google/README.md

**Interfaces:**
- Consumes: Identity Platform tenant configuration, organization plan, membership, and storage/firestore repositories.
- Produces: tenant routing, SAML/OIDC configuration references, MFA requirement, retention policy, export job, deletion job, and legal hold.

- [ ] **Step 1: Write enterprise boundary RED tests**

~~~python
def test_identity_tenant_must_match_organization(service, principal) -> None:
    with pytest.raises(EnterpriseIdentityDenied):
        service.load_context(principal_with_tenant("tenant_B"), organization_id="org_A")


def test_legal_hold_blocks_destructive_retention_job(governance, held_org) -> None:
    result = governance.execute_retention(held_org)
    assert result.deleted_count == 0
    assert result.status == "legal_hold"
~~~

Cover SAML/OIDC misconfiguration, tenant confusion, MFA downgrade, owner recovery, data export cross-tenant access, interrupted deletion, artifact/source retention mismatch, and audit preservation.

- [ ] **Step 2: Implement explicit enterprise configuration**

Store only Identity Platform tenant IDs and provider configuration references, not SAML secrets. MFA policy is evaluated before sensitive role or billing operations.

- [ ] **Step 3: Implement export/deletion state machines**

Exports are encrypted, expire, and require owner reauthentication. Deletion is staged, idempotent, audit-recorded, and respects legal hold.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_enterprise_identity.py tests/humanwire/test_data_governance.py -q
git add src/humanwire/enterprise_identity.py src/humanwire/data_governance.py src/humanwire/decisionos_app.py tests/humanwire/test_enterprise_identity.py tests/humanwire/test_data_governance.py infra/google/README.md
git commit -m "feat: add DecisionOS enterprise controls"
~~~

### Task 7: Commercial release gate

**Files:**
- Create: tests/humanwire/test_decisionos_commercial_e2e.py
- Create: docs/decisionos-commercial-operations.md
- Modify: README.md

**Interfaces:**
- Consumes: all commercial tasks.
- Produces: one deterministic commercial lifecycle proof and an operator runbook.

- [ ] **Step 1: Add end-to-end lifecycle proof**

Create a free organization, hit an entitlement limit, start checkout through a fake provider, apply one signed plan event, invite an approver, complete and share a sanitized decision, record privacy-safe analytics, opt into one notification, export the organization, then exercise cancellation/retention.

- [ ] **Step 2: Add claim and pricing checks**

Tests fail if the UI claims guaranteed ROI/funding, hides usage limits, shows an unconfigured price, treats plan as role authority, or calls a trial “free forever.”

- [ ] **Step 3: Run final gates**

~~~powershell
python -m pytest tests/humanwire/test_entitlements.py tests/humanwire/test_billing.py tests/humanwire/test_product_analytics.py tests/humanwire/test_analytics_export.py tests/humanwire/test_growth_loops.py tests/humanwire/test_notifications.py tests/humanwire/test_notification_worker.py tests/humanwire/test_enterprise_identity.py tests/humanwire/test_data_governance.py tests/humanwire/test_decisionos_commercial_e2e.py -q
python -m pytest tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py -q
python -m ruff check src tests
git diff --check
~~~

- [ ] **Step 4: Independent revenue, security, privacy, and first-time-user review**

Verify activation clarity, upgrade truth, cancellation, invoices/portal, referral attribution, analytics privacy, tenant isolation, notification consent, and deletion/export behavior. Resolve every Critical or Important issue.

- [ ] **Step 5: Commit**

~~~bash
git add tests/humanwire/test_decisionos_commercial_e2e.py docs/decisionos-commercial-operations.md README.md
git commit -m "docs: qualify DecisionOS commercial launch"
~~~
