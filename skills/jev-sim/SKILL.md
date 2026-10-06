---
name: jev-sim
description: Fast mobile-simulator computer-use. Use when automating iOS Simulator or Android emulator via sim-use with a selected Jev, Rizzo, or Tev1 backend choosing from a bounded candidate list (jev-sim step/loop).
---

# jev-sim

Two-layer mobile computer-use:

1. **sim-use** — observe (`ui --json --no-raw`) and act (`tap @N`, `gesture scroll-up`, …).
2. **Selected decision provider** — Jev, Rizzo, or Tev1 returns a Choice over candidate IDs produced in code (not free-form generation).

Do **not** clone `lycorp-jp/sim-use` into this repo; call the installed CLI only.

## Preflight

```bash
jev-sim doctor
# or:
sim-use --version && sim-use devices
test -n "$TYPESAFE_API_KEY"
```

Install sim-use on Mac:

```bash
brew tap lycorp-jp/tap
brew install lycorp-jp/tap/sim-use
```

## Loop

```bash
jev-sim step "<goal>" --device "$UDID"
jev-sim loop "<goal>" --device "$UDID" --max-steps 20
jev-sim loop "<goal>" --backend rizzo --device "$UDID" --max-steps 20 --trace ./decisions.jsonl
jev-sim dry-run "<goal>" --backend tev1 --ui-file path/to/ui.json --trace ./decisions.jsonl
jev-sim dry-run "<goal>" --ui-file path/to/ui.json --list-only
```

Per step, jev-sim:

1. Runs `sim-use ui --json --no-raw`
2. Builds candidates `{id, kind, selector, label}` (taps + `done` / `scroll-up` / `scroll-down` / `wait`)
3. Sends a typed Choice to only the explicitly selected backend: hosted Jev `jev-1.13.0`, pinned Rizzo on port `18094`, or digest-pinned Tev1 on port `11434`
4. Validates the selected ID, the full probability distribution, and truncation status
5. Splits local choices over 26 candidates into deterministic groups. Each original candidate stays in one leaf question, and the trace records every provider call.
   A local question with one candidate selects that candidate in code without an endpoint call.
6. Re-observes before an action and checks the selected alias against its semantic target
7. Executes via `sim-use tap @N` or `sim-use gesture …`
8. Logs timings, decisions, model metadata, outcomes, and errors to JSONL when `--trace` is set

Full loops use a shared device lock under `~/.local/state/agent-device-android/locks/`. Set `AGENT_DEVICE_IDENTITY` to the identity that the parent caller uses.

## Rules

- Never commit `TYPESAFE_API_KEY`. Read it only from the environment (or the user's local config).
- Prefer `--json --no-raw` for observe; do not dump `data.raw` into prompts.
- Do not start a local model service from jev-sim. The selected endpoint and pin must pass preflight.
- Do not retry an action when its execution result is uncertain. Re-observe and make a new decision.
- If Jev confidence is low, re-observe or ask the user before destructive taps.
- Physical iOS devices: sim-use has a reduced verb set — prefer label/`#id` taps; see sim-use skill.

## Offline check

```bash
pytest   # mocked HTTP + fixture UI; must pass without network
```
