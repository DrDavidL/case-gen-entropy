#!/bin/bash

# DEPRECATED: Manual deployment to Azure Container Instances is deprecated.
# Use Container Apps via deploy-container-apps.sh or GitHub Actions instead.
# Manual Deployment Script for Azure Container Instances
# Run this AFTER setup-azure.sh and editing deployment-config.yaml

set -e

RESOURCE_GROUP="medical-case-generator-rg"
CONTAINER_GROUP_NAME="medical-case-generator"

echo "⚠️  DEPRECATED: Use deploy-container-apps.sh for Container Apps. Proceeding with legacy ACI deployment..."

# Check if deployment config exists
if [ ! -f deployment-config.yaml ]; then
    echo "❌ deployment-config.yaml not found. Run setup-azure.sh first."
    exit 1
fi

# Validate environment variables are set
if grep -q "REPLACE_WITH" deployment-config.yaml; then
    echo "❌ Please edit deployment-config.yaml and replace placeholder values:"
    echo "   - REPLACE_WITH_POSTGRES_URL"
    echo "   - REPLACE_WITH_OPENAI_KEY"
    exit 1
fi

echo "✅ Configuration file validated"

# Deploy container group
echo "📦 Creating container group..."
az container create \
    --resource-group $RESOURCE_GROUP \
    --file deployment-config.yaml

# Wait for deployment
echo "⏳ Waiting for deployment to complete..."
sleep 60

# Get deployment status
echo "📊 Checking deployment status..."
az container show \
    --resource-group $RESOURCE_GROUP \
    --name $CONTAINER_GROUP_NAME \
    --query "{Status:instanceView.state,IP:ipAddress.ip,FQDN:ipAddress.fqdn}" \
    --output table

# Get service URLs
FQDN=$(az container show \
    --resource-group $RESOURCE_GROUP \
    --name $CONTAINER_GROUP_NAME \
    --query "ipAddress.fqdn" \
    --output tsv)

echo ""
echo "🎉 Deployment complete!"
echo ""
echo "📱 Access your application:"
echo "  Frontend: http://$FQDN:8501"
echo "  Backend API: http://$FQDN:8000"  
echo "  API Documentation: http://$FQDN:8000/docs"
echo ""
echo "📋 Management commands:"
echo "  Check logs: az container logs --resource-group $RESOURCE_GROUP --name $CONTAINER_GROUP_NAME --container-name backend"
echo "  Check status: az container show --resource-group $RESOURCE_GROUP --name $CONTAINER_GROUP_NAME"
echo "  Delete: az container delete --resource-group $RESOURCE_GROUP --name $CONTAINER_GROUP_NAME --yes"
echo ""
echo "💰 Cost monitoring:"
echo "  Monitor usage: az consumption usage list --scope /subscriptions/$(az account show --query id --output tsv)/resourceGroups/$RESOURCE_GROUP"
