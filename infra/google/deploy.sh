#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:?usage: deploy.sh PROJECT_ID [REGION] [IMAGE_TAG]}"
REGION="${2:-us-central1}"
IMAGE_TAG="${3:-build-$(date -u +%Y%m%d%H%M%S)}"
REPOSITORY="humanwire"
MODEL_ID="gemini-3.6-flash"
WEB_SERVICE="humanwire-web"
WORKER_SERVICE="humanwire-worker"
TOPIC="humanwire-runs"
SUBSCRIPTION="humanwire-runs-worker"
WEB_ACCOUNT="humanwire-web@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_ACCOUNT="humanwire-worker@${PROJECT_ID}.iam.gserviceaccount.com"
PUSH_ACCOUNT="humanwire-push@${PROJECT_ID}.iam.gserviceaccount.com"
BASE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/humanwire"
TAGGED_IMAGE="${BASE_IMAGE}:${IMAGE_TAG}"

gcloud config set project "${PROJECT_ID}"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com pubsub.googleapis.com aiplatform.googleapis.com logging.googleapis.com
gcloud artifacts repositories describe "${REPOSITORY}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 || gcloud artifacts repositories create "${REPOSITORY}" --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}"
gcloud firestore databases describe --database='(default)' --project="${PROJECT_ID}" >/dev/null 2>&1 || gcloud firestore databases create --database='(default)' --location="${REGION}" --type=firestore-native --project="${PROJECT_ID}"

for account in humanwire-web humanwire-worker humanwire-push; do
  gcloud iam service-accounts describe "${account}@${PROJECT_ID}.iam.gserviceaccount.com" --project="${PROJECT_ID}" >/dev/null 2>&1 || gcloud iam service-accounts create "${account}" --project="${PROJECT_ID}"
done
for role in roles/datastore.user roles/pubsub.publisher; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${WEB_ACCOUNT}" --role="${role}" --condition=None >/dev/null
done
for role in roles/aiplatform.user roles/datastore.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${WORKER_ACCOUNT}" --role="${role}" --condition=None >/dev/null
done

gcloud builds submit --config=infra/google/cloudbuild.yaml --substitutions="_IMAGE=${TAGGED_IMAGE}" .
DIGEST="$(gcloud artifacts docker images describe "${TAGGED_IMAGE}" --format='value(image_summary.digest)')"
[[ "${DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo google_image_digest_invalid >&2; exit 1; }
# Deploy image@sha256 digest, never a mutable tag.
PINNED_IMAGE="${BASE_IMAGE}@${DIGEST}"

gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT_ID}" >/dev/null 2>&1 || gcloud pubsub topics create "${TOPIC}" --project="${PROJECT_ID}"
gcloud run deploy "${WORKER_SERVICE}" --image="${PINNED_IMAGE}" --region="${REGION}" --project="${PROJECT_ID}" --service-account="${WORKER_ACCOUNT}" --no-allow-unauthenticated --min-instances=0 --max-instances=1 --concurrency=1 --timeout=600 --cpu=1 --memory=1Gi --set-env-vars="HUMANWIRE_SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},HUMANWIRE_FIRESTORE_DATABASE=(default),HUMANWIRE_WORKER_HOST=worker.invalid,HUMANWIRE_MODEL_ID=${MODEL_ID},HUMANWIRE_GOOGLE_LOCATION=${REGION}"
WORKER_URL="$(gcloud run services describe "${WORKER_SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
WORKER_HOST="${WORKER_URL#https://}"
gcloud run services update "${WORKER_SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --image="${PINNED_IMAGE}" --update-env-vars="HUMANWIRE_WORKER_HOST=${WORKER_HOST}"
gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --member="serviceAccount:${PUSH_ACCOUNT}" --role=roles/run.invoker --condition=None
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" --role=roles/iam.serviceAccountTokenCreator --condition=None >/dev/null

if gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${SUBSCRIPTION}" --project="${PROJECT_ID}" --push-endpoint="${WORKER_URL}/internal/pubsub/runs" --push-auth-service-account="${PUSH_ACCOUNT}" --push-auth-token-audience="${WORKER_URL}" --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s --message-retention-duration=1d
else
  gcloud pubsub subscriptions create "${SUBSCRIPTION}" --topic="${TOPIC}" --project="${PROJECT_ID}" --push-endpoint="${WORKER_URL}/internal/pubsub/runs" --push-auth-service-account="${PUSH_ACCOUNT}" --push-auth-token-audience="${WORKER_URL}" --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s --message-retention-duration=1d
fi

gcloud run deploy "${WEB_SERVICE}" --image="${PINNED_IMAGE}" --region="${REGION}" --project="${PROJECT_ID}" --service-account="${WEB_ACCOUNT}" --allow-unauthenticated --min-instances=0 --max-instances=1 --concurrency=20 --timeout=60 --cpu=1 --memory=512Mi --set-env-vars="HUMANWIRE_SERVICE_ROLE=web,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},HUMANWIRE_FIRESTORE_DATABASE=(default),HUMANWIRE_PUBSUB_TOPIC=${TOPIC},HUMANWIRE_PUBLIC_ORIGINS=https://web.invalid"
WEB_URL="$(gcloud run services describe "${WEB_SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
gcloud run services update "${WEB_SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --image="${PINNED_IMAGE}" --update-env-vars="HUMANWIRE_PUBLIC_ORIGINS=${WEB_URL}"

printf 'web_url=%s\nworker_url=%s\nimage_digest=%s\n' "${WEB_URL}" "${WORKER_URL}" "${DIGEST}"
