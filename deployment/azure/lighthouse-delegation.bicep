// =============================================================================
// ILLUSTRATIVE ONLY -- NOT PART OF main.bicep'S DEPLOYMENT, AND NOT APPLIED
// TO ANY LIVE SUBSCRIPTION IN THIS EXERCISE.
//
// WHO DEPLOYS THIS: the DATA-OWNING TENANT (e.g. "acme", "globex",
// "initech" in otel_mock/topology.py), from their own subscription/tenant
// context -- never the RCA operator. It cannot be meaningfully validated
// standalone outside that tenant's subscription (managedByTenantId must be
// a *different* real Azure AD tenant than the one deploying it, and the
// registration assignment targets a resource group that must already exist
// in the deploying tenant's subscription). It is included here, in the
// shape it would actually take, purely to document the delegation pattern
// referenced from main.bicep's header comment and from
// docs/AZURE_DEPLOYMENT.md.
//
// WHAT IT DOES: models an Azure Lighthouse delegation using
// Microsoft.ManagedServices/registrationDefinitions (the "offer" the tenant
// approves, published here by the RCA operator's tenant ID) and
// Microsoft.ManagedServices/registrationAssignments (the tenant's act of
// accepting it, scoped to a specific resource group -- *not* their whole
// subscription). Once accepted, principals in the RCA operator's tenant
// show up as "guest" principals able to act under the roles granted below,
// entirely within the tenant's own subscription boundary: no telemetry
// data is copied anywhere, and no cross-tenant credential is created. The
// federation layer (federation/access_control.py) still has to separately
// scope *which* tenants/tables a given query may touch -- Lighthouse only
// grants the underlying Azure RBAC, not row/column-level scoping.
//
// Reader + Monitoring Reader below are illustrative least-privilege
// choices: enough to run read-only KQL/SQL queries against the tenant's
// ADX cluster, Synapse serverless pool, or Fabric workspace, and to read
// the diagnostic settings/metrics needed for RCA, without any write access.
// =============================================================================

targetScope = 'subscription'

@description('Name of the resource group, in the DATA-OWNING TENANT\'s subscription, that contains the telemetry resources being delegated (e.g. the resource group holding their ADX cluster or Synapse workspace). Delegation is scoped to this resource group, not the whole subscription.')
param tenantTelemetryResourceGroupName string

@description('Azure AD tenant ID of the RCA control-plane operator (the "managedBy" tenant) -- supplied by the operator, not the data-owning tenant.')
param managedByTenantId string

@description('Object ID of the operator principal (an Entra ID security group or service principal representing the RCA agent runtime / on-call engineers) that receives the delegated roles inside the data-owning tenant.')
param operatorPrincipalId string

@description('Display name for the delegation shown in the tenant\'s Azure portal "Service providers" blade.')
param registrationDefinitionName string = 'RCA Control-Plane - Read-Only Telemetry Access'

@description('Description shown to the tenant admin approving this delegation.')
param registrationDescription string = 'Grants the RCA control-plane operator read-only (Reader + Monitoring Reader) access to this resource group\'s telemetry resources (ADX / Synapse / Fabric), for federated cross-tenant root-cause analysis. No data is copied out of this subscription; queries run against the tenant\'s own stores.'

// Built-in role GUIDs granted to the operator, scoped only to
// tenantTelemetryResourceGroupName. The data-owning tenant chooses these
// (or a narrower set) when reviewing/approving the delegation request.
var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader
var monitoringReaderRoleId = '43d0d8ad-25c7-4714-9337-8ba259a9fe05' // Monitoring Reader
var delegatedRoleIds = [
  readerRoleId
  monitoringReaderRoleId
]

resource lighthouseDefinition 'Microsoft.ManagedServices/registrationDefinitions@2022-10-01' = {
  name: guid(subscription().subscriptionId, operatorPrincipalId, 'rca-lighthouse-definition')
  properties: {
    registrationDefinitionName: registrationDefinitionName
    description: registrationDescription
    managedByTenantId: managedByTenantId
    authorizations: [for roleId in delegatedRoleIds: {
      principalId: operatorPrincipalId
      principalIdDisplayName: 'rca-control-plane-operator'
      roleDefinitionId: roleId
    }]
  }
}

resource lighthouseAssignment 'Microsoft.ManagedServices/registrationAssignments@2022-10-01' = {
  name: guid(tenantTelemetryResourceGroupName, lighthouseDefinition.name)
  scope: resourceGroup(tenantTelemetryResourceGroupName)
  properties: {
    registrationDefinitionId: lighthouseDefinition.id
  }
}

output registrationDefinitionId string = lighthouseDefinition.id
output registrationAssignmentId string = lighthouseAssignment.id
