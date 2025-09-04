# Azure Deployment Guide

## Option 1: Azure Container Instances (Recommended for MVP)

### Prerequisites:
1. **Azure CLI**: `az login`
2. **Docker installed locally**
3. **Azure Container Registry** (or use Docker Hub)

### Quick Setup:

```bash
# 1. Create resource group
az group create --name medical-case-generator-rg --location eastus

# 2. Create Azure Container Registry
az acr create --resource-group medical-case-generator-rg --name medcasegen --sku Basic --admin-enabled true

# 3. Get ACR credentials
az acr credential show --name medcasegen

# 4. Build and push images
az acr build --registry medcasegen --image case-generator-backend:latest -f Dockerfile.backend .
az acr build --registry medcasegen --image case-generator-frontend:latest -f Dockerfile.frontend .

# 5. Deploy with container group
az container create \
  --resource-group medical-case-generator-rg \
  --name medical-case-generator \
  --yaml container-group.yaml
```

### Required GitHub Secrets:
- `ACR_USERNAME`: Azure Container Registry username
- `ACR_PASSWORD`: Azure Container Registry password  
- `POSTGRES_URL`: Your external PostgreSQL connection string
- `OPENAI_API_KEY`: Your OpenAI API key

---

## Option 2: Azure App Service (Alternative)

### For direct GitHub deployment without Docker:

1. **Create App Service Plan**:
```bash
az appservice plan create --name medical-case-plan --resource-group medical-case-generator-rg --sku B1 --is-linux
```

2. **Create Web Apps**:
```bash
# Backend
az webapp create --resource-group medical-case-generator-rg --plan medical-case-plan --name medical-case-backend --runtime "PYTHON|3.11"

# Frontend  
az webapp create --resource-group medical-case-generator-rg --plan medical-case-plan --name medical-case-frontend --runtime "PYTHON|3.11"
```

3. **Configure GitHub Actions deployment**:
   - Enable GitHub Actions in Azure portal
   - Add secrets for database connections

---

## Cost Comparison:

| Service | Monthly Cost (Estimated) | Pros | Cons |
|---------|-------------------------|------|------|
| **Container Instances** | $20-50 | Easy scaling, multi-service | Pay per second |
| **App Service Basic** | $55/month | Integrated CI/CD | Single service per app |
| **Container Apps** | $10-30 | Serverless scaling | More complex setup |

---

## Recommended Architecture:

```
GitHub Actions → Azure Container Registry → Azure Container Instances
     ↓
[Backend Container] ← → [Redis Container] ← → [Frontend Container]
     ↓
[External PostgreSQL]
```

## Environment Variables Needed:

- `POSTGRES_URL`: Your external PostgreSQL URL
- `OPENAI_API_KEY`: OpenAI API key  
- `REDIS_URL`: Internal Redis connection (handled by Docker)
- `BACKEND_URL`: Backend service URL (for frontend)