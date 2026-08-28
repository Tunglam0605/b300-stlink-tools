# B300 ST-Link CLI Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a stable, automation-friendly B300 ST-Link CLI with read-only engineering diagnostics, safe single-probe Factory support, signed CLI self-update, and native Windows x64/Linux x64/Linux ARM64 packages close to GUI feature parity.

**Architecture:** Keep all hardware discovery, target validation, flash ownership, memory bounds, metadata decoding, updater verification, and installation safety in `b300_core`. Add a focused `b300_cli` presentation/controller package and retain `b300_stlink.py` as the backward-compatible executable entry point. Extend the signed release contract with separate CLI asset keys so GUI update selection remains unchanged.

**Tech Stack:** Python 3.9+, `argparse`, immutable dataclasses, OpenOCD 0.12.0-7, xPack GNU Arm Embedded GCC/GDB 15.2.1-1.1, `unittest`, PyInstaller, PowerShell, POSIX shell, GitHub Actions.

**Spec:** `C:\Users\Admin\.codex\attachments\805a9284-a7d1-4cc7-9364-1fdf04948ffe\pasted-text-1.txt`

## Global Constraints

- Source version and baseline are `0.5.3`; do not tag, publish, or create a release in this task.
- Application Flash owns exactly Sector 3 through Sector 7; Sector 0 through Sector 2 remain untouched.
- Factory provisioning owns exactly Sector 0 through Sector 2 and must restore and verify their WRP state after every attempted write.
- Never add mass erase, chip erase, RDP modification, arbitrary memory write, Option Byte write outside the existing Factory service, or automatic hardware retry.
- `doctor`, `probes`, `target inspect`, `metadata show`, `memory *`, and `debug` are read-only with respect to Flash and Option Bytes.
- `b300_core` remains the single source of truth; the CLI must not duplicate probe, OpenOCD, WRP, Flash-map, metadata, or updater policy.
- Preserve `flash`, `provision-bootloader`, and `debug` compatibility, including `b300-stlink debug --gdb-port 3333`.
- Default debug bind address is `127.0.0.1`; Telnet and TCL remain disabled unless an existing loopback-only Telnet option is explicitly selected.
- Debug readiness must be derived from the OpenOCD GDB-listener log, never by opening a raw TCP socket that OpenOCD reports as a rejected GDB client.
- OpenOCD and GDB child processes run without a visible console window on Windows while stdout/stderr remain piped into the application log.
- GUI debug artifacts bundle a pinned xPack `arm-none-eabi-gdb` runtime `15.2.1-1.1` for Windows x64, Linux x64, and Linux ARM64; build inputs are verified against exact SHA-256 trust anchors and binaries are not committed to Git.
- Every suitable command supports stable JSON with `schema_version: 1`, a command identifier, status, and a stable machine-readable reason code on failure.
- Do not modify Bootloader firmware, OTA protocol, Application address `0x08010000`, Metadata address `0x0800C000`, trusted Bootloader bytes, or GUI layout/source files.
- Do not access real hardware or flash a board during automated implementation or packaging smoke tests.
- Windows x64, Linux x64, and Linux ARM64 artifacts must be self-contained and resolve the trusted bundled OpenOCD runtime without CubeIDE or a system Python.

---

### Task 1: CLI foundation, version output, and complete probe discovery

**Files:**
- Create: `b300_cli/__init__.py`
- Create: `b300_cli/parser.py`
- Create: `b300_cli/reporting.py`
- Create: `b300_core/probe_selection.py`
- Modify: `b300_stlink.py`
- Modify: `b300_core/models.py`
- Modify: `b300_core/probe.py`
- Test: `tests/test_cli_version_probes.py`
- Test: `tests/test_core_probe_selection.py`
- Modify: `tests/test_core_probe_memory_metadata.py`
- Modify: `tests/test_gui_smoke.py` only if a constructor compatibility assertion is required; do not alter GUI behavior.

**Interfaces:**
- Consumes: `b300_version.__version__`, `b300_core.offline_setup.OPENOCD_VERSION`, `current_platform_name()`, and `list_probes()`.
- Produces: `ProbeInfo(serial: Optional[str], name: str, source: str, usb_identity: Optional[str] = None, status: str = "available")`; `ProbeInfo.serial_available`; `ProbeSelectionError(code, message)`; `select_probe(probes, requested_serial) -> tuple[ProbeInfo, ProbeRef]`; `build_parser()`; stable JSON/text reporting helpers.

- [ ] **Step 1: Add failing model and discovery tests for serial-less clones**

```python
def test_linux_serialless_clone_is_reported(self):
    probe = parse_linux_sysfs(self.sysfs_root_without_serial)[0]
    self.assertIsNone(probe.serial)
    self.assertFalse(probe.serial_available)
    self.assertIn("0483:374", probe.usb_identity)

def test_windows_composite_instance_is_reported_without_fake_serial(self):
    probe = parse_windows_pnp_output(self.composite_json)[0]
    self.assertIsNone(probe.serial)
    self.assertNotEqual(probe.usb_identity, "")
```

- [ ] **Step 2: Run the focused discovery tests and verify RED**

Run: `python -m unittest tests.test_core_probe_memory_metadata -v`

Expected: failures because `ProbeInfo.serial` is mandatory, serial-less probes are dropped, and USB identity is not exposed.

- [ ] **Step 3: Extend `ProbeInfo` and discovery without inventing serials**

Implement optional serials with backward-compatible defaults. Deduplicate serial-bearing probes by serial and serial-less probes by `source + usb_identity`; preserve deterministic sorting. On Linux, read `serial` independently so a missing file does not discard a matching VID `0483` / PID `374x` device. On Windows, retain composite/clone InstanceIds as `usb_identity` but never pass them to OpenOCD as serials.

- [ ] **Step 4: Add failing centralized selection tests**

```python
def test_single_serialless_probe_uses_safe_openocd_auto_selection(self):
    info, ref = select_probe((ProbeInfo(None, "Clone", "test", "usb:1"),), None)
    self.assertIsNone(ref.serial)
    self.assertEqual(info.usb_identity, "usb:1")

def test_multiple_probes_without_explicit_match_are_ambiguous(self):
    with self.assertRaisesRegex(ProbeSelectionError, "multiple"):
        select_probe(self.two_probes, None)
```

- [ ] **Step 5: Run selection tests and verify RED**

Run: `python -m unittest tests.test_core_probe_selection -v`

Expected: import failure because `b300_core.probe_selection` does not exist.

- [ ] **Step 6: Implement one safe selection policy in core**

Return the only physical probe when exactly one exists, including a serial-less clone via `ProbeRef(None)`. Match an explicit serial exactly. Raise stable codes `NO_PROBE`, `PROBE_NOT_FOUND`, `MULTIPLE_PROBES`, or `UNPINNABLE_MULTIPLE_PROBES`; never synthesize a serial.

- [ ] **Step 7: Add failing CLI tests for `--version`, `probes`, and JSON**

```python
def test_version_json_is_one_stable_object(self):
    code, records = run_cli(["--version", "--json"])
    self.assertEqual(code, 0)
    self.assertEqual(records[0]["schema_version"], 1)
    self.assertEqual(records[0]["version"], "0.5.3")

def test_probes_zero_is_nonzero_with_reason_code(self):
    code, records = run_cli(["probes", "--json"], probes=())
    self.assertNotEqual(code, 0)
    self.assertEqual(records[-1]["reason_code"], "NO_PROBE")
```

Cover `probes`, optional `probes list`, zero/one/multiple probes, a serial-less probe, global or command-local `--json`, and stable text fields: index, type/name, serial, `serial_available`, USB identity, source, and status.

- [ ] **Step 8: Run CLI tests and verify RED**

Run: `python -m unittest tests.test_cli_version_probes -v`

Expected: parser rejects `--version` and `probes`.

- [ ] **Step 9: Implement the thin CLI parser/reporting foundation**

Keep `b300_stlink.py` imports/re-exports used by existing tests, but delegate parser construction and output formatting to `b300_cli`. Preserve JSON Lines for existing streaming commands; new snapshot commands emit exactly one final JSON object. Version text reports CLI/Core `0.5.3`, OpenOCD `0.12.0-7`, and normalized platform.

- [ ] **Step 10: Run focused and GUI compatibility tests**

Run: `python -m unittest tests.test_cli_version_probes tests.test_core_probe_selection tests.test_core_probe_memory_metadata tests.test_b300_stlink tests.test_gui_smoke -v`

Expected: PASS with no frontend source changes.

- [ ] **Step 11: Commit Task 1**

```text
git add b300_cli b300_stlink.py b300_core/models.py b300_core/probe.py b300_core/probe_selection.py tests/test_cli_version_probes.py tests/test_core_probe_selection.py tests/test_core_probe_memory_metadata.py
git commit -m "feat(cli): add version and complete probe discovery"
```

### Task 2: Read-only target and system diagnostics

**Files:**
- Create: `b300_core/application_vector.py`
- Create: `b300_core/diagnostics.py`
- Create: `tests/test_core_diagnostics.py`
- Create: `tests/test_cli_doctor_target.py`
- Modify: `b300_core/models.py`
- Modify: `b300_core/service.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_cli/reporting.py`
- Modify: `b300_stlink.py`

**Interfaces:**
- Consumes: `select_probe`, `B300Service.inspect_target`, `B300Service.read_memory`, `B300Service.read_metadata`, `verify_openocd_tree`, fixed B300 flash constants.
- Produces: `ApplicationVector(initial_msp, reset_vector, valid, reason)`; `inspect_application_vector(data) -> ApplicationVector`; immutable `DiagnosticCheck`/`DiagnosticReport`; `DiagnosticsService.run() -> DiagnosticReport`; CLI `doctor` and `target inspect` snapshot reports.

- [ ] **Step 1: Add failing pure vector validation tests**

```python
def test_valid_application_vector_requires_sram_msp_and_thumb_reset_in_app(self):
    data = struct.pack("<II", 0x20020000, 0x08010101)
    self.assertTrue(inspect_application_vector(data).valid)

def test_reset_vector_outside_application_is_invalid(self):
    data = struct.pack("<II", 0x20020000, 0x08000101)
    self.assertFalse(inspect_application_vector(data).valid)
```

Use the F407 SRAM/CCM bounds declared in the module and the existing Application Flash bounds; the helper must never touch hardware.

- [ ] **Step 2: Run vector tests and verify RED**

Run: `python -m unittest tests.test_core_diagnostics.ApplicationVectorTests -v`

Expected: import failure for the missing helper.

- [ ] **Step 3: Implement vector parsing and add a session-safe `B300Service.read_memory` wrapper**

The service wrapper acquires `HardwareMode.READING`, calls the existing bounded `b300_core.memory.read_memory`, and exposes no write API. Refactor `read_sector` and `read_metadata` to use one non-nested private read helper so the session manager is acquired exactly once.

- [ ] **Step 4: Add failing injected diagnostics tests**

Cover no OpenOCD, untrusted bundled OpenOCD, zero/multiple probes, Linux `LIBUSB_ERROR_ACCESS`, wrong device ID, wrong flash size, RDP enabled, incomplete WRP reporting, S0-S2 protected, vector valid/invalid, and metadata ERASED/VALID/CORRUPT. Each test injects discovery/service functions and asserts no erase/program/protect operation is called.

- [ ] **Step 5: Run diagnostics tests and verify RED**

Run: `python -m unittest tests.test_core_diagnostics -v`

Expected: import failure for `b300_core.diagnostics`.

- [ ] **Step 6: Implement ordered read-only diagnostics**

The report order is runtime, OpenOCD availability/trust, probes/USB, target identity/voltage, RDP/WRP, Application vector, OTA metadata, then conclusion. Conclusions are `READY_FOR_APPLICATION_FLASH`, `BLOCKED`, or `LIMITED_READ_ONLY`; failures carry stable codes and a concrete `next_action`. Do not call `flash`, `factory_*`, reset commands, or Option Byte commands.

- [ ] **Step 7: Add failing CLI tests for enhanced `doctor` and `target inspect`**

```python
def test_target_inspect_auto_selects_exactly_one_probe(self):
    code, value = run_snapshot(["target", "inspect", "--json"], probes=(self.probe,))
    self.assertEqual(code, 0)
    self.assertEqual(value["target"]["device_id"], "0x00000413")

def test_target_inspect_blocks_ambiguous_probes(self):
    code, value = run_snapshot(["target", "inspect", "--json"], probes=self.two)
    self.assertEqual(value["reason_code"], "MULTIPLE_PROBES")
```

Assert target output includes device ID, MCU family `STM32F407`, flash KiB, voltage, RDP, WRP reported, protected sectors, vector validity, and classification. Assert doctor output includes all ordered checks and final conclusion.

- [ ] **Step 8: Run CLI tests and verify RED**

Run: `python -m unittest tests.test_cli_doctor_target -v`

Expected: parser has no `target inspect`, and doctor only reports OpenOCD.

- [ ] **Step 9: Wire the diagnostics and target snapshot handlers**

Use core report objects directly. Support `--probe-serial` for target inspection, auto-select exactly one probe, and block ambiguity. Keep all target operations read-only and use one final JSON object for each snapshot command.

- [ ] **Step 10: Run focused regression and static read-only assertions**

Run: `python -m unittest tests.test_core_diagnostics tests.test_cli_doctor_target tests.test_flash_service tests.test_hardware_session tests.test_core_openocd -v`

Expected: PASS; no diagnostic command contains `erase_sector`, `program`, `flash protect`, `mww`, or RDP commands.

- [ ] **Step 11: Commit Task 2**

```text
git add b300_core/application_vector.py b300_core/diagnostics.py b300_core/models.py b300_core/service.py b300_cli b300_stlink.py tests/test_core_diagnostics.py tests/test_cli_doctor_target.py
git commit -m "feat(cli): add read-only target diagnostics"
```

### Task 3: Metadata and bounded memory commands

**Files:**
- Create: `tests/test_cli_memory_metadata.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_cli/reporting.py`
- Modify: `b300_stlink.py`
- Modify: `b300_core/service.py`
- Modify: `b300_core/metadata.py` only if a presentation-neutral normalized mapping is needed.

**Interfaces:**
- Consumes: `B300Service.read_memory`, `read_sector`, `read_metadata`, `sector_by_index`, `validate_read_range`, and `OtaMetadata`.
- Produces: CLI `metadata show`, `memory read ADDRESS LENGTH`, `memory read-sector SECTOR`, and `memory dump ADDRESS LENGTH OUTPUT.bin`; no write command.

- [ ] **Step 1: Add failing parser and range tests**

```python
def test_memory_read_accepts_absolute_hex_address(self):
    args = parse_args(["memory", "read", "0x08010000", "64", "--json"])
    self.assertEqual(args.address, 0x08010000)

def test_memory_range_outside_f407_is_rejected_before_service(self):
    code, value = run_cli(["memory", "read", "0x07FFFFFF", "8", "--json"])
    self.assertEqual(value["reason_code"], "INVALID_MEMORY_RANGE")
```

Cover invalid number, zero/negative length, overflow, Sector outside 0..7, and output path validation.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_cli_memory_metadata -v`

Expected: parser rejects `memory` and `metadata`.

- [ ] **Step 3: Implement memory read and read-sector output**

Text mode renders 16-byte rows with absolute addresses. JSON includes `address`, `end_address`, `size`, and lowercase hexadecimal data. The handler must call the bounded service API and never construct OpenOCD commands.

- [ ] **Step 4: Add failing binary dump behavior tests**

Use a temporary directory and a real output file. Assert exact bytes, start/end/size, uppercase SHA-256 in text and lowercase SHA-256 in JSON, refusal to overwrite unless `--force` is explicit, and deletion of a partial output on failure.

- [ ] **Step 5: Run dump tests and verify RED**

Run: `python -m unittest tests.test_cli_memory_metadata.MemoryDumpTests -v`

Expected: `memory dump` is not implemented.

- [ ] **Step 6: Implement atomic dump output**

Read bytes first, write a sibling temporary file, flush and `os.replace` only after the complete read succeeds. Refuse directories and existing files without `--force`. The command remains read-only on the target.

- [ ] **Step 7: Add failing metadata presentation tests**

Cover ERASED fields rendered as `-`/`null` instead of `4294967295`, plus all VALID and CORRUPT fields: magic, format, state, image size, image CRC32, board token, sequence, stored metadata CRC32, calculated CRC32, and `valid`.

- [ ] **Step 8: Implement metadata snapshot output from `OtaMetadata`**

Do not reimplement CRC or state parsing in CLI. ERASED preserves raw magic `0xFFFFFFFF` but semantic fields become null. VALID/CORRUPT use the decoder output verbatim.

- [ ] **Step 9: Verify help contains no target memory write surface**

Run: `python b300_stlink.py memory --help`

Expected: only `read`, `read-sector`, and `dump`; no `write`, `poke`, `mww`, or `flash write`.

- [ ] **Step 10: Run focused tests**

Run: `python -m unittest tests.test_cli_memory_metadata tests.test_core_probe_memory_metadata tests.test_hardware_session -v`

Expected: PASS.

- [ ] **Step 11: Commit Task 3**

```text
git add b300_cli b300_stlink.py b300_core/service.py tests/test_cli_memory_metadata.py
git commit -m "feat(cli): expose read-only memory and metadata"
```

### Task 4: Factory one-probe safety, flash preflight detail, and debug compatibility

**Files:**
- Create: `tests/test_cli_factory_probe_policy.py`
- Create: `tests/test_cli_flash_debug_ux.py`
- Modify: `tests/test_core_hex_policy.py`
- Modify: `tests/test_b300_stlink.py`
- Modify: `b300_core/hex_image.py`
- Modify: `b300_core/models.py`
- Modify: `b300_core/probe_selection.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_cli/reporting.py`
- Modify: `b300_stlink.py`
- Modify: `AGENTS.md` to replace the old mandatory-serial statement with the approved single-physical-probe rule.

**Interfaces:**
- Consumes: trusted Bootloader verification, `select_probe`, `B300Service.inspect_target`, `factory_plan`, `provision_bootloader`, normal Application Flash plan/service, and `DebugService`.
- Produces: safe real Factory with one serial-less physical probe; `ImageInfo.initial_msp/reset_vector`; richer flash preflight/result output; optional compatibility spelling `debug server`.

- [ ] **Step 1: Add failing Application vector extraction tests**

Create a literal Intel HEX fixture with eight bytes at `0x08010000`. Assert `inspect_image()` exposes MSP `0x20020000` and reset vector `0x08010101`, and rejects an incomplete/invalid vector before provisioning.

- [ ] **Step 2: Run HEX tests and verify RED**

Run: `python -m unittest tests.test_core_hex_policy -v`

Expected: `ImageInfo` lacks MSP and reset-vector fields.

- [ ] **Step 3: Extend image inspection using the shared vector helper**

Add optional fields with defaults so existing test factories remain compatible. Require the real Application vector for newly inspected Application HEX files, but keep Bootloader validation within its existing fixed range.

- [ ] **Step 4: Replace the old Factory serial test with failing one-probe policy tests**

Cover zero probe blocked, one serialized probe allowed with confirmation, one serial-less probe allowed with confirmation and `ProbeRef(None)`, multiple probes blocked without an exact serial, bad target blocked, incomplete WRP report blocked, wrong F407/flash size/RDP blocked, and missing confirmation blocked before target access.

- [ ] **Step 5: Run Factory CLI tests and verify RED**

Run: `python -m unittest tests.test_cli_factory_probe_policy -v`

Expected: confirmed serial-less Factory is rejected by the current mandatory `--probe-serial` branch.

- [ ] **Step 6: Use centralized probe selection before real Factory inspection**

Dry-run remains hardware-free and can use an explicit serial or auto placeholder. Real Factory first checks confirmation, then discovers/selects the physical probe, then performs the existing target/trusted artifact/WRP transaction unchanged. Multiple unpinnable probes remain blocked. Do not modify `FactoryService` transaction order.

- [ ] **Step 7: Add failing flash UX tests**

Assert preflight reports SHA-256, image start/end/size, MSP/reset vector, selected probe, target identity, exact erase sectors, `S0-S2 untouched`, Sector 3 metadata erase, and Sector 4-7 Application. Assert final result reports status, PC, BKP1R, WRP summary, and `application_running`.

- [ ] **Step 8: Implement richer output without changing normal Flash commands**

Derive presentation from `ImageInfo`, `FlashPlan`, `TargetInfo`, and `FlashResult`. Keep command generation and safety policy in core. Existing JSON event names and fields stay compatible; additions are append-only.

- [ ] **Step 9: Add failing `debug server` and remote warning tests**

Assert `debug` and `debug server` build equivalent commands; default loopback, disabled Telnet/TCL, and no erase/program/write command remain true. A non-loopback bind emits an explicit unauthenticated/unencrypted GDB warning and recommends an SSH tunnel.

- [ ] **Step 10: Implement optional debug mode token and warning**

Do not add a new Debug service or any firmware-loading option. Preserve direct legacy flags after `debug`.

- [ ] **Step 11: Run focused safety regression**

Run: `python -m unittest tests.test_cli_factory_probe_policy tests.test_cli_flash_debug_ux tests.test_b300_stlink tests.test_factory_service tests.test_factory_openocd tests.test_factory_policy tests.test_flash_service tests.test_debug_service -v`

Expected: PASS; normal Application path has no WRP/RDP/mass erase and Factory still programs only S0-S2 with WRP restore verification.

- [ ] **Step 12: Commit Task 4**

```text
git add AGENTS.md b300_core/hex_image.py b300_core/models.py b300_core/probe_selection.py b300_cli b300_stlink.py tests/test_cli_factory_probe_policy.py tests/test_cli_flash_debug_ux.py tests/test_core_hex_policy.py tests/test_b300_stlink.py
git commit -m "feat(cli): harden factory selection and flash UX"
```

### Task 5: Reliable bundled GDB and background OpenOCD process lifecycle

**Files:**
- Create: `b300_core/process_startup.py`
- Create: `b300_core/gdb_runtime.py`
- Create: `tests/test_process_startup.py`
- Create: `tests/test_gdb_runtime.py`
- Modify: `b300_core/openocd.py`
- Modify: `b300_core/debug_service.py`
- Modify: `b300_core/gdb_mi.py`
- Modify: `build_native_bundle.py`
- Modify: `package_internal.py`
- Modify: `tests/test_core_openocd.py`
- Modify: `tests/test_debug_service.py`
- Modify: `tests/test_gdb_mi.py`
- Modify: `tests/test_build_native_bundle.py`
- Modify: `tests/test_gui_packaging.py`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/release-dry-run.yml`
- Modify: `docs/04_DEBUG.md`

**Interfaces:**
- Consumes: existing OpenOCD command builders/log sink, GDB/MI transport, native bundle builder, and platform bundle roots.
- Produces: `child_process_kwargs() -> dict`; `resolve_gdb(explicit: Optional[str] = None) -> str`; `GdbRuntimeInfo`; log-correlated `DebugService` readiness; pinned GDB runtime inside GUI release artifacts.

- [ ] **Step 1: Add failing Windows child-process policy tests**

```python
def test_windows_child_process_is_hidden_without_losing_pipes(self):
    kwargs = child_process_kwargs(platform_name="windows")
    self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

def test_non_windows_child_process_has_no_windows_creation_flags(self):
    self.assertEqual(child_process_kwargs(platform_name="linux"), {})
```

Also inject process factories into `OpenOcdRunner`, `DebugService`, and `GdbMiBackend`; assert Windows startup kwargs are passed together with `stdout=PIPE`, `stderr=STDOUT`, `text=True`, and `shell=False` where supported.

- [ ] **Step 2: Run process tests and verify RED**

Run: `python -m unittest tests.test_process_startup tests.test_core_openocd tests.test_debug_service tests.test_gdb_mi -v`

Expected: missing helper and no `CREATE_NO_WINDOW` in captured `Popen` kwargs.

- [ ] **Step 3: Implement one shared child-process startup policy**

On Windows use `subprocess.CREATE_NO_WINDOW` (and a hidden `STARTUPINFO` fallback when available); on Linux return no Windows-only flags. Apply it to one-shot OpenOCD, persistent Debug OpenOCD, and GDB/MI. Preserve redirected output so GUI/CLI logging is unchanged.

- [ ] **Step 4: Replace the failing raw-TCP readiness test with failing log-readiness tests**

Cover the exact listener line `Info : Listening on port 3333 for gdb connections`, unrelated logs, wrong port, process exit before readiness, and bounded timeout. Assert `socket.create_connection` is never called and no `attempted 'gdb' connection rejected` line is generated by the tool itself.

- [ ] **Step 5: Run DebugService tests and verify RED**

Run: `python -m unittest tests.test_debug_service -v`

Expected: current `port_waiter`/raw socket design does not expose log-correlated readiness.

- [ ] **Step 6: Implement log-correlated readiness without losing live logs**

Start one output-forwarding thread before waiting. For every line, forward to the existing sink and set a readiness event only when the requested GDB port and listener phrase match. While waiting, also detect process exit. On timeout, stop OpenOCD, release the hardware-session lease, and report the last bounded log context.

- [ ] **Step 7: Add failing GDB resolver tests**

Cover explicit safe path, `B300_GDB`, verified bundled `vendor/gdb/bin/arm-none-eabi-gdb(.exe)`, PATH `arm-none-eabi-gdb`, Linux `gdb-multiarch` fallback, missing executable with actionable error, and platform-normalized runtime information. `GdbMiBackend()` must defer resolution until `start()` so the GUI can open even when diagnostic state is incomplete.

- [ ] **Step 8: Run resolver tests and verify RED**

Run: `python -m unittest tests.test_gdb_runtime tests.test_gdb_mi -v`

Expected: hard-coded `arm-none-eabi-gdb` remains and no bundled candidate exists.

- [ ] **Step 9: Implement GDB resolution and the shared diagnostic contract**

Prefer an explicit/configured executable, then packaged runtime, then PATH. Validate unsafe control characters and executable existence. Expose path/version/availability through `GdbRuntimeInfo`; Task 2 consumes this shared contract when it creates the full Doctor diagnostics. Lack of GDB blocks integrated debug but does not block Application Flash.

- [ ] **Step 10: Add failing pinned-runtime packaging tests**

Pin these official xPack archives and SHA-256 values:

```text
Windows x64: xpack-arm-none-eabi-gcc-15.2.1-1.1-win32-x64.zip
SHA256: bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea
Linux x64: xpack-arm-none-eabi-gcc-15.2.1-1.1-linux-x64.tar.gz
SHA256: da6a49ad4003944b823c6c93702a8787c922ab34bd7e918ec0eaf6933a9b1ff6
Linux ARM64: xpack-arm-none-eabi-gcc-15.2.1-1.1-linux-arm64.tar.gz
SHA256: 67980c7990eba7bb7ffdf39699102effd70889f5ac427be19a8c8a6c5fab2972
```

Assert the builder rejects a filename/hash mismatch, extracts a portable `vendor/gdb` runtime, includes upstream license/provenance, packages GDB only in GUI artifacts (CLI debug-server artifacts remain small), and smoke-runs bundled `arm-none-eabi-gdb --version` on each native CI runner.

- [ ] **Step 11: Implement trusted build-time GDB acquisition and packaging**

Download only from immutable xPack GitHub Release URLs, verify the pinned digest before bounded safe extraction, and never commit the archive/binaries. Reuse the GUI bundle root so Windows ZIP/installer, AppImage, and DEB automatically carry `vendor/gdb`. Preserve stable release filenames.

- [ ] **Step 12: Run focused backend and packaging regression**

Run: `python -m unittest tests.test_process_startup tests.test_gdb_runtime tests.test_debug_service tests.test_gdb_mi tests.test_core_openocd tests.test_build_native_bundle tests.test_gui_packaging tests.test_gui_smoke tests.test_debug_tab -v`

Expected: PASS; OpenOCD/GDB log pipes remain live, no raw readiness connection occurs, and no GUI source file changed.

- [ ] **Step 13: Commit Task 5**

```text
git add b300_core/process_startup.py b300_core/gdb_runtime.py b300_core/openocd.py b300_core/debug_service.py b300_core/gdb_mi.py build_native_bundle.py package_internal.py .github/workflows docs/04_DEBUG.md tests
git commit -m "fix(debug): bundle GDB and hide backend processes"
```

### Task 6: Signed CLI update check and download contract

**Files:**
- Create: `b300_core/cli_update.py`
- Create: `b300_cli/update_commands.py`
- Create: `tests/test_cli_update.py`
- Modify: `b300_core/release_manifest.py`
- Modify: `scripts/release/release_contract.py`
- Modify: `scripts/release/build_metadata.py` only if required by the expanded mapping.
- Modify: `scripts/release/verify_published.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_stlink.py`
- Modify: `tests/test_release_manifest.py`
- Modify: `tests/test_release_metadata.py`
- Modify: `tests/test_release_published_verifier.py`

**Interfaces:**
- Consumes: signed `latest.json`, Minisign public key, `UpdateClient.check/download`, CLI package names already present in the release contract.
- Produces: signed platform keys `windows-x64-cli`, `linux-x64-cli`, `linux-arm64-cli`; `detect_cli_update_platform()`; CLI `update check` and `update download`.

- [ ] **Step 1: Add failing release-contract tests for separate GUI and CLI keys**

```python
def test_latest_manifest_contains_gui_and_cli_platforms(self):
    self.assertEqual(UPDATE_PLATFORM_FILES["windows-x64"], "B300-STLink-GUI-Windows-x64.exe")
    self.assertEqual(UPDATE_PLATFORM_FILES["windows-x64-cli"], "B300-STLink-CLI-Windows-x64.zip")
```

Also assert Linux x64/ARM64 CLI tar names and that existing GUI keys/filenames are unchanged.

- [ ] **Step 2: Run release tests and verify RED**

Run: `python -m unittest tests.test_release_manifest tests.test_release_metadata tests.test_release_published_verifier -v`

Expected: CLI platform keys are absent.

- [ ] **Step 3: Extend the signed schema additively**

Add CLI keys to both the generator contract and core accepted filename map. Keep schema version 1 and exact immutable GitHub URLs. The published verifier must require every GUI and CLI key after the next release, without changing signature/SHA validation.

- [ ] **Step 4: Add failing platform detector tests**

Cover Windows AMD64/x86_64, Linux AMD64/x86_64, Linux ARM64/aarch64, empty machine fallback, and unsupported OS/CPU. CLI detection must not depend on AppImage/DEB execution mode.

- [ ] **Step 5: Implement CLI-only platform selection**

Return only the three CLI keys and leave `detect_update_platform()` untouched for GUI consumers.

- [ ] **Step 6: Add failing `update check/download` command tests with signed fixtures**

Use the real manifest parser and Ed25519 test signatures. Assert current/latest/availability, no HTML scraping, correct CLI asset selection, destination file SHA/size verification, cancellation cleanup, unsafe filename rejection, wrong platform rejection, invalid signature rejection, and SHA mismatch rejection.

- [ ] **Step 7: Run CLI updater tests and verify RED**

Run: `python -m unittest tests.test_cli_update -v`

Expected: parser has no `update` command.

- [ ] **Step 8: Implement update check and verified download handlers**

Build `UpdateClient` with the embedded public key and detected CLI platform. Default downloads to the platform user cache and allow an explicit destination directory. Text/JSON must identify the signed version, asset, size, SHA-256, and final path. Never scrape a Release page and never install in this task step.

- [ ] **Step 9: Run focused updater regression**

Run: `python -m unittest tests.test_cli_update tests.test_updater tests.test_release_manifest tests.test_release_metadata tests.test_release_published_verifier tests.test_gui_updater -v`

Expected: PASS and GUI still selects its original installer/AppImage/DEB keys.

- [ ] **Step 10: Commit Task 6**

```text
git add b300_core/cli_update.py b300_core/release_manifest.py b300_cli/update_commands.py b300_cli/parser.py b300_stlink.py scripts/release tests/test_cli_update.py tests/test_release_manifest.py tests/test_release_metadata.py tests/test_release_published_verifier.py
git commit -m "feat(cli): add signed update check and download"
```

### Task 7: Controlled self-install, Ubuntu setup, and native CLI packaging

**Files:**
- Create: `b300_core/cli_update_install.py`
- Create: `b300_core/linux_usb.py`
- Create: `tests/test_cli_update_install.py`
- Create: `tests/test_linux_usb_setup.py`
- Modify: `b300_cli/update_commands.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_stlink.py`
- Modify: `build_native_bundle.py`
- Modify: `package_internal.py`
- Modify: `install.ps1`
- Modify: `install.sh`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/release-dry-run.yml`
- Modify: `tests/test_build_native_bundle.py`
- Modify: `tests/test_gui_packaging.py` only for shared package-helper behavior, not GUI behavior.
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: a package already selected from signed metadata and verified by `UpdateClient.download`; trusted archive filename/SHA/size; platform-standard user installation roots.
- Produces: `update install`/`self-update`; controlled Linux `setup`; Windows CLI onedir; native package smoke commands for three platforms.

- [ ] **Step 1: Add failing safe archive-install tests**

Cover ZIP and TAR path traversal, symlink/hardlink/device rejection, entry/expanded-size limits, wrong filename/platform, digest mismatch, missing executable/bootstrap, atomic staging, rollback on replacement failure, and refusal to target a system directory. Use only temporary directories.

- [ ] **Step 2: Run install tests and verify RED**

Run: `python -m unittest tests.test_cli_update_install -v`

Expected: import failure for `b300_core.cli_update_install`.

- [ ] **Step 3: Implement verified CLI bundle installation and detached handoff**

The active process rechecks the signed filename, size, and SHA-256 before extraction. Extract into a private user-cache staging directory using bounded safe-path rules. On Windows/Linux, spawn a staged helper process, wait for the parent PID to exit, atomically replace only the standard per-user B300 installation root, restore the previous tree on failure, recreate the CLI launcher, and emit a durable result log. Never invoke a shell with untrusted strings.

- [ ] **Step 4: Add failing end-to-end command tests**

Assert `update install` performs a fresh signed check and verified download unless `--verified-package` is accompanied by matching signed metadata; `self-update` aliases install; invalid signature/SHA/platform never reaches the installer; source/portable execution reports a safe manual fallback when managed replacement is not supported.

- [ ] **Step 5: Implement command orchestration**

Keep signature and download verification in existing updater core. The install helper receives only the already-verified asset contract plus explicit user-root destinations.

- [ ] **Step 6: Add failing Linux USB/udev diagnostics and setup tests**

Cover non-Linux unsupported, rule already present, missing rule dry-run, controlled install command, no full-CLI sudo, reload/trigger limited to VID `0483` PID `374?`, and actionable replug guidance. Inject filesystem and command runner dependencies; never modify the test host.

- [ ] **Step 7: Implement `setup` as an explicit controlled Ubuntu operation**

Default is inspect/dry-run. Require `--install-udev-rule --confirm-system-change` before invoking `pkexec`/`sudo` for the one rule-copy/reload step. The main CLI process remains unprivileged and prints the exact proposed system change first.

- [ ] **Step 8: Add failing Windows onedir packaging tests**

Assert Windows CLI PyInstaller uses `--onedir`, package input is an application root containing `b300-stlink.exe` and `_internal`, the installed launcher resolves it, OpenOCD remains beside the application tree, and Linux CLI remains a self-contained native bundle. Assert no external Python/CubeIDE dependency.

- [ ] **Step 9: Convert only Windows CLI to onedir and preserve stable archive names**

Keep GUI packaging unchanged. Teach `package_internal.py` to package the CLI application tree using its existing safe `application_root` mechanism. Update Windows install launcher paths without altering the CLI command name.

- [ ] **Step 10: Add CI smoke gates for all native CLI artifacts**

For Windows x64, Linux x64, and Linux ARM64, stage the CLI archive and run `--help`, `--version`, and `doctor --json` without probe access. Verify the packaged OpenOCD executable/version and no Python dependency. Do not flash hardware.

- [ ] **Step 11: Run focused packaging/setup regression**

Run: `python -m unittest tests.test_cli_update_install tests.test_linux_usb_setup tests.test_build_native_bundle tests.test_gui_packaging tests.test_release_workflow tests.test_offline_setup -v`

Expected: PASS.

- [ ] **Step 12: Commit Task 7**

```text
git add b300_core/cli_update_install.py b300_core/linux_usb.py b300_cli build_native_bundle.py package_internal.py install.ps1 install.sh .github/workflows tests/test_cli_update_install.py tests/test_linux_usb_setup.py tests/test_build_native_bundle.py tests/test_gui_packaging.py tests/test_release_workflow.py
git commit -m "feat(cli): add managed updates and native packaging"
```

### Task 8: Operator documentation, full regression, packaging smoke, and final safety audit

**Files:**
- Modify: `README.md`
- Modify: `DOWNLOAD.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/00_START_HERE.md`
- Modify: `docs/01_SETUP_WINDOWS.md`
- Modify: `docs/02_SETUP_UBUNTU_IPC.md`
- Modify: `docs/03_FLASH_FIRMWARE.md`
- Modify: `docs/04_DEBUG.md`
- Modify: `docs/05_TROUBLESHOOTING.md`
- Modify: `docs/06_AI_AGENT_MANUAL.md`
- Modify: `docs/08_RELEASE_ACCEPTANCE.md`
- Create: `docs/10_CLI_REFERENCE.md`
- Create: `docs/11_CLI_HARDWARE_ACCEPTANCE.md`
- Modify: `tests/test_release_documentation.py`
- Create: `tests/test_cli_safety_contract.py`

**Interfaces:**
- Consumes: every command and package completed in Tasks 1 through 7.
- Produces: concise operator flow, complete AI/automation reference, hardware acceptance checklist, and final test/safety evidence.

- [ ] **Step 1: Add failing documentation behavior tests**

Assert download tables link distinct GUI/CLI artifacts for Windows x64, Linux x64, and Linux ARM64; CLI reference lists every exact command; setup docs never recommend running the whole CLI as sudo; and Factory documentation states the confirmed single-physical-probe serial-less exception.

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python -m unittest tests.test_release_documentation -v`

Expected: missing CLI reference and new command coverage.

- [ ] **Step 3: Write numbered operator and AI-agent documentation**

Keep human operation to numbered steps: download/install, `doctor`, select probe, dry-run, run. Put complete schemas, exit codes, reason codes, update/setup behavior, debug tunnel guidance, and memory-read examples in `docs/10_CLI_REFERENCE.md`. Record version `0.5.3` source state in CHANGELOG without creating a new release tag.

- [ ] **Step 4: Write the hardware acceptance checklist without running destructive tests**

Provide Windows x64, Linux x64, and Linux ARM64 matrices for doctor/probes/target/metadata/memory/Application dry-run/Application real/Factory dry-run/Factory real/debug. Every destructive row records before state, command, after state, WRP, RDP, Sector 3, and Application state. Mark real hardware steps `NOT RUN` until explicitly authorized.

- [ ] **Step 5: Add static and behavioral safety-contract tests**

Exercise generated commands through real core builders with literal safe fixtures. Assert Application erase exactly `(3,4,5,6,7)`, Factory erase exactly `(0,1,2)`, Factory WRP off/verify/program/on/verify sequence, and absence of mass erase/RDP modification. Assert memory/doctor/debug expose no write subcommand or generated write command.

- [ ] **Step 6: Run focused CLI and safety tests**

Run: `python -m unittest tests.test_cli_safety_contract tests.test_b300_stlink tests.test_cli_version_probes tests.test_cli_doctor_target tests.test_cli_memory_metadata tests.test_cli_factory_probe_policy tests.test_cli_flash_debug_ux tests.test_cli_update tests.test_cli_update_install -v`

Expected: PASS.

- [ ] **Step 7: Run full regression in the isolated worktree**

Run: `python -m unittest discover -s tests -q`

Expected: all tests PASS with `QT_QPA_PLATFORM=offscreen` on Windows test hosts.

- [ ] **Step 8: Run syntax, whitespace, and prohibited-command audit**

```text
python -m compileall -q b300_core b300_cli b300_stlink.py scripts
git diff --check origin/main...HEAD
rg -n "mass_erase|stm32f2x (lock|unlock)|option_write|memory write|\bmww\b" b300_core b300_cli b300_stlink.py
```

Expected: compile and diff checks pass; any search hit is confined to explicit rejection/tests or the approved Factory WRP builder, never Application/read-only production paths.

- [ ] **Step 9: Build and smoke-test the Windows x64 CLI artifact locally**

Run the native CLI-only build with `--internal-distribution-approved`, extract/stage it, then run packaged `--help`, `--version --json`, and `doctor --json`. Verify bundled OpenOCD `--version`. Record exact output and artifact SHA-256; do not discover/flash a board during packaging smoke.

- [ ] **Step 10: Verify Linux x64 and Linux ARM64 packaging gates**

Use GitHub Actions workflow validation/tests as the authoritative cross-architecture evidence unless native runners are available. If a native runner is available, build and run the same artifact smoke commands. Clearly distinguish `PASS`, `CI-CONFIGURED NOT RUN`, and `HARDWARE NOT RUN`.

- [ ] **Step 11: Run GUI regression after all shared-core changes**

Run: `python -m unittest tests.test_gui_smoke tests.test_gui_memory tests.test_gui_updater tests.test_gui_interlocks tests.test_debug_tab -v`

Expected: PASS without any GUI source modifications on this branch.

- [ ] **Step 12: Commit Task 8**

```text
git add README.md DOWNLOAD.md CHANGELOG.md docs tests/test_release_documentation.py tests/test_cli_safety_contract.py
git commit -m "docs(cli): add operation and acceptance guidance"
```

- [ ] **Step 13: Request final whole-branch review**

Review `origin/main...HEAD` against the authoritative spec and every Global Constraint. Resolve Critical/Important findings through the bounded review loop, rerun affected tests, and leave the branch untagged/unpublished.
