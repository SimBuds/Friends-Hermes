#!/usr/bin/env bash
# Friends-Hermes tunnel: lend the ZBOX this PC's llama.cpp router (127.0.0.1:8080).
# Normally run by the PC user service systemd/fh-tunnel.service (README, Tunnel to the router).
# It also runs by hand in a terminal, until Ctrl+C. While it is down the bot answers offline.
#
#   bash ~/Apps/Friends-Hermes/scripts/fh-tunnel.sh
set -euo pipefail

# The ZBOX's address lives outside the repo (host addresses are never committed), in one file.
# No DHCP reservation (Casey, 2026-11-14): if the ZBOX's IP changes, edit that file.
ADDR_FILE="$HOME/.config/friends-hermes/zbox"
KEY="$HOME/.ssh/fh_tunnel"
ROUTER=http://127.0.0.1:8080

[ "$(uname -n)" != hermes ] || { echo "STOP: this runs on the PC, not the ZBOX." >&2; exit 1; }
[ -f "$KEY" ] || { echo "STOP: $KEY is missing. See README, Tunnel to the router, step 1." >&2; exit 1; }
[ -r "$ADDR_FILE" ] || { echo "STOP: $ADDR_FILE is missing. It holds the ZBOX's address. See README, Tunnel to the router, step 3." >&2; exit 1; }
ZBOX="$(tr -d '[:space:]' < "$ADDR_FILE")"
[[ "$ZBOX" =~ ^[A-Za-z0-9.:-]+$ ]] || { echo "STOP: $ADDR_FILE must hold one address or host name, got '$ZBOX'." >&2; exit 1; }
if curl -fsS -m 3 "$ROUTER/health" >/dev/null; then
    echo "ok: router is up at $ROUTER"
else
    echo "note: the router at $ROUTER is not answering. The tunnel still opens, but the bot gets no model until it is up."
fi

echo "tunnel: open to tunnel@$ZBOX. Ctrl+C to close it."
# -N: forwarding only, no shell. ExitOnForwardFailure: stop at once if the ZBOX cannot take 8080
# (another tunnel already holds it). ServerAlive: notice a dead link within about 45 s.
exec ssh -N -T -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -R 127.0.0.1:8080:127.0.0.1:8080 "tunnel@$ZBOX"
