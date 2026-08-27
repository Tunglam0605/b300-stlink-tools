#!/bin/sh
set -eu
bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_root="${HOME}/.local/share/b300-stlink"
bin_root="${HOME}/.local/bin"
mkdir -p "$install_root" "$bin_root"
cp -a "$bundle_root"/. "$install_root"/
cat > "$bin_root/b300-stlink" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
export B300_OPENOCD="$tool_root/vendor/openocd/bin/openocd"
exec "$tool_root/b300-stlink" "$@"
EOF
chmod +x "$bin_root/b300-stlink"
if [ -x "$install_root/b300-stlink-gui" ]; then
cat > "$bin_root/b300-stlink-gui" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
export B300_OPENOCD="$tool_root/vendor/openocd/bin/openocd"
exec "$tool_root/b300-stlink-gui" "$@"
EOF
chmod +x "$bin_root/b300-stlink-gui"
mkdir -p "${HOME}/.local/share/applications" "${HOME}/.local/share/icons/hicolor/scalable/apps"
cp "$install_root/b300-stlink-gui.desktop" "${HOME}/.local/share/applications/"
cp "$install_root/b300-stlink-gui.svg" \
  "${HOME}/.local/share/icons/hicolor/scalable/apps/b300-stlink-gui.svg"
fi
printf '%s\n' "Installed. Ensure $bin_root is on PATH, then run: b300-stlink doctor or b300-stlink-gui"
