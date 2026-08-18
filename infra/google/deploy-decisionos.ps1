[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z][a-z0-9-]{4,62}$')][string]$ProjectId,
    [ValidatePattern('^[a-z][a-z0-9-]{1,62}$')][string]$Region = 'us-central1',
    [ValidatePattern('^[a-z][a-z0-9-]{1,62}$')][string]$Repository = 'humanwire',
    [ValidatePattern('^[A-Za-z0-9._-]{1,120}$')][string]$ImageTag = "decisionos-$([DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss'))",
    [ValidatePattern('^[A-Za-z0-9_-]{1,255}$')][string]$FirebaseApiKeySecret = 'decisionos-firebase-api-key',
    [ValidatePattern('^[A-Za-z0-9_-]{1,255}$')][string]$FirebaseAppIdSecret = 'decisionos-firebase-app-id',
    [ValidatePattern('^[A-Za-z0-9_-]{1,255}$')][string]$AppCheckSiteKeySecret = 'decisionos-app-check-site-key'
)

$ErrorActionPreference = 'Stop'
$FirebaseProjectId = $ProjectId
$Service = 'humanwire-decisionos'
$AccountId = 'humanwire-decisionos'
$Account = "humanwire-decisionos@$ProjectId.iam.gserviceaccount.com"
$BaseImage = "$Region-docker.pkg.dev/$ProjectId/$Repository/humanwire"
$TaggedImage = "${BaseImage}:$ImageTag"
$SecretBindings = "HUMANWIRE_DECISIONOS_FIREBASE_API_KEY=${FirebaseApiKeySecret}:latest,HUMANWIRE_DECISIONOS_FIREBASE_APP_ID=${FirebaseAppIdSecret}:latest,HUMANWIRE_DECISIONOS_APP_CHECK_SITE_KEY=${AppCheckSiteKeySecret}:latest"

function Invoke-Gcloud {
    & gcloud @args
    if ($LASTEXITCODE -ne 0) { throw 'decisionos_deployment_failed' }
}

Invoke-Gcloud config set project $ProjectId
Invoke-Gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com firebase.googleapis.com firebaseappcheck.googleapis.com firebaserules.googleapis.com firebasestorage.googleapis.com identitytoolkit.googleapis.com iam.googleapis.com logging.googleapis.com recaptchaenterprise.googleapis.com secretmanager.googleapis.com storage.googleapis.com

& gcloud artifacts repositories describe $Repository --location=$Region --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud artifacts repositories create $Repository --repository-format=docker --location=$Region --project=$ProjectId
}
& gcloud firestore databases describe '--database=(default)' --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud firestore databases create '--database=(default)' --location=$Region --type=firestore-native --project=$ProjectId
}
& gcloud iam service-accounts describe $Account --project=$ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud iam service-accounts create $AccountId --project=$ProjectId --display-name='HumanWire DecisionOS runtime'
}

Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$Account" --role=roles/datastore.user --condition=None
Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$Account" --role=roles/firebaseauth.admin --condition=None
Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$Account" --role=roles/logging.logWriter --condition=None
foreach ($SecretName in @($FirebaseApiKeySecret, $FirebaseAppIdSecret, $AppCheckSiteKeySecret)) {
    Invoke-Gcloud secrets describe $SecretName --project=$ProjectId
    Invoke-Gcloud secrets add-iam-policy-binding $SecretName --project=$ProjectId --member="serviceAccount:$Account" --role=roles/secretmanager.secretAccessor --condition=None
}

& npx --yes firebase-tools@15.27.0 deploy --project $FirebaseProjectId --config infra/firebase/firebase.json --only 'firestore:rules,firestore:indexes,storage'
if ($LASTEXITCODE -ne 0) { throw 'decisionos_rules_deployment_failed' }

Invoke-Gcloud builds submit --config=infra/google/cloudbuild.yaml --substitutions="_IMAGE=$TaggedImage" .
$Digest = (& gcloud artifacts docker images describe $TaggedImage --format='value(image_summary.digest)').Trim()
if ($LASTEXITCODE -ne 0 -or $Digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'decisionos_image_digest_invalid' }
$PinnedImage = "$BaseImage@$Digest"
$PublicEnvironment = "HUMANWIRE_SERVICE_ROLE=decisionos,HUMANWIRE_DECISIONOS_PROJECT_ID=$FirebaseProjectId,HUMANWIRE_DECISIONOS_FIRESTORE_DATABASE=(default),HUMANWIRE_DECISIONOS_ALLOWED_HOSTS=decisionos.invalid,HUMANWIRE_DECISIONOS_FIREBASE_AUTH_DOMAIN=$FirebaseProjectId.firebaseapp.com,HUMANWIRE_DECISIONOS_FIREBASE_STORAGE_BUCKET=$FirebaseProjectId.firebasestorage.app,HUMANWIRE_DECISIONOS_APP_CHECK_ENFORCED=false"

Invoke-Gcloud run deploy $Service --image=$PinnedImage --region=$Region --project=$ProjectId --service-account=$Account --allow-unauthenticated --min-instances=0 --max-instances=3 --concurrency=40 --timeout=60 --cpu=1 --memory=512Mi --set-env-vars=$PublicEnvironment --set-secrets=$SecretBindings
$DecisionOSUrl = (& gcloud run services describe $Service --region=$Region --project=$ProjectId --format='value(status.url)').Trim()
$DecisionOSHost = ([Uri]$DecisionOSUrl).Host
Invoke-Gcloud run services update $Service --region=$Region --project=$ProjectId --image=$PinnedImage --update-env-vars="HUMANWIRE_DECISIONOS_ALLOWED_HOSTS=$DecisionOSHost"

Write-Output "decisionos_url=$DecisionOSUrl"
Write-Output "image_digest=$Digest"
Write-Output 'app_check_enforced=false'
