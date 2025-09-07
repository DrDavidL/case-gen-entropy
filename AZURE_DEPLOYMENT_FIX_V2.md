# Azure Backend Deployment Fix - Version 2

## Issue Summary
The FastAPI backend was crashing on Azure Container Apps due to multiple issues:
1. The `reload=True` parameter in the uvicorn configuration (initial issue)
2. Redis connection timeouts causing startup failures (current issue)
3. Lack of proper error handling for critical services

## Root Cause Identified
The actual root cause of the backend crashes on Azure was **Redis connection timeouts**. The error message showed:
```
redis.exceptions.TimeoutError: Timeout connecting to server
```

This was happening because:
1. The Redis service in Azure Container Apps was not accessible via the configured URL
2. The application was not handling Redis connection failures gracefully
3. Critical Redis operations were causing the entire application to crash

## Changes Made

### 1. Fixed Startup Script (`start_backend.py`)
- Changed `reload=True` to `reload=False` and added `workers=1` for production stability

### 2. Enhanced Redis Error Handling (`backend/app/main.py`)
- Added comprehensive error handling for all Redis operations
- Implemented graceful degradation when Redis is unavailable
- Added proper logging for Redis connection issues
- Added checks to prevent Redis operations when client is not available

### 3. Enhanced Database Error Handling (`backend/app/main.py`)
- Added proper error handling for database operations
- Implemented retry logic with exponential backoff
- Added logging for database connection issues

### 4. Enhanced LLM Service Error Handling (`backend/app/main.py`)
- Added error handling for LLM service initialization
- Implemented graceful degradation when LLM service is unavailable

### 5. Enhanced Health Check Endpoint (`backend/app/main.py`)
- Added detailed health check information
- Added logging for health check operations
- Added database connection testing
- Added more detailed environment variable reporting

### 6. Updated Dockerfile.backend
- Added a safeguard sed command to ensure the reload parameter is removed during container build
- This provides a double safeguard against the reload parameter in production

### 7. Updated Environment Configuration
- Added `ENVIRONMENT=production` to Azure Bicep deployment
- Added `ENVIRONMENT=development` to local docker-compose for proper environment detection

## Key Improvements

### Graceful Degradation
The application now handles service failures gracefully:
- **Redis Unavailable**: Editing functionality is disabled, but case generation still works
- **Database Issues**: Proper error messages are returned to users
- **LLM Service Issues**: Clear error messages indicate service unavailability

### Comprehensive Logging
Added detailed logging throughout the application:
- Startup process logging
- Service initialization logging
- Error logging with full stack traces
- Health check logging

### Error Handling
Implemented proper error handling for all critical operations:
- Redis operations with try-catch blocks
- Database operations with retry logic
- LLM service operations with error handling
- Session management with proper error responses

## Testing
The fix has been tested locally and the backend starts successfully with all services connecting properly.

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
   - **Redis Connection Issues**: Verify the Redis URL in the Bicep template
   - **Database Connection Issues**: Verify the PostgreSQL connection string
   - **OpenAI API Key Issues**: Verify the API key is correctly set

## Expected Behavior

With these fixes, the backend should:
1. Start successfully without crashing
2. Handle Redis connection failures gracefully
3. Continue operating even when Redis is unavailable
4. Provide clear error messages when services are unavailable
5. Log detailed information for debugging

The application will now be more resilient and provide a better user experience even when individual services are temporarily unavailable.