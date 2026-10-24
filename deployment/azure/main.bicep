// =============================================================================
// ILLUSTRATIVE ARCHITECTURE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION.
//
// This Bicep file (and everything under deployment/azure/) is a template for
// architecture illustration. It has NOT been deployed, validated against a
// real resource group, or applied in this exercise -- no Azure resources
// were provisioned. Treat it as a reviewable starting point, not a
// production-ready deployment; review names, SKUs, network posture, and
// RBAC scope before ever running `az deployment group create` against a
// real subscription. See docs/AZURE_DEPLOYMENT.md for the full write-up.
//
// CONTROL PLANE vs. DATA PLANE
// -----------------------------------------------------------------------
// This template provisions the RCA CONTROL PLANE only:
//   - A Container Apps environment + container app running the agent
//     runtime, rule engine, jury evaluator, and audit log (agent/, rules/,
//     evaluator/, audit/ in this repo).
//   - An Azure Container Registry holding that control plane's own image.
//   - A Key Vault holding the control plane's own secrets (e.g. an
//     audit-log signing key) -- never tenant data or tenant credentials.
//   - A Log Analytics workspace + Application Insights component for the
//     control plane's own operational telemetry, and as the landing zone
//     for real OTel data in a production deployment of this repo's
//     ingestion path (see otel_mock/ for the local stand-in).
//
// It deliberately does NOT provision the DATA PLANE: each tenant's own
// telemetry (the logs/traces/metrics tables modeled in federation/ and
// otel_mock/) lives in that TENANT's own subscription, in one of:
//   - An Azure Data Explorer (ADX) cluster/database, or
//   - Synapse serverless SQL pool over the tenant's own ADLS Gen2 storage
//     account, or
//   - Microsoft Fabric OneLake (consumed via OneLake shortcuts).
//
// Cross-tenant access to those stores is modeled via Azure Lighthouse
// delegation (scoped RBAC roles such as Reader/Monitoring Reader on
// specific tenant resources, granted by the tenant, never a copy of their
// data) -- see ../azure/lighthouse-delegation.bicep, which is a SEPARATE,
// standalone template meant to be deployed by the data-owning tenant, not
// by this file. This mirrors the zero-copy federation model implemented
// locally with DuckDB ATTACH in federation/query_layer.py.
// =============================================================================

targetScope = 'resourceGroup'

@description('Short name used to derive resource names, e.g. "rca-dev". Keep it short: it is combined with suffixes for resources that have tight name-length limits (Key Vault, Container Registry).')
@minLength(3)
@maxLength(12)
param environmentName string = 'rca-dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Container image to run initially. Defaults to a small public placeholder so the first deployment succeeds before any image has been pushed to the registry provisioned here.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Log Analytics retention period, in days.')
param logAnalyticsRetentionDays int = 30

@description('Container Registry SKU.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param containerRegistrySku string = 'Basic'

@description('Optional principal ID (user or service principal) to grant "Key Vault Secrets Officer" so they can seed secret values into the vault after deployment. Leave empty to skip.')
param deploymentPrincipalId string = ''

@description('Tags applied to every resource in this deployment.')
param tags object = {
  project: 'telemetry-rca-agentic-runtime'
  component: 'rca-control-plane'
  deploymentStatus: 'illustrative-template-only'
}

var resourcePrefix = toLower(environmentName)
var logAnalyticsWorkspaceName = '${resourcePrefix}-rca-law'
var appInsightsName = '${resourcePrefix}-rca-appi'
// ACR names must be alphanumeric only (no hyphens), 5-50 chars, globally unique.
var containerRegistryName = replace('${resourcePrefix}rcaacr', '-', '')
// Key Vault names must be 3-24 chars, alphanumeric + hyphens, globally unique.
var keyVaultName = '${resourcePrefix}-rca-kv'
var managedEnvironmentName = '${resourcePrefix}-rca-env'
var containerAppName = '${resourcePrefix}-rca-runtime'

module monitoring 'modules/monitoring.bicep' = {
  name: 'rca-monitoring'
  params: {
    location: location
    logAnalyticsWorkspaceName: logAnalyticsWorkspaceName
    appInsightsName: appInsightsName
    retentionInDays: logAnalyticsRetentionDays
    tags: tags
  }
}

module registry 'modules/registry.bicep' = {
  name: 'rca-registry'
  params: {
    location: location
    registryName: containerRegistryName
    sku: containerRegistrySku
    tags: tags
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'rca-keyvault'
  params: {
    location: location
    keyVaultName: keyVaultName
    deploymentPrincipalId: deploymentPrincipalId
    tags: tags
  }
}

module containerApps 'modules/containerapps.bicep' = {
  name: 'rca-containerapps'
  params: {
    location: location
    environmentName: managedEnvironmentName
    containerAppName: containerAppName
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    containerRegistryLoginServer: registry.outputs.registryLoginServer
    containerImage: containerImage
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    keyVaultUri: keyVault.outputs.keyVaultUri
    tags: tags
  }
}

// Re-reference the registry and vault by name so role assignments can be
// scoped directly to them. Both were already created by the modules above,
// so Bicep infers the dependency from the `existing` resource's `name`
// referencing a module output.
resource existingRegistry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: registry.outputs.registryName
}

resource existingKeyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVault.outputs.keyVaultName
}

@description('Built-in "AcrPull" role: lets the control plane pull its own image using its managed identity instead of registry admin credentials.')
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

@description('Built-in "Key Vault Secrets User" role: read-only secret access, granted to the control plane\'s managed identity (never write access).')
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingRegistry.id, containerApps.outputs.containerAppPrincipalId, 'AcrPull')
  scope: existingRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: containerApps.outputs.containerAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource keyVaultSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingKeyVault.id, containerApps.outputs.containerAppPrincipalId, 'KeyVaultSecretsUser')
  scope: existingKeyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: containerApps.outputs.containerAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output containerAppFqdn string = containerApps.outputs.containerAppFqdn
output containerAppPrincipalId string = containerApps.outputs.containerAppPrincipalId
output containerRegistryLoginServer string = registry.outputs.registryLoginServer
output keyVaultUri string = keyVault.outputs.keyVaultUri
output logAnalyticsWorkspaceId string = monitoring.outputs.logAnalyticsWorkspaceId
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
