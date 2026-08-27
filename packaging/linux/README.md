# Ubuntu GUI packaging

Build and extract the native Linux bundle, then stage/build artifacts:

```bash
python3 packaging/build_gui.py \
  --bundle-dir ./bundle \
  --output-dir ./release/gui \
  --architecture x86_64 \
  --version 0.1.0 \
  --appimagetool /opt/appimagetool-x86_64.AppImage \
  --build-deb
```

`appimagetool` must be supplied by the release environment and verified against
the SHA-256 pinned in `.github/workflows/release.yml`. The script does not
download or silently update packaging tools.
