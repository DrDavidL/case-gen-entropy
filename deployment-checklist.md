# Azure Deployment Prerequisites

## Required Tools:
- [ ] Azure CLI installed (`az --version` to check)
- [ ] Docker Desktop installed and running
- [ ] GitHub account with your repository
- [ ] OpenAI API key ready
- [ ] PostgreSQL database URL ready

## Required Information:
- [ ] Azure subscription ID
- [ ] Resource group name (we'll use: `medical-case-generator-rg`)
- [ ] Container registry name (we'll use: `medcasegen[YOURNAME]` - must be globally unique)
- [ ] PostgreSQL connection string
- [ ] OpenAI API key

## Azure Resources We'll Create:
- [ ] Resource Group
- [ ] Azure Container Registry (ACR)
- [ ] Container Instance Group
- [ ] Public IP with DNS name