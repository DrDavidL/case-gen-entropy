# GitHub Secrets Configuration

After running `setup-azure.sh`, you'll need to configure these GitHub repository secrets:

## 🔑 Required GitHub Secrets

Go to your GitHub repository → Settings → Secrets and variables → Actions → New repository secret

### 1. Azure Service Principal
Create a service principal for GitHub Actions:

```bash
# Get your subscription ID
SUBSCRIPTION_ID=$(az account show --query id --output tsv)

# Create service principal
az ad sp create-for-rbac \
  --name "medical-case-generator-sp" \
  --role contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/medical-case-generator-rg \
  --json-auth

# Copy the output JSON and add as AZURE_CREDENTIALS secret
```

### 2. Container Registry Credentials
From setup-azure.sh output:

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `ACR_NAME` | Container registry name | `medcasegen12345` |
| `ACR_USERNAME` | Registry username | `medcasegen12345` |
| `ACR_PASSWORD` | Registry password | `abc123...` |

### 3. Application Configuration

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `POSTGRES_URL` | PostgreSQL connection string | `postgresql://user:pass@host:5432/db?sslmode=require` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-proj-...` |

### 4. Azure Service Principal JSON
The `AZURE_CREDENTIALS` secret should contain:

```json
{
  "clientId": "12345678-1234-1234-1234-123456789012",
  "clientSecret": "your-client-secret",
  "subscriptionId": "12345678-1234-1234-1234-123456789012",
  "tenantId": "12345678-1234-1234-1234-123456789012"
}
```

## ✅ Verification Checklist

- [ ] `AZURE_CREDENTIALS` - Azure service principal JSON
- [ ] `ACR_NAME` - Container registry name (without .azurecr.io)
- [ ] `ACR_USERNAME` - Registry username
- [ ] `ACR_PASSWORD` - Registry password
- [ ] `POSTGRES_URL` - PostgreSQL connection string
- [ ] `OPENAI_API_KEY` - OpenAI API key

## 🚀 Test Deployment

After setting secrets:
1. Push code to main branch
2. Watch GitHub Actions workflow
3. Access your app at the generated URL

## 🔧 Troubleshooting

**Common Issues:**
- Service principal lacks permissions → Re-run with correct scope
- Registry credentials expired → Regenerate in Azure portal
- Container startup fails → Check environment variables in logs