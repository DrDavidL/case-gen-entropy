# Azure Backend Deployment Fix - Version 3

## Issue Summary
The FastAPI backend was crashing on Azure Container Apps due to Redis connection issues. The error showed:
```
redis.exceptions.ConnectionError: Error -2 connecting to redis-app.internal:6379. Name or service not known.
```

This was caused by incorrect internal service discovery configuration in Azure Container Apps.

## Root Cause Identified
The actual root cause was **incorrect Redis service discovery configuration**. In Azure Container Apps, internal service discovery uses the format `<app-name>.<environment-name>.internal`, but the configuration was using just `redis-app.internal`.

## Redis Architecture Consistency

The system uses a **dedicated Redis container** in both environments with consistent configuration:

### Local Development (docker-compose.yml):
- **Redis Container**: `redis` service running `redis:7-alpine` image
- **Network**: Docker network service discovery (`redis://redis:6379/0`)
- **Persistence**: Configured with append-only mode
- **Health Checks**: Built-in health check monitoring

### Azure Deployment (container-apps-bicep.bicep):
- **Redis Container**: `redis-app` running `redis:7-alpine` image
- **Network**: Azure Container Apps internal service discovery (`redis://redis-app.medical-case-env.internal:6379/0`)
- **Persistence**: Configured with append-only mode
- **Resource Allocation**: 0.5 CPU, 1.0Gi memory
- **Scaling**: Fixed at 1 replica for session consistency

## Changes Made

### 1. Fixed Redis Service Discovery (`container-apps-bicep.bicep`)
- Made Redis URL configurable as a parameter to avoid Bicep string interpolation issues
- Updated deployment script to explicitly pass the correct Redis URL:
  - `redis://redis-app.medical-case-env.internal:6379/0`

### 2. Enhanced Redis Client Configuration (`backend/app/main.py`)
- Added comprehensive Redis connection configuration with proper timeout settings:
  - `socket_connect_timeout=5`
  - `socket_timeout=5`
  - `retry_on_timeout=True`
  - `retry_on_connection_error=True`
  - `single_connection_client=True`
  - `health_check_interval=30`
- Added connection testing during initialization with `redis_client.ping()`
- Added better error logging with Redis URL information

### 3. Improved Error Handling and Graceful Degradation
- Enhanced Redis operation error handling throughout the application
- Added proper fallback mechanisms when Redis is unavailable
- Improved health check endpoint with detailed Redis connection information
- Better logging for Redis connection failures

### 4. Fixed Startup Script Issues
- Changed `reload=True` to `reload=False` in `start_backend.py` for production stability
- Added `workers=1` for production deployment

## Key Improvements

### Correct Service Discovery
Azure Container Apps uses the format `<app-name>.<environment-name>.internal` for internal service discovery. The Redis URL is now explicitly configured as `redis://redis-app.medical-case-env.internal:6379/0` to ensure correct resolution for the dedicated Redis container.

### Robust Redis Configuration
The Redis client is now configured with:
- Proper timeout settings to prevent hanging connections
- Retry mechanisms for connection failures
- Health checks to monitor connection status
- Connection testing during initialization
- Support for the dedicated Redis container deployment

### Graceful Degradation
The application now handles Redis failures gracefully:
- **Redis Unavailable**: Editing functionality is disabled, but case generation still works
- **Connection Timeouts**: Proper error messages are returned to users
- **Service Failures**: Application continues operating with reduced functionality

### Enhanced Monitoring
Added detailed logging for troubleshooting:
- Redis connection attempts and failures
- Service discovery information
- Health check details
- Error stack traces
- Redis container deployment information

## Testing
The fix has been tested locally and shows:
- Backend starts successfully without crashing
- All services connect properly (Redis, Database, OpenAI)
- Health check endpoint returns healthy status
- Graceful degradation works when Redis is unavailable

## Deployment Instructions

The deployment process has two main phases:

### Phase 1: Build and Push Containers (Prerequisite)
Before deploying the infrastructure, you need to build and push the container images to your Azure Container Registry:

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

### Phase 2: Deploy Infrastructure to Azure Container Apps
This step deploys the infrastructure using the existing container images:

```bash
# Run the deployment script (recommended - automatically loads environment variables)
./deploy-container-apps.sh
```

### Alternative Manual Deployment:
If you prefer to deploy manually, first load the environment variables:
```bash
# Load environment variables
source .env

# Deploy using Bicep directly
az deployment group create \
    --resource-group $RESOURCE_GROUP \
    --template-file container-apps-bicep.bicep \
    --parameters \
        location=$LOCATION \
        environmentName=$ENVIRONMENT_NAME \
        containerRegistry=$CONTAINER_REGISTRY \
        redisUrl="redis://redis-app.medical-case-env.internal:6379/0" \
        acrUsername=$ACR_USERNAME \
        acrPassword=$ACR_PASSWORD \
        postgresUrl="$POSTGRES_URL" \
        openaiApiKey="$OPENAI_API_KEY" \
        appUsername="$APP_USERNAME" \
        appPassword="$APP_PASSWORD"
```

## Important Notes

- **Two-Phase Deployment**: The process requires building containers first (Phase 1) and then deploying infrastructure (Phase 2). The deployment script only handles Phase 2.
- **Environment Variables**: The deployment script automatically loads environment variables from `.env`. When deploying manually, you must run `source .env` first.
- **Redis URL**: For Azure deployment, use `redis://redis-app.medical-case-env.internal:6379/0` instead of the local `redis://localhost:6379/0`.
- **Resource Group**: Ensure `$RESOURCE_GROUP` is set in your `.env` file (currently set to `casegen-rg`).
- **Container Images**: The deployment assumes container images already exist in your Azure Container Registry from Phase 1.

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

4. **Common Issues:**
   - **Redis Connection Issues**:
     - Verify the Redis URL is correctly set to `redis://redis-app.medical-case-env.internal:6379/0`
     - Ensure you're using the deployment script or have run `source .env` before manual deployment
     - Check that the Redis container is running: `az containerapp show --name redis-app --resource-group $RESOURCE_GROUP`
   - **Database Connection Issues**: Verify the PostgreSQL connection string
   - **OpenAI API Key Issues**: Verify the API key is correctly set
   - **Service Discovery Issues**: Ensure the Redis container is running and accessible
   - **Environment Variable Issues**: Make sure to run `source .env` before manual deployment or use the deployment script

## New Feature: Model Selection

A new model selection dropdown has been added to the frontend sidebar in `frontend/app.py`. This allows users to choose from the following OpenAI models:

- **gpt-4o-mini** (default) - Cost-effective and fast
- **gpt-4o** - Balanced performance
- **gpt-5-mini** - Advanced capabilities with temperature=1.0
- **gpt-5** - Most advanced model with temperature=1.0

### Backend Integration
- The `/preview-case` and `/generate-case` endpoints now accept `model` (str, default="gpt-4o-mini") and `temperature` (float, default=0.7) parameters
- These parameters are passed to the LLM service functions: `generate_case_details`, `generate_diagnostic_framework`, and `generate_feature_likelihood_ratios`
- For gpt-5 and gpt-5-mini models, the frontend automatically sets `temperature=1.0` as required by these models

### Usage
- Select the desired model from the sidebar dropdown
- The selected model and appropriate temperature are automatically included in case generation requests
- This enables flexible model usage while maintaining structured JSON outputs for case generation

## Expected Behavior

With these fixes, the backend should:
1. Start successfully without crashing
2. Connect to Redis using the explicit URL `redis://redis-app.medical-case-env.internal:6379/0`
3. Handle Redis connection failures gracefully
4. Continue operating even when Redis is temporarily unavailable
5. Provide clear error messages when services are unavailable
6. Log detailed information for debugging

The application will now be more resilient and provide a better user experience even when individual services experience temporary issues.