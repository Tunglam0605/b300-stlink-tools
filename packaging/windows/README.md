# Windows GUI packaging

Build the native bundle on Windows, extract it, then compile
`b300-stlink-gui.iss` with Inno Setup 6:

```powershell
py build_native_bundle.py --internal-distribution-approved
iscc /DSourceRoot="C:\path\to\extracted-bundle" `
  /DOutputDir="C:\path\to\release" /DAppVersion="0.1.0" `
  packaging\windows\b300-stlink-gui.iss
```

The installer runs per-user and never installs drivers or modifies WRP/Option
Bytes. ST-Link USB driver availability remains an operator prerequisite.
