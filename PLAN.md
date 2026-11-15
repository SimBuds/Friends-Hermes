# PLAN.md: Friends-Hermes

A second Hermes Agent, in its own QEMU VM on Casey's workstation, that
answers Casey's friends in a private Discord server as a sports-betting
research assistant. It reports odds, lines, scores, and schedules (stats
are not in v1). It never bets, and every number in an answer comes from a documented API, not
from the model.

## The shape of it

```text
PC (on when Casey uses it, sleeps)           ZBOX (Debian 13, always on, Wi-Fi)
  llama.cpp router 127.0.0.1:8080              Hermes Agent v0.23.0, base_url http://127.0.0.1:8080/v1
  fh-tunnel user service ---- ssh -R ---->    127.0.0.1:8080 (the tunnel user's one allowed forward)
                                               Discord gateway, allowlisted friends only
                                               plugin: friends_odds tools  --------> The Odds API
Discord (friends) <------------------------->  gateway
```

The ZBOX replaces the VM (Debian, quickemu, SSH port 22221), which reached
the router at `10.0.2.2:8080`. The VM stays as the rollback until the move
is checked. The tunnel is a user service on the PC (Casey, 2026-11-15) that
starts at login and restarts when the link drops, so the bot has the model
whenever the PC is on and answers offline otherwise. The PC needs no SSH
server, and the router's bind address and contract do not change. The
ZBOX's address is kept on the PC in `~/.config/friends-hermes/zbox`, never in
this repo.

## Decisions

All made by Casey on 2026-11-07 unless noted.

- **A separate repo and a separate VM from Auto-Agent.** Reason: friends are
  untrusted input, so they never reach Casey's own Hermes, its memory,
  sessions, skills, tokens, or Telegram.
- **Discord, in a private server Casey owns, allowlisted by user ID.** Casey
  creates the bot and the server.
- **Always on, model on demand** (Casey, 2026-11-15, was "started by hand
  only" from 2026-11-07). The bot runs all the time on the ZBOX. The model
  stays on the PC, whose one GPU slot is shared with Auto-Agent's 03:00 night
  job, and is reached only while the PC's tunnel service is up. Otherwise
  the bot answers offline from code, and slash commands still work. The VM
  is the rollback, its gateway disabled (2026-11-15).
- **v1 scope:** odds and lines across books, plus scores and schedules.
  Finance is out, and so are stats (below).
- **Odds from [The Odds API](https://the-odds-api.com), free Starter plan.**
  500 credits a month. An odds call costs markets times regions, so the
  three main markets for US books cost 3. Scores cost 1 (2 with `daysFrom`),
  and the sports and events lists are free. Its terms allow showing the data
  in your own product and forbid reselling or redistributing it as a raw
  feed. Chat answers to a few friends are the first kind. The collector
  caches responses and stops fetching when the remaining quota falls below
  a floor. Moving to the paid 20K plan changes only the key.
- **Cached odds are reused for 6 hours, and a friend can ask for fresh
  ones** (Casey, 2026-11-07). A fresh request refetches only when the cached
  answer is at least 10 minutes old, which stops repeated requests from
  draining credits while there is no per-friend rate limit. Every answer
  shows its fetch time, so a reused one is never passed off as current.
  The default markets stay moneyline, spread and total (3 credits a pull):
  about 158 fresh full pulls a month on the free plan's 475 usable credits.
- **Scores and schedules come from The Odds API, and stats are out of v1**
  (Casey, 2026-11-07). Planned first from TheSportsDB, whose terms limit the
  free key to "development projects" and allow apps and services only on
  paid plans ($9 a month). No free stats API both allows a friends' bot and
  includes stats: balldontlie's terms allow it, but its free plan has only
  teams, players, and games (stats $9.99 a month per league). MySportsFeeds
  is for "individual use" from $5 a month per league. football-data.org is
  soccer only. API-Sports refused every fetch of its terms. MLB's Stats API
  is undocumented. If stats come back, the paid choices are TheSportsDB (all
  sports) or balldontlie (one league each).
- **"Today" and "tomorrow" are worked out by code** in `FH_TIMEZONE`, never
  by the model, and a reply with no game that day names the next one. A
  scores question about today also asks for one day of finals (2 credits,
  not 1), because without it the API returns no game already finished.
- **One Hermes plugin carries the tools.** Rejected: an MCP stdio server
  (a second process, and the deferred rate limit would still need a
  plugin hook) and a skill that runs a script (needs the terminal tool).
- **No per-friend rate limit in v1.** The allowlist and starting by hand
  bound use. A `pre_gateway_dispatch` hook is the route if it is needed.
- **VM: 4 GB RAM, 2 cores, 20 GB disk.** See the budget below.
- **No "not betting advice" line** (Casey, 2026-11-10). It was on every
  odds reply and every other Discord reply from 2026-11-07. Removed at
  Casey's request. Replies after an odds tool still carry the source and
  fetch time, written by code.
- **Model `freedom`** (Casey, 2026-11-09, was `qwen`): the router's
  uncensored Qwen 3.6 35B, because Casey wants an unfiltered bot. Same
  context (131,072) and about the same size as `qwen` (21 GB GGUF). It does
  not refuse, so research-only is held by the tools (none can bet, log in,
  or move money) and by the plugin's reply hook, never by the model. Its
  tool calling is not in Local-LLM's tool-calling results. Auto-Agent stays
  on `qwen`, so the router swaps models when both VMs are used in turn.
- **General questions too, on Hermes's keyless web search** (Casey,
  2026-11-11). Friends may ask about any topic, not only sports. No web key
  is set, so Hermes rotates across free vendor tiers (search on Parallel and
  Keenable, page reads on Firecrawl and Exa). Tested 2026-11-11 with 10
  general questions: search 10 of 10 with no rate limit, page reads 8 of 10.
  The two failures were the sites (an Exa timeout, a Firecrawl certificate
  error), and Hermes did not retry them on another vendor. Exa page reads
  stop at about 3,100 characters. Rejected: a Tavily key (1,000 free credits
  a month), because keyless held and needs no account. The vendors' terms
  for keyless use were not checked.

- **Pick'em, straight-up winner** (Casey, 2026-11-12). `/pick <sport> <team>`
  records a pick on that team's next game, found in The Odds API's free
  events list, and `/picks` lists your own. A pick locks at game start. Picks
  are the bot's first stored record about friends (Discord ID, display name,
  picks), kept in Hermes's plugin-data folder, never in this repo (Casey,
  2026-11-14). Grading and a leaderboard come next.

- **Offline reply from code** (Casey, ZBOX plan). On the ZBOX the bot runs
  all the time but the model only while Casey's tunnel is open. A
  `pre_llm_call` hook checks the router's `/health` (2 s) and, when nothing
  answers, ends the turn with a fixed "offline" text and no model call
  (v0.23.0 `agent/turn_context.py`, `{"action": "block", "message"}`). Only a
  failed connection counts as offline, never an HTTP error, so a model swap
  is not reported as offline. `FH_ROUTER_URL` turns it on, so the VM is
  unchanged.

Rejected sources: odds-api.io (new free keys paused, free tier for
development only), ESPN's endpoints (undocumented), balldontlie (free covers
one sport at 5 requests a minute), SportsDataIO free (last season,
next-day delayed).

## How an answer is built

1. A friend asks in the Discord server or by DM. The gateway answers only
   allowlisted user IDs, and keeps each friend's session separate
   (`group_sessions_per_user`, on by default).
2. Hermes (`freedom`) picks an odds tool and its arguments.
3. The plugin calls the collector, which fetches the API (cached, rate
   limited per host, backoff on 429 and 5xx) and returns plain data with
   `source` and `fetched_at`.
4. The plugin formats the numbers, the source, and the fetch time in code,
   and its reply hook sends that text to Discord exactly, whatever the
   model wrote.

The slash commands `/odds` and `/scores` skip steps 2 and 3's model: the
plugin parses the friend's words, matches the sport against the free sports
list, and sends the same code-written text (Casey, 2026-11-12). Built on
v0.23.0's `register_command`, which Discord shows as native slash commands.

## Threat model

Friends' messages, and any web page the web tool reads, are untrusted.

| Risk | What stops it |
|---|---|
| A friend asks it to bet, log in, or move money | No tool can. The project rule makes it a refusal |
| A message gets it to run commands or read files (the `.env` with keys) | Discord toolset has no terminal and no file tools |
| A message plants instructions that persist | No `memory` tool, no `session_search`, skills need Casey's approval, background review off |
| A stranger reaches the bot | User ID allowlist only. No roles, no allow-all, no channel list alone |
| Earlier channel messages steer the model | History backfill off |
| The model invents a number | Numbers and citations are written by the plugin, never the model |
| A friend burns the odds quota | Cache plus a quota floor in the collector |
| A friend runs a built-in slash command (`/model`, `/tools`, `/restart`) | Not stopped. Slash commands stay ungated (Casey, 2026-11-12: friends are trusted). Gating would set `allow_admin_from` to Casey's ID. `/tools` is not offered on Discord ("Unknown command /tools", seen 2026-11-14), so it cannot widen the toolset. `/yolo` works: any friend can turn on auto-approval for their own session (seen 2026-11-14). With no terminal or file tools there is little for it to approve. `/new` turns it off again. `/model` is offered too (seen 2026-11-14), so a friend can switch their session to another router model. That makes the router swap models, which slows Auto-Agent when it shares the GPU |

## Load on the host

The host has 31 GiB of RAM and 24 threads. With `qwen` loaded the router
holds about 19.1 GiB (measured in Auto-Agent, 2026-09-26), and Auto-Agent's
VM takes 6 GB. This VM's tools run no commands, so it needs little: 4 GB and
2 cores. Running both VMs while a 35B model is loaded pushes the host into swap,
so run one at a time. Each new Discord session starts by sending Hermes's
system prompt and tool definitions. With five tools that is about 2.7K
tokens, against Auto-Agent's 15.6K (measured 2026-11-09 in the router log:
a new session's first request held 2,736 tokens, of which 1,042 were new
and the rest came from the router's cached prefix). On `freedom` that read
in about 1 second, and replies generated at 41 to 51 tokens per second.

## The ZBOX (moving here, 2026-11-14)

The bot is moving from the VM to a fanless ZOTAC ZBOX CI321 nano, always on,
with the model still on the PC's router (`ZBOX-HERMES-PLAN.md`). The VM
stays as the rollback until the move is checked. Read on the box,
2026-11-14:

| Part | What is fitted | Note |
|---|---|---|
| OS | Debian 13.7, kernel 6.12.117, UEFI, GPT | Installed with GRUB on the removable path, because the firmware ignored the normal boot entry |
| CPU | Intel Celeron 2961Y, 2 cores | |
| RAM | One 2 GB DDR3L stick at 1,333 MT/s, 1.35 V (single slot) | 390 MB used at idle. The VM's gateway peaked at about 490 MB while answering, so 2 GB fits with about 900 MB spare. An 8 GB DDR3L stick is the upgrade if swap is used |
| Disk | Toshiba MQ01ABD032, 320 GB 2.5" HDD | SMART passed, 0 reallocated, pending or uncorrectable sectors, about 5,500 hours. Lifetime maximum 76 C, so it needs airflow |
| Network | Intel Wireless 3160 (in use), 2x Realtek RTL8111/8168 (unused) | Wi-Fi stays (Casey). DHCP from the Rogers gateway, address reserved there |
| Admin | No `sudo`. Root work uses `su -` | The `hermes` user cannot change the system |

## Persistence split

The ZBOX is rebuildable from this repo plus two hand-copied files. The VM is
the rollback, unchanged since the cutover (2026-11-15). This repo is the
durable home of everything except secrets and picks.

| Location | Holds | Reboot | Rebuild |
|---|---|---|---|
| This repo | Docs, collectors, plugin, tests, `scripts/install-zbox.sh`, `scripts/fh-tunnel.sh`, `systemd/fh-tunnel.service` | Kept | Kept |
| `~/.hermes/.env` on the ZBOX | Discord token, friends' IDs, API keys, `FH_*` settings | Kept | Lost. Copied again from the VM, or entered again by Casey |
| `~/.hermes/config.yaml` on the ZBOX | Model, toolsets, Discord keys | Kept | Lost. `install-zbox.sh` applies it again |
| `~/.hermes/plugins/friends_odds/` on the ZBOX | The plugin and a copy of `tools/odds_collect.py` | Kept | Lost. `install-zbox.sh` copies it again |
| `~/.hermes/plugin-data/friends_odds/picks.json` on the ZBOX | Friends' pick'em picks | Kept | Lost. No copy anywhere by design |
| `~/.cache/friends-hermes/odds/` on the ZBOX | Raw API responses and the last known quota | Kept | Lost, by design |
| `~/.config/systemd/user/hermes-gateway.service` and linger on the ZBOX | The gateway's start at boot | Kept | Lost. `hermes gateway install` (answer n to "start now"), then a restart |
| The ZBOX `tunnel` user and its `authorized_keys` | The tunnel's one allowed forward | Kept | Lost. README, Tunnel to the router, step 2 |
| `/etc/modprobe.d/fh-wifi-awake.conf` on the ZBOX | Wi-Fi power save off | Kept | Lost. README, ZBOX setup |
| `~/.ssh/fh_tunnel`, `~/.config/friends-hermes/zbox` and `~/.config/systemd/user/fh-tunnel.service` on the PC | The tunnel key, the ZBOX's address, the tunnel service | Kept | Lost with the PC's home. README, Tunnel to the router |
| The VM (`~/VMs/friends-hermes`) with snapshots `clean` and `hermes-configured` | The rollback: Hermes, the old `.env` and picks as of 2026-11-15, gateway disabled | Kept | Lost with the disk image |

The token, friends' IDs, and keys have no copy in this repo by design. A
change made by hand on the ZBOX is copied here in the same phase.

## Findings

- **The model rewrote the plugin's text** (2026-11-07, twice). The sports
  list came back reformatted, and "Lakers lines tonight" came back
  paraphrased with the source, fetch time and not-advice line dropped and
  a claim of its own added. Telling the model to quote, in the tool
  descriptions, did not hold. Fixed in code (Casey, option A): the plugin's
  `post_tool_call` hook keeps each odds tool's text by `turn_id`, and its
  `transform_llm_output` hook, registered with `hold_stream=True`, makes
  the Discord reply that text exactly. Both hooks receive `turn_id` in v0.23.0
  (`model_tools._CallIds`, `agent/turn_finalizer.py`).

- **Tool search hid the odds tools** (2026-11-07). Hermes v0.23.0 defaults
  `tools.tool_search.enabled` to `auto`, which moves plugin tools behind
  `tool_search`, `tool_describe` and `tool_call`. A Discord odds question was
  answered from web search, with made-up trends and a "prop lean" and no
  citation, and `odds_lines` was never called (no cache file, 500 credits
  untouched). The source shows `tool_call` refuses tools outside the
  session's list, so it was not a way past the toolset. Turned off, and
  the session lists the five tools directly.

## Open questions

- Answered 2026-11-07: `platform_toolsets.discord` accepts the plugin's
  toolset name (`[web, friends_odds]`, confirmed with
  `hermes tools list --platform discord`).

## Sources

- [The Odds API v4 guide](https://the-odds-api.com/liveapi/guides/v4/) and
  [terms](https://the-odds-api.com/terms-and-conditions.html), read 2026-11-07
- [Hermes Discord](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord),
  [plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins),
  [hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks),
  [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), read 2026-11-07
- Auto-Agent repo: README setup steps 1 to 7, PLAN persistence split and
  memory budget, `jobs/news-collect.py`
