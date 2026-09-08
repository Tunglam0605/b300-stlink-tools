# Task 7 report

Status: complete

Commit: `2462b5a49bc30380af8992f594ddded1174105ad` (`docs: package and explain gateway lease lifecycle`)

Changed paths: `b300_core/support_bundle.py`, `build_native_bundle.py`, `package_internal.py`, `packaging/windows/b300-stlink-gui.iss`, `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md`, `README.md`, `tests/test_gateway_support_evidence.py`.

Validation: focused Task 7 modules (2 + 7 + 18 + 24 tests), release metadata (5), release manifest (7), compileall, and `git diff --check` all passed.

Concerns: no hardware, flashing, SSH, or packaging network operations were performed.

Fix round: added documented --mode alias, wired CLI/GUI public Agent evidence, corrected troubleshooting table structure and mapped lease/control reason codes. Validation: gateway protocol (10), gateway support (2), support bundle (7), compileall and diff check passed.

Final fix round: documented acquire now works with bounded defaults (`b300-cli`/`B300-CLI`), and AppContext carries Agent evidence into GUI support exports. Validation: gateway protocol (10), CLI gateway setup (25), support evidence (2), compileall and diff check passed.

Accuracy fix: AppContext now has typed Agent/lease setters, clears both snapshots on connection/profile changes, and CLI exports the current public lease snapshot only. Validation: AppContext (10), support evidence (2), compileall and diff check passed.

Round 4: GatewayHealthController now publishes optional authoritative Agent/lease public snapshots into AppContext and clears absent values, preventing stale support evidence. Validation: gateway health controller (8), AppContext (10), gateway health UI (3), compileall and diff check passed.

Correction: GatewayHealthController previously polled only `gateway-status`; its frozen GatewaySnapshot had no Agent/lease producers, so GUI support evidence was always None. Gateway Agent status now includes the current public lease snapshot, and health polling parses/attaches typed evidence while preserving token redaction. Rebinding clears both evidence values. Validation: gateway health controller (9), support evidence (2), lease (10), lease client (3), remote session (15), compileall, and diff check passed.
