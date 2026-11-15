#!/usr/bin/env bash
# Install Hermes v0.23.0 and the friends_odds plugin on the ZBOX, as the hermes user.
#
#   git clone https://github.com/SimBuds/Friends-Hermes.git ~/Friends-Hermes   # or: git -C ~/Friends-Hermes pull --ff-only
#   bash ~/Friends-Hermes/scripts/install-zbox.sh [--dry-run]
#
# Safe to run twice: every step checks first and prints "skip:" when there is
# nothing to do. Root-only prerequisites are not done here (hermes has no sudo):
# the script stops and prints the one `su -c` line that installs them.
# Secrets are never touched: ~/.hermes/.env is copied from the old machine by hand.
set -euo pipefail

HERMES_TAG="v0.23.0"
HERMES_COMMIT="2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"   # what the v0.23.0 tag points at
INSTALLER_URL="https://raw.githubusercontent.com/NousResearch/hermes-agent/$HERMES_TAG/scripts/install.sh"
ROUTER_URL="http://127.0.0.1:8080/v1"                       # the PC's router, through the reverse tunnel
ROOT_PACKAGES="git curl python3 libatomic1"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/friends_odds"
HERMES="$HOME/.local/bin/hermes"

DRY_RUN=false
case "${1:-}" in
    "") ;;
    --dry-run) DRY_RUN=true ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

say()  { printf '%s\n' "$*"; }
skip() { printf 'skip: %s\n' "$*"; }
stop() { printf 'STOP: %s\n' "$*" >&2; exit 1; }
run()  {
    if $DRY_RUN; then printf 'would run: %s\n' "$*"; else printf 'run: %s\n' "$*"; "$@"; fi
}

# --- 0. Checks that need nothing installed --------------------------------
[ "$(id -u)" -ne 0 ] || stop "run this as the hermes user, not root."
[ -f "$REPO/hermes-plugin/friends_odds/__init__.py" ] && [ -f "$REPO/tools/odds_collect.py" ] \
    || stop "$REPO is not a Friends-Hermes checkout (plugin or collector missing)."

missing=""
for cmd in git curl python3 sha256sum; do
    command -v "$cmd" >/dev/null || missing="$missing $cmd"
done
# Captured first: with pipefail, grep -q closing the pipe early would fail the whole check.
libs="$(ldconfig -p 2>/dev/null || /sbin/ldconfig -p)"
grep -q 'libatomic\.so\.1' <<<"$libs" || missing="$missing libatomic1"
if [ -n "$missing" ]; then
    stop "missing:$missing. Install them as root, then run this again:
    su - -c 'apt install -y $ROOT_PACKAGES'"
fi
say "ok: prerequisites present ($ROOT_PACKAGES)"

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
    say "ok: linger is on, so the gateway starts at boot without a login"
else
    say "note: linger is off. The gateway will only run while hermes is logged in. As root:
    su - -c 'loginctl enable-linger $USER'"
fi

# --- 1. Hermes, pinned ---------------------------------------------------
installed=""
[ -x "$HERMES" ] && installed="$("$HERMES" --version 2>&1 | head -1 || true)"
if [[ "$installed" == *"${HERMES_TAG#v}"* ]]; then
    skip "Hermes already installed ($installed)"
else
    [ -z "$installed" ] || stop "Hermes is installed but not $HERMES_TAG ($installed). Not changing it."
    want="$(cut -d' ' -f1 "$REPO/scripts/install-zbox.sha256")"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    run curl -fsSL "$INSTALLER_URL" -o "$tmp/install.sh"
    if ! $DRY_RUN; then
        got="$(sha256sum "$tmp/install.sh" | cut -d' ' -f1)"
        [ "$got" = "$want" ] || stop "the installer's sha256 changed.
    expected $want (scripts/install-zbox.sha256, the reviewed copy)
    got      $got
    Read the new installer before updating the pinned hash."
        say "ok: installer sha256 matches the reviewed copy"
    fi
    run bash "$tmp/install.sh" --commit "$HERMES_COMMIT" --non-interactive --skip-browser --skip-computer-use
fi

# --- 2. Base config (README step 6, with the tunnel's address) -------------
# `hermes config set` overwrites the same key, so these are safe to repeat.
run "$HERMES" config set model.provider custom
run "$HERMES" config set model.base_url "$ROUTER_URL"
run "$HERMES" config set model.default freedom
run "$HERMES" config set model.api_key local
run "$HERMES" config set model.context_length 131072
run "$HERMES" config set skills.write_approval true
run "$HERMES" config set auxiliary.background_review.enabled false

# --- 3. Plugin and collector (README Discord step 4) -----------------------
copy() {  # copy SRC to PLUGIN_DIR unless an identical file is already there
    local src="$1" dst
    dst="$PLUGIN_DIR/$(basename "$1")"
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then skip "$(basename "$src") unchanged"; else run cp "$src" "$dst"; fi
}
[ -d "$PLUGIN_DIR" ] && skip "plugin folder exists" || run mkdir -p "$PLUGIN_DIR"
copy "$REPO/hermes-plugin/friends_odds/plugin.yaml"
copy "$REPO/hermes-plugin/friends_odds/__init__.py"
copy "$REPO/tools/odds_collect.py"

run "$HERMES" plugins enable friends_odds
run "$HERMES" config set platform_toolsets.discord "[web, friends_odds]"
run "$HERMES" config set tools.tool_search.enabled off

# --- 4. Checks (read-only) ------------------------------------------------
if $DRY_RUN; then
    say "dry run: nothing was changed, checks skipped"
    exit 0
fi
say "--- checks ---"
"$HERMES" --version
cfg="$HERMES_HOME/config.yaml"
# Each key's section and value. Must show model: for base_url, default and context_length, and skills: for write_approval.
awk '/^[a-z_]+:/{sec=$0} /^[[:space:]]+(base_url|default|context_length|write_approval):/ && !/^[[:space:]]*#/{print NR": "sec" -> "$0}' "$cfg"
grep -n -A1 '^  background_review:' "$cfg"     # the line after must show enabled: false
grep -n -A1 '^  discord:' "$cfg"               # must show [web, friends_odds]
grep -n -A1 '^  tool_search:' "$cfg"           # the line after must show enabled: off
"$HERMES" tools list --platform discord        # enabled: web and friends_odds only
ls "$PLUGIN_DIR"                               # must list __init__.py odds_collect.py plugin.yaml
if curl -fsS -m 2 "${ROUTER_URL%/v1}/health"; then
    echo; say "ok: router reachable at ${ROUTER_URL%/v1}"
else
    say "note: router not reachable at ${ROUTER_URL%/v1}. Expected until the tunnel (Phase Z3) is up."
fi
for dev in /sys/class/net/wl*; do
    [ -e "$dev" ] || continue
    if command -v iw >/dev/null; then
        say "Wi-Fi $(basename "$dev") power save: $(iw dev "$(basename "$dev")" get power_save 2>&1 || true)"
    else
        say "note: iw is not installed, so Wi-Fi power save is unknown. As root: su - -c 'apt install -y iw'"
    fi
done
free -m
