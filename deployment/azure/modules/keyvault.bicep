// ---------------------------------------------------------------------------
// ILLUSTRATIVE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION.
// See deployment/azure/main.bicep and docs/AZURE_DEPLOYMENT.md for the
// full disclaimer. This Key Vault holds CONTROL-PLANE secrets only (e.g.
// an audit-log signing key, third-party API keys the agent runtime may
// need). It never holds tenant telemetry or tenant credentials -- those
// are scoped via Azure Lighthouse (see ../lighthouse-delegation.bicep),
// not copied into this vault.
// ---------------------------------------------------------------------------

@description('Azure region for the vault.')
param location string

@description('Globally-unique Key Vault name (3-24 chars, alphanumeric and hyphens).')
param keyVaultName string

@description('Azure AD tenant ID that owns this vault (the RCA operator\'s own tenant -- not any data-owning tenant).')
param tenantId string = subscription().tenantId

@description('Optional principal ID (e.g. the user or service principal running this deployment) to grant "Key Vault Secrets Officer" so they can seed secret values after deployment. Leave empty to skip -- secrets can always be granted later with a separate role assignment.')
param deploymentPrincipalId string = ''

@description('Tags applied to the vault.')
param tags object = {}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // RBAC authorization (not vault access policies) so access can be
    // granted with ordinary Microsoft.Authorization/roleAssignments
    // resources, consistent with how the container app's managed identity
    // is granted read access in main.bicep.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

@description('Key Vault Secrets Officer built-in role (read/write secrets, but not vault-level management).')
var secretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource deploymentPrincipalSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deploymentPrincipalId)) {
  name: guid(keyVault.id, deploymentPrincipalId, 'KeyVaultSecretsOfficer')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsOfficerRoleId)
    principalId: deploymentPrincipalId
    principalType: 'User'
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
