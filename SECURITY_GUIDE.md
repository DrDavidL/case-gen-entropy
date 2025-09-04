# 🔒 Security Best Practices

## ⚠️ Critical Security Rules

### 1. **NEVER commit secrets to Git**
```bash
# ❌ NEVER do this:
git add deployment-config.yaml  # Contains secrets!

# ✅ Instead:
git add deployment-config.template.yaml  # Safe template only
```

### 2. **Files that contain secrets (git-ignored)**
- `deployment-config.yaml` - Contains all secrets
- `.env` - Local environment variables
- Any file ending in `-config.yaml`

### 3. **Files safe to commit**
- `deployment-config.template.yaml` - Template with placeholders
- `.env.example` - Example environment variables
- All scripts (`setup-azure.sh`, etc.)

## 🔐 Secure Workflow

### Local Deployment:
1. Run `./setup-azure.sh` (creates infrastructure)
2. Run `./create-deployment-config.sh` (creates secrets file locally)
3. Run `./deploy-manual.sh` (uses local secrets file)

### GitHub Actions Deployment:
- Uses GitHub Secrets (encrypted)
- Never stores secrets in files
- Generates deployment config dynamically

## 🛡️ Security Measures Implemented

### Git Protection:
```bash
# .gitignore includes:
deployment-config.yaml        # Local secrets file
deployment-config-*.yaml      # Any config with secrets
.env                         # Environment variables
```

### Azure Security:
- **Container Registry**: Private, requires authentication
- **Environment Variables**: Marked as `secureValue` in Azure
- **Service Principal**: Least-privilege access to resource group only
- **HTTPS**: Automatic SSL termination available

### GitHub Security:
- **Repository Secrets**: Encrypted at rest
- **Action Logs**: Secrets are masked in logs
- **Branch Protection**: Can be enabled for additional security

## 🚨 What to do if secrets are exposed:

### If you accidentally committed secrets:
```bash
# Remove from git history
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch deployment-config.yaml' \
  --prune-empty --tag-name-filter cat -- --all

# Force push (WARNING: destructive)
git push origin --force --all
```

### Rotate compromised credentials:
1. **OpenAI API Key**: Create new key in OpenAI dashboard
2. **PostgreSQL**: Change password in your database provider
3. **Azure Container Registry**: Regenerate password in Azure portal

## ✅ Security Checklist

- [ ] `.gitignore` excludes secret files
- [ ] `deployment-config.yaml` never committed
- [ ] GitHub secrets configured properly
- [ ] Local `.env` file secured (not committed)
- [ ] ACR passwords rotated regularly
- [ ] Database uses SSL/TLS connections
- [ ] Service principal has minimal permissions

## 🔍 Audit Your Repository

```bash
# Check what's tracked by git
git ls-files | grep -E "\.(yaml|env)$"

# Should NOT include:
# - deployment-config.yaml
# - .env

# Should include:
# - deployment-config.template.yaml
# - .env.example
```

**Remember: Security is not optional for medical applications!** 🏥🔒