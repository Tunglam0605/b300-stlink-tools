# Managed remote Application programming — two-machine acceptance

Date: 2026-09-23 (Asia/Bangkok)
Source branch: `codex/remote-application-programming`
Final source commit: `8c8b40b43512562e6bb126279697ace05d06e9c2`
Status: **CLI candidate accepted on the tested Windows x64 Client, Ubuntu x64 Gateway, and attached B300 F407 board at the exact final source commit**. The GUI source flow was exercised on the board at the preceding candidate and the final packaged GUI passed startup smoke, but a real flash initiated from the visible packaged GUI window remains pending. This report is not a public Stable release or cross-platform CI verdict.

## Devices and immutable inputs

| Item | Evidence |
|---|---|
| Client | Windows x64, packaged B300 GUI/CLI candidate under `C:\Users\Admin\Documents\STM32\B300-STLink-Candidate-8c8b40b\app` |
| Gateway | `aubot@192.168.1.104`, `uname -m=x86_64`, per-user Gateway Agent systemd service active |
| Target/probe | One ST-Link 3748, serial unavailable; one STM32F407, 512 KiB Flash, 3.08 V, WRP S0–S2 protected, RDP off |
| Application HEX | `C:\Users\Admin\Documents\STM32\B300-Main-Custom\Objects\F407\Main_V2_F407.hex`; SHA-256 `E94C44983005EC9D93C4EC932DE65E7811E05D7F21B9468233C372D5D616D37F` |
| AXF | Matching `Main_V2_F407.axf`; SHA-256 `CB165838CAE876FBEC8D2B92C74277AAD5A34DEEC6ADED3E3331B10CFB712676` |
| Application span | `0x08010000..0x0803005B`, canonical span 131164 bytes, CRC32 `0x8C8A6ED2` |
| Trusted Bootloader | Bundled COM3/USART1 Bootloader v0.6.5, artifact SHA-256 `085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1` |

The Gateway's original installed CLI was saved at `/home/aubot/b300-remote-candidate-ToGrzn/backup-installed-b300-stlink` before candidate installation. The final Linux CLI was packaged with the pinned OpenOCD/GDB runtime and a regenerated `B300-RUNTIME.sha256`; `validate_runtime` passed on both the extracted bundle and the installed tree.

## Candidate artifacts

| Artifact | SHA-256 |
|---|---|
| Final Linux CLI archive `/home/aubot/b300-remote-candidate-ToGrzn/B300-STLink-CLI-Linux-x64-8c8b40b.tar.gz` | `3DB3E47ADB31E27DF1AC42813AE6B4923CE93C66E170AAF89F84CDB98483D92E` |
| Installed Gateway CLI binary | `674411EEA1BD7A35B830C5F7DFA521025484F36C284403DB2F3CC83C8266BEAC` |
| Windows GUI ZIP `B300-STLink-GUI-Windows-x64.zip` | `74DC7194356897B5573120172C06F1D022E88B3AFE512230DF9D8A728B4E34AA` |
| Windows CLI ZIP `B300-STLink-CLI-Windows-x64.zip` | `89F5566A8ED06CD6E4FB8F8765901612A4116984EAFBBF5A719C24EB7A9694E4` |

The Windows candidate ZIPs are retained outside Git at `C:\Users\Admin\Documents\STM32\B300-STLink-Candidate-8c8b40b`. The extracted Windows runtime passed `validate_runtime`, and `b300-stlink-gui.exe --smoke-test` exited 0. The existing installed Windows GUI and its profiles/credentials were left unchanged.

The existing Windows Gateway profile store was backed up to `C:\Users\Admin\Documents\STM32\B300-STLink-Candidate-8c8b40b\gateway_profiles.before.json`. The `aubot-tech-104` profile for `aubot@192.168.1.104:22` was added; three existing profiles and the prior default ID were preserved. No SSH password was stored by this acceptance run.

## Results

| Gate | Result and evidence |
|---|---|
| Gateway Agent upgrade | PASS. Service active; new Agent advertises `remote_application_flash_v1`. New CLI correctly withheld that capability while the old Agent process was still running. |
| Bootloader integrity and factory dry-run | PASS for read-only evidence. `provision-bootloader --dry-run --json` showed only S0–S2 erase/program, WRP off/on with reset/halt reload, and no RDP or mass erase. A read-only 49152-byte dump of S0–S2 had SHA-256 `1250392A70528B3CACA99F2B7123688A211A1A2E28A130A2DE2BE68CB8C34D58`, exactly matching the trusted HEX expanded with `0xFF` through `0x0800BFFF`. `doctor` showed WRP S0–S2 on, RDP off; target health remained `BOOTABLE` after read/resume. This run did not reprogram Bootloader. |
| Client SSH trust | PASS. Gateway's local ED25519 fingerprint `SHA256:idfXdR5NM7/rTvu3/FGwyTeyvHwChTREA9LepNWx+CY` was compared and pinned using `gateway trust-host`. Unpinned remote programming failed `HOST_KEY_UNTRUSTED` before upload. |
| CLI remote dry-run | PASS. Packaged Windows CLI uploaded the exact HEX; Gateway returned Sector 3–7, S0–S2 WRP, `0x0800C000`/44-byte metadata and the exact five-step transaction, then canceled and cleaned the staged artifact. The exact final Windows and Linux `8c8b40b` artifacts passed dry-run as job `e775dd72f68640d98b72b53859938294`. |
| CLI remote flash | PASS on exact final `8c8b40b` artifacts. Explicitly confirmed job `1dc745058e884bff8300b890ff9c46a7` returned `SUCCEEDED`, `PC=0x0802BC70`, `BKP1R=0`, `STLM CONFIRMED` sequence 8. Gateway log contains exact `** Verified OK **`. Client JSON log: `C:\Users\Admin\AppData\Local\Temp\b300-remote-flash-8c8b40b.log`. An earlier packaged candidate also succeeded as job `2fa2ecef39b247c2bdcca204551086d4` at sequence 4. |
| GUI real Gateway dry-run | PASS. Production GUI PROGRAM button used the authenticated Gateway, uploaded the same HEX, showed Sector 3–7/44-byte AppMeta/SHA-256 and completed without flash. |
| GUI source remote flash | PASS on preceding candidate `b421cb6`. Production GUI PROGRAM confirmation created job `18cdf8884de3483189e63ccd91cad091`, returned `Nạp Application từ xa thành công`, `PC=0x080270A4`, `BKP1R=0`. Gateway log contains exact `** Verified OK **`; AppMeta became `STLM CONFIRMED` sequence 6. This field operation used the real Qt window offscreen from source; final `8c8b40b` changed only the Gateway flash-log path guard and passed focused tests. |
| Packaged GUI remote flash | DEFERRED. `b300-stlink-gui.exe --smoke-test` passed, but the visible packaged GUI window was not used to initiate a destructive flash on this board. |
| Installed Application health | PASS. Independent `target health --json` on Gateway after final flash showed `BOOTABLE`, image CRC actual/expected `0x8C8A6ED2`, valid vector, valid `STLM CONFIRMED` sequence 8. Bootloader sectors remained WRP protected. |
| Remote source Debug | PASS after final flash. Final Windows CLI Client attached with the matching AXF through SSH GDB/TCL forwarding, resolved `prvIdleTask` in `FreeRTOS Source\\tasks.c`, read registers/stack, and reported `resumed_to_initial_state=true` with initial target `running`. |
| Remote Live Monitor | PASS after final flash. Final Windows CLI Client collected five `xTickCount:u32` samples at 0.5 s, zero overruns, final target `running`. |
| Lease contention | PASS. With one `VSCODE_DEBUG` lease active, a second `FLASH_APPLICATION` acquire returned `GATEWAY_BUSY` with the correct sanitized owner label/mode and did not flash. |
| Reconnect and cleanup | PASS. Packaged CLI `program-status` found the completed GUI job after reconnect; a newly constructed GUI window's **Kiểm tra job gần nhất** showed `STLM CONFIRMED`, PC and BKP. Only `job.json` and private `flash.log` remained in the job directory; HEX was removed. After Debug/Monitor/Flash, Agent returned `IDLE`, no OpenOCD process remained, and ports 3333/6666 were closed. |
| Software tests | PASS for the complete inventory of 155 `test_*.py` modules on clean Windows source HEAD `0fc0aec`, executed in bounded module isolation; all passed with no module left uncovered. Python 3.9 focused remote suite: 94 tests OK (2 skipped). Qt cases used `scripts/run_unittest_module.py --split-cases --case-timeout 45`. Focused Factory/Bootloader/owner policy run: 63 tests OK. Ubuntu x64 focused job/protocol/owner run: 31 tests OK. Packaged Windows GUI smoke and both installed runtime-integrity checks PASS. The required CI workflow's module-isolated suite passed at `37bbc28` on Windows x64, Ubuntu x64 and Ubuntu ARM64. A direct monolithic Windows `unittest discover` run spent a long time in cumulative GUI window suites and was stopped without a single-run verdict; that exact command is not claimed as PASS. |
| Cross-platform candidate packaging | PASS at source commit `37bbc28`. The no-publish Development packages workflow built and smoke-tested Windows, Ubuntu x64 and ARM64 artifacts, including Windows installer fresh/upgrade/forced rollback verification. These CI artifacts are separate from the earlier hardware-tested local `8c8b40b` bundle; `37bbc28` changed only a Python 3.9 test fixture. |

Gateway job logs:

- `/home/aubot/.b300-stlink/gateway-runtime/program-jobs/2fa2ecef39b247c2bdcca204551086d4/flash.log`
- `/home/aubot/.b300-stlink/gateway-runtime/program-jobs/18cdf8884de3483189e63ccd91cad091/flash.log`
- `/home/aubot/.b300-stlink/gateway-runtime/program-jobs/1dc745058e884bff8300b890ff9c46a7/flash.log`

All three retained logs now have mode `0600`; the final job log was created with `0600` by the final candidate. No terminal job retained a firmware HEX after cleanup.

After installation of the exact final Linux bundle and the final flash, a separate Windows CLI Debug inspect resolved `prvIdleTask` from the matching AXF and returned `resumed_to_initial_state=true`. A separate Live Monitor run produced five samples, no overruns, and final target `running`; Gateway Agent returned `IDLE` with no OpenOCD process or debug listeners.

## Remaining release gates

- CI and development packaging passed on Windows x64, Ubuntu x64 and Ubuntu ARM64 for `37bbc28`: CI runs `35835338059` and `35835342197`, Development packages run `35835437593`. The direct monolithic `unittest discover` command has no single-run verdict; CI uses the repository's module-isolated runner to handle Qt native teardown.
- Confirm a real Application flash from the visible packaged Windows GUI, or keep that packaged GUI hardware gate deferred.
- Exercise a physical SSH disconnect during an active flash and Agent crash recovery in a controlled test setup. These behaviors have automated failure-injection coverage but were not induced on this operating board.
- Publish a versioned, signed Stable release through the repository's release process if desired. The current installed Gateway and Windows download folder are internal candidate artifacts reporting source version `0.23.2`; no public release/tag is claimed here.

## Follow-up candidate `a953bc8` — remote dry-run evidence and SSH polling

The packaged Windows GUI `8c8b40b` was opened by the operator and its PROGRAM page returned **Gateway dry-run đạt** for the exact HEX, SHA-256 `E94C4498...16D37F`, Sector 3–7 and 44-byte AppMeta. The screenshot also showed the MCU card still reading **Chưa kiểm tra**. Source commit `a953bc8` fixes that presentation using the authenticated Gateway plan's actual device ID, 512-KiB capacity, measured voltage, WRP evidence and RDP state; it labels the result **Gateway dry-run**, clears it on HEX change, and refuses confirmation if any required field is missing or malformed. It also returns a pending job ID if SSH drops during CLI status polling after commit. Invalid or incomplete Gateway plans cancel and clean the prepared job before the Client releases its lease.

The new source GUI performed another real-Gateway dry-run without flash: `STM32F407 · 512 KiB · 3.08 V`, `WRP S0–S2 protected`, `RDP level 0`, and **Gateway dry-run đạt**. Windows GUI/CLI ZIPs built from `a953bc8` are stored outside Git at `C:\Users\Admin\Documents\STM32\B300-STLink-Candidate-a953bc8` (GUI SHA-256 `06386E018A9F809D11A1C792DEA2EA616E82709EEC910D9E4C130E85B74F5D6D`; CLI SHA-256 `FEDBA5A5C41DB46C6BB9098EB4BA4B3A6BE75DD52F8A21D36B05E230A40E09E3`). The bundle integrity and packaged GUI smoke gates passed. Ubuntu x64 Gateway has the matching Linux CLI bundle installed and verified (binary SHA-256 `4191DA544F9F233A92438965337067EEDB011B9C4CCBA78F8991EDD615EE69B6`; archive SHA-256 `3FC82C7525A49A11DB3B0C94FDFEA0FB8A9217B2A022219582EE00BBE7FEE382`). Agent is active/idle and target health remains `BOOTABLE`, CRC `0x8C8A6ED2`, metadata `CONFIRMED` sequence 8. No flash was performed with `a953bc8` in this follow-up.

Ten remote PROGRAM GUI cases passed in Qt case isolation, including missing/NaN/overflow target evidence and cleanup; 34 Python 3.9 core/CLI cases passed (2 skipped); Ubuntu candidate source passed 28 focused job/protocol cases. Packaged `a953bc8` GUI dry-run and destructive flash by the operator remain pending, as do cross-platform CI and a signed public release for this newer commit.

## Follow-up candidate `450c8bb` — packaged GUI dry-run confirmed

The operator opened the packaged Windows GUI at `C:\Users\Admin\Documents\STM32\B300-STLink-Candidate-450c8bb\app\b300-stlink-gui.exe` and completed a real-Gateway dry-run of the same Application HEX. The screenshot shows **Gateway dry-run đạt**, SHA-256 `E94C4498...16D37F`, Sector 3–7, 44-byte AppMeta, and the MCU card populated from Gateway evidence: `STM32F407 · 512 KiB · 3.08 V`, WRP S0–S2 protected, RDP level 0, badge **Gateway dry-run**. The packed GUI/CLI bundle passed `validate_runtime` and GUI smoke. Windows GUI ZIP SHA-256: `5B2CE96F2A04285F14D6AE72A013BA492E43C2E9AE50CA4A66297C6B21103AB5`; CLI ZIP SHA-256: `3A60183F169E4A0D79246DE93B7733BC88FBBFDF0134C79E928647D1DA240D22`.

The Ubuntu x64 Gateway runs matching protocol code built from `a953bc8`; `450c8bb` changed only the Windows GUI's behavior when local job-history persistence fails. In that case GUI now cancels the uncommitted Gateway job and releases the lease before showing a failure. Eleven Qt-isolated remote PROGRAM GUI tests pass, including this failure path. The exact source commit `93c338a` added the remote GUI module to CI's split-case list after ordinary 60-second Linux module runs timed out; both CI runs `35846413516` and `35846419503` passed Windows x64, Ubuntu x64 and ARM64 on Python 3.9. Development packages at source commit `450c8bb` passed on all three platforms in no-publish run `35845399712`.

Independent `target health --json` after the operator's packaged-GUI dry-run remained `BOOTABLE`, CRC `0x8C8A6ED2`, AppMeta `STLM CONFIRMED` sequence 8; Gateway Agent returned `GATEWAY_IDLE`. No destructive GUI flash was performed for this candidate. A signed Stable version, packaged GUI destructive flash acceptance, and controlled disconnect/crash hardware tests remain pending.
