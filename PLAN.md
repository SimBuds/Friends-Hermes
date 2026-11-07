# PLAN.md: Friends-Hermes

A second Hermes Agent, in its own QEMU VM on Casey's workstation, that
answers Casey's friends in a private Discord server as a sports-betting
research assistant. It reports odds, lines, scores, schedules, and stats. It
never bets, and every number in an answer comes from a documented API, not
from the model.

## The shape of it

```text
Host                                        VM (Debian, quickemu, port 22221)
  llama.cpp router 127.0.0.1:8080  <-----     Hermes Agent v0.23.0, base_url http://10.0.2.2:8080/v1
                                              Discord gateway, allowlisted friends only
                                              plugin: odds tool, stats tool  -----> The Odds API, TheSportsDB
Discord (friends) <------------------------>  gateway
```

## Decisions

All made by Casey on 2026-11-07 unless noted.

- **A separate repo and a separate VM from Auto-Agent.** Reason: friends are
  untrusted input, so they never reach Casey's own Hermes, its memory,
  sessions, skills, tokens, or Telegram.
- **Discord, in a private server Casey owns, allowlisted by user ID.** Casey
  creates the bot and the server.
- **Started by hand only.** Reason: the host has one GPU slot, shared with
  Auto-Agent's 03:00 night job and hand runs, and the router serves one
  model at a time. Friends' messages sent while the VM is off are dropped.
  Casey boots the VM by hand, and the gateway runs inside it as a user
  service that starts with the VM (kept 2026-11-07, after
  `hermes gateway setup` installed it).
- **v1 scope:** odds and lines across books, plus scores, schedules, and
  stats. Finance is out.
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
- **Stats, scores, and schedules from [TheSportsDB](https://www.thesportsdb.com).**
  Its terms and limits are read, and recorded here, before the stats
  collector is built.
- **One Hermes plugin carries the tools.** Rejected: an MCP stdio server
  (a second process, and the deferred rate limit would still need a
  plugin hook) and a skill that runs a script (needs the terminal tool).
- **No per-friend rate limit in v1.** The allowlist and starting by hand
  bound use. A `pre_gateway_dispatch` hook is the route if it is needed.
- **VM: 4 GB RAM, 2 cores, 20 GB disk.** See the budget below.

Rejected sources: odds-api.io (new free keys paused, free tier for
development only), ESPN's endpoints (undocumented), balldontlie (free covers
one sport at 5 requests a minute), SportsDataIO free (last season,
next-day delayed).

## How an answer is built

1. A friend asks in the Discord server or by DM. The gateway answers only
   allowlisted user IDs, and keeps each friend's session separate
   (`group_sessions_per_user`, on by default).
2. Hermes (`qwen`) picks the odds or stats tool and its arguments.
3. The plugin calls the collector, which fetches the API (cached, rate
   limited per host, backoff on 429 and 5xx) and returns plain data with
   `source` and `fetched_at`.
4. The plugin formats the numbers, the source, the fetch time, and the
   not-advice line in code. The model may summarize around that block but
   is told to quote it as given.

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

## Load on the host

The host has 31 GiB of RAM and 24 threads. With `qwen` loaded the router
holds about 19.1 GiB (measured in Auto-Agent, 2026-09-26), and Auto-Agent's
VM takes 6 GB. This VM's tools run no commands, so it needs little: 4 GB and
2 cores. Running both VMs while `qwen` is loaded pushes the host into swap,
so run one at a time. Each new Discord session starts by sending Hermes's
system prompt and tool definitions, which is smaller here than Auto-Agent's
15.6K tokens because the toolset is small. Measure it in the gateway phase.

## Persistence split

The VM is disposable. This repo is the durable home of everything except
secrets.

| Location | Holds | VM reboot | `--snapshot apply` | Rebuild |
|---|---|---|---|---|
| This repo | Docs, collectors, plugin, tests | Kept | Kept | Kept |
| `~/.hermes/.env` in the VM | Discord token, friends' IDs, API keys | Kept | Rolled back | Lost. Casey enters them again |
| `~/.hermes/config.yaml` in the VM | Model, toolsets, Discord keys | Kept | Rolled back | Lost. The README blocks re-apply them |
| `~/.hermes/plugins/friends_odds/` in the VM | The plugin and a copy of `tools/odds_collect.py`, copied from this repo | Kept | Rolled back | Lost. Copied again from the repo |
| Collector cache in the VM (`~/.cache/friends-hermes/odds/`) | Raw API responses and the last known quota | Kept | Rolled back | Lost, by design |
| `~/.config/systemd/user/` and `/var/lib/systemd/linger/hermes` in the VM | The gateway's user service and its start at boot | Kept | Rolled back | Lost. `hermes gateway setup` installs it again |
| VM snapshots in `~/VMs/friends-hermes` | `clean` (fresh install) and `hermes-configured` (Hermes v0.23.0, base config, SSH keys only), both 2026-11-07 | Kept | Kept | Lost with the disk image |

The token, friends' IDs, and keys have no copy in this repo by design. A
change made by hand in the VM is copied here in the same phase.

## Findings

- **Tool search hid the odds tools** (2026-11-07). Hermes v0.23.0 defaults
  `tools.tool_search.enabled` to `auto`, which moves plugin tools behind
  `tool_search`, `tool_describe` and `tool_call`. A Discord odds question was
  answered from web search, with made-up trends and a "prop lean" and no
  citation, and `odds_lines` was never called (no cache file, 500 credits
  untouched). The source shows `tool_call` refuses tools outside the
  session's list, so it was not a way past the toolset. Turned off, and
  the session lists the five tools directly.

## Open questions

- Does `platform_toolsets.discord` accept the plugin's toolset name? Checked
  in the plugin phase.
- TheSportsDB's terms, free key, and rate limit.
- System prompt size with the small toolset.

## Sources

- [The Odds API v4 guide](https://the-odds-api.com/liveapi/guides/v4/) and
  [terms](https://the-odds-api.com/terms-and-conditions.html), read 2026-11-07
- [Hermes Discord](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord),
  [plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins),
  [hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks),
  [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), read 2026-11-07
- Auto-Agent repo: README setup steps 1 to 7, PLAN persistence split and
  memory budget, `jobs/news-collect.py`
