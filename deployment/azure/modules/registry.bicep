// ---------------------------------------------------------------------------
// ILLUSTRATIVE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION.
// See deployment/azure/main.bicep and docs/AZURE_DEPLOYMENT.md for the
// full disclaimer. This is a CONTROL-PLANE resource only: it stores the
// *image* for the RCA runtime container. No tenant telemetry is stored in
// a container registry, ever.
// ---------------------------------------------------------------------------

@description('Azure region for the registry.')
param location string

@description('Globally-unique registry name (alphanumeric only, 5-50 chars).')
param registryName string

@description('Registry SKU. Basic is sufficient for a single control-plane image; use Premium if you need geo-replication or private endpoints in a real deployment.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param sku string = 'Basic'

@description('Tags applied to the registry.')
param tags object = {}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    // Admin user is intentionally disabled: the control plane pulls images
    // using its own system-assigned managed identity (granted "AcrPull" in
    // main.bicep) rather than a shared admin username/password.
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output registryId string = registry.id
output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
