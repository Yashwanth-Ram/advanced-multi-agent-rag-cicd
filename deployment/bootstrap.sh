#!/usr/bin/env bash
# Run once as a project administrator after editing .env.
set -euo pipefail
set -a
source .env
set +a
PROJECT="$GOOGLE_CLOUD_PROJECT"
REGION="$GOOGLE_CLOUD_LOCATION"
gcloud services enable aiplatform.googleapis.com cloudbuild.googleapis.com storage.googleapis.com   secretmanager.googleapis.com modelarmor.googleapis.com dlp.googleapis.com   discoveryengine.googleapis.com cloudtrace.googleapis.com logging.googleapis.com   monitoring.googleapis.com --project="$PROJECT"
for ACCOUNT in advanced-rag-runtime advanced-rag-build advanced-rag-ci; do
  if ! gcloud iam service-accounts describe "$ACCOUNT@$PROJECT.iam.gserviceaccount.com" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$ACCOUNT" --project="$PROJECT"
  fi
done
for ROLE in roles/aiplatform.user roles/discoveryengine.user roles/modelarmor.user roles/dlp.user roles/cloudtrace.agent roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME_SERVICE_ACCOUNT" --role="$ROLE" --quiet --condition=None
done
for ROLE in roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" --role="$ROLE" --quiet --condition=None
done
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$CI_SERVICE_ACCOUNT" --role=roles/logging.logWriter --quiet --condition=None
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SERVICE_ACCOUNT" --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" --role=roles/iam.serviceAccountUser --project="$PROJECT" --quiet --condition=None
if ! gcloud storage buckets describe "gs://$ARTIFACT_BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$ARTIFACT_BUCKET" --location="$REGION" --project="$PROJECT" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://$ARTIFACT_BUCKET" --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" --role=roles/storage.objectAdmin --condition=None
# The Cloud Build source archive uses a dedicated bucket; no company documents are changed.
SOURCE_BUCKET="$PROJECT-advanced-rag-build-source"
if ! gcloud storage buckets describe "gs://$SOURCE_BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$SOURCE_BUCKET" --location="$REGION" --project="$PROJECT" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://$SOURCE_BUCKET" --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" --role=roles/storage.objectViewer --condition=None
# Provision the service identity, then permit runtime service-account impersonation.
gcloud beta services identity create --service=aiplatform.googleapis.com --project="$PROJECT"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SERVICE_ACCOUNT" --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com" --role=roles/iam.serviceAccountTokenCreator --project="$PROJECT" --quiet --condition=None
printf 'Bootstrap completed. Next: create security templates and configure Phoenix secret.\n'
