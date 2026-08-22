# HumanWire Google Cloud deployment

## HumanWire DecisionOS service

DecisionOS deploys as the separate `humanwire-decisionos` Cloud Run service with
its own `humanwire-decisionos` service account. It is the backend for the current
Firebase-hosted Agent Council submission. This deployment does not update or route
traffic to `humanwire-web` or `humanwire-worker`, the earlier foundation services.
It uses Firebase Authentication for identity, Firestore membership for tenant
authority, and Secret Manager references for the Firebase web app ID, public API
key, and App Check site key. No secret value is accepted on a command line.

Before the first deploy, create a Firebase Web App in the same billing-enabled
project, enable the approved sign-in providers, register reCAPTCHA Enterprise for
App Check, and place the three configuration values into these secrets:

- `decisionos-firebase-api-key`
- `decisionos-firebase-app-id`
- `decisionos-app-check-site-key`

Then run one of:

```powershell
./infra/google/deploy-decisionos.ps1 -ProjectId YOUR_PROJECT_ID -Region us-central1
```

```bash
./infra/google/deploy-decisionos.sh YOUR_PROJECT_ID us-central1
```

The script deploys Firestore indexes/rules and Storage rules, builds one immutable
image, deploys by digest, binds the dedicated runtime identity, and prints only the
DecisionOS URL, digest, and App Check rollout state. App Check begins in monitored
mode (`HUMANWIRE_DECISIONOS_APP_CHECK_ENFORCED=false`). Review valid/invalid token
metrics, register every production hostname, then change that flag to `true`; do
not enforce it before verified traffic is visible. The service scales to zero.

Verify `/health`, Firebase Google/email-link sign-in, two separate organizations,
one invitation, one workspace per organization, and cross-tenant denial. Roll back
only `humanwire-decisionos` traffic to its prior revision. Do not delete Firestore,
Storage, Firebase identities, audit records, or the existing submission services.

## Earlier two-service coordination foundation

The following stack remains in the repository for tested compatibility and replay
evidence. It is not the architecture claimed for the current Agent Council
submission or film. It deploys one immutable image as two Cloud Run services:

- `humanwire-web` is public and can use only Firestore plus Pub/Sub publish.
- `humanwire-worker` is private, accepts authenticated Pub/Sub push, and alone can use Vertex AI, Firestore, and Cloud Logging.
- `humanwire-push` has only `roles/run.invoker` on the worker.

Both services use Application Default Credentials. No Gemini API key belongs in an image, environment variable, browser, or deployment command. The worker is bounded to zero-to-one instances, concurrency one, and a 600-second timeout. Firestore holds the durable event history; rollback must **preserve Firestore history**.

## Prerequisites

Install Docker and the Google Cloud CLI, authenticate an operator account, select a billing-enabled project for which that account may create the listed resources, and set a quota/budget alert in the Cloud Console. Deployment creates billable Google Cloud resources but uses scale-to-zero settings.

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

The application itself never uses the operator login. Cloud Run uses the three dedicated service accounts created by the scripts.

## Local image verification

```powershell
docker build --pull --tag humanwire-google:local .
docker run --rm --name humanwire-worker-local -p 18081:8080 `
  -e HUMANWIRE_SERVICE_ROLE=worker `
  -e GOOGLE_CLOUD_PROJECT=humanwire-local `
  -e HUMANWIRE_WORKER_HOST=worker.local.test `
  -e HUMANWIRE_MODEL_ID=gemini-3.5-flash `
  -e HUMANWIRE_GOOGLE_LOCATION=global `
  -e FIRESTORE_EMULATOR_HOST=host.docker.internal:18082 `
  humanwire-google:local
```

Call `http://127.0.0.1:18081/healthz` with `Host: worker.local.test`; stop it, then repeat with the web role, `PUBSUB_EMULATOR_HOST`, `HUMANWIRE_PUBSUB_TOPIC=humanwire-runs`, and `HUMANWIRE_PUBLIC_ORIGINS=https://web.local.test`. These are startup/route checks only; the deterministic E2E suite is the provider-free behavior proof.

## Deploy

From the repository root, choose one script:

```powershell
./infra/google/deploy.ps1 -ProjectId YOUR_PROJECT_ID -Region us-central1
```

```bash
./infra/google/deploy.sh YOUR_PROJECT_ID us-central1
```

The script enables only required APIs, creates dedicated identities, builds once, resolves the Artifact Registry digest, deploys both revisions by digest, configures the exact public origin, and creates an OIDC-authenticated push subscription. It prints only the public URL, private URL, and image digest.

## Inspect and smoke-test

```powershell
gcloud run services describe humanwire-web --region us-central1 --format=yaml
gcloud run services describe humanwire-worker --region us-central1 --format=yaml
gcloud run services get-iam-policy humanwire-worker --region us-central1
gcloud pubsub subscriptions describe humanwire-runs-worker
gcloud firestore databases describe '--database=(default)'
```

Verify the web health route without authentication. Verify an unauthenticated worker request receives `403` from Cloud Run itself. Start one run in the product, refresh during execution, then download JSON and CSV and compare their final digests and ordinals. Cloud Logging evidence must contain only the fixed event/state/service-role schema.

## Rollback without deleting history

First stop new public starts by shifting web traffic to a known-good revision:

```powershell
gcloud run services update-traffic humanwire-web --region us-central1 --to-revisions=KNOWN_GOOD_WEB=100
gcloud run services update-traffic humanwire-worker --region us-central1 --to-revisions=KNOWN_GOOD_WORKER=100
```

To pause delivery while retaining the subscription backlog, remove the push identity's worker invoker binding. Restore that binding and re-assert the authenticated endpoint with `gcloud pubsub subscriptions update` after rollback. Do not delete Firestore, the topic, or the run documents.

## Clean-environment reproduction

Clone the public repository into a new directory, run the focused deployment-contract test, build the pinned container, and deploy with a new immutable tag. Record the git SHA, image digest, two revision names, public URL, and a safe completed run alias. Never record access tokens, project credentials, prompts, private model output, or raw logs.
