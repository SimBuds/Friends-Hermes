# Friends-Hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent) in a QEMU VM,
answering a few friends in a private Discord server as a sports-betting
research assistant: odds and lines across books, scores, schedules, and
stats. It does not bet, and every number comes from a documented API with
its source and fetch time. Information, not betting advice.

It uses the Local-LLM router on the host (`qwen`, context 131,072) and runs
only when started by hand.

[`PLAN.md`](PLAN.md) holds the why. [`AGENTS.md`](AGENTS.md) holds the rules
for coding agents.

## Setup guide

Written in Phase 2 (VM, Hermes, router) and Phase 3 (Discord).

## Tools

Written in Phases 4 to 6 (odds and stats collectors and the plugin).

## Tests

None yet.
