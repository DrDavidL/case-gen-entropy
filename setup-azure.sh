#!/bin/bash

# Azure Container Instances Setup Script for Medical Case Generator
# Run this script after installing Azure CLI and logging in with 'az login'

set -e  # Exit on any error

echo "🏥 Setting up Medical Case Generator on Azure..."

# Configuration - CHANGE THESE VALUES
RESOURCE_GROUP="medical-case-generator-rg"
LOCATION="eastus"
ACR_NAME="medcasegen$RANDOM"  # Must be globally unique
CONTAINER_GROUP_NAME="medical-case-generator"
DNS_NAME="medical-case-gen-$RANDOM"

echo "Using configuration:"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Location: $LOCATION"
echo "  Container Registry: $ACR_NAME"
echo "  DNS Name: $DNS_NAME"
echo ""

# Step 1: Create Resource Group
echo "📁 Creating resource group..."
az group create \
    --name $RESOURCE_GROUP \
    --location $LOCATION

# Step 2: Create Azure Container Registry
echo "🐳 Creating Azure Container Registry..."
az acr create \
    --resource-group $RESOURCE_GROUP \
    --name $ACR_NAME \
    --sku Basic \
    --admin-enabled true

# Step 3: Get ACR credentials
echo "🔑 Getting ACR credentials..."
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username --output tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query passwords[0].value --output tsv)
ACR_SERVER="${ACR_NAME}.azurecr.io"

echo "Container Registry Details:"
echo "  Server: $ACR_SERVER"
echo "  Username: $ACR_USERNAME"
echo "  Password: [HIDDEN]"
echo ""

# Step 4: Build and push images
echo "🔨 Building and pushing backend image..."
az acr build \
    --registry $ACR_NAME \
    --image case-generator-backend:latest \
    --file Dockerfile.backend \
    .

echo "🔨 Building and pushing frontend image..."
az acr build \
    --registry $ACR_NAME \
    --image case-generator-frontend:latest \
    --file Dockerfile.frontend \
    .

# Step 5: Create deployment template
echo "📝 Creating deployment template..."
cat > deployment-config.yaml << EOF
apiVersion: 2021-10-01
location: $LOCATION
name: $CONTAINER_GROUP_NAME
properties:
  containers:
  - name: redis
    properties:
      image: redis:7-alpine
      ports:
      - port: 6379
      resources:
        requests:
          cpu: 0.5
          memoryInGb: 0.5
      command:
      - redis-server
      - --appendonly
      - "yes"
      
  - name: backend
    properties:
      image: $ACR_SERVER/case-generator-backend:latest
      ports:
      - port: 8000
        protocol: TCP
      environmentVariables:
      - name: POSTGRES_URL
        secureValue: "REPLACE_WITH_POSTGRES_URL"
      - name: OPENAI_API_KEY
        secureValue: "REPLACE_WITH_OPENAI_KEY"
      - name: REDIS_URL
        value: redis://localhost:6379/0
      resources:
        requests:
          cpu: 1
          memoryInGb: 2
          
  - name: frontend
    properties:
      image: $ACR_SERVER/case-generator-frontend:latest
      ports:
      - port: 8501
        protocol: TCP
      environmentVariables:
      - name: BACKEND_URL
        value: http://localhost:8000
      resources:
        requests:
          cpu: 0.5
          memoryInGb: 1

  osType: Linux
  restartPolicy: Always
  ipAddress:
    type: Public
    ports:
    - protocol: TCP
      port: 8501
    - protocol: TCP  
      port: 8000
    dnsNameLabel: $DNS_NAME

  imageRegistryCredentials:
  - server: $ACR_SERVER
    username: $ACR_USERNAME
    password: $ACR_PASSWORD

tags:
  Environment: Production
  Application: MedicalCaseGenerator
EOF

echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Edit deployment-config.yaml and replace:"
echo "   - REPLACE_WITH_POSTGRES_URL with your PostgreSQL connection string"
echo "   - REPLACE_WITH_OPENAI_KEY with your OpenAI API key"
echo ""
echo "2. Deploy with:"
echo "   az container create --resource-group $RESOURCE_GROUP --file deployment-config.yaml"
echo ""
echo "3. Your app will be available at:"
echo "   Frontend: http://$DNS_NAME.eastus.azurecontainer.io:8501"
echo "   Backend API: http://$DNS_NAME.eastus.azurecontainer.io:8000"
echo ""
echo "🔧 Save these values for GitHub Actions:"
echo "ACR_SERVER=$ACR_SERVER"
echo "ACR_USERNAME=$ACR_USERNAME"
echo "ACR_PASSWORD=$ACR_PASSWORD"