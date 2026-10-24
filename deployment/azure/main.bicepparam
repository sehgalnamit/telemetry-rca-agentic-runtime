// Example parameters for deployment/azure/main.bicep.
//
// ILLUSTRATIVE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION. See the
// disclaimer at the top of main.bicep and docs/AZURE_DEPLOYMENT.md.
using './main.bicep'

param environmentName = 'rca-dev'
param location = 'eastus2'
param containerImage = 'mcr.microsoft.com/k8se/quickstart:latest'
param logAnalyticsRetentionDays = 30
param containerRegistrySku = 'Basic'
// Set this to your own user/service-principal object ID after a real
// deployment if you want to seed Key Vault secrets via `az keyvault secret
// set` without a separate role-assignment step.
param deploymentPrincipalId = ''
param tags = {
  project: 'telemetry-rca-agentic-runtime'
  component: 'rca-control-plane'
  deploymentStatus: 'illustrative-template-only'
}
