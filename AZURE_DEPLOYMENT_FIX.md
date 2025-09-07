# Azure Backend Deployment Fix

## Issue Summary
The FastAPI backend was crashing on Azure Container Apps due to the `reload=True` parameter in the uvicorn configuration, which is meant for development only and can cause instability in production environments.

## Changes Made

### 1. Fixed Startup Script (`start_backend.py`)
- Changed `reload=True` to `reload=False` and added `workers=1` for production stability

### 2. Updated Dockerfile.backend
- Added a sed command to ensure the reload parameter is removed during container build
- This provides a double safeguard against the reload parameter in production

### 3. Enhanced Database Initialization (`backend/app/main.py`)
- Uncommented and made conditional database table creation
- Added error handling to prevent startup failures if database connection fails
- Used environment variable to distinguish between development and production

### 4. Updated Environment Configuration
- Added `ENVIRONMENT=production` to Azure Bicep deployment
- Added `ENVIRONMENT=development` to local docker-compose for proper environment detection

## Testing
The fix has been tested locally and the backend starts successfully without the reload parameter.

## Deployment Instructions

1. **Build and Push Containers:**
   ```bash
   # Login to Azure
   az login
   
   # Set your subscription (if needed)
   az account set --subscription "your-subscription-id"
   
   # Build and push backend container
   az acr build --registry your-acr-name --image case-generator-backend:latest -f Dockerfile.backend .
   
   # Build and push frontend container
   az acr build --registry your-acr-name --image case-generator-frontend:latest -f Dockerfile.frontend .
   ```

2. **Deploy to Azure Container Apps:**
   ```bash
   # Run the deployment script
   ./deploy-container-apps.sh
   ```

3. **Alternative Manual Deployment:**
   ```bash
   # Deploy using Bicep directly
   az deployment group create \
       --resource-group $RESOURCE_GROUP \
       --template-file container-apps-bicep.bicep \
       --parameters \
           location=$LOCATION \
           environmentName=$ENVIRONMENT_NAME \
           containerRegistry=$CONTAINER_REGISTRY \
           acrUsername=$ACR_USERNAME \
           acrPassword=$ACR_PASSWORD \
           postgresUrl="$POSTGRES_URL" \
           openaiApiKey="$OPENAI_API_KEY" \
           appUsername="$APP_USERNAME" \
           appPassword="$APP_PASSWORD"
   ```

## Additional Improvements

1. **Better Error Handling:** Added try-catch blocks around database initialization to prevent startup crashes
2. **Environment Detection:** The application now distinguishes between development and production environments
3. **Production Optimization:** Disabled hot-reloading and set appropriate worker configuration for production

## Troubleshooting

If you encounter issues after deployment:

1. **Check Container Logs:**
   ```bash
   az containerapp logs show --name backend-app --resource-group $RESOURCE_GROUP
   ```

2. **Verify Environment Variables:**
   ```bash
   az containerapp show --name backend-app --resource-group $RESOURCE_GROUP --query "properties.template.containers[0].env"
   ```

3. **Check Health Endpoint:**
   Visit `https://your-backend-url/health` to verify the backend is running correctly

The backend should now deploy successfully to Azure Container Apps without crashing.