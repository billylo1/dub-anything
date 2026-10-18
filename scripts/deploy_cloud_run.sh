#!/usr/bin/env bash
# ==============================================================================
# Deploy Dub Anything Pipeline to Google Cloud Run Jobs with NVIDIA L4 GPU
# ==============================================================================
set -eo pipefail

PROJECT_ID="${GCP_PROJECT:-videos-billylo}"
REGION="${GCP_REGION:-us-central1}"
REPO_NAME="dub-anything-repo"
IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/dub-runner:latest"
JOB_NAME="ctv-news-dub"
BUCKET_NAME="${PROJECT_ID}-models"

echo "=========================================================="
echo " Deploying Dub Anything to Cloud Run Jobs (GPU)"
echo " Project: $PROJECT_ID | Region: $REGION"
echo "=========================================================="

# 1. Enable required GCP APIs
echo "[*] Step 1: Ensuring required GCP services are enabled..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    storage.googleapis.com \
    --project "$PROJECT_ID"

# 2. Artifact Registry
echo "[*] Step 2: Checking Artifact Registry repository..."
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    echo "[*] Creating Artifact Registry repository '$REPO_NAME'..."
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Container images for Dub Anything GPU jobs" \
        --project "$PROJECT_ID"
else
    echo "[*] Artifact Registry repository '$REPO_NAME' already exists."
fi

# 3. Model Storage Bucket
echo "[*] Step 3: Checking Cloud Storage bucket for models cache..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="$PROJECT_ID" &>/dev/null; then
    echo "[*] Creating bucket gs://${BUCKET_NAME}..."
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --location="$REGION" \
        --project="$PROJECT_ID"
else
    echo "[*] Bucket gs://${BUCKET_NAME} already exists."
fi

# 4. Secret Manager for GEMINI_API_KEY
echo "[*] Step 4: Checking Secret Manager for GEMINI_API_KEY..."
if ! gcloud secrets describe GEMINI_API_KEY --project="$PROJECT_ID" &>/dev/null; then
    if [ -n "$GEMINI_API_KEY" ]; then
        echo "[*] Creating Secret 'GEMINI_API_KEY' from current environment..."
        echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY \
            --data-file=- \
            --project="$PROJECT_ID"
    else
        echo "[!] WARNING: GEMINI_API_KEY is not set in environment and secret does not exist."
        echo "    Create it with: echo -n 'KEY' | gcloud secrets create GEMINI_API_KEY --data-file=- --project=$PROJECT_ID"
    fi
else
    echo "[*] Secret 'GEMINI_API_KEY' exists."
fi

# Grant Cloud Run Service Account access to Secret
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "[*] Granting Secret Manager Secret Accessor to $COMPUTE_SA..."
gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" >/dev/null 2>&1 || true

# 5. Cloud Build: Build and push container image
echo "[*] Step 5: Building container image via Cloud Build..."
gcloud builds submit \
    --config cloudbuild.yaml \
    --project "$PROJECT_ID"

# 6. Deploy Cloud Run Job with NVIDIA L4 GPU
echo "[*] Step 6: Deploying Cloud Run Job '$JOB_NAME' with NVIDIA L4 GPU..."
gcloud run jobs deploy "$JOB_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --execution-environment=gen2 \
    --gpu=1 \
    --gpu-type=nvidia-l4 \
    --no-gpu-zonal-redundancy \
    --cpu=4 \
    --memory=16Gi \
    --task-timeout=3600s \
    --max-retries=1 \
    --set-secrets=GEMINI_API_KEY=GEMINI_API_KEY:latest \
    --add-volume=name=models-vol,type=cloud-storage,bucket="${BUCKET_NAME}" \
    --add-volume-mount=volume=models-vol,mount-path=/models \
    --project="$PROJECT_ID"

# 7. Setup Cloud Scheduler
echo "[*] Step 7: Configuring Cloud Scheduler (Daily at 7:45 PM Toronto / Eastern time)..."
SCHEDULER_JOB="daily-ctv-dub"
SCHEDULER_URI="https://${REGION}-run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

if gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    echo "[*] Updating existing Cloud Scheduler job '$SCHEDULER_JOB'..."
    gcloud scheduler jobs update http "$SCHEDULER_JOB" \
        --location="$REGION" \
        --schedule="45 19 * * *" \
        --time-zone="America/Toronto" \
        --uri="$SCHEDULER_URI" \
        --http-method=POST \
        --oauth-service-account-email="$COMPUTE_SA" \
        --project="$PROJECT_ID"
else
    echo "[*] Creating new Cloud Scheduler job '$SCHEDULER_JOB'..."
    gcloud scheduler jobs create http "$SCHEDULER_JOB" \
        --location="$REGION" \
        --schedule="45 19 * * *" \
        --time-zone="America/Toronto" \
        --uri="$SCHEDULER_URI" \
        --http-method=POST \
        --oauth-service-account-email="$COMPUTE_SA" \
        --project="$PROJECT_ID"
fi

echo "=========================================================="
echo " Deployment Successfully Completed!"
echo " Job Name: $JOB_NAME ($REGION)"
echo " Image: $IMAGE_TAG"
echo " Model Cache: gs://${BUCKET_NAME}"
echo ""
echo " To run the job manually on-demand:"
echo "   gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "=========================================================="
