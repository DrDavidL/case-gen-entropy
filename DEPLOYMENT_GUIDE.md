# Azure Container Apps Deployment Guide

## 🚀 Phase 1: Local Setup & Testing

### 1. Install Prerequisites
```bash
# Install Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Verify installation
az --version
docker --version
```

### 2. Login to Azure
```bash
az login
# Follow browser login prompt
```

### 3. Test Locally First
```bash
# Make scripts executable
chmod +x test-local.sh

# Create .env file with your values
cp .env.example .env
nano .env  # Edit with your actual values

# Test locally
./test-local.sh
```

## 🏗️ Phase 2: Azure Infrastructure Setup (Container Apps)

### 4. Prepare GitHub Secrets (see github-secrets-setup.md)
Required:
- `AZURE_CREDENTIALS` (Service Principal JSON: clientId, clientSecret, subscriptionId, tenantId)
- `ACR_NAME`, `ACR_USERNAME`, `ACR_PASSWORD`
- `POSTGRES_URL`, `OPENAI_API_KEY`
- `APP_USERNAME`, `APP_PASSWORD`

### 5. (Optional) One-time infra creation
```bash
az group create --name medical-case-generator-rg --location eastus
```

## 🚀 Phase 3: Manual Deployment (Optional)

### 6. Deploy to Azure Container Apps via Bicep
```bash
./deploy-container-apps.sh
```

## 🔄 Phase 4: GitHub Actions CI/CD

### 7. Setup GitHub Secrets
Follow `github-secrets-setup.md` to configure:
- `AZURE_CREDENTIALS`
- `ACR_NAME`, `ACR_USERNAME`, `ACR_PASSWORD`
- `POSTGRES_URL`, `OPENAI_API_KEY`

### 8. Enable Auto-Deployment (Container Apps)
```bash
# Push to trigger deployment
git add .
git commit -m "Deploy to Azure Container Apps"
git push origin main

# Watch GitHub Actions tab for deployment progress
```

## 📊 Phase 5: Monitoring & Management

### 9. Monitor Your App (Container Apps)
```bash
# View logs
az containerapp logs show --name backend-app --resource-group medical-case-generator-rg --follow

# Show FQDNs
az containerapp show --name backend-app --resource-group medical-case-generator-rg --query "properties.configuration.ingress.fqdn" -o tsv
az containerapp show --name frontend-app --resource-group medical-case-generator-rg --query "properties.configuration.ingress.fqdn" -o tsv
```

### 10. Scale & Update
```bash
# Update app: Just push to main branch
# Scale: Modify CPU/memory in deployment config
# Delete: az container delete --resource-group medical-case-generator-rg --name medical-case-generator --yes
```

## 🎯 Expected Costs

| Resource | Monthly Cost |
|----------|--------------|
| Container Apps | $10-30 |
| Container Registry | $5 |
| Bandwidth | $5-10 |
| **Total** | **~$20-45/month** |

## 🔧 Troubleshooting

### Container Won't Start
```bash
# Check logs for all containers
az containerapp logs show --name backend-app --resource-group medical-case-generator-rg --follow
az containerapp logs show --name frontend-app --resource-group medical-case-generator-rg --follow
az containerapp logs show --name redis-app --resource-group medical-case-generator-rg --follow
```

### Database Connection Issues
- Verify PostgreSQL URL includes `sslmode=require`
- Check firewall allows Azure IPs
- Test connection string locally first

### High Costs
- Monitor with Azure Cost Management
- Use container instance scheduling for dev environments
- Use Azure Container Apps minimal replicas and autoscaling

## ✅ Success Checklist

- [ ] Local testing works
- [ ] Azure infrastructure created
- [ ] Manual deployment successful
- [ ] GitHub Actions configured
- [ ] Application accessible via public URL
- [ ] Database connections working
- [ ] Case generation & export functioning

**Your Medical Case Generator is now live on Azure! 🏥✨**
