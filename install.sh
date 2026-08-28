#!/bin/sh
set -eu
bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

case "${HOME-}" in
  /*) ;;
  *)
    printf '%s\n' 'Managed install requires an absolute per-user HOME.' >&2
    exit 1
    ;;
esac
home_root=$(CDPATH= cd -- "$HOME" && pwd -P)
case "$home_root" in
  /|/bin|/bin/*|/boot|/boot/*|/dev|/dev/*|/etc|/etc/*|/lib|/lib/*|/opt|/opt/*|/proc|/proc/*|/root|/root/*|/run|/run/*|/sbin|/sbin/*|/sys|/sys/*|/usr|/usr/*|/var|/var/*)
    printf '%s\n' 'Managed install refuses a system destination as HOME.' >&2
    exit 1
    ;;
esac

path_within() {
  case "$1/" in
    "$2/"*) return 0 ;;
    *) return 1 ;;
  esac
}

reject_symlink() {
  if [ -L "$1" ]; then
    printf '%s\n' "Managed install path contains an unsafe symlink: $1" >&2
    exit 1
  fi
}

install_root="${home_root}/.local/share/b300-stlink"
bin_root="${home_root}/.local/bin"
if path_within "$bundle_root" "$install_root" || path_within "$install_root" "$bundle_root"; then
  printf '%s\n' 'Run b300-stlink self-update from a managed install; source and destination overlap.' >&2
  exit 1
fi
for managed_path in \
  "$home_root/.local" "$home_root/.local/share" "$install_root" \
  "$bin_root" "$bin_root/b300-stlink" "$bin_root/b300-stlink-gui"
do
  reject_symlink "$managed_path"
done
if [ ! -x "$bundle_root/b300-stlink" ] && [ ! -x "$bundle_root/b300-stlink-gui" ]; then
  printf '%s\n' 'Incomplete B300 native bundle: executable is missing.' >&2
  exit 1
fi
mkdir -p "$install_root" "$bin_root"
cp -a "$bundle_root"/. "$install_root"/
cat > "$bin_root/b300-stlink" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
exec "$tool_root/b300-stlink" "$@"
EOF
chmod +x "$bin_root/b300-stlink"
if [ -x "$install_root/b300-stlink-gui" ]; then
cat > "$bin_root/b300-stlink-gui" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
exec "$tool_root/b300-stlink-gui" "$@"
EOF
chmod +x "$bin_root/b300-stlink-gui"
mkdir -p "${HOME}/.local/share/applications" "${HOME}/.local/share/icons/hicolor/scalable/apps"
cp "$install_root/b300-stlink-gui.desktop" "${HOME}/.local/share/applications/"
cp "$install_root/b300-stlink-gui.svg" \
  "${HOME}/.local/share/icons/hicolor/scalable/apps/b300-stlink-gui.svg"
fi
printf '%s\n' "Installed. Ensure $bin_root is on PATH, then run: b300-stlink doctor, b300-stlink setup, or b300-stlink-gui"
