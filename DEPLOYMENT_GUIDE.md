# Complete Azure Container Instances Deployment Guide

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

## 🏗️ Phase 2: Azure Infrastructure Setup

### 4. Run Azure Setup
```bash
# Make script executable
chmod +x setup-azure.sh

# Run setup (takes 5-10 minutes)
./setup-azure.sh

# Save the output values for GitHub secrets!
```

### 5. Configure Environment
```bash
# Edit the generated deployment config
nano deployment-config.yaml

# Replace placeholder values:
# - REPLACE_WITH_POSTGRES_URL → your PostgreSQL URL
# - REPLACE_WITH_OPENAI_KEY → your OpenAI API key
```

## 🚀 Phase 3: Manual Deployment

### 6. Deploy to Azure
```bash
# Make script executable  
chmod +x deploy-manual.sh

# Deploy
./deploy-manual.sh

# Your app will be live in ~2 minutes!
```

## 🔄 Phase 4: GitHub Actions CI/CD

### 7. Setup GitHub Secrets
Follow `github-secrets-setup.md` to configure:
- `AZURE_CREDENTIALS`
- `ACR_NAME`, `ACR_USERNAME`, `ACR_PASSWORD`
- `POSTGRES_URL`, `OPENAI_API_KEY`

### 8. Enable Auto-Deployment
```bash
# Push to trigger deployment
git add .
git commit -m "Deploy to Azure Container Instances"
git push origin main

# Watch GitHub Actions tab for deployment progress
```

## 📊 Phase 5: Monitoring & Management

### 9. Monitor Your App
```bash
# Check container status
az container show --resource-group medical-case-generator-rg --name medical-case-generator

# View logs
az container logs --resource-group medical-case-generator-rg --name medical-case-generator --container-name backend

# Monitor costs
az consumption usage list --scope /subscriptions/$(az account show --query id --output tsv)/resourceGroups/medical-case-generator-rg
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
| Container Instances | $20-40 |
| Container Registry | $5 |
| Bandwidth | $5-10 |
| **Total** | **~$30-55/month** |

## 🔧 Troubleshooting

### Container Won't Start
```bash
# Check logs for all containers
az container logs --resource-group medical-case-generator-rg --name medical-case-generator --container-name backend
az container logs --resource-group medical-case-generator-rg --name medical-case-generator --container-name frontend
az container logs --resource-group medical-case-generator-rg --name medical-case-generator --container-name redis
```

### Database Connection Issues
- Verify PostgreSQL URL includes `sslmode=require`
- Check firewall allows Azure IPs
- Test connection string locally first

### High Costs
- Monitor with Azure Cost Management
- Use container instance scheduling for dev environments
- Consider Azure Container Apps for auto-scaling

## ✅ Success Checklist

- [ ] Local testing works
- [ ] Azure infrastructure created
- [ ] Manual deployment successful
- [ ] GitHub Actions configured
- [ ] Application accessible via public URL
- [ ] Database connections working
- [ ] Case generation & export functioning

**Your Medical Case Generator is now live on Azure! 🏥✨**