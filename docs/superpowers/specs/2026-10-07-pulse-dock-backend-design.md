# Pulse Dock production integration

The accepted native Qt Pulse Dock becomes the production default. Keep its compact toolbar, floating workspace, gradient titles and approved logo. Following the user's merge request, expose two bottom actions: Debug (default) and application programming. Debug has one project/connection selector: Máy này for local ST-Link or a saved IPC/SSH profile for remote debug. Both routes open VS Code. Monitoring, logs, probe selection and advanced settings remain accessible through Options.

MainWindowPulse extends ProductionMainWindow and retains its canonical services, shared AppContext, persisted project/Gateway stores and safe programming transaction. PulseView contains presentation and signals only. No production import depends on work/native-gui-demos or simulated state.

SSH login accepts a saved connection or host/user/port/password; only non-secret profile data is persisted. Password fields clear after submission/cancellation. Connect and debug lifecycle operations run on one serialized background worker; outcomes and shared state reach Qt through queued signals. Closing waits for pending operations, then releases bridge/leases and SSH before destroying the window.

Remote debug and remote VS Code use the existing attach-only VS Code controller, authenticated SSH and loopback GDB forward. Local debug uses the selected physical probe and matching saved workspace/symbol file. Ambiguous probes require explicit selection. launch.json replacement requires the existing confirmation. No debug load or flash commands are added.

The selected Debug connection, including an explicit local choice, is remembered when switching to programming. Programming selects local ST-Link; returning to Debug restores the remembered connection if the profile still exists.

Application programming remains local only. The compact view forwards the selected HEX to the canonical inspection, target preflight and confirmation dialog. No direct OpenOCD/programming path is introduced. No physical programming or target halt/reset is part of integration verification.

Acceptance: real saved profiles bind to both surfaces; setup/login/actions reach canonical services; duplicate operations and selection changes are blocked while busy; errors cannot appear as success; close safely releases ownership. Run focused Qt/controller tests, the repository unittest suite, entrypoint smoke and native visual QA. Include approved assets in frozen bundles and open the integrated source GUI for review.
