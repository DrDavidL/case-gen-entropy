// Azure Container Apps Bicep template
param location string = resourceGroup().location
param environmentName string = 'medical-case-env'
param deploymentTimestamp string = utcNow()
param containerRegistry string = 'labdlcontainer.azurecr.io'
param acrUsername string
@secure()
param acrPassword string
@secure() 
param postgresUrl string
@secure()
param openaiApiKey string
@secure()
param appUsername string
@secure()
param appPassword string

// Container Apps Environment
resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2022-03-01' = {
  name: environmentName
  location: location
  properties: {
    daprAIInstrumentationKey: null
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
  }
}

// Log Analytics Workspace
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${environmentName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Redis Container App
resource redisApp 'Microsoft.App/containerApps@2022-03-01' = {
  name: 'redis-app'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      ingress: {
        external: false
        targetPort: 6379
      }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: 'redis:7-alpine'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          command: [
            'redis-server'
            '--appendonly'
            'yes'
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// Backend Container App
resource backendApp 'Microsoft.App/containerApps@2022-03-01' = {
  name: 'backend-app'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'postgres-url'
          value: postgresUrl
        }
        {
          name: 'openai-api-key'
          value: openaiApiKey
        }
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'app-username'
          value: appUsername
        }
        {
          name: 'app-password'
          value: appPassword
        }
      ]
      registries: [
        {
          server: containerRegistry
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: '${containerRegistry}/case-generator-backend:latest'
          resources: {
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: [
            {
              name: 'POSTGRES_URL'
              secretRef: 'postgres-url'
            }
            {
              name: 'OPENAI_API_KEY'
              secretRef: 'openai-api-key'
            }
            {
              name: 'REDIS_URL'
              value: 'redis://redis-app:6379/0'
            }
            {
              name: 'APP_USERNAME'
              secretRef: 'app-username'
            }
            {
              name: 'APP_PASSWORD'
              secretRef: 'app-password'
            }
            {
              name: 'DEPLOYMENT_TIMESTAMP'
              value: deploymentTimestamp
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
      }
    }
  }
}

// Frontend Container App
resource frontendApp 'Microsoft.App/containerApps@2022-03-01' = {
  name: 'frontend-app'  
  location: location
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'app-username'
          value: appUsername
        }
        {
          name: 'app-password'
          value: appPassword
        }
      ]
      registries: [
        {
          server: containerRegistry
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 8501
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: '${containerRegistry}/case-generator-frontend:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'BACKEND_URL'
              value: 'https://${backendApp.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'APP_USERNAME'
              secretRef: 'app-username'
            }
            {
              name: 'APP_PASSWORD'
              secretRef: 'app-password'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// Outputs
output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'
output backendUrl string = 'https://${backendApp.properties.configuration.ingress.fqdn}'
