# Friends-Hermes on the ZBOX: always-on bot, model on the PC

## Context

Friends-Hermes runs today in a QEMU VM on the PC (`~/VMs/friends-hermes`),
started by hand, because the PC's GPU and router are shared. Casey wants it
always on, on a fanless ZBOX CI321 nano (Celeron 2961Y, 2 cores, up to 16 GB
DDR3L, 2.5" SATA, dual gigabit). The plan:

- Debian 13 on bare metal. No VM.
- An install script in the Friends-Hermes repo, fetched from GitHub.
- The bot's current state restored on the new box.

The model (`freedom`) stays on the PC's llama.cpp router. The VM also stops
competing with `qwen` for the PC's RAM (the 2026-11-04 OOM kill).

Decisions (Casey, 2026-11-15):

- **Model link:** an SSH tunnel. The router contract and bind address stay
  unchanged.
- **Secrets:** the VM's `~/.hermes/.env` is copied straight to the ZBOX, never
  through the repo or GitHub.
- **PC offline:** the bot replies with a fixed "offline" message from code, with
  no model call. This replaces PLAN.md's "started by hand only" with "always on,
  model on demand".

## Where the work happens

- **Friends-Hermes repo:** all code and docs live here. Tier 0 forbids this
  Local-LLM session from writing there, so the work runs from a session opened
  in `~/Apps/Friends-Hermes`, under its own AGENTS.md and IMPLEMENT.md.
- **Local-LLM repo:** only a doc update to *Serving other apps*. Friends-Hermes
  now reaches the router from the ZBOX through the tunnel, not from a QEMU guest
  at `10.0.2.2`. Port, model names, context and request shapes are unchanged.
- **Casey:** the ZBOX hardware and OS install, the PC-side tunnel service,
  copying secrets, and the Discord cutover.

## Tunnel direction: reverse, started by the PC

`sshd` on the PC is off and disabled, read on 2026-11-15. A reverse tunnel needs
no SSH server on the PC:

- **PC side:** a user service (`autossh -N -R 127.0.0.1:8080:127.0.0.1:8080
  tunnel@zbox`) connects out to the ZBOX's sshd.
- **ZBOX side:** `127.0.0.1:8080` reaches the PC's router.
- **Online signal:** when the PC is off or asleep, the tunnel is down and
  `/health` fails. That is the bot's "offline" signal.
- **Locking down the tunnel account:** a dedicated `tunnel` user on the ZBOX,
  with a key restricted in `authorized_keys` to
  `restrict,port-forwarding,permitlisten="127.0.0.1:8080"`, and no shell use.

## Phases (Friends-Hermes IMPLEMENT.md)

1. **Debian on the ZBOX** (execution-assist, Casey runs it)
   - Install Debian 13 netinst: no desktop, SSH server, user `hermes`.
   - Check the box has 16 GB DDR3L and an SSD fitted.
   - Read-only checks: `free -g`, `lsblk`, `ip -br a`.
   - Record hardware facts in PLAN.md.

2. **Install script `scripts/install-zbox.sh`** (repo, idempotent, no secrets)
   - Steps: apt `git curl python3 autossh`, clone or update the repo, then the
     Hermes installer pinned to v0.23.0. It downloads, checks the sha256 against
     a value in the repo, and runs `--non-interactive`.
   - Then the README's step-6 `hermes config set` block, with
     `base_url http://127.0.0.1:8080/v1`.
   - Then the plugin copy from `hermes-plugin/friends_odds` and
     `tools/odds_collect.py`, the step-4 toolset keys, and linger on.
   - Every step prints a skip message on rerun, never suppresses errors, and
     ends with the README's verification greps.
   - Repo visibility: if the GitHub repo is private, the ZBOX needs a read-only
     deploy key (Casey creates it).
   - Tests: shellcheck, plus a dry-run mode that prints each action, checked in
     a scratch HOME.

3. **Tunnel** (Casey installs the PC side)
   - Repo ships the PC user unit `systemd/fh-tunnel.service` and the ZBOX
     `tunnel` user setup (in the install script, behind a flag).
   - Check: on the ZBOX, `curl -s 127.0.0.1:8080/health` prints
     `{"status":"ok"}` and `/v1/models` lists `freedom`.

4. **Offline reply** (plugin code)
   - Before a model call, check the tunnel's `/health` with a 2 s timeout. If it
     is down, return the fixed text "The assistant is offline right now. Try
     again later." with no model call.
   - First confirm which pre-LLM hook v0.23.0 offers (the plugin already uses
     `post_tool_call` and `transform_llm_output` through `ctx.register_hook`).
     If none can short-circuit the call, the fallback is a ZBOX timer that
     stops the gateway while the tunnel is down. That fallback drops messages
     rather than replying, so it is a decision for Casey.
   - Tests: fixture-based, with health up and down, in
     `hermes-plugin/test_friends_odds.py`.

5. **Restore and cutover** (execution-assist)
   - **5a.** In the VM, run `systemctl --user stop hermes-gateway.service`.
     Two gateways on one token would both answer.
   - **5b.** Casey runs one `scp` from the VM to the ZBOX:
     `~/.hermes/.env` to `~/.hermes/.env`, then `chmod 600`. Checked by
     counts only (token 1, allowed IDs N, `ODDS_API_KEY` 1, backfill false,
     forbidden keys 0), never values.
   - **5c.** On the ZBOX: `systemctl --user enable --now
     hermes-gateway.service`, then the README step-3 checks.
   - **5d.** End-to-end checks: the `odds_sports` local check, a DM from an
     allowlisted account, and an unlisted account getting no reply. Then put
     the PC to sleep and confirm the offline reply.
   - **5e.** Leave the VM and its snapshots in place as a rollback until Casey
     removes them.
   - Docs: update the PLAN.md persistence table (the ZBOX replaces the VM rows,
     `.env` lives on the ZBOX), the README setup guide, the "Started by hand
     only" decision, and the Local-LLM README *Serving other apps*.

## Risks

- **No VM sandbox.** Friends' toolset is web plus odds only, with no terminal,
  files or memory, so the blast radius stays small. Defense in depth is the
  `hermes` user without sudo, a dedicated box, and the firewall allowing only
  SSH inbound from the LAN.
- **Haswell-era hardware.** Confirm Debian 13 boots and the Wi-Fi works.
  Prefer wired Ethernet.
- **The router can be busy** with a benchmark or another model. Replies are
  slower, not broken. The router swaps models.

## Verification (overall)

- The install script runs twice on the ZBOX, and the second run makes only
  skip lines.
- `curl 127.0.0.1:8080/health` works on the ZBOX with the PC up, and fails
  cleanly with it down.
- Discord: an allowlisted DM gets a cited odds reply, an unlisted account gets
  none, and the PC asleep gives the offline text.
- Friends-Hermes test suite passes, and the PLAN, README and IMPLEMENT docs
  are updated.

## Deliverable in this session

On approval, this plan is written to `~/Apps/Local-LLM/ZBOX-HERMES-PLAN.md`
(untracked, for Casey to stage or move) as Casey asked. Tier 0 bars this session
from writing into Friends-Hermes, so Casey moves it there or opens a session in
that repo to start Phase 1. The Local-LLM Phase 8 HMM-off soak keeps running
meanwhile, until about 09:36 UTC.
