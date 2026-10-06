# Backend, trace, and shared-lock guide

Date: 2026-10-06
Status: Current implementation
Related: jev-sim CLI and the mobile benchmark caller

This guide records the explicit model routes, candidate policy, trace format, and loop lock contract. jev-sim never starts a model service and never substitutes a different backend when a selected route fails.

## CLI and pinned routes

`step`, `loop`, and `dry-run` accept `--backend jev|rizzo|tev1`. Jev is the default. Each command that makes a choice accepts `--trace PATH`. A loop accepts `--max-steps 30` and `--max-steps=30`; both forms are valid.

| Backend | Model and route | Readiness and pin checks |
|---|---|---|
| `jev` | Hosted TypeSafe System One, model `jev-1.13.0` | Reads `TYPESAFE_API_KEY` through the existing credential resolver. The selected CLI model pin is fixed; `TYPESAFE_MODEL` does not override it. |
| `rizzo` | `http://127.0.0.1:18094/v1/systemone`, model `rizzo-flow-4b-q8_0` | Checks `/health` and that `/v1/models` serves the expected model name. Verifies ready status, runtime fingerprint and pinned model artifact hash. |
| `tev1` | `http://127.0.0.1:11434/v1/systemone`, model `tev1:4b-q4_K_M` | Checks the Ollama `/api/tags` digest against the source-pinned digest. |

Local routes require their service to be available already. A missing service, failed readiness check, name mismatch, digest mismatch, malformed choice, unexpected choice ID, invalid or incomplete probability distribution, or response truncation is an error. There is no fallback. For a one-candidate choice, local backends deterministically select the sole candidate with confidence 1 and make no model request.

## Candidate and hierarchy policy

Candidate construction preserves UI order. It includes enabled entries that have a sim-use alias and pass the tappability policy, deduplicates aliases, and limits tap candidates to 80 by default. It then appends `done`, `scroll-up`, `scroll-down`, and `wait`. Read-only chrome roles without a unique/resource ID are generally skipped. Labels are capped at 120 characters. The cap can omit later tappable entries; the agent does not prune candidates based on model preference or confidence.

Jev accepts up to 255 criteria in one request. The Rizzo and Tev1 routes use a 26-choice limit. For more than 26 local choices, jev-sim partitions the candidates in their original order into groups of at most 26, asks the provider to select a winner in each group, then repeats on the winners until one remains. Singleton groups pass through without a request. Every original candidate remains represented in the final distribution. The final probability for a candidate is the product of its conditional probabilities along its winning path, normalized over all original candidates. This procedure is order-sensitive and these multiplied probabilities are derived and uncalibrated; they must not be treated as a calibrated confidence measure. Provider subcalls and their timings/outcomes are included in the trace.

The action capability remains limited to tapping an existing alias, scrolling, waiting, or stopping. jev-sim adds no text-generation or typing action. Before any non-dry-run action, it observes the UI again. It rejects a missing/stale candidate, and for taps it checks that the alias still points to the same semantic target ID. An uncertain mutation is not replayed automatically.

## JSONL trace

Use `--trace ./decisions.jsonl` with `step`, `loop`, or `dry-run`. The file is opened in append mode, one JSON object per line. Each attempted step records:

- event type, goal, backend, model, and model digest when available;
- candidate IDs, selected ID, confidence, full distribution, and probability source;
- provider subcalls with their choice evidence, timings, usage, and outcome;
- observe/decide/execute/total timings, dry-run and executed flags, outcome, and bounded error details.

Backend setup failures and loop-level errors also produce error records. A new trace file is created with mode `0600`; an existing file keeps its current permissions. The trace does not include credentials or raw UI, but it does include the goal and action labels/IDs. Do not put secrets in goals. Because traces append, use a fresh path or remove/archive the old trace before a benchmark repeat; reusing a path can mix events from earlier runs.

The mobile benchmark caller uses `loop GOAL --backend NAME --device 100.74.61.90:5555 --max-steps 30 --trace PATH`. The caller reads the final `step` outcome as the model declaration and counts `step` records with `executed: true`. Independent grading stays outside this trace.

## Shared full-loop lock

The `step` and full `loop` commands acquire the cross-process device lock. The root is:

```text
~/.local/state/agent-device-android/locks/
```

The identity is the non-empty, trimmed `AGENT_DEVICE_IDENTITY`, else the selected `--device` value, else `device`. The filename is the first 24 lowercase hexadecimal characters of `SHA256(identity)`, followed by `.lock`. The lock directory contains `owner.json` with exactly:

```json
{"pid":1234,"deviceId":"R52T50B4F1D","runId":"uuid"}
```

An existing lock with a live PID blocks the new loop. jev-sim reclaims a lock only when its valid owner PID is dead. Missing, malformed, or mismatched owner records fail closed. A process removes the lock on exit only when its owner record still matches its own PID, device identity, and run ID. This makes independently launched callers serialize on the same physical identity even when they use different transport addresses. Set `AGENT_DEVICE_IDENTITY` to the same physical identity in every caller; the transport `--device` can remain the reachable ADB/sim-use address.

The lock coordinates loop processes; it does not serialize unrelated external tools.

Acquisition and stale recovery use the same short `.lock.guard` directory as mobile-jev. Owner records publish by atomic rename. A guard left by a crash fails closed; verify its recorded PID is dead before cleanup. Both implementations share `AGENT_DEVICE_LOCK_ROOT`. The final offline suite passed 44 tests, including cross-language contention races. Source changes made after the frozen live comparison do not alter its scores.
