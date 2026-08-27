# B300 ST-Link Tools Release/Update Roadmap

**Spec:** `docs/superpowers/specs/2026-08-27-b300-stlink-release-update-design.md`

| Track | Version | Deliverable | Status |
|---|---:|---|---|
| Release backend | 0.3.0 | Tag-driven GitHub Release, stable direct downloads, checksums, signed manifests | Planned |
| Product downloads | 0.3.0 | Independent GUI/CLI packages for Windows x64, Ubuntu 22.04 x64/ARM64 | Planned |
| Update client | 0.3.0 | Background check, notification, download and signature/hash verification | Planned |
| Windows update | 0.3.0 | Verified per-user installer launch after GUI closes | Planned |
| Linux download | 0.3.0 | Verified AppImage/DEB download with manual install handoff | Planned |
| AppImage update | 0.3.1 | Helper-based atomic replacement and restart | Deferred |
| DEB update | 0.4.0 | Privileged managed install and restart | Deferred |
| Production | 1.0.0 | Hardware acceptance, signing and deployment stability gates | Deferred |

## Required execution order

1. Release backend contract and direct-download assets.
2. Signed update metadata and verifier.
3. GUI update checker and hardware-busy interlock.
4. Windows managed update and Linux manual handoff.
5. Cross-platform release acceptance and v0.3.0 publication.

The release backend must be accepted before GUI update behavior is integrated.
