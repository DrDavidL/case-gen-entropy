# Azure Deployment Guide (Container Apps)

This project deploys to Azure Container Apps using a Bicep template and GitHub Actions. Legacy Azure Container Instances (ACI) instructions have been deprecated.

## Prerequisites
- Azure subscription with rights to create Resource Groups and Container Apps
- Azure CLI: `az login`
- Azure Container Registry (ACR)

## One‑Time Setup
```bash
# 1) Create resource group (idempotent)
az group create --name medical-case-generator-rg --location eastus

# 2) Create ACR if you don't have one
az acr create --resource-group medical-case-generator-rg --name <your_acr_name> --sku Basic --admin-enabled true
```

## GitHub Secrets (required)
- `AZURE_CREDENTIALS`: JSON for a Service Principal with `clientId`, `clientSecret`, `subscriptionId`, `tenantId`
- `ACR_NAME`: Your ACR name (without `.azurecr.io`)
- `ACR_USERNAME`: ACR username
- `ACR_PASSWORD`: ACR password
- `POSTGRES_URL`: PostgreSQL connection string (include `sslmode=require`)
- `OPENAI_API_KEY`: OpenAI API key
- `APP_USERNAME`, `APP_PASSWORD`: App auth credentials

These are consumed by `.github/workflows/deploy.yml` and passed to the Bicep template as secrets.

## Deploy Pipeline (GitHub Actions)
On push to `main`, the workflow:
- Builds backend and frontend images in ACR
- Deploys Container Apps environment and apps via `container-apps-bicep.bicep`
- Outputs frontend and backend URLs

## Manual Deploy (alternative)
You can also deploy locally using the provided script:
```bash
# Ensure .env contains non-secret parameters and required secrets locally
./deploy-container-apps.sh
```

## Architecture
```
GitHub Actions → Azure Container Registry → Azure Container Apps (redis-app, backend-app, frontend-app)
     ↓
[Backend] ← → [Redis] ← → [Frontend]
     ↓
[External PostgreSQL]
```

## Environment Variables

- `POSTGRES_URL`: External PostgreSQL URL
- `OPENAI_API_KEY`: OpenAI API key
- `REDIS_URL`: Set via Bicep to internal DNS `redis://redis-app.<env>.internal:6379/0`
- `BACKEND_URL`: Provided to frontend as `https://<backend fqdn>`
