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

reject_path_components() {
  candidate=$1
  base=$2
  if ! path_within "$candidate" "$base"; then
    printf '%s\n' "Managed install write target escapes the per-user root: $candidate" >&2
    exit 1
  fi
  current=$base
  reject_symlink "$current"
  relative=${candidate#"$base"}
  while [ -n "$relative" ]; do
    relative=${relative#/}
    [ -n "$relative" ] || break
    component=${relative%%/*}
    current="${current}/${component}"
    reject_symlink "$current"
    if [ "$relative" = "$component" ]; then
      break
    fi
    relative=${relative#*/}
  done
}

local_root="${home_root}/.local"
share_root="${local_root}/share"
install_root="${share_root}/b300-stlink"
bin_root="${local_root}/bin"
cli_launcher="${bin_root}/b300-stlink"
gui_launcher="${bin_root}/b300-stlink-gui"
applications_root="${share_root}/applications"
desktop_target="${applications_root}/b300-stlink-gui.desktop"
icons_root="${share_root}/icons/hicolor/scalable/apps"
icon_target="${icons_root}/b300-stlink-gui.svg"
if path_within "$bundle_root" "$install_root" || path_within "$install_root" "$bundle_root"; then
  printf '%s\n' 'Run b300-stlink self-update from a managed install; source and destination overlap.' >&2
  exit 1
fi
for managed_path in \
  "$local_root" "$share_root" "$install_root" \
  "$bin_root" "$cli_launcher"
do
  reject_path_components "$managed_path" "$home_root"
done
if [ ! -x "$bundle_root/b300-stlink" ] && [ ! -x "$bundle_root/b300-stlink-gui" ]; then
  printf '%s\n' 'Incomplete B300 native bundle: executable is missing.' >&2
  exit 1
fi
if [ -x "$bundle_root/b300-stlink-gui" ]; then
  for managed_path in \
    "$gui_launcher" "$applications_root" "$desktop_target" \
    "$icons_root" "$icon_target"
  do
    reject_path_components "$managed_path" "$home_root"
  done
fi
mkdir -p "$install_root" "$bin_root"
cp -a "$bundle_root"/. "$install_root"/
cat > "$cli_launcher" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
exec "$tool_root/b300-stlink" "$@"
EOF
chmod +x "$cli_launcher"
if [ -x "$install_root/b300-stlink-gui" ]; then
cat > "$gui_launcher" <<'EOF'
#!/bin/sh
set -eu
runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)
exec "$tool_root/b300-stlink-gui" "$@"
EOF
chmod +x "$gui_launcher"
mkdir -p "$applications_root" "$icons_root"
cp "$install_root/b300-stlink-gui.desktop" "$desktop_target"
cp "$install_root/b300-stlink-gui.svg" "$icon_target"
fi
printf '%s\n' "Installed. Ensure $bin_root is on PATH, then run: b300-stlink doctor, b300-stlink setup, or b300-stlink-gui"
