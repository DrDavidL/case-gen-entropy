#!/bin/bash

# Secure Deployment Config Generator
# This script creates deployment-config.yaml with your secrets (locally only)

set -e

echo "🔐 Creating secure deployment configuration..."

# Check if template exists
if [ ! -f deployment-config.template.yaml ]; then
    echo "❌ deployment-config.template.yaml not found!"
    exit 1
fi

# Prompt for required values
echo "Please provide the following values:"
echo ""

read -p "ACR Server (e.g., myregistry.azurecr.io): " ACR_SERVER
read -p "ACR Username: " ACR_USERNAME  
read -s -p "ACR Password: " ACR_PASSWORD
echo ""
read -s -p "PostgreSQL URL: " POSTGRES_URL
echo ""
read -s -p "OpenAI API Key: " OPENAI_KEY
echo ""
read -p "DNS Name (e.g., my-medical-app): " DNS_NAME

# Create deployment config from template
cp deployment-config.template.yaml deployment-config.yaml

# Replace placeholders with actual values
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s|YOUR_ACR_SERVER|$ACR_SERVER|g" deployment-config.yaml
    sed -i '' "s|YOUR_ACR_USERNAME|$ACR_USERNAME|g" deployment-config.yaml
    sed -i '' "s|YOUR_ACR_PASSWORD|$ACR_PASSWORD|g" deployment-config.yaml
    sed -i '' "s|YOUR_POSTGRES_URL_HERE|$POSTGRES_URL|g" deployment-config.yaml
    sed -i '' "s|YOUR_OPENAI_KEY_HERE|$OPENAI_KEY|g" deployment-config.yaml
    sed -i '' "s|YOUR_DNS_NAME_HERE|$DNS_NAME|g" deployment-config.yaml
else
    # Linux
    sed -i "s|YOUR_ACR_SERVER|$ACR_SERVER|g" deployment-config.yaml
    sed -i "s|YOUR_ACR_USERNAME|$ACR_USERNAME|g" deployment-config.yaml
    sed -i "s|YOUR_ACR_PASSWORD|$ACR_PASSWORD|g" deployment-config.yaml
    sed -i "s|YOUR_POSTGRES_URL_HERE|$POSTGRES_URL|g" deployment-config.yaml
    sed -i "s|YOUR_OPENAI_KEY_HERE|$OPENAI_KEY|g" deployment-config.yaml
    sed -i "s|YOUR_DNS_NAME_HERE|$DNS_NAME|g" deployment-config.yaml
fi

echo ""
echo "✅ deployment-config.yaml created successfully!"
echo ""
echo "⚠️  SECURITY NOTICE:"
echo "   - deployment-config.yaml contains secrets and is excluded from git"
echo "   - Do NOT commit this file to version control"
echo "   - Keep this file secure on your local machine"
echo ""
echo "🚀 Ready to deploy with:"
echo "   ./deploy-manual.sh"