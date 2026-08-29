# B300 ST-Link Tools v0.9.0 — SSH loopback hardware acceptance (2026-08-29)

## Scope

This acceptance validates the real SSH forwarding path on one Windows laptop while the real B300 STM32F407 board is connected through ST-Link. It uses an ephemeral SSH server bound only to `127.0.0.1:2222`, real Windows OpenSSH client, public-key authentication, strict host-key checking and real `direct-tcpip` forwarding into the Gateway loopback endpoints.

It validates the complete SSH tunnel/debug stack without requiring a second laptop. It does **not** claim to exercise two-machine LAN/Wi-Fi latency, routing or firewall behaviour.

## Hardware / firmware

- Target: STM32F407, 512 KiB Flash, ~3.08 V.
- Bootloader: trusted v0.6.5 already provisioned.
- Application: `B300-Main-Custom`, AXF `Objects/F407/Main_V2_F407.axf`.
- Application metadata before/after: VALID / STLM / CONFIRMED.
- WRP S0-S2 before/after: protected.
- Initial/final target state: RUNNING.

## Gateway

Gateway was started by the final packaged v0.9.0 Windows CLI. OpenOCD remained loopback-only:

- GDB: `127.0.0.1:3333`
- TCL: `127.0.0.1:6666`
- Telnet: disabled
- GDB flash programming: disabled
- Hardware breakpoint override: enabled

Gateway remote-state guard recorded `initial_target_state=running` and repeatedly restored/verified `final=running` after GDB disconnects.

## CLI Client over real SSH — PASS

Real tunnel topology:

`CLI Client -> OpenSSH -> 127.0.0.1:2222 -> direct-tcpip -> Gateway 3333/6666 -> ST-Link -> STM32F407`

Observed results:

- Client attach: PASS.
- AXF/Flash source match: PASS.
- Source-level frame: `vApplicationIdleHook()` at `User\main.c:87`.
- Stack/register capture: PASS.
- Variable: `xTickCount = 2194662` in the first explicit variable test.
- Hardware breakpoint: `vApplicationIdleHook()` hit at `User\main.c:87`.
- Hardware watchpoint: `xTickCount` triggered in `xTaskIncrementTick()` at `FreeRTOS Source\tasks.c:2813`, sampled value `2352434`.
- Target restored to RUNNING after every one-shot operation.

Reconnect stress:

- 5/5 consecutive SSH Client sessions PASS.
- `xTickCount`: `2365162 -> 2367352 -> 2369547 -> 2372045 -> 2374263`.
- Values increased monotonically, confirming the Application resumed between sessions.

## GUI Client over real SSH — PASS

The real `DebugTab` Client role was exercised using Qt automation with the same production `SshDebugTunnel` and `DebugSession` classes; no fake tunnel or fake GDB backend was used.

Observed GUI state:

- `CLIENT CONNECTED · TARGET RUNNING`
- AXF verified against remote Application Flash.
- GUI diagnostic view displayed `xTickCount = 2275455`.
- GUI Disconnect closed the Client tunnel/session safely.
- Gateway target after GUI disconnect: RUNNING.

## VS Code / Cortex-Debug SSH path — PASS

The final packaged CLI generated the VS Code kit for SSH host `127.0.0.1:2222`. Because Gateway and Client were on the same laptop, Client-local GDB port `13336` was used while Gateway remained on `3333`.

Generated Cortex-Debug profile included:

- `type: cortex-debug`
- `request: attach`
- `servertype: external`
- `device: STM32F407ZE`
- `rtos: FreeRTOS`
- hardware breakpoints/watchpoints required
- `gdbTarget: 127.0.0.1:13336`
- GDB auto-resolved from STM32CubeIDE
- SSH command with `BatchMode=yes`, `StrictHostKeyChecking=yes`, `ExitOnForwardFailure=yes`

The exact generated SSH forwarding topology was then opened:

`127.0.0.1:13336 -> SSH 127.0.0.1:2222 -> Gateway 127.0.0.1:3333`

Using the same ARM GDB path generated for Cortex-Debug, external attach through this SSH tunnel produced:

- source frame `prvIdleTask()` at `FreeRTOS Source\tasks.c:3483`
- `xTickCount = 2329167`
- backtrace available
- clean detach
- Gateway remote guard confirmed final target RUNNING

Cortex-Debug itself had already been validated against the same real Gateway/GDB endpoint in the preceding hardware acceptance. This loopback test additionally validates the generated SSH transport used by the VS Code profile.

## Cleanup / final board state

- Ephemeral SSH server stopped.
- Test RSA key removed.
- User SSH `id_ed25519` and config were not modified.
- `known_hosts` restored from the pre-existing backup after the loopback test.
- Gateway stopped and ports released.
- Final packaged `target inspect`: STM32F407/512 KiB, RDP disabled, WRP S0-S2 protected, Application vector valid, metadata VALID/CONFIRMED, classification `READY_FOR_APPLICATION_FLASH`.

## Acceptance conclusion

**PASS** for the real SSH tunnel/debug path on one host:

- GUI Client over SSH: PASS
- CLI Client over SSH: PASS
- VS Code/Cortex-Debug generated SSH transport + external GDB: PASS
- variable/source/stack/register: PASS
- hardware breakpoint/watchpoint over SSH: PASS
- repeated SSH reconnect/cleanup: PASS
- target-state restoration: PASS

A separate two-laptop test can still be useful for field networking/latency/firewall behaviour, but it is no longer required to prove the correctness of the B300 SSH forwarding/debug implementation itself.
