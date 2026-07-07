# AMBA Enterprise Coverage Matrix

All 17 AMBA policy set definitions mapped to service domains, assignment scopes, and deployment status.

## Initiative-to-Scope Mapping

| # | Initiative | Policy Set Definition | Assignment Scope | Domain | Default State |
|---|---|---|---|---|---|
| 1 | Resource & Service Health | Deploy-ResourceAndServiceHealth-Alerts | Pseudo-root MG | Platform + LZ | Enabled |
| 2 | Notification Assets | Deploy-Notification-Assets | Pseudo-root MG | Platform + LZ | Enabled |
| 3 | Connectivity Part 1 | Deploy-Connectivity-Alerts | Platform-Connectivity MG | Platform | Enabled |
| 4 | Connectivity Part 2 | Deploy-Connectivity-Alerts-2 | Platform-Connectivity MG | Platform | Enabled |
| 5 | Management | Deploy-Management-Alerts | Platform-Management MG | Platform | Enabled |
| 6 | Identity | Deploy-Identity-Alerts | Platform-Identity MG | Platform | Enabled |
| 7 | VM | Deploy-VM-Alerts | Platform + Landing Zones MG | Platform + LZ | Enabled |
| 8 | VMSS | Deploy-VMSS-Alerts | Platform + Landing Zones MG | Platform + LZ | Enabled |
| 9 | HybridVM (Arc) | Deploy-HybridVM-Alerts | Platform + Landing Zones MG | Platform + LZ | Enabled |
| 10 | Key Management | Deploy-KeyManagement-Alerts | Landing Zones MG | LZ | Enabled |
| 11 | Load Balancing | Deploy-LoadBalancing-Alerts | Landing Zones MG | LZ | Enabled |
| 12 | Network Changes | Deploy-NetworkChanges-Alerts | Landing Zones MG | LZ | Enabled |
| 13 | Recovery Services | Deploy-RecoveryServices-Alerts | Landing Zones MG | LZ | Enabled |
| 14 | Storage | Deploy-Storage-Alerts | Landing Zones MG | LZ | Enabled |
| 15 | Web | Deploy-Web-Alerts | Landing Zones MG | LZ | Enabled |
| 16 | Service Health (Deprecated) | Deploy-ServiceHealth-Alerts | — | Deprecated | Disabled |
| 17 | Landing Zone (Deprecated) | Deploy-LandingZone-Alerts | — | Deprecated | Disabled |

## Service Domain Coverage (42 Resource Types)

| Service | Resource Type | Initiative | Alert Types | Key Metrics |
|---|---|---|---|---|
| Compute | virtualMachines | VM | Metric, Log | CPU, Memory, Disk Space, Disk Latency, Network, Heartbeat |
| Compute | virtualMachineScaleSets | VMSS | Metric, Log | CPU, Memory, Disk Space, Disk Latency, Network, Heartbeat |
| HybridCompute | machines | HybridVM | Metric, Log | CPU, Memory, Disk Space, Disk Latency, Network, Heartbeat, Disconnected |
| ContainerService | managedClusters | Load Balancing/LZ | Metric | Node CPU, Node Memory, Pod Count, OOM Kills |
| App | containerApps | Web | Metric | CPU, Memory, Requests, Restarts |
| Storage | storageAccounts | Storage | Metric, Activity | Availability, Throttling, Capacity, Delete |
| Network | virtualNetworkGateways | Connectivity | Metric, Activity | Bandwidth, Tunnel, BGP, Egress/Ingress |
| Network | expressRouteCircuits | Connectivity | Metric | BGP Availability, ARP, QoS Drops |
| Network | azureFirewalls | Connectivity | Metric, Activity | Health, SNAT, Application/Network Rules |
| Network | publicIPAddresses | Connectivity/LB | Metric | VIP Availability, DDoS, Bytes/Packets |
| Network | virtualNetworks | Connectivity/LB | Metric | DDoS Attack |
| Network | privateDnsZones | Connectivity | Metric | Capacity, Query Volume, Record Set, Registration |
| Network | loadBalancers | Load Balancing | Metric | Data Path, Backend, Health Probe, SNAT |
| Network | applicationGateways | Load Balancing | Metric | Total Time, Backend Response, Capacity, CPU, Failed Requests, Unhealthy Hosts |
| Network | frontDoors | Load Balancing | Metric | Backend Health, Request Latency |
| Network | frontDoorCDNProfiles | Load Balancing | Metric | Origin Health, Origin Latency, 4XX, 5XX |
| Network | trafficManagerProfiles | Load Balancing | Metric | Endpoint Health |
| Network | networkSecurityGroups | Network Changes | Activity | Delete |
| Network | routeTables | Network Changes | Activity | Update, Delete, Routes Delete |
| KeyVault | vaults | Identity/Key Mgmt | Metric, Activity | Requests, Availability, Latency, Capacity, Delete |
| KeyVault | managedHSMs | Identity/Key Mgmt | Metric, Activity | Availability, Latency, Delete |
| OperationalInsights | workspaces | Management | Metric, Activity | Daily Cap, Delete, Key Regen |
| Automation | automationAccounts | Management | Metric | Total Jobs |
| RecoveryServices | vaults | Recovery Services | Metric | Backup Health, ASR Health |
| Web | sites | Web | Metric | Response Time, HTTP Errors |
| Web | serverFarms | Web | Metric | CPU%, Memory%, Disk Queue, HTTP Queue |
| Insights | components | Web | Metric, Activity | Throttling Limit, Delete |
| Resources | subscriptions | Service Health | Activity | Incidents, Advisories, Maintenance, Security |
| AlertsManagement | actionRules | Notification | Policy | APR Deploy, APR Suppression |
| ApiManagement | service | LZ (Landing Zone) | Metric | Capacity, Requests, Gateway Errors |
| Cache | redis | LZ | Metric | CPU, Memory, Connected Clients, Cache Hits |
| Cdn | profiles | LZ | Metric | Origin Health, Latency |
| CognitiveServices | accounts | LZ | Metric | Availability, Latency, Errors |
| ContainerRegistry | registries | LZ | Metric | Storage Used, Runs |
| Databricks | workspaces | LZ | Metric | Jobs, Clusters |
| DataFactory | factories | LZ | Metric | Pipeline Failures, Activity Failures |
| DBforMySQL | flexibleServers | LZ | Metric | CPU, Memory, Storage, IO, Connections |
| DBforPostgreSQL | flexibleServers | LZ | Metric | CPU, Memory, Storage, IO, Connections |
| DocumentDB | databaseAccounts | LZ | Metric | Availability, Latency, RU Consumption |
| EventHub | namespaces | LZ | Metric | Throttled Requests, Messages, Errors |
| ServiceBus | namespaces | LZ | Metric | Throttled Requests, Messages, Errors |
| Sql | servers/databases | LZ | Metric | DTU, CPU, Storage, Deadlocks |

## Deployment Sequencing

| Wave | Initiatives | Rationale |
|---|---|---|
| Wave 1 | Notification Assets, Resource & Service Health | Foundation — APR must exist before workload alerts |
| Wave 2 | Identity, Management, Connectivity (1 & 2) | Platform services — protect shared infrastructure first |
| Wave 3 | VM, VMSS, HybridVM | Compute — high-impact workloads with VM Insights prerequisite |
| Wave 4 | Key Management, Load Balancing, Network Changes, Recovery Services, Storage, Web | Landing zone services — broad workload coverage |

## Prerequisites by Initiative

| Initiative | Prerequisites |
|---|---|
| VM | VM Insights DCR deployed on target VMs |
| VMSS | VM Insights DCR deployed on VMSS instances (Flexible mode only) |
| HybridVM | VM Insights DCR deployed on Arc-connected machines, Azure Connected Machine Agent |
| Management | Log Analytics workspace exists |
| Notification Assets | None (deploys APR and AG) |
| All | UAMI with Monitoring Reader at pseudo-root MG |
