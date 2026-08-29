#!/bin/sh
set -eu

REPO='Tunglam0605/b300-stlink-tools'
MANIFEST_URL="https://github.com/$REPO/releases/latest/download/latest-cli.json"
SIGNATURE_URL="https://github.com/$REPO/releases/latest/download/latest-cli.json.minisig"
PUBLIC_KEY='RWSjwseDEGd6o+Ykylwi3nmXPA7DYtOhvuXvHBQxf58Dej383Hd+5eYN'
MINISIGN_VERSION='0.12'
MINISIGN_URL="https://github.com/jedisct1/minisign/releases/download/$MINISIGN_VERSION/minisign-0.12-linux.tar.gz"
MINISIGN_SHA256='9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73'

fail() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Required command is missing: $1"; }
need curl
need tar
need sha256sum
need grep
need sed
need wc
need awk
need tr

[ "$(uname -s)" = 'Linux' ] || fail 'B300 CLI bootstrap shell installer supports Linux only.'
case "$(uname -m)" in
  x86_64|amd64) platform='linux-x64-cli'; mini_arch='x86_64'; expected_file='B300-STLink-CLI-Linux-x64.tar.gz' ;;
  aarch64|arm64) platform='linux-arm64-cli'; mini_arch='aarch64'; expected_file='B300-STLink-CLI-Linux-arm64.tar.gz' ;;
  *) fail "Unsupported Linux architecture: $(uname -m)" ;;
esac

case "${HOME-}" in /*) ;; *) fail 'HOME must be an absolute per-user path.' ;; esac

tmp=$(mktemp -d "${TMPDIR:-/tmp}/b300-cli-bootstrap.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
manifest="$tmp/latest-cli.json"
signature="$tmp/latest-cli.json.minisig"
curl -fsSL --retry 3 -o "$manifest" "$MANIFEST_URL"
curl -fsSL --retry 3 -o "$signature" "$SIGNATURE_URL"

if command -v minisign >/dev/null 2>&1; then
  minisign_bin=$(command -v minisign)
else
  mini_archive="$tmp/minisign-0.12-linux.tar.gz"
  curl -fsSL --retry 3 -o "$mini_archive" "$MINISIGN_URL"
  actual=$(sha256sum "$mini_archive" | awk '{print $1}')
  [ "$actual" = "$MINISIGN_SHA256" ] || fail "Pinned minisign bootstrap SHA-256 mismatch: $actual"
  tar -xzf "$mini_archive" -C "$tmp"
  minisign_bin="$tmp/minisign-linux/$mini_arch/minisign"
  [ -x "$minisign_bin" ] || fail 'Pinned minisign executable is missing after extraction.'
fi

"$minisign_bin" -Vm "$manifest" -x "$signature" -P "$PUBLIC_KEY" >/dev/null || fail 'B300 latest-cli.json signature verification failed.'

record=$(sed -n "s/.*\"$platform\":{\([^}]*\)}.*/\1/p" "$manifest")
[ -n "$record" ] || fail "Signed manifest does not contain platform $platform."
file=$(printf '%s' "$record" | sed -n 's/.*"file":"\([^"]*\)".*/\1/p')
sha=$(printf '%s' "$record" | sed -n 's/.*"sha256":"\([0-9a-f]*\)".*/\1/p')
size=$(printf '%s' "$record" | sed -n 's/.*"size":\([0-9][0-9]*\).*/\1/p')
url=$(printf '%s' "$record" | sed -n 's/.*"url":"\([^"]*\)".*/\1/p')
[ "$file" = "$expected_file" ] || fail "Signed manifest selected unexpected file: $file"
printf '%s' "$sha" | grep -Eq '^[0-9a-f]{64}$' || fail 'Signed manifest contains an invalid SHA-256.'
printf '%s' "$size" | grep -Eq '^[0-9]+$' || fail 'Signed manifest contains an invalid size.'
case "$url" in
  "https://github.com/$REPO/releases/download/v"*"/$expected_file") ;;
  *) fail "Signed manifest contains an unexpected immutable asset URL: $url" ;;
esac

package="$tmp/$expected_file"
curl -fsSL --retry 3 -o "$package" "$url"
actual=$(sha256sum "$package" | awk '{print $1}')
[ "$actual" = "$sha" ] || fail 'Downloaded B300 CLI package SHA-256 mismatch.'
actual_size=$(wc -c < "$package" | tr -d ' ')
[ "$actual_size" = "$size" ] || fail 'Downloaded B300 CLI package size mismatch.'
printf '%s\n' 'Verified signed B300 CLI package.'

if [ "${B300_BOOTSTRAP_DOWNLOAD_ONLY-0}" = '1' ]; then
  cp "$package" "./$expected_file"
  printf '%s\n' "Verified package copied to: $(pwd)/$expected_file"
  exit 0
fi

if tar -tzf "$package" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  fail 'Verified package contains an unsafe archive path.'
fi
stage="$tmp/bundle"
mkdir -p "$stage"
tar -xzf "$package" -C "$stage"
[ -x "$stage/b300-stlink" ] || fail 'Verified package is missing b300-stlink.'
[ -d "$stage/_internal" ] || fail 'Verified package is missing _internal runtime.'
[ -f "$stage/install.sh" ] || fail 'Verified package is missing install.sh.'
chmod +x "$stage/install.sh"
"$stage/install.sh"
launcher="$HOME/.local/bin/b300-stlink"
[ -x "$launcher" ] || fail 'B300 CLI launcher was not created by the managed installer.'
"$launcher" --version
printf '%s\n' '' 'Gateway preflight:'
if ! "$launcher" gateway doctor; then
  printf '%s\n' 'B300 CLI is installed; Gateway preflight still reports a host setup requirement (commonly SSH Server, udev permission, or ST-Link).'
fi
printf '%s\n' '' 'Installed. Ensure ~/.local/bin is on PATH, then run: b300-stlink gateway doctor' 'When READY, start the headless Gateway with: b300-stlink debug'
