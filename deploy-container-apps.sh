#!/bin/bash

# Deploy to Azure Container Apps
# Usage: ./deploy-container-apps.sh

set -e

# Load environment variables
source .env

# Required variables check
if [[ -z "$RESOURCE_GROUP" || -z "$ACR_USERNAME" || -z "$ACR_PASSWORD" || -z "$POSTGRES_URL" || -z "$OPENAI_API_KEY" || -z "$APP_USERNAME" || -z "$APP_PASSWORD" ]]; then
    echo "Error: Missing required environment variables in .env file"
    echo "Required: RESOURCE_GROUP, ACR_USERNAME, ACR_PASSWORD, POSTGRES_URL, OPENAI_API_KEY, APP_USERNAME, APP_PASSWORD"
    exit 1
fi

echo "=== Deploying Medical Case Generator to Azure Container Apps ==="

# Set default values
LOCATION=${LOCATION:-eastus}
ENVIRONMENT_NAME=${ENVIRONMENT_NAME:-medical-case-env}
CONTAINER_REGISTRY=${CONTAINER_REGISTRY:-labdlcontainer.azurecr.io}

echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"  
echo "Environment: $ENVIRONMENT_NAME"
echo "Registry: $CONTAINER_REGISTRY"

# Create resource group if it doesn't exist
echo "Creating resource group..."
az group create --name $RESOURCE_GROUP --location $LOCATION

# Deploy using Bicep
echo "Deploying Container Apps..."
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

# Get the URLs
echo "Getting application URLs..."
FRONTEND_URL=$(az containerapp show --name frontend-app --resource-group $RESOURCE_GROUP --query "properties.configuration.ingress.fqdn" -o tsv)
BACKEND_URL=$(az containerapp show --name backend-app --resource-group $RESOURCE_GROUP --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "=== Deployment Complete ==="
echo "Frontend URL: https://$FRONTEND_URL"
echo "Backend URL: https://$BACKEND_URL"
echo ""
echo "Your application is now running on Azure Container Apps!"
