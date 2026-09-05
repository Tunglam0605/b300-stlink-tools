# B300 v0.19 Functional Audit and Shared-Workspace Plan

Baseline audited: `main` / v0.18.2 (`f6b7554`).

## Product boundary

B300 remains the ST-Link programming/safety/control plane. Interactive source-level debugging belongs to VS Code + Cortex-Debug. Live Monitor remains a separate zero-halt workflow.

## Functional inventory and canonical owner

| Capability | Existing surfaces found | Canonical owner after refactor | Decision |
|---|---|---|---|
| ST-Link selection / rescan | Header, Program, Device | Global header | Keep one visible selector/rescan action. |
| Application HEX validate/flash/dry-run | Program, CLI | Program | Keep. |
| Trusted Bootloader factory provision | Program Advanced, CLI | Program > Maintenance | Keep guarded; never expose arbitrary bootloader write. |
| Target inspect: ID/VDD/WRP/RDP | Program, Device, CLI | Device | Remove page-local duplicates elsewhere. |
| Memory / metadata diagnostics | Program Advanced, CLI | Device/Program Advanced | Keep advanced/read-only. |
| Live RAM/DWT sampling | Monitor, CLI | Monitor | Keep zero-halt contract. |
| ELF/AXF selection | Monitor, Debug Local, Debug Client | Project Manager | Replace repeated file pickers with a shared saved project. |
| Workspace selection | Debug Local, Debug Client | Project Manager | Replace repeated workspace fields. |
| Gateway endpoint Host/User/Port | Debug Client, Monitor loader, Gateway setup/CLI | Gateway Manager | Replace single profile and repeated fields with saved profiles. |
| SSH password | Remote login + LocalCredentialStore | Gateway Login + in-memory session credential store | Never persist in production GUI; clear on app exit. |
| Authenticated SSH session | MainWindow `_vscode_remote_session`, Monitor provider | Gateway Session Manager | Reuse one authenticated session across Monitor/Debug for the same gateway. |
| Local VS Code debug | Debug | Debug | Keep one primary action. |
| Gateway OpenOCD role | Debug | Debug > Gateway | Keep start; use one shared Stop Debug action. |
| Client remote VS Code debug | Debug | Debug > Client | Select saved Gateway + Project, then open VS Code. |
| Machine prerequisites | Header + Settings + base sidebar | Settings > Machine Setup | One owner; header shortcut removed. |
| Theme | Header + Settings | Settings | One owner. |
| Update | Settings + hidden sidebar + Help menu | Settings | One visible owner. |
| About/release/support bundle | Header/menus/Settings | Settings | Consolidate user-facing entry points. |
| Gateway host OS preparation | CLI + legacy GatewaySetupTab | Shared Gateway Host Setup (later reusable dialog) | Keep backend; do not duplicate inside Debug. |
| Remote Application programming foundation | hidden Program foundation + core | Not production-visible until transport acceptance | Keep fail-closed; no premature UI. |

## Problems confirmed by audit

1. v0.18 has five visible workspaces, but legacy UI classes remain in the tree; production must never instantiate them.
2. `RemoteGatewayProfile` persists only one endpoint, so Client workflows cannot manage several gateways cleanly.
3. Monitor and Debug independently ask for ELF/AXF; Debug Local and Client independently ask for workspace.
4. Client host/user/port are owned by Debug UI even though Monitor and future remote programming require the same endpoint.
5. `LocalCredentialStore` can persist a password on disk while the desired production workflow is session-only authentication.
6. Several hidden compatibility buttons still exist (Program/Device refresh, Program Target inspect, Settings setup/theme). Their backend compatibility can remain, but only the canonical owner is visible.
7. Monitor's embedded `AXF/ELF...` symbol-browser button is not wired in the v0.18 production owner and duplicates symbol selection semantics.

## Target production layout

### Global header
- ST-Link selector
- Rescan ST-Link
- concise connection/activity state only

### Program
- Application HEX selection / validation / dry-run / flash
- Advanced: trusted Bootloader factory flow, memory map, metadata/log evidence

### Monitor
- Mode: Local / Client
- Project selector
- Gateway selector only when Client
- Start/Stop, sampling rate, watch list, timeline, export

### Debug
- Mode: Local / Gateway / Client
- Local: Project selector -> Open in VS Code
- Gateway: Start Gateway -> shared Stop Debug
- Client: Gateway selector + Project selector -> Open remote debug in VS Code
- Environment and connection diagnostics remain collapsible

### Device
- Inspect Target (read-only)
- Target ID/flash/VDD
- WRP/RDP/Option Bytes evidence
- selected ST-Link details

### Settings
- Manage Gateways
- Manage Projects
- Machine Setup
- Theme
- Runtime/toolchain readiness
- Update / Release notes / Support bundle / About

## Shared resources

- `GatewayProfileStore`: multiple named non-secret Gateway profiles; sync default endpoint to legacy `remote_gateway.json` for CLI compatibility.
- `ProjectProfileStore`: multiple named workspace + ELF/AXF pairs.
- `GatewaySessionManager`: one in-memory credential cache per app process and reusable authenticated `RemoteSession` instances.
- `GatewayManagerDialog`: add/edit/delete/default/connect/disconnect named gateways.
- `GatewayLoginDialog`: password only; profile identity is fixed by the selection.
- `ProjectManagerDialog`: add/edit/delete/default debug projects.

## Safety invariants that must not change

- HardwareSession arbitration remains authoritative.
- No normal mass erase or Bootloader sector 0-2 write.
- Trusted Bootloader factory provisioning remains guarded and explicit.
- Monitor remains zero-halt/non-mutating.
- OpenOCD GDB/TCL stay loopback-only; Client forwards GDB through authenticated SSH.
- Remote programming remains fail-closed until the transfer/authorization path is accepted.
- UI consolidation must not reinterpret `HW-P1-001` as PASS.
