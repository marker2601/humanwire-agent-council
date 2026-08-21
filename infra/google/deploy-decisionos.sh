#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:?usage: deploy-decisionos.sh PROJECT_ID [REGION]}"
REGION="${2:-us-central1}"
REPOSITORY="${HUMANWIRE_REPOSITORY:-humanwire}"
IMAGE_TAG="${HUMANWIRE_IMAGE_TAG:-decisionos-$(date -u +%Y%m%d%H%M%S)}"
FIREBASE_PROJECT_ID="$PROJECT_ID"
FIREBASE_API_KEY_SECRET="${HUMANWIRE_FIREBASE_API_KEY_SECRET:-decisionos-firebase-api-key}"
FIREBASE_APP_ID_SECRET="${HUMANWIRE_FIREBASE_APP_ID_SECRET:-decisionos-firebase-app-id}"
APP_CHECK_SITE_KEY_SECRET="${HUMANWIRE_APP_CHECK_SITE_KEY_SECRET:-decisionos-app-check-site-key}"
MODEL_ID="${HUMANWIRE_COUNCIL_MODEL_ID:-gemini-3.5-flash}"
MODEL_LOCATION="${HUMANWIRE_COUNCIL_MODEL_LOCATION:-global}"
SERVICE="humanwire-decisionos"
ACCOUNT_ID="humanwire-decisionos"
ACCOUNT="humanwire-decisionos@$PROJECT_ID.iam.gserviceaccount.com"
BASE_IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/humanwire"
TAGGED_IMAGE="$BASE_IMAGE:$IMAGE_TAG"
SECRET_BINDINGS="HUMANWIRE_DECISIONOS_FIREBASE_API_KEY=$FIREBASE_API_KEY_SECRET:latest,HUMANWIRE_DECISIONOS_FIREBASE_APP_ID=$FIREBASE_APP_ID_SECRET:latest,HUMANWIRE_DECISIONOS_APP_CHECK_SITE_KEY=$APP_CHECK_SITE_KEY_SECRET:latest"

[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,62}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$FIREBASE_PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,62}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$REGION" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$REPOSITORY" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$IMAGE_TAG" =~ ^[A-Za-z0-9._-]{1,120}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$MODEL_ID" =~ ^gemini-[0-9]+\.[0-9]+-[a-z0-9][a-z0-9.-]{0,127}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
[[ "$MODEL_LOCATION" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
for secret_name in "$FIREBASE_API_KEY_SECRET" "$FIREBASE_APP_ID_SECRET" "$APP_CHECK_SITE_KEY_SECRET"; do
  [[ "$secret_name" =~ ^[A-Za-z0-9_-]{1,255}$ ]] || { echo 'decisionos_deployment_input_invalid' >&2; exit 2; }
done

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com aiplatform.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com firebase.googleapis.com firebaseappcheck.googleapis.com firebaserules.googleapis.com firebasestorage.googleapis.com identitytoolkit.googleapis.com iam.googleapis.com logging.googleapis.com recaptchaenterprise.googleapis.com secretmanager.googleapis.com storage.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" --repository-format=docker --location="$REGION" --project="$PROJECT_ID"
fi
if ! gcloud firestore databases describe '--database=(default)' --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud firestore databases create '--database=(default)' --location="$REGION" --type=firestore-native --project="$PROJECT_ID"
fi
if ! gcloud iam service-accounts describe "$ACCOUNT" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$ACCOUNT_ID" --project="$PROJECT_ID" --display-name='HumanWire DecisionOS runtime'
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$ACCOUNT" --role=roles/datastore.user --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$ACCOUNT" --role=roles/firebaseauth.admin --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$ACCOUNT" --role=roles/logging.logWriter --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$ACCOUNT" --role=roles/aiplatform.user --condition=None
for secret_name in "$FIREBASE_API_KEY_SECRET" "$FIREBASE_APP_ID_SECRET" "$APP_CHECK_SITE_KEY_SECRET"; do
  gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null
  gcloud secrets add-iam-policy-binding "$secret_name" --project="$PROJECT_ID" --member="serviceAccount:$ACCOUNT" --role=roles/secretmanager.secretAccessor --condition=None
done

npx --yes firebase-tools@15.27.0 deploy --project "$FIREBASE_PROJECT_ID" --config infra/firebase/firebase.json --only firestore:rules,firestore:indexes,storage,hosting

gcloud builds submit --config=infra/google/cloudbuild.yaml --substitutions="_IMAGE=$TAGGED_IMAGE" .
DIGEST="$(gcloud artifacts docker images describe "$TAGGED_IMAGE" --format='value(image_summary.digest)')"
case "$DIGEST" in sha256:????????????????????????????????????????????????????????????????) ;; *) echo 'decisionos_image_digest_invalid' >&2; exit 1 ;; esac
PINNED_IMAGE="$BASE_IMAGE@$DIGEST"
PUBLIC_ENVIRONMENT="HUMANWIRE_SERVICE_ROLE=decisionos,HUMANWIRE_DECISIONOS_PROJECT_ID=$FIREBASE_PROJECT_ID,HUMANWIRE_DECISIONOS_FIRESTORE_DATABASE=(default),HUMANWIRE_DECISIONOS_ALLOWED_HOSTS=decisionos.invalid,HUMANWIRE_DECISIONOS_FIREBASE_AUTH_DOMAIN=$FIREBASE_PROJECT_ID.firebaseapp.com,HUMANWIRE_DECISIONOS_FIREBASE_STORAGE_BUCKET=$FIREBASE_PROJECT_ID.firebasestorage.app,HUMANWIRE_DECISIONOS_APP_CHECK_ENFORCED=false,HUMANWIRE_DECISIONOS_ORGANIZATION_FEATURES_ENABLED=true,HUMANWIRE_DECISIONOS_COUNCIL_FEATURES_ENABLED=true,HUMANWIRE_DECISIONOS_MISSION_FEATURES_ENABLED=true,HUMANWIRE_DECISIONOS_COUNCIL_MODEL_ID=$MODEL_ID,HUMANWIRE_DECISIONOS_COUNCIL_GOOGLE_LOCATION=$MODEL_LOCATION,HUMANWIRE_DECISIONOS_COUNCIL_TIMEOUT_SECONDS=180"

gcloud run deploy "$SERVICE" --image="$PINNED_IMAGE" --region="$REGION" --project="$PROJECT_ID" --service-account="$ACCOUNT" --allow-unauthenticated --min-instances=0 --max-instances=3 --concurrency=4 --timeout=300 --cpu=1 --memory=1Gi --set-env-vars="$PUBLIC_ENVIRONMENT" --set-secrets="$SECRET_BINDINGS"
DECISIONOS_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')"
DECISIONOS_HOST="${DECISIONOS_URL#https://}"
ALLOWED_HOSTS="$DECISIONOS_HOST;$FIREBASE_PROJECT_ID.firebaseapp.com;$FIREBASE_PROJECT_ID.web.app"
gcloud run services update "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --image="$PINNED_IMAGE" --update-env-vars="HUMANWIRE_DECISIONOS_ALLOWED_HOSTS=$ALLOWED_HOSTS"

printf 'decisionos_url=%s\n' "$DECISIONOS_URL"
printf 'image_digest=%s\n' "$DIGEST"
printf 'app_check_enforced=false\n'
