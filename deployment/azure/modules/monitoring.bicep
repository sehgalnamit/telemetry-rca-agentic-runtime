// ---------------------------------------------------------------------------
// ILLUSTRATIVE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION.
// See deployment/azure/main.bicep and docs/AZURE_DEPLOYMENT.md for the
// full disclaimer. This module provisions CONTROL-PLANE observability only
// (Log Analytics + Application Insights for the RCA runtime itself) -- it
// has nothing to do with tenant telemetry, which is never ingested here.
// ---------------------------------------------------------------------------

@description('Azure region for the workspace and Application Insights component.')
param location string

@description('Name of the Log Analytics workspace used for Container Apps platform logs and as the Application Insights backing workspace.')
param logAnalyticsWorkspaceName string

@description('Name of the Application Insights component used for the control plane\'s own operational telemetry (agent runtime / rule engine / jury evaluator / audit log traces) -- distinct from tenant OTel data analyzed by the RCA engine.')
param appInsightsName string

@description('Log Analytics retention period, in days.')
param retentionInDays int = 30

@description('Tags applied to both resources.')
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    // Workspace-based Application Insights: ingestion lands in the Log
    // Analytics workspace above, so both the Container Apps platform logs
    // and the control plane's own app traces/metrics are queryable from a
    // single workspace.
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

@description('Resource ID of the Log Analytics workspace. Consumed by modules/containerapps.bicep to configure the managed environment\'s diagnostics destination.')
output logAnalyticsWorkspaceId string = logAnalytics.id

output logAnalyticsWorkspaceName string = logAnalytics.name

@description('Workspace customer ID (not a secret) -- the shared key itself is resolved separately via listKeys() inside modules/containerapps.bicep so it never has to pass through a module output.')
output logAnalyticsCustomerId string = logAnalytics.properties.customerId

@description('Connection string the control-plane container can use to emit its own OTel/App Insights telemetry. Not a high-value secret (ingestion-only), but still handled as a Container Apps secret downstream rather than a plain env var.')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
