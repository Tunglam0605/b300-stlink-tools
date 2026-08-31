# Windows GUI packaging

Build the native bundle on Windows, extract it, then compile
`b300-stlink-gui.iss` with Inno Setup 6:

```powershell
py build_native_bundle.py --internal-distribution-approved
iscc /DSourceRoot="C:\path\to\extracted-bundle" `
  /DOutputDir="C:\path\to\release" /DAppVersion="0.2.0" `
  packaging\windows\b300-stlink-gui.iss
```

The installer runs per-user and bundles the pinned official STSW-LINK009 USB
driver plus its SLA0048 notice. On first launch it runs the idempotent fresh-machine
bootstrap: bundled runtime/OpenOCD are verified, a missing ST-Link driver is installed
through UAC, and the GUI re-checks readiness automatically. It never modifies STM32
WRP/Option Bytes/flash during workstation setup. Role-specific OpenSSH Server/firewall
changes are still deferred until the operator explicitly chooses Gateway mode.
