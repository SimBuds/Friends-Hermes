# Friends-Hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent) in a QEMU VM,
answering a few friends in a private Discord server as a sports-betting
research assistant: odds and lines across books, scores, and schedules
(stats are not in v1). It does not bet, and every number comes from a documented API with
its source and fetch time.

It uses the Local-LLM router on the host (`freedom`, an uncensored Qwen 3.6
35B, context 131,072) and runs
only when started by hand.

[`PLAN.md`](PLAN.md) holds the why. [`AGENTS.md`](AGENTS.md) holds the rules
for coding agents.

## Setup guide

These steps write outside this repo (`~/VMs/friends-hermes`, a disk image,
installs in the guest). Each block says where it runs. The VM lives in its
own folder because Auto-Agent's VM already uses the name
`debian-13.7.0-netinst` in `~/VMs`.

### 1. Check the host

```bash
# Runs in: local terminal, host. Read-only.
quickemu --version                        # 4.9.9 was used to write this guide
curl -s http://127.0.0.1:8080/health      # must print {"status":"ok"}
free -g                                   # "available" must leave room for a 4 GB VM next to the model
ss -ltn | grep ':22221 '                  # must print nothing: the port is free
```

### 2. Create the VM

```bash
# Runs in: local terminal, host. Creates ~/VMs/friends-hermes. Safe to re-run: it replaces the four sizing lines.
mkdir -p ~/VMs/friends-hermes && cd ~/VMs/friends-hermes
quickget debian 13.7.0 netinst
CONF=debian-13.7.0-netinst.conf          # EDIT if quickget wrote a different name
sed -i '/^ram=/d;/^cpu_cores=/d;/^disk_size=/d;/^ssh_port=/d' "$CONF"
printf 'ram="4G"\ncpu_cores="2"\ndisk_size="20G"\nssh_port="22221"\n' >> "$CONF"
cat "$CONF"                               # must show the four lines above
quickemu --vm "$CONF" --public-dir none   # boots the installer
```

`ssh_port` is pinned because quickemu otherwise takes the first free port
from 22220, which is Auto-Agent's whenever its VM is off.

In the installer, choose no desktop, the SSH server and standard utilities.
Set the hostname to `friends-hermes` and the user to `hermes`. Leave the
root password blank, so your user gets `sudo`. Shut down with
`sudo poweroff` when it finishes.

### 3. Snapshot the clean install

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes, with the VM shut down.
quickemu --vm debian-13.7.0-netinst.conf --snapshot create clean
quickemu --vm debian-13.7.0-netinst.conf --snapshot info   # must list "clean"
```

### 4. Reach the router from the VM

Boot the VM (see [Connecting](#connecting)), log in, then:

```bash
# Runs in: inside the VM.
sudo apt update && sudo apt install -y curl git
curl -s http://10.0.2.2:8080/health                               # must print {"status":"ok"}
curl -s http://10.0.2.2:8080/v1/models | grep -o '"id":"[a-z]*"'  # must list "freedom"
```

`10.0.2.2` is QEMU's address for the host's loopback. If the first `curl`
fails, stop: nothing after this works without it.

### 5. Install Hermes, reading the installer first

```bash
# Runs in: inside the VM, as hermes.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o install.sh
sha256sum install.sh          # compare with the hash of the copy you reviewed
less install.sh               # read what it installs
bash install.sh --non-interactive
source ~/.bashrc
hermes --version              # this guide was written for v0.23.0
```

`--non-interactive` skips API-key setup and the gateway service.

### 6. Point Hermes at the router

```bash
# Runs in: inside the VM. Safe to re-run: each line overwrites the same key.
hermes config set model.provider custom
hermes config set model.base_url http://10.0.2.2:8080/v1
hermes config set model.default freedom
hermes config set model.api_key local
hermes config set model.context_length 131072
hermes config set skills.write_approval true
hermes config set auxiliary.background_review.enabled false
# Each key's section and value, comments skipped. Must show model: for base_url, default and context_length, and skills: for write_approval.
awk '/^[a-z_]+:/{sec=$0} /^[[:space:]]+(base_url|default|context_length|write_approval):/ && !/^[[:space:]]*#/{print NR": "sec" -> "$0}' ~/.hermes/config.yaml
grep -n -A1 '^  background_review:' ~/.hermes/config.yaml   # the line after must show enabled: false
grep -n -A2 '^local_runtime:' ~/.hermes/config.yaml          # nothing, or enabled: false
```

This block is the durable copy of Hermes's base config. Re-run it after
restoring an older snapshot. Each key is in [Hermes config](#hermes-config).

### 7. Check it, then snapshot

Run `hermes` and send:

```text
Reply with the single word: ready
```

It must answer `ready`, and the router's log on the host must show the
request. Then shut the VM down and snapshot it:

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes. Refuses while this VM is up.
PIDF=debian-13.7.0-netinst/debian-13.7.0-netinst.pid    # this VM's own pid file. Both VMs share one process name
if [ -r "$PIDF" ] && kill -0 "$(cat "$PIDF")"; then echo "STOP: the VM is still running (pid $(cat "$PIDF"))."; else
  quickemu --vm debian-13.7.0-netinst.conf --snapshot create hermes-configured
  quickemu --vm debian-13.7.0-netinst.conf --snapshot info   # must list clean and hermes-configured
fi
```

### Hermes config

| Key                                     | Value                       | Why                                                          |
| --------------------------------------- | --------------------------- | ------------------------------------------------------------ |
| `model.provider`                      | `custom`                  | Any OpenAI-compatible server                                 |
| `model.base_url`                      | `http://10.0.2.2:8080/v1` | The host's router, through QEMU's address for the host       |
| `model.default`                       | `freedom`                  | One pinned model: the uncensored Qwen 3.6 35B (Casey, 2026-11-09)|
| `model.api_key`                       | `local`                   | A placeholder: the router has no auth                        |
| `model.context_length`                | `131072`                  | The router's served context, stated not guessed              |
| `skills.write_approval`               | `true`                    | Skills Hermes writes wait for approval (`/skills pending`) |
| `auxiliary.background_review.enabled` | `false`                   | No skills or notes drafted on its own from friends' chats    |
| `tools.tool_search.enabled`           | `off`                     | Plugin tools sit beside the web tools, not hidden behind `tool_search` (Discord step 4) |

Leave `local_runtime` off: a second llama.cpp would fight the router for
VRAM.

### Removing it

```bash
# Runs in: local terminal, host. Deletes this VM and its disk. Not undoable. Auto-Agent's VM is not touched.
rm -ri ~/VMs/friends-hermes
```

## Connecting

Start the VM in its own terminal and leave it open. Shut Auto-Agent's VM
down first: both together with a 35B model loaded push the host into swap.
Auto-Agent uses `qwen` and this VM `freedom`, and the router holds one model
at a time, so alternating between them swaps models each time.

```bash
# Runs in: local terminal, host, in ~/VMs/friends-hermes.
quickemu --vm debian-13.7.0-netinst.conf --public-dir none                   # normal session
quickemu --vm debian-13.7.0-netinst.conf --snapshot apply hermes-configured  # reset (VM shut down)
```

```bash
# Runs in: local terminal, host. A shell in the VM.
ssh -p 22221 hermes@localhost
```

quickemu forwards SSH on all of the host's interfaces, so use a key and
turn off password login. The key is your host user's existing public key.
It gives the VM nothing, since only the public half is copied.

```bash
# Runs in: local terminal, host, with the VM running. Safe to run twice.
ssh-copy-id -p 22221 hermes@localhost
ssh -p 22221 -o BatchMode=yes hermes@localhost true; echo "key check exit=$?"   # must print 0
```

```bash
# Runs in: inside the VM. Keys only for SSH. Safe to run twice.
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/10-no-password.conf
sudo sshd -t && echo "sshd config OK"
sudo systemctl reload ssh
```

## ZBOX setup

The bot is moving from the VM to a ZBOX on the LAN (see PLAN.md, The ZBOX).
`scripts/install-zbox.sh` does steps 5 and 6 above and the plugin part of
Discord step 4 there, pinned to Hermes v0.23.0. It never touches `.env`.

The `hermes` user has no sudo, so root does the prerequisites once:

```bash
# Runs in: ZBOX, as hermes. Asks for the ROOT password. Safe to run twice.
su - -c 'apt install -y git curl python3 libatomic1 && loginctl enable-linger hermes'
loginctl show-user hermes -p Linger                      # must print: Linger=yes
```

Then, as `hermes`:

```bash
# Runs in: ZBOX, as hermes. Safe to run twice: each step prints "skip:" when done.
if [ -d ~/Friends-Hermes/.git ]; then git -C ~/Friends-Hermes pull --ff-only; \
  else git clone https://github.com/SimBuds/Friends-Hermes.git ~/Friends-Hermes; fi
bash ~/Friends-Hermes/scripts/install-zbox.sh --dry-run  # lists every action, changes nothing
bash ~/Friends-Hermes/scripts/install-zbox.sh
```

- Hermes comes from the installer in the v0.23.0 tag, checked against the
  sha256 in `scripts/install-zbox.sha256`, and `--commit` pins the checkout
  to that release. A changed installer stops the script with both hashes.
  Read the new installer before updating the pinned hash.
- The router is `http://127.0.0.1:8080/v1`, reached through the reverse
  tunnel from the PC. Until the tunnel exists, the script's router check
  says it is unreachable. That is expected.
- The checks at the end print Wi-Fi power saving. It must be off. With it on
  (seen 2026-11-15), the ZBOX dropped off the network for a moment ("No
  route to host" from the PC) and answered pings in 70 to 100 ms. The ZBOX's
  Intel 3160 uses the `iwlmvm` driver, whose `power_scheme=1` keeps the card
  awake whatever manages the network:

```bash
# Runs in: ZBOX, as hermes. Asks for the ROOT password. Safe to run twice. Reboots the ZBOX.
su - -c 'apt install -y iw && echo "options iwlmvm power_scheme=1" > /etc/modprobe.d/fh-wifi-awake.conf && cat /etc/modprobe.d/fh-wifi-awake.conf && reboot'
# after the reboot, as hermes:
/sbin/iw dev wlp2s0 get power_save                      # must print: Power save: off
```

### Start the bot on the ZBOX

Copy `~/.hermes/.env` (and `~/.hermes/plugin-data/friends_odds/picks.json`, to
keep picks) from the old machine, never through this repo. Add
`FH_ROUTER_URL=http://127.0.0.1:8080` to that `.env` (see Offline reply).
Then install the gateway service:

```bash
# Runs in: ZBOX, as hermes. Answer "n" to "Start the gateway now?", "y" to start at boot.
~/.local/bin/hermes gateway install
systemctl --user restart hermes-gateway.service
sleep 60
systemctl --user is-active hermes-gateway.service                    # must print: active
systemctl --user show -p NRestarts --value hermes-gateway.service    # must print 0
```

Two traps in v0.23.0, both seen 2026-11-15:

- Answering "y" to "Start the gateway now?" starts a gateway outside the
  service, in your login session. The service then loops, printing "The host
  gateway already serves profile 'default'", and the stray copy dies with the
  session. If it happened: `systemctl --user stop hermes-gateway.service`,
  `hermes gateway stop`, then start the service.
- On the gateway's first start, Hermes installs the Discord library while it
  starts, and `adapter_slash_auth.py` keeps `discord = None` for that process.
  Commands picked from Discord's menu then fail with "The application did
  not respond" (journal: `'NoneType' object has no attribute 'DMChannel'`),
  while typed messages work. The restart above clears it.

### Tunnel to the router

The bot on the ZBOX reaches the PC's router through a reverse SSH tunnel
that the PC opens: `scripts/fh-tunnel.sh`, run by the PC user service
`systemd/fh-tunnel.service` (Casey, 2026-11-15). The service starts when you
log in and restarts within 10 seconds when the link drops. The PC needs no
SSH server. While the tunnel is up, the PC's `127.0.0.1:8080` is also
`127.0.0.1:8080` on the ZBOX. While it is down (the service stopped, or the
PC asleep or off), `127.0.0.1:8080` on the ZBOX refuses connections and the
bot answers offline.

The ZBOX's address is kept on the PC in `~/.config/friends-hermes/zbox`,
never in this repo. With no IP reservation, the ZBOX's address can change.
If it does, edit that one file and restart the service.

The ZBOX side is a `tunnel` user that can do one thing: listen on
`127.0.0.1:8080` for the forward. No shell, no other ports, no agent or X11
forwarding.

1. On the PC, make a key just for the tunnel (no passphrase, since a service
   uses it) and copy its public half to the ZBOX:

```bash
# Runs in: local terminal, host. Safe to run twice: the key is made only if missing.
ZBOX=203.0.113.10                                        # EDIT: the ZBOX's address (ip -br a on the ZBOX)
[ -f ~/.ssh/fh_tunnel ] && echo "skip: ~/.ssh/fh_tunnel exists" || ssh-keygen -t ed25519 -N '' -C fh-tunnel -f ~/.ssh/fh_tunnel
scp ~/.ssh/fh_tunnel.pub hermes@"$ZBOX":fh_tunnel.pub
```

2. On the ZBOX, as root, create the `tunnel` user and install that key with
   its limits:

```bash
# Runs in: ZBOX, as hermes. Asks for the ROOT password. Safe to run twice.
su - -c '
set -e
id tunnel >/dev/null 2>&1 && echo "skip: user tunnel exists" || adduser --disabled-password --gecos "" --shell /usr/sbin/nologin tunnel
install -d -m 700 -o tunnel -g tunnel ~tunnel/.ssh
KEY=$(cat /home/hermes/fh_tunnel.pub)
case "$KEY" in ssh-ed25519\ *fh-tunnel) ;; *) echo "STOP: /home/hermes/fh_tunnel.pub is not the fh-tunnel key"; exit 1 ;; esac
printf "restrict,port-forwarding,permitlisten=\"127.0.0.1:8080\" %s\n" "$KEY" > ~tunnel/.ssh/authorized_keys
chown tunnel:tunnel ~tunnel/.ssh/authorized_keys && chmod 600 ~tunnel/.ssh/authorized_keys
cat ~tunnel/.ssh/authorized_keys
'
```

   The last line must print one line starting
   `restrict,port-forwarding,permitlisten="127.0.0.1:8080" ssh-ed25519`.

3. On the PC, save the ZBOX's address and start the service:

```bash
# Runs in: local terminal, host (the PC, not the ZBOX). Safe to run twice.
ZBOX=203.0.113.10                                        # EDIT: the ZBOX's address
mkdir -p ~/.config/friends-hermes && printf '%s\n' "$ZBOX" > ~/.config/friends-hermes/zbox
cat ~/.config/friends-hermes/zbox                        # must print the ZBOX's address
install -D -m 644 ~/Apps/Friends-Hermes/systemd/fh-tunnel.service ~/.config/systemd/user/fh-tunnel.service
systemctl --user daemon-reload
systemctl --user enable --now fh-tunnel
systemctl --user is-active fh-tunnel                     # must print: active
journalctl --user -u fh-tunnel -n 20 --no-pager          # expect "tunnel: open to tunnel@..." and no STOP line
```

   The service needs the ZBOX's host key in `~/.ssh/known_hosts`, because it
   cannot answer a prompt. It is there if you have run `ssh hermes@` to the
   ZBOX from the PC. To take the bot offline: `systemctl --user stop
   fh-tunnel`. To stop it starting at login: `systemctl --user disable
   fh-tunnel`. `bash ~/Apps/Friends-Hermes/scripts/fh-tunnel.sh` still runs
   the tunnel by hand when the service is stopped.

4. With the service running, check it from the ZBOX:

```bash
# Runs in: ZBOX, as hermes. Read-only.
curl -s -m 5 127.0.0.1:8080/health; echo                 # must print the router's ok status
curl -s -m 5 127.0.0.1:8080/v1/models | grep -o '"id":"[^"]*"'   # must list "freedom"
```

## Discord

Friends talk to Hermes in your private Discord server, or by DM. The
gateway answers only the user IDs you list, gives each friend their own
session, and offers chats the web tools only. It runs in a terminal you
installed as a user service, so it starts when the VM boots and stops when
the VM shuts down. The token stays in the VM's `~/.hermes/.env`.

Checked against the v0.23.0 source (2026-11-07):

- With a user ID list set, unknown DMs are ignored and get no pairing code.
- `DISCORD_ALLOWED_CHANNELS` with no user list opens the channel to
  everyone in it, so it is never set without the list.
- Channel history backfill is on by default and pulls up to 50 earlier
  messages into context, so it is turned off.

### 1. Create the bot (Discord website, by you)

1. In the [Developer Portal](https://discord.com/developers/applications),
   create an application. Under **Bot**, turn **Public Bot** off, turn
   **Message Content Intent** on, and leave **Server Members Intent** off
   (numeric IDs do not need it). Reset the token and keep it for the setup
   prompt only, never in a command line, chat or note.
2. Under **OAuth2, URL Generator**, pick the scopes `bot` and
   `applications.commands`, and the permissions View Channels, Send
   Messages, Send Messages in Threads, Create Public Threads, Read Message
   History and Add Reactions. Open the URL and add the bot to your private
   server.
3. In Discord, turn on **Settings, Advanced, Developer Mode**. Right-click
   each friend (and yourself), **Copy User ID**.

### 2. Configure the gateway

```bash
# Runs in: inside the VM. Interactive: choose Discord, paste the token, give the user IDs comma-separated. Skip any service install.
hermes gateway setup
```

```bash
# Runs in: inside the VM. Safe to run twice: each line overwrites or skips.
hermes config set platform_toolsets.discord "[web]"
ENVF=~/.hermes/.env
if grep -q '^DISCORD_HISTORY_BACKFILL=' "$ENVF"; then echo "skip: DISCORD_HISTORY_BACKFILL already set"; else
  echo 'DISCORD_HISTORY_BACKFILL=false' >> "$ENVF" && echo "added DISCORD_HISTORY_BACKFILL=false"; fi
grep -n -A3 '^  discord:' ~/.hermes/config.yaml                     # must list only web
grep -c '^DISCORD_BOT_TOKEN=' "$ENVF"                                # must print 1 (a count, never the token)
grep '^DISCORD_ALLOWED_USERS=' "$ENVF" | cut -d= -f2 | tr -d "\"' " | tr ',' '\n' | grep -c -E '^[0-9]{17,20}$'   # must print the number of IDs you gave
grep -c -E '^(DISCORD_ALLOWED_ROLES|DISCORD_ALLOWED_CHANNELS|DISCORD_ALLOW_ALL_USERS|GATEWAY_ALLOW_ALL_USERS|GATEWAY_ALLOWED_USERS)=' "$ENVF"   # must print 0
grep '^DISCORD_HISTORY_BACKFILL=' "$ENVF"                            # must print DISCORD_HISTORY_BACKFILL=false
hermes tools list --platform discord                                 # enabled: web_search and web_extract only
hermes pairing list                                                  # no approved users
```

The ID count only counts 17 to 20 digit numbers, so a typo or a username
shows up as a lower count. If any check is off, fix it before step 3.

### 3. Run it

`hermes gateway setup` installs the service. Check it, and restart it after
any change to config, `.env` or the plugin:

```bash
# Runs in: inside the VM. A restart takes Discord down for about 30 s.
systemctl --user restart hermes-gateway.service
sleep 30
systemctl --user is-active hermes-gateway.service    # must print active
systemctl --user is-enabled hermes-gateway.service   # enabled: starts with the VM
loginctl show-user hermes -p Linger                  # Linger=yes: starts at boot with no login
```

To keep the bot off while the VM is up, run
`systemctl --user stop hermes-gateway.service`. Friends @mention the bot in a
server channel (each mention opens a thread), or DM it. Messages sent while
it is stopped are dropped.

To check the allowlist, send the bot a DM and an @mention from an account
that is not on the list, with the gateway `active`. Both must get no reply
and no pairing code, and `hermes pairing list` must show no approved users.
The Discord adapter drops a message from someone not on the list without
logging it, so the log stays quiet. Taking your own ID off the list for the
test works: back up `~/.hermes/.env`, remove the ID, restart the service,
test, then restore the backup and restart again (done 2026-11-10).

### 4. Add the odds tools

The `friends_odds` plugin gives Discord chats three tools: `odds_sports`,
`odds_lines` and `odds_scores` (see [Tools](#tools)). Register a free key at
[The Odds API](https://the-odds-api.com) first. Signing up is yours to do.

Copy the plugin and the collector into the VM:

```bash
# Runs in: local terminal, host, in this repo, with the VM running. Safe to run twice: scp overwrites.
ssh -p 22221 hermes@localhost 'mkdir -p ~/.hermes/plugins/friends_odds'
scp -P 22221 hermes-plugin/friends_odds/plugin.yaml hermes-plugin/friends_odds/__init__.py \
  tools/odds_collect.py hermes@localhost:.hermes/plugins/friends_odds/
ssh -p 22221 hermes@localhost 'ls ~/.hermes/plugins/friends_odds'   # must list __init__.py odds_collect.py plugin.yaml
```

Add the key and settings to `.env`. The key is typed at a hidden prompt, so
it never appears in a command line or the shell history:

```bash
# Runs in: inside the VM. Safe to run twice: each line is skipped when its key exists.
ENVF=~/.hermes/.env
if grep -q '^ODDS_API_KEY=' "$ENVF"; then echo "skip: ODDS_API_KEY already set"; else
  read -rs -p 'The Odds API key: ' K; echo
  case "$K" in ''|*[!A-Za-z0-9]*) echo "STOP: that is not a key (letters and digits only). Nothing changed." ;;
    *) printf 'ODDS_API_KEY=%s\n' "$K" >> "$ENVF" && echo "added ODDS_API_KEY (${#K} characters)" ;; esac
  unset K
fi
CONTACT='you@example.invalid'   # EDIT: your contact address, for the User-Agent
TZNAME='America/New_York'       # EDIT: the time zone answers show times in
grep -q '^FH_CONTACT=' "$ENVF" && echo "skip: FH_CONTACT already set" || { printf 'FH_CONTACT=%s\n' "$CONTACT" >> "$ENVF"; echo "added FH_CONTACT"; }
grep -q '^FH_TIMEZONE=' "$ENVF" && echo "skip: FH_TIMEZONE already set" || { printf 'FH_TIMEZONE=%s\n' "$TZNAME" >> "$ENVF"; echo "added FH_TIMEZONE"; }
grep -c '^ODDS_API_KEY=' "$ENVF"                           # must print 1 (a count, never the key)
grep -E '^(FH_CONTACT|FH_TIMEZONE)=' "$ENVF"               # must show your two values
```

Enable the plugin and give Discord its toolset:

```bash
# Runs in: inside the VM. Safe to run twice.
hermes plugins enable friends_odds
hermes config set platform_toolsets.discord "[web, friends_odds]"
hermes config set tools.tool_search.enabled off
grep -n -A1 '^  discord:' ~/.hermes/config.yaml           # must show [web, friends_odds]
grep -n -A1 '^  tool_search:' ~/.hermes/config.yaml       # the line after must show enabled: off
hermes tools list --platform discord                      # enabled: web and friends_odds only
```

Tool search is turned off because v0.23.0 hides plugin tools behind
`tool_search` and `tool_call` by default. With it on, the model answered an
odds question from web search and never found `odds_lines` (seen
2026-11-07). With it off, a new Discord chat lists exactly `odds_lines`,
`odds_scores`, `odds_sports`, `web_extract` and `web_search`.

Check it in a local session before friends use it. The sports list costs
no credits:

```text
Use the odds_sports tool and reply with its reply text.
```

It must list in-season sports, end with the source and a fetch time, and
print no key. Then restart
the gateway service (step 3), so Discord sessions load the plugin.

## Tools

### Odds collector

`tools/odds_collect.py` reads [The Odds API](https://the-odds-api.com) v4:
odds and lines per bookmaker, scores, and the list of in-season sports. It
uses only the Python standard library and runs inside the VM, where the key
is. Each result is JSON with `source`, `fetched_at` (when the API answered,
kept through the cache), `from_cache` and `credits_remaining`. Odds are
American format, and the default markets are moneyline, spread and total.

An answer is reused for 6 hours. `--fresh` refetches when a friend asks for
current numbers, but only if the cached answer is at least 10 minutes old,
so asking again and again cannot drain the quota.

```bash
# Runs in: inside the VM. Prints JSON, logs to stderr.
python3 odds_collect.py sports                              # free, no credits
python3 odds_collect.py odds basketball_nba                 # h2h,spreads,totals for US books: 3 credits
python3 odds_collect.py odds basketball_nba h2h             # 1 credit
python3 odds_collect.py scores basketball_nba 3             # with the last 3 days of results: 2 credits
python3 odds_collect.py odds basketball_nba --fresh         # refetch if the cached answer is 10 minutes old or more
```

| Setting                | Default                     | What it does                                                                             |
| ---------------------- | --------------------------- | ---------------------------------------------------------------------------------------- |
| `ODDS_API_KEY`       | none, required              | Your key, in `~/.hermes/.env` only                                                     |
| `FH_CONTACT`         | none, required              | Contact address in the User-Agent                                                        |
| `FH_CACHE_DIR`       | `~/.cache/friends-hermes` | Cache and the last known quota, under `odds/`                                          |
| `ODDS_CACHE_TTL`     | `21600` (6 hours)         | Seconds a response is reused                                                             |
| `ODDS_FRESH_MIN_AGE` | `600` (10 minutes)        | A fresh request refetches only a cached answer at least this old                         |
| `ODDS_QUOTA_FLOOR`   | `25`                      | Paid calls stop when fewer credits remain. Cached answers and the sports list still work |

Exit codes: 0 ok, 1 a missing or refused key or contact, 2 bad input (sport,
market, region, days), 3 the API failed or the quota floor was reached.

The key is sent as the `apiKey` query parameter, as the API requires, and
is stripped from every log line, cache file name and error.

### Odds tools in Discord

The `friends_odds` plugin (`hermes-plugin/friends_odds/`) wraps the
collector for chats. Its code, not the model, writes every number, the
source, the fetch time, and "cached" when the answer was reused.

The plugin also decides what a friend sees in Discord, in code:

- When an odds tool ran in a turn, the reply is that tool's text exactly.
  Whatever the model wrote is dropped. A sports-list lookup before a lines
  or scores call is left out. A tool error is shown plainly, never the
  model's guess.
- Any other Discord reply is left as the model wrote it.
- While a reply is being made, Discord shows only the typing indicator
  (`hold_stream`). If the plugin's hook fails, Hermes sends a short "reply
  withheld" notice, never the unchecked text.
- Other platforms, the local CLI included, are unchanged.

- `odds_sports`: in-season sports and their keys. Free.
- `odds_lines`: with no team, the list of games for a sport. With a team,
  every US book's moneyline, spread and total for that team's games. A
  game list is followed by a team question at no cost, since the pull is
  cached.
- `odds_scores`: live and upcoming games, and finals up to 3 days back.

Both `odds_lines` and `odds_scores` take `day` (`today` or `tomorrow`). The
tool works out the date in `FH_TIMEZONE` and keeps only that day's games.
With none, it says so and names the next game. A `today` scores question
also fetches today's finals, which costs 2 credits instead of 1.

`FH_TIMEZONE` (default `UTC`) sets the time zone of the times shown.

### Offline reply

On the ZBOX the model is only reachable while the tunnel from the PC is open.
Set `FH_ROUTER_URL` in `~/.hermes/.env` to the router's address there
(`http://127.0.0.1:8080`). Before every model call, the plugin checks
`/health` with a 2-second limit. If nothing answers, the reply is "The
assistant is offline right now. Try again later." and no model call is made.

- Any HTTP answer counts as up, including a 503 while the router swaps
  models, so a slow router is never reported offline.
- Unset (the VM, which reaches the router directly), there is no check.
- Slash commands (`/odds`, `/scores`, `/pick`, `/picks`) never use the model,
  so they keep working while the PC is off.

### Slash commands in Discord

`/odds` and `/scores` answer from the same code without calling the model,
so they are quick, use no GPU time, and cannot pick the wrong tool. The
friend types the arguments in one text field:

```text
/odds nba lakers
/odds nfl tomorrow
/scores nba today
/odds nba lakers fresh
```

- The first word is the sport: its key (`basketball_nba`), its title
  (`NBA`), or the key's last part (`nba`). A word that matches more than one
  sport, such as `soccer`, lists the keys to choose from. The rest is the
  team.
- `today`, `tomorrow` and `fresh` work in any position, as in the tools.
- With no arguments, the command shows its usage and the in-season sports.
- The reply is the same text `odds_lines` or `odds_scores` gives, with the
  source and fetch time. Looking up the sport costs no credits.

Hermes v0.23.0 adds plugin commands to Discord's slash menu when the
gateway starts, so restart the gateway service (step 3) after copying the
plugin. Only allowlisted users can run them.

### Pick'em in Discord

Friends pick a team to win its next game. There is no money and no spread:
a pick is right when the team wins.

```text
/pick nba lakers      # the Lakers' next game that has not started
/picks                # your own picks, soonest first
```

- The game comes from The Odds API's events list, which costs no credits.
- A pick locks when its game starts. Before that, a new pick on the same
  game replaces the old one.
- A word that fits both teams in the game (`los angeles`) is refused. Use
  more of the name.
- Picks are stored in `~/.hermes/plugin-data/friends_odds/picks.json` on the
  bot's machine, with each friend's Discord ID and display name. The file is
  never in this repo. If it becomes unreadable, the bot says so and never
  overwrites it.

Built-in Hermes commands (`/model`, `/tools`, `/new` and the rest) are not
gated here. v0.23.0 gates slash commands only when `allow_admin_from` is set
for the platform, so every allowlisted friend can run every built-in
command the gateway offers (Casey, 2026-11-12: friends are trusted).

## Tests

No network and no key: a fake fetch serves the sample responses from The
Odds API's v4 guide, saved in `tools/fixtures/odds/`.

```bash
# Runs in: local terminal, host, in this repo. Must end with failed=0 and exit 0.
python3 tools/test_odds_collect.py       # passed=33
python3 hermes-plugin/test_friends_odds.py  # passed=49
```
