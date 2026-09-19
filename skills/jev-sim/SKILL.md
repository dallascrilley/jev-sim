---
name: jev-sim
description: Fast mobile-simulator computer-use. Use when automating iOS Simulator or Android emulator via sim-use with TypeSafe Jev choosing the next tap from a bounded candidate list (jev-sim step/loop).
---

# jev-sim

Two-layer mobile computer-use:

1. **sim-use** — observe (`ui --json --no-raw`) and act (`tap @N`, `gesture scroll-up`, …).
2. **TypeSafe Jev** — Choice over candidate IDs produced in code (not free-form generation).

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
jev-sim dry-run "<goal>" --ui-file path/to/ui.json --list-only
```

Per step, jev-sim:

1. Runs `sim-use ui --json --no-raw`
2. Builds candidates `{id, kind, selector, label}` (taps + `done` / `scroll-up` / `scroll-down` / `wait`)
3. POSTs TypeSafe `https://api.typesafe.ai/v1/systemone` model `jev-latest` with a Choice over those IDs
4. Executes via `sim-use tap @N` or `sim-use gesture …`
5. Logs latency + confidence; repeats until `done` or max steps

## Rules

- Never commit `TYPESAFE_API_KEY`. Read it only from the environment (or the user's local config).
- Prefer `--json --no-raw` for observe; do not dump `data.raw` into prompts.
- After every execute, treat the next `ui` as verification.
- If Jev confidence is low, re-observe or ask the user before destructive taps.
- Physical iOS devices: sim-use has a reduced verb set — prefer label/`#id` taps; see sim-use skill.

## Offline check

```bash
pytest   # mocked HTTP + fixture UI; must pass without network
```
