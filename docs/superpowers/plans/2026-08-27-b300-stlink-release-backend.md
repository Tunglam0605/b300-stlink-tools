# B300 ST-Link Release Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish reproducible v0.3.0 Windows/Ubuntu GUI and CLI downloads, checksums, and signed update metadata on GitHub Releases from one validated version tag.

**Architecture:** Keep `b300_version.py` as the source version and add small release-only scripts for SemVer validation, CHANGELOG extraction, asset validation, and deterministic manifest generation. Platform jobs build isolated GUI/CLI packages; a least-privilege final job signs metadata and publishes a draft release only after every build passes.

**Tech Stack:** Python 3.9 standard library, unittest, PyInstaller, Inno Setup, AppImage, dpkg-deb, GitHub Actions, Minisign, OpenOCD xPack 0.12.0-7.

**Spec:** `docs/superpowers/specs/2026-08-27-b300-stlink-release-update-design.md`

## Global Constraints

- Official version is `0.3.0`; official tag is `v0.3.0`.
- `b300_version.py` is the only manually edited source version.
- Stable release asset names are exactly the 14 names in the spec.
- GUI and CLI packages are independent and self-contained.
- Linux binaries target Ubuntu 22.04 glibc compatibility on x64 and ARM64.
- No firmware HEX, OpenOCD binary, private signing key, or release archive is committed.
- Build/test workflows do not discover, connect to, reset, or flash ST-Link hardware.
- Only the final publish job has `contents: write`.

---

### Task 1: Strict release version tooling

**Files:**
- Create: `scripts/release/__init__.py`
- Create: `scripts/release/version_tools.py`
- Create: `scripts/release/bump_version.py`
- Create: `scripts/release/validate_version.py`
- Modify: `b300_version.py`
- Test: `tests/test_release_version_tools.py`

**Interfaces:**
- Produces: `parse_semver(value: str) -> tuple[int, int, int]`.
- Produces: `read_source_version(path: Path) -> str` and `replace_source_version(path: Path, version: str) -> None`.
- CLIs return zero only for valid input and matching tag/source values.

- [ ] **Step 1: Write failing tests for strict SemVer, source replacement, and no Git side effects**

```python
def test_parse_semver_rejects_prefix_and_leading_zero(self):
    for value in ("v0.3.0", "00.3.0", "0.03.0", "0.3"):
        with self.assertRaises(ValueError):
            parse_semver(value)

def test_replace_source_version_changes_only_assignment(self):
    replace_source_version(path, "0.3.0")
    self.assertEqual(read_source_version(path), "0.3.0")
    self.assertNotIn("git ", path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run `python -m unittest tests.test_release_version_tools -v`; confirm missing module failure**
- [ ] **Step 3: Implement strict `MAJOR.MINOR.PATCH` parsing and atomic UTF-8 source replacement**

```python
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
```

- [ ] **Step 4: Add `--check-tag v0.3.0` to `validate_version.py` and `VERSION` to `bump_version.py`; neither imports Git nor spawns a process**
- [ ] **Step 5: Run the focused test and full suite; commit `feat: add strict release version tooling`**

### Task 2: CHANGELOG release-note extraction

**Files:**
- Create: `scripts/release/changelog.py`
- Test: `tests/test_release_changelog.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `extract_release_notes(text: str, version: str) -> str`.
- Rejects missing, duplicate, empty, or still-unreleased version sections.

- [ ] **Step 1: Write failing tests using complete miniature CHANGELOG documents**

```python
def test_extracts_only_requested_version(self):
    notes = extract_release_notes(TEXT, "0.3.0")
    self.assertIn("### Added", notes)
    self.assertNotIn("0.2.0", notes)
```

- [ ] **Step 2: Run the test and confirm the missing function is the failure**
- [ ] **Step 3: Implement heading-boundary parsing without a Markdown dependency**
- [ ] **Step 4: Add a dated `0.3.0` CHANGELOG section describing release/update productization**
- [ ] **Step 5: Run focused/full tests; commit `docs: prepare v0.3.0 release notes`**

### Task 3: Independent GUI and CLI native bundles

**Files:**
- Modify: `package_internal.py`
- Modify: `build_native_bundle.py`
- Modify: `install.ps1`
- Modify: `install.sh`
- Test: `tests/test_build_native_bundle.py`
- Test: `tests/test_gui_packaging.py`

**Interfaces:**
- `package_internal.py --flavor gui|cli --platform PLATFORM --output PATH`.
- GUI bundle contains GUI executable, branding, license, installer helper, and OpenOCD runtime; it does not contain the CLI executable.
- CLI bundle contains CLI executable, license, installer helper, and OpenOCD runtime; it does not contain the GUI executable or Qt assets.

- [ ] **Step 1: Add failing archive-content tests for GUI/CLI separation and exact stable filenames**

```python
self.assertIn("b300-stlink-gui.exe", gui_names)
self.assertNotIn("b300-stlink.exe", gui_names)
self.assertIn("b300-stlink.exe", cli_names)
self.assertNotIn("b300-stlink-gui.exe", cli_names)
```

- [ ] **Step 2: Run focused tests and confirm current combined bundle violates them**
- [ ] **Step 3: Add a required flavor argument and shared safe archive helpers**
- [ ] **Step 4: Make `build_native_bundle.py` build both flavors and emit the three stable CLI archive names plus platform GUI staging archives**
- [ ] **Step 5: Update installers so each flavor creates only its own launchers**
- [ ] **Step 6: Run focused/full tests; commit `feat: split GUI and CLI release bundles`**

### Task 4: Stable GUI package names and version metadata

**Files:**
- Modify: `packaging/windows/b300-stlink-gui.iss`
- Modify: `packaging/build_gui.py`
- Modify: `b300_gui.spec`
- Modify: `b300-stlink.spec`
- Test: `tests/test_gui_packaging.py`
- Test: `tests/test_build_native_bundle.py`

**Interfaces:**
- Windows installer output: `B300-STLink-GUI-Windows-x64.exe`.
- Linux outputs: exact AppImage/DEB names from the spec.
- `B300_BUILD_COMMIT` is injected at build time and exposed to the About dialog later.

- [ ] **Step 1: Write failing naming/version-propagation tests**
- [ ] **Step 2: Run them and observe current versioned package names fail**
- [ ] **Step 3: Change output naming while retaining `AppVersion={#AppVersion}` and package-control version metadata**
- [ ] **Step 4: Replace the absolute path generated in `b300-stlink.spec` with `Path(SPECPATH)` so clean machines can build it**
- [ ] **Step 5: Run focused/full tests; commit `build: standardize release asset names`**

### Task 5: Deterministic release manifests and checksums

**Files:**
- Create: `scripts/release/build_metadata.py`
- Create: `scripts/release/release_contract.py`
- Test: `tests/test_release_metadata.py`

**Interfaces:**
- `EXPECTED_ASSETS: tuple[str, ...]` is the canonical non-metadata asset list.
- `build_release_metadata(asset_dir: Path, version: str, commit: str, published_at: str, base_url: str, notes: str) -> None` writes `SHA256SUMS.txt`, `release-manifest.json`, and `latest.json`.
- JSON uses `sort_keys=True`, `ensure_ascii=False`, `separators=(",", ":")`, and one trailing LF.

- [ ] **Step 1: Write failing tests for exact assets, deterministic bytes, hashes, URLs, missing/extra files, and path safety**

```python
first = build_fixture_release(root)
second = build_fixture_release(root)
self.assertEqual(first.read_bytes(), second.read_bytes())
self.assertEqual(latest["schema_version"], 1)
```

- [ ] **Step 2: Run the test and confirm missing module failure**
- [ ] **Step 3: Implement exact-set validation and streaming SHA-256 computation**
- [ ] **Step 4: Generate immutable tag asset URLs and escape untrusted notes only through JSON serialization**
- [ ] **Step 5: Run focused/full tests; commit `feat: generate deterministic release metadata`**

### Task 6: Tag-driven release workflow

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `.github/workflows/release-dry-run.yml`
- Test: `tests/test_release_workflow.py`

**Interfaces:**
- `release.yml` publishes only on `push.tags: ["v*"]`.
- `release-dry-run.yml` is manual, read-only, and never creates a tag or Release.
- Platform jobs upload temporary artifacts; `finalize-release` alone publishes.

- [ ] **Step 1: Write failing YAML text/structure tests for triggers, permissions, job dependencies, stable assets, and Ubuntu 22.04 containers**
- [ ] **Step 2: Run the test and confirm current workflow fails publish-contract assertions**
- [ ] **Step 3: Split manual dry-run from official tagged release workflow**
- [ ] **Step 4: Build ARM64 inside `ubuntu:22.04` on the ARM runner and run the produced binary smoke test inside that same userspace**
- [ ] **Step 5: Add a final job that downloads artifacts, validates exact names, builds metadata, signs both JSON files, creates a draft Release, uploads assets, and publishes after validation**
- [ ] **Step 6: Pin third-party actions to immutable commit SHAs and keep job-level permissions least-privileged**
- [ ] **Step 7: Run workflow tests, parse YAML where available, run full tests; commit `ci: publish signed GitHub Releases from tags`**

### Task 7: Download-first README and release operations guide

**Files:**
- Modify: `README.md`
- Create: `docs/09_RELEASE_PROCESS.md`
- Modify: `docs/00_START_HERE.md`
- Modify: `docs/01_SETUP_WINDOWS.md`
- Modify: `docs/02_SETUP_UBUNTU_IPC.md`
- Modify: `CONTRIBUTING.md`
- Test: `tests/test_release_documentation.py`

**Interfaces:**
- README direct links use `/releases/latest/download/<stable-name>`.
- End-user setup begins with one platform download, not `git clone`.
- Contributor instructions retain clone/source build workflow separately.

- [ ] **Step 1: Write failing documentation-contract tests for all download links and absence of clone in the end-user quick start**
- [ ] **Step 2: Run and confirm current README fails**
- [ ] **Step 3: Rewrite the README top section with GUI/CLI download tables and three-step operation links**
- [ ] **Step 4: Document `bump -> changelog -> CI -> tag -> automated publish`, rollback, draft cleanup, and never-push behavior of local scripts**
- [ ] **Step 5: Run link/UTF-8/full tests; commit `docs: add product download and release guides`**

### Task 8: Backend release acceptance

**Files:**
- Modify: `docs/08_RELEASE_ACCEPTANCE.md`
- Modify: `docs/superpowers/roadmaps/2026-08-27-b300-stlink-release-update.md`

**Interfaces:**
- Produces a recorded software-only acceptance result for all platform artifacts.

- [ ] **Step 1: Run `python -m unittest discover -s tests -q` with `QT_QPA_PLATFORM=offscreen`; require zero failures**
- [ ] **Step 2: Run `python -m compileall -q b300_core b300_gui scripts/release` and compile all entry/build scripts**
- [ ] **Step 3: Run the release dry-run workflow and download each platform artifact**
- [ ] **Step 4: Verify package names, manifest hashes, signatures, CLI `--help`, GUI `--smoke-test`, and OpenOCD `--version` without discovering a probe**
- [ ] **Step 5: Record evidence and mark the backend roadmap track complete**
- [ ] **Step 6: Commit `test: record v0.3.0 release backend acceptance`**
