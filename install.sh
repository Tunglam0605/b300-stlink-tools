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
printf '%s\n' "Installed. Ensure $bin_root is on PATH, then run: b300-stlink doctor"
