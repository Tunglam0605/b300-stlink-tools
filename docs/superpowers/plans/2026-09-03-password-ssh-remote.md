# Password SSH Remote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace managed-key SSH as the default B300 remote Client transport with ordinary password-interactive SSH while persisting only the Gateway endpoint.

**Architecture:** A shared password SSH option builder will create argv without any key path, B300 known-hosts store, or secret. Connectivity, live/debug tunnels, and VS Code consume it; endpoint persistence remains `RemoteGatewayProfile` v1. OpenOCD remains loopback-only and remote access stays SSH port forwarding.

**Tech Stack:** Python 3, stdlib subprocess, OpenSSH client, PySide6, unittest.

**Spec:** `docs/superpowers/specs/2026-09-03-password-ssh-remote-design.md`

## Global Constraints

- Persist only `host`, `user`, and `port`; never persist/pass/log a password.
- Do not accept a password CLI flag or write a password to an environment variable/generated kit.
- Retain GDB/TCL loopback forwarding only; never bind remote debug services to LAN.
- Do not change flash/provisioning/WRP/RDP/Bootloader code or run a board flash.
- Per user instruction, complete all source and test edits before executing any test command.

---

### Task 1: Shared ordinary-SSH transport and core consumers

**Files:**
- Modify: `b300_core/ssh_client.py`
- Modify: `b300_core/remote_connectivity.py`
- Modify: `b300_core/ssh_debug_tunnel.py`
- Modify: `b300_core/ssh_live_tunnel.py`
- Modify: `b300_core/live_session.py`
- Modify: `b300_cli/live_commands.py`
- Modify: `b300_stlink.py`

**Interfaces:**
- Produces: `password_ssh_options() -> tuple[str, ...]`, containing only normal OpenSSH password-interactive choices and no secret value.
- Consumes: validated `RemoteGatewayProfile(host, user, port)` and existing loopback forward shapes.
- Removes default dependencies on `managed_identity_file()` and `trusted_known_hosts_file()` from Client connection paths.

- [x] Replace `managed_ssh_options(identity_file, known_hosts_file)` use in remote Client paths with a shared `password_ssh_options()` builder.
- [x] Make the builder use `PreferredAuthentications=password,keyboard-interactive`, `PasswordAuthentication=yes`, `KbdInteractiveAuthentication=yes`, and `PubkeyAuthentication=no`; do not set `-i`, `IdentityFile`, `UserKnownHostsFile`, `GlobalKnownHostsFile`, `BatchMode=yes`, or any password-bearing option.
- [x] Retain `ConnectTimeout`, `ExitOnForwardFailure`, keepalive options, validated destination/user/port and exact `127.0.0.1:local:127.0.0.1:remote` forwarding shapes.
- [x] Make `gateway connect-check` execute in interactive mode, retain the `B300_SSH_READY` token validation, and report password-interactive readiness without secret output.
- [x] Make Live Monitor, integrated debug client and all CLI entry points create configs without managed key/trust files; preserve their existing OpenOCD action restrictions.
- [x] Ensure Windows tunnel subprocesses that must prompt use a visible console rather than `CREATE_NO_WINDOW`; retain normal inherited terminal behavior for CLI and POSIX.
- [x] Leave legacy key setup/trust utility commands available but remove all default-flow prerequisites and user-facing requirements for them.

### Task 2: VS Code and GUI password-interactive experience

**Files:**
- Modify: `b300_core/remote_vscode.py`
- Modify: `b300_gui/remote_vscode_dialog.py`
- Modify: `b300_gui/debug_tab.py`
- Modify: `b300_gui/gateway_setup_tab.py`
- Modify: `b300_cli/gateway_workflows.py`
- Modify: `b300_cli/parser.py`

**Interfaces:**
- Consumes: `password_ssh_options()` and `RemoteGatewayProfile` endpoint fields.
- Produces: interactive VS Code tunnel commands and GUI copy that describes endpoint memory plus native password prompts.

- [x] Remove `identity_file` and `known_hosts_file` requirements from `RemoteVsCodeProfile`, its tunnel argv, preview, kit write and GUI profile construction.
- [x] Preserve Windows PowerShell and POSIX command quoting; generated command must prompt natively when the tunnel starts and must contain no password string.
- [x] Update GUI gateway setup/status/client-setup copy to save endpoint only and direct the operator to enter their account password at connection time.
- [x] Remove default `--identity-file` plumbing from connect-check; retain only legacy key-maintenance arguments that serve explicit legacy commands.
- [x] Keep the host/user/port validation and the non-overwrite behavior of VS Code kits.

### Task 3: Tests, documentation, release workflow and verification

**Files:**
- Modify: `tests/test_remote_profile.py`
- Modify: `tests/test_ssh_debug_tunnel.py`
- Modify: `tests/test_ssh_live_tunnel.py`
- Modify: `tests/test_remote_vscode.py`
- Modify: `tests/test_remote_vscode_dialog.py`
- Modify: `tests/test_cli_gateway_setup.py`
- Modify: `tests/test_cli_live_monitor.py`
- Modify: `tests/test_b300_stlink.py`
- Modify: `tests/test_debug_tab.py`
- Modify: `tests/test_live_session.py`
- Modify: `tests/test_release_workflow.py`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/release-dry-run.yml`
- Modify: `docs/00_START_HERE.md`, `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md` where managed-key instructions are user-facing.

**Interfaces:**
- Consumes: Task 1 command builder and Task 2 VS Code/GUI behavior.
- Produces: regression tests proving no managed key/trust requirement and no password leakage; package smokes requiring only OpenSSH client.

- [x] Rewrite existing managed-key assertions to assert ordinary password-interactive argv, standard host-key interaction, endpoint-only persistence and absence of `-i`, managed known-host paths, `BatchMode=yes`, and password values.
- [x] Add focused tests for connect-check, Live Monitor, debug tunnel and VS Code kit with no identity/trust fixture; assert loopback forwarding and destination preservation.
- [x] Add GUI tests for preview/export without key prerequisites and user-facing password-prompt guidance.
- [x] Remove release workflow fixtures that create keys/B300 known-hosts; retain OpenSSH client installation and package dry-runs that do not attempt network authentication.
- [x] Update documentation to give normal `ssh user@host -p port` first-contact/password expectations, retain the prohibition on direct GDB/TCL LAN exposure, and state passwords are never stored by B300.
- [x] After all source/test/document edits are complete, run focused modules, the full isolated 83-module suite, compile/version/YAML/diff checks, then native package smoke tests. Do not flash hardware during verification.
