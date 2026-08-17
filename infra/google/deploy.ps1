[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z][a-z0-9-]{4,62}$')][string]$ProjectId,
    [ValidatePattern('^[a-z][a-z0-9-]{1,62}$')][string]$Region = 'us-central1',
    [ValidatePattern('^[a-z][a-z0-9-]{1,62}$')][string]$Repository = 'humanwire',
    [ValidatePattern('^[A-Za-z0-9._-]{1,120}$')][string]$ImageTag = "build-$([DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss'))",
    [ValidatePattern('^gemini-[0-9]+\.[0-9]+-[a-z0-9][a-z0-9.-]{0,127}$')][string]$ModelId = 'gemini-3.6-flash'
)

$ErrorActionPreference = 'Stop'
$WebService = 'humanwire-web'
$WorkerService = 'humanwire-worker'
$Topic = 'humanwire-runs'
$Subscription = 'humanwire-runs-worker'
$WebAccount = "humanwire-web@$ProjectId.iam.gserviceaccount.com"
$WorkerAccount = "humanwire-worker@$ProjectId.iam.gserviceaccount.com"
$PushAccount = "humanwire-push@$ProjectId.iam.gserviceaccount.com"
$BaseImage = "$Region-docker.pkg.dev/$ProjectId/$Repository/humanwire"
$TaggedImage = "${BaseImage}:$ImageTag"

function Invoke-Gcloud {
    & gcloud @args
    if ($LASTEXITCODE -ne 0) { throw 'google_deployment_failed' }
}

Invoke-Gcloud config set project $ProjectId
Invoke-Gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com pubsub.googleapis.com aiplatform.googleapis.com logging.googleapis.com

& gcloud artifacts repositories describe $Repository --location=$Region --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud artifacts repositories create $Repository --repository-format=docker --location=$Region --project=$ProjectId
}
& gcloud firestore databases describe '--database=(default)' --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud firestore databases create '--database=(default)' --location=$Region --type=firestore-native --project=$ProjectId
}

foreach ($AccountId in @('humanwire-web', 'humanwire-worker', 'humanwire-push')) {
    & gcloud iam service-accounts describe "$AccountId@$ProjectId.iam.gserviceaccount.com" --project=$ProjectId *> $null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Gcloud iam service-accounts create $AccountId --project=$ProjectId
    }
}
foreach ($Role in @('roles/datastore.user', 'roles/pubsub.publisher')) {
    Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$WebAccount" --role=$Role --condition=None
}
foreach ($Role in @('roles/aiplatform.user', 'roles/datastore.user', 'roles/logging.logWriter')) {
    Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$WorkerAccount" --role=$Role --condition=None
}

Invoke-Gcloud builds submit --config=infra/google/cloudbuild.yaml --substitutions="_IMAGE=$TaggedImage" .
$Digest = (& gcloud artifacts docker images describe $TaggedImage --format='value(image_summary.digest)').Trim()
if ($LASTEXITCODE -ne 0 -or $Digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'google_image_digest_invalid' }
# Deploy image@sha256 digest, never a mutable tag.
$PinnedImage = "$BaseImage@$Digest"

& gcloud pubsub topics describe $Topic --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) { Invoke-Gcloud pubsub topics create $Topic --project=$ProjectId }

Invoke-Gcloud run deploy $WorkerService --image=$PinnedImage --region=$Region --project=$ProjectId --service-account=$WorkerAccount --no-allow-unauthenticated --min-instances=0 --max-instances=1 --concurrency=1 --timeout=600 --cpu=1 --memory=1Gi --set-env-vars="HUMANWIRE_SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=$ProjectId,HUMANWIRE_FIRESTORE_DATABASE=(default),HUMANWIRE_WORKER_HOST=worker.invalid,HUMANWIRE_MODEL_ID=$ModelId,HUMANWIRE_GOOGLE_LOCATION=$Region"
$WorkerUrl = (& gcloud run services describe $WorkerService --region=$Region --project=$ProjectId --format='value(status.url)').Trim()
$WorkerHost = ([Uri]$WorkerUrl).Host
Invoke-Gcloud run services update $WorkerService --region=$Region --project=$ProjectId --image=$PinnedImage --update-env-vars="HUMANWIRE_WORKER_HOST=$WorkerHost"

Invoke-Gcloud run services add-iam-policy-binding $WorkerService --region=$Region --project=$ProjectId --member="serviceAccount:$PushAccount" --role=roles/run.invoker --condition=None
$ProjectNumber = (& gcloud projects describe $ProjectId --format='value(projectNumber)').Trim()
$PubSubAgent = "service-$ProjectNumber@gcp-sa-pubsub.iam.gserviceaccount.com"
Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$PubSubAgent" --role=roles/iam.serviceAccountTokenCreator --condition=None

& gcloud pubsub subscriptions describe $Subscription --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud pubsub subscriptions create $Subscription --topic=$Topic --project=$ProjectId --push-endpoint="$WorkerUrl/internal/pubsub/runs" --push-auth-service-account=$PushAccount --push-auth-token-audience=$WorkerUrl --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s --message-retention-duration=1d
} else {
    Invoke-Gcloud pubsub subscriptions update $Subscription --project=$ProjectId --push-endpoint="$WorkerUrl/internal/pubsub/runs" --push-auth-service-account=$PushAccount --push-auth-token-audience=$WorkerUrl --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s --message-retention-duration=1d
}

Invoke-Gcloud run deploy $WebService --image=$PinnedImage --region=$Region --project=$ProjectId --service-account=$WebAccount --allow-unauthenticated --min-instances=0 --max-instances=1 --concurrency=20 --timeout=60 --cpu=1 --memory=512Mi --set-env-vars="HUMANWIRE_SERVICE_ROLE=web,GOOGLE_CLOUD_PROJECT=$ProjectId,HUMANWIRE_FIRESTORE_DATABASE=(default),HUMANWIRE_PUBSUB_TOPIC=$Topic,HUMANWIRE_PUBLIC_ORIGINS=https://web.invalid"
$WebUrl = (& gcloud run services describe $WebService --region=$Region --project=$ProjectId --format='value(status.url)').Trim()
Invoke-Gcloud run services update $WebService --region=$Region --project=$ProjectId --image=$PinnedImage --update-env-vars="HUMANWIRE_PUBLIC_ORIGINS=$WebUrl"

Write-Output "web_url=$WebUrl"
Write-Output "worker_url=$WorkerUrl"
Write-Output "image_digest=$Digest"
