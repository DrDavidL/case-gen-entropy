#!/bin/bash

# Deploy Medical Case Generator to Azure Container Apps
# Follows the CLI-based approach (no Bicep/ARM templates)
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - .env file with required secrets
#
# Usage:
#   ./deploy-aca.sh          # First-time setup + deploy
#   ./deploy-aca.sh redeploy # Rebuild images and update apps

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────
APP="casegen"
RESOURCE_GROUP="${APP}-rg"
ACR_NAME="${APP}acr"
ENVIRONMENT="${APP}-env"
LOCATION="eastus"

ACR_SERVER="${ACR_NAME}.azurecr.io"

# ── Load secrets from .env ─────────────────────────────────────────
if [[ ! -f .env ]]; then
    echo "Error: .env file not found. Create one with required variables."
    echo "Required: OPENAI_API_KEY, POSTGRES_URL"
    echo "Optional: POSTGRES_URL_SIM_READY, APP_USERNAME, APP_PASSWORD"
    exit 1
fi
set -a; source .env; set +a

# Validate required vars
for var in OPENAI_API_KEY POSTGRES_URL; do
    if [[ -z "${!var:-}" ]]; then
        echo "Error: $var is not set in .env"
        exit 1
    fi
done

# ── Redeploy shortcut ─────────────────────────────────────────────
if [[ "${1:-}" == "redeploy" ]]; then
    echo "=== Rebuilding and redeploying ==="

    echo "Building backend image..."
    az acr build --registry $ACR_NAME --image ${APP}-backend:v1 \
        --file Dockerfile.backend --platform linux/amd64 .

    echo "Building frontend image..."
    az acr build --registry $ACR_NAME --image ${APP}-frontend:v1 \
        --file Dockerfile.frontend --platform linux/amd64 .

    echo "Updating backend..."
    az containerapp update --name ${APP}-backend --resource-group $RESOURCE_GROUP \
        --image ${ACR_SERVER}/${APP}-backend:v1

    echo "Updating frontend..."
    az containerapp update --name ${APP}-frontend --resource-group $RESOURCE_GROUP \
        --image ${ACR_SERVER}/${APP}-frontend:v1

    echo ""
    echo "=== Redeploy complete ==="
    FRONTEND_FQDN=$(az containerapp show --name ${APP}-frontend --resource-group $RESOURCE_GROUP \
        --query "properties.configuration.ingress.fqdn" -o tsv)
    echo "Frontend: https://$FRONTEND_FQDN"
    exit 0
fi

# ── Step 1: Subscription ──────────────────────────────────────────
echo "=== Current Azure subscription ==="
az account show --query "{name:name, id:id}" -o table
echo ""
read -p "Continue with this subscription? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Run 'az account set --subscription <id>' first."
    exit 1
fi

# ── Step 2: Resource Group ─────────────────────────────────────────
echo "Creating resource group..."
az group create --name $RESOURCE_GROUP --location $LOCATION -o none

# ── Step 3: Container Registry ─────────────────────────────────────
echo "Creating container registry..."
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME \
    --sku Basic --admin-enabled true -o none

# ── Step 4: Build & push images ───────────────────────────────────
echo "Building backend image (this takes a few minutes)..."
az acr build --registry $ACR_NAME --image ${APP}-backend:v1 \
    --file Dockerfile.backend --platform linux/amd64 .

echo "Building frontend image..."
az acr build --registry $ACR_NAME --image ${APP}-frontend:v1 \
    --file Dockerfile.frontend --platform linux/amd64 .

# ── Step 5: Register providers ─────────────────────────────────────
echo "Registering Container Apps providers..."
az extension add --name containerapp --upgrade --yes 2>/dev/null || true
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait

# ── Step 6: Container Apps Environment ─────────────────────────────
echo "Creating Container Apps environment..."
az containerapp env create --name $ENVIRONMENT --resource-group $RESOURCE_GROUP \
    --location $LOCATION -o none

# ── Step 7: Deploy Redis (internal only) ───────────────────────────
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

echo "Deploying Redis..."
az containerapp create \
    --name ${APP}-redis \
    --resource-group $RESOURCE_GROUP \
    --environment $ENVIRONMENT \
    --image redis:7-alpine \
    --target-port 6379 \
    --ingress internal \
    --transport tcp \
    --cpu 0.25 --memory 0.5Gi \
    --min-replicas 1 --max-replicas 1 \
    --command "redis-server" "--" "--appendonly" "yes" \
    -o none

# Get Redis internal FQDN
REDIS_FQDN=$(az containerapp show --name ${APP}-redis --resource-group $RESOURCE_GROUP \
    --query "properties.configuration.ingress.fqdn" -o tsv)
REDIS_URL="redis://${REDIS_FQDN}:6379/0"

echo "Redis URL: $REDIS_URL"

# ── Step 8: Deploy Backend ─────────────────────────────────────────
echo "Deploying backend..."

# Build env vars array
ENV_VARS="OPENAI_API_KEY=secretref:openai-api-key"
ENV_VARS="$ENV_VARS POSTGRES_URL=secretref:postgres-url"
ENV_VARS="$ENV_VARS REDIS_URL=${REDIS_URL}"
ENV_VARS="$ENV_VARS APP_USERNAME=${APP_USERNAME:-admin}"
ENV_VARS="$ENV_VARS APP_PASSWORD=secretref:app-password"

SECRETS="openai-api-key=${OPENAI_API_KEY}"
SECRETS="$SECRETS postgres-url=${POSTGRES_URL}"
SECRETS="$SECRETS app-password=${APP_PASSWORD:-dhds-bypass}"

# Add sim-ready DB if configured
if [[ -n "${POSTGRES_URL_SIM_READY:-}" ]]; then
    SECRETS="$SECRETS postgres-url-sim-ready=${POSTGRES_URL_SIM_READY}"
    ENV_VARS="$ENV_VARS POSTGRES_URL_SIM_READY=secretref:postgres-url-sim-ready"
fi

az containerapp create \
    --name ${APP}-backend \
    --resource-group $RESOURCE_GROUP \
    --environment $ENVIRONMENT \
    --image ${ACR_SERVER}/${APP}-backend:v1 \
    --registry-server $ACR_SERVER \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8000 \
    --ingress external \
    --cpu 1.0 --memory 2.0Gi \
    --min-replicas 1 --max-replicas 1 \
    --secrets $SECRETS \
    --env-vars $ENV_VARS \
    -o none

# Get Backend URL
BACKEND_FQDN=$(az containerapp show --name ${APP}-backend --resource-group $RESOURCE_GROUP \
    --query "properties.configuration.ingress.fqdn" -o tsv)
BACKEND_URL_EXTERNAL="https://${BACKEND_FQDN}"

echo "Backend URL: $BACKEND_URL_EXTERNAL"

# ── Step 9: Deploy Frontend ────────────────────────────────────────
echo "Deploying frontend..."
az containerapp create \
    --name ${APP}-frontend \
    --resource-group $RESOURCE_GROUP \
    --environment $ENVIRONMENT \
    --image ${ACR_SERVER}/${APP}-frontend:v1 \
    --registry-server $ACR_SERVER \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8501 \
    --ingress external \
    --cpu 0.5 --memory 1.0Gi \
    --min-replicas 1 --max-replicas 1 \
    --env-vars "BACKEND_URL=${BACKEND_URL_EXTERNAL}" "APP_USERNAME=${APP_USERNAME:-admin}" "APP_PASSWORD=${APP_PASSWORD:-dhds-bypass}" \
    -o none

# ── Done ───────────────────────────────────────────────────────────
FRONTEND_FQDN=$(az containerapp show --name ${APP}-frontend --resource-group $RESOURCE_GROUP \
    --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""
echo "Frontend:  https://$FRONTEND_FQDN"
echo "Backend:   $BACKEND_URL_EXTERNAL"
echo ""
echo "To redeploy after code changes:"
echo "  ./deploy-aca.sh redeploy"
echo ""
echo "To tear down everything:"
echo "  az group delete --name $RESOURCE_GROUP --yes"
