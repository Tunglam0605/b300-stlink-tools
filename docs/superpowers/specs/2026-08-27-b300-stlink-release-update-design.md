# B300 ST-Link Tools v0.3.0 Release and Update Design

**Date:** 2026-08-27  
**Status:** Approved for implementation  
**Scope:** Product releases, direct downloads, signed update metadata, and safe GUI updates

## 1. Goal

Turn the source repository into a versioned desktop product. A release tag must
produce tested Windows x64, Ubuntu 22.04 x64, and Ubuntu 22.04 ARM64 packages,
publish them on GitHub Releases, and let the GUI discover and verify newer
versions without affecting ST-Link operations.

GitHub Releases is the distribution source of truth from v0.3.0 onward. GitHub
Actions artifacts remain temporary build inputs and are not user-facing
downloads.

## 2. Release contract

Official releases are created only from a `vMAJOR.MINOR.PATCH` tag whose version
exactly matches `b300_version.__version__`. The first release using this contract
is `v0.3.0`.

Stable asset names deliberately omit the version so README and updater URLs do
not change between releases:

```text
B300-STLink-GUI-Windows-x64.exe
B300-STLink-GUI-Windows-x64.zip
B300-STLink-GUI-Ubuntu-x64.AppImage
b300-stlink-gui_amd64.deb
B300-STLink-GUI-Ubuntu-arm64.AppImage
b300-stlink-gui_arm64.deb
B300-STLink-CLI-Windows-x64.zip
B300-STLink-CLI-Linux-x64.tar.gz
B300-STLink-CLI-Linux-arm64.tar.gz
SHA256SUMS.txt
release-manifest.json
release-manifest.json.minisig
latest.json
latest.json.minisig
```

Every GUI and CLI package is self-contained for its platform and carries the
pinned OpenOCD xPack 0.12.0-7 runtime. A user downloads only the package they
intend to use; cloning the source repository is not part of product setup.

## 3. Version ownership

`b300_version.py` is the single source version. GUI, CLI, executable metadata,
installer, DEB, AppImage, release manifest, update manifest, release title, and
tag validation consume that value.

`scripts/release/bump_version.py VERSION` validates strict SemVer and changes
source metadata only. It never commits, pushes, tags, or publishes.

## 4. Release pipeline

Tag publication triggers these stages:

```text
prepare-release
  -> build-windows
  -> build-linux-x64
  -> build-linux-arm64
  -> finalize-release
```

`prepare-release` validates tag/source equality and extracts the matching
CHANGELOG section. Platform jobs build independent GUI and CLI packages and run
software-only smoke tests. `finalize-release` downloads all build artifacts,
requires the exact asset set, generates checksums and signed manifests, creates
a draft GitHub Release, uploads assets, validates the public download contract,
then publishes it as Latest.

Only `finalize-release` receives `contents: write`. CI and platform build jobs
keep read-only permissions. Signing credentials live only in a protected GitHub
Actions environment; private keys are never committed or included in packages.

## 5. Compatibility

- Windows: Windows 10/11 x64, per-user installer, no administrator requirement.
- Linux x64: Ubuntu 22.04 or newer on x86_64.
- Linux ARM64: Ubuntu 22.04 or newer on aarch64.
- Python source development: Python 3.9 or newer.

Linux executables must be built inside an Ubuntu 22.04 userspace. Running an
ARM64 job on an Ubuntu 24.04 host is acceptable only when the actual build and
smoke test occur inside an ARM64 Ubuntu 22.04 container. This prevents newer
glibc dependencies from entering ARM64 packages.

## 6. Release metadata and signing

`release-manifest.json` records product version, source commit, publication
time, OpenOCD version, and each platform asset's size and SHA-256 digest.

`latest.json` is the updater-facing subset. It contains schema version, product
version, release notes, release page URL, publication time, and supported
platform downloads. URLs point to immutable assets in the tagged release, not
mutable branch content.

Both JSON files use deterministic UTF-8 serialization and detached Ed25519
signatures compatible with Minisign. The GUI embeds only the public key. Update
verification order is:

1. download `latest.json` and its detached signature;
2. verify the signature before parsing trusted fields;
3. reject malformed schema, unsupported platform, invalid SemVer, downgrade,
   or same-version installation;
4. download the selected package to a temporary file;
5. compare size and SHA-256 with the signed manifest;
6. atomically mark the package ready for installation.

## 7. Updater architecture

Updater behavior is UI-independent:

```text
b300_core/versioning.py          strict SemVer parsing and comparison
b300_core/release_manifest.py    signed manifest parsing and validation
b300_core/updater.py             check, download, verify, prepare install
b300_gui/update_worker.py        Qt background bridge
b300_gui/update_dialog.py        user-facing update state and progress
b300_gui/about_dialog.py         version/build details and manual check
```

Network failures and timeouts are silent during automatic checks and visible
during manual checks. Automatic checks are enabled by default and rate-limited
to once every 24 hours. They never delay application startup.

## 8. Installation behavior

### Windows

After download and verification, the GUI launches the per-user installer and
then closes. The installer upgrades the same AppId and may relaunch the new GUI.
No administrative privileges are required.

### Linux AppImage

v0.3.0 downloads and verifies the replacement package. Managed atomic
replacement is delivered in v0.3.1 because it needs a separate helper process
and must handle mounted/running AppImages safely.

### Linux DEB

v0.3.0 downloads and verifies the DEB, opens its containing directory, and
shows the exact installation command. Privileged managed installation is
deferred to v0.4.0.

## 9. Hardware-operation safety

The updater never calls OpenOCD, discovers probes, resets the target, or reads
STM32 memory. It may check and download while the application is idle, but the
Install/Restart action is disabled whenever any flash, erase, program, verify,
marker, reset, target inspection, memory read, metadata read, or debug operation
is active.

If a package finishes downloading while hardware is busy, the GUI reports that
the update is ready and waits. It does not close or restart automatically.

CI and updater tests do not connect to ST-Link and do not flash hardware.

## 10. User experience

The README begins with a Download table for Windows x64, Ubuntu x64, and Ubuntu
ARM64, followed by concise install and operation links. Source build and clone
instructions move to the contributor section.

The GUI Help menu contains:

```text
Check for Updates
Release Notes
About
```

The About dialog displays product version, core version, OpenOCD version,
target, and build commit. After a successful upgrade, What's New is shown once
per newly observed version.

## 11. Acceptance gates

An official release is accepted only when:

- the tag, source version, package metadata, and manifest version match;
- all 14 named release assets exist;
- Windows x64, Linux x64, and Linux ARM64 tests and package smoke tests pass;
- all hashes in `SHA256SUMS.txt` and signed manifests verify;
- updater tests cover no update, new update, timeout, malformed data, bad
  signature, wrong hash, wrong architecture, downgrade, and busy-state blocking;
- stable `/releases/latest/download/<asset>` links resolve after publication;
- release notes match the versioned CHANGELOG section;
- no firmware HEX, OpenOCD binary, signing private key, or release archive is
  committed to Git source;
- STM32 hardware acceptance remains a separate manual gate.

## 12. Roadmap

- **v0.3.0:** professional GitHub Releases, direct downloads, signed manifests,
  update checker/notification, Windows managed update, verified Linux downloads,
  About and What's New.
- **v0.3.1:** managed AppImage atomic updater.
- **v0.4.0:** managed DEB updater and optional update channels.
- **v1.0.0:** provisioning hardware acceptance, updater/signing stability, and
  production deployment validation complete.
