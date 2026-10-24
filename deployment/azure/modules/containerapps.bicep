// ---------------------------------------------------------------------------
// ILLUSTRATIVE TEMPLATE -- NOT APPLIED TO ANY LIVE SUBSCRIPTION.
// See deployment/azure/main.bicep and docs/AZURE_DEPLOYMENT.md for the
// full disclaimer.
//
// This module provisions the RCA CONTROL PLANE: a single Container Apps
// managed environment + container app running the agent runtime, rule
// engine, jury evaluator, and audit log (the code in agent/, rules/,
// evaluator/, audit/). It is intentionally NOT where tenant telemetry
// lives -- see the top-level comment in ../main.bicep for the control
// plane / data plane split.
// ---------------------------------------------------------------------------

@description('Azure region for the managed environment and container app.')
param location string

@description('Name of the Container Apps managed environment.')
param environmentName string

@description('Name of the container app running the RCA control plane.')
param containerAppName string

@description('Resource ID of the Log Analytics workspace the managed environment forwards platform logs to (see modules/monitoring.bicep).')
param logAnalyticsWorkspaceId string

@description('Customer ID (workspace ID) of the same Log Analytics workspace, as a plain output value -- not a secret.')
param logAnalyticsCustomerId string

@description('API version to use when resolving the Log Analytics shared key via listKeys(). Must match the apiVersion the workspace was deployed with in modules/monitoring.bicep.')
param logAnalyticsWorkspaceApiVersion string = '2025-02-01'

@description('Login server of the Azure Container Registry the control-plane image is pulled from, e.g. myregistry.azurecr.io.')
param containerRegistryLoginServer string

@description('Fully-qualified container image reference to run. Defaults to a small public placeholder image so a first deployment succeeds before any image has been pushed; replace with "<registryLoginServer>/telemetry-rca-agentic-runtime:<tag>" after building the Dockerfile at the repo root and pushing it (e.g. via "az acr build").')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Application Insights connection string for the control plane\'s own telemetry (see modules/monitoring.bicep).')
param appInsightsConnectionString string

@description('Base URI of the Key Vault holding control-plane secrets, e.g. https://<vault-name>.vault.azure.net/ (see modules/keyvault.bicep).')
param keyVaultUri string

@description('CPU cores reserved for the single control-plane replica, as a decimal string (Container Apps cpu is a fractional value, so it must be passed through json() rather than a Bicep int literal).')
param cpuCores string = '0.5'

@description('Memory reserved for the single control-plane replica.')
param memorySize string = '1Gi'

@description('Minimum replica count. 1 keeps the control plane warm; set to 0 to scale to zero between RCA runs in a non-production demo.')
param minReplicas int = 1

@description('Maximum replica count.')
param maxReplicas int = 2

@description('Tags applied to both resources.')
param tags object = {}

// Resolved once here (not passed through monitoring.bicep's outputs) so the
// shared key never appears in a module output.
var logAnalyticsSharedKey = listKeys(logAnalyticsWorkspaceId, logAnalyticsWorkspaceApiVersion).primarySharedKey

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    zoneRedundant: false
  }
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    // Used to pull from the registry and read Key Vault secrets without
    // any stored credential -- role assignments for both are created in
    // ../main.bicep once this resource's principal ID is known.
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        // Not internet-facing: this control plane is meant to be reached
        // from inside the environment's network (or fronted by your own
        // gateway/private endpoint/VPN). Flip to true + add an auth layer
        // if you need a public endpoint for a real deployment.
        external: false
        targetPort: 8080
        transport: 'auto'
      }
      registries: [
        {
          server: containerRegistryLoginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
        {
          // Illustrative only: this assumes an operator has already run
          // something like
          //   az keyvault secret set --vault-name <vault> \
          //     --name audit-log-signing-key --value <...>
          // against the Key Vault from modules/keyvault.bicep. The
          // container app's system-assigned identity is granted
          // "Key Vault Secrets User" on that vault in ../main.bicep.
          name: 'audit-log-signing-key'
          keyVaultUrl: '${keyVaultUri}secrets/audit-log-signing-key'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'rca-runtime'
          image: containerImage
          resources: {
            cpu: json(cpuCores)
            memory: memorySize
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'AUDIT_LOG_SIGNING_KEY'
              secretRef: 'audit-log-signing-key'
            }
            {
              name: 'PYTHONUNBUFFERED'
              value: '1'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output containerAppId string = containerApp.id
output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.latestRevisionFqdn
output containerAppPrincipalId string = containerApp.identity.principalId
output managedEnvironmentId string = environment.id
