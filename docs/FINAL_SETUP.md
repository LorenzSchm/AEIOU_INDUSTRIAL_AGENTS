# Prepared final setup — not started

Entry point: `scripts/final.ps1`. Preparation does not run it, start a server,
register a simulator account, call a model, or open a practice/final session.

## Inspect and start later

From the repository root, in PowerShell:

```powershell
.\scripts\final.ps1          # offline inspection; no API calls
.\scripts\final.ps1 -Start   # explicit autonomous start, only when intended
```

If PowerShell script execution is restricted, the equivalent project command is:

```powershell
.\.venv\Scripts\python.exe -m aeiou final --check
.\.venv\Scripts\python.exe -m aeiou final --start --agent strategic --parallel 10 --waves 5
```

The existing `.venv` is used; `uv` is not required on this already provisioned laptop.
On a new machine install Python 3.12 and uv, then `uv sync --frozen --dev`.
Supply `ARENA_URL=https://arena.tacto.ai`, `ARENA_API_KEY`, and
`ANTHROPIC_API_KEY` through `.env` or the environment. Shell values take precedence.
Never commit keys. Inspection displays presence booleans only.

Default agent: the general `strategic` agent with the configured Anthropic model.
Known-case P1/P18 experimental policies and local learned artifacts are not selected.
The procurement package and Sunday release are checked locally; the shared evidence
index is prepared once before sessions open. The starting commit is recorded.

## Runtime safeguards

- No start without `--start` / `-Start`.
- An OS-held lock prevents a second runner in this checkout. It releases after a
  crash; the remaining lock file must not be deleted while a process holds it.
- Uncommitted runtime code/data blocks startup; unrelated local reports do not.
- Startup reads the team's practice sessions and refuses to proceed while any
  remain open. Stop practice loops first. No sessions or other processes are killed.
- The kit discovers final cases and runs five waves with at most ten workers.
  Before wave one, `403` means wait. Duplicate-session `409` resumes that session.
- HTTP reads allow 180 seconds; retries honor `Retry-After` and reuse the same
  move ID. Transient wave-poll failures cause automatic polling recovery.
- The kit follows `seconds_left` and retains its 90-second closing reserve, plus
  its latency-based margin. The supplier's response time still affects outcomes.
- The dedicated client blocks leaderboard, event, screen, result and other
  non-session endpoints. There are no manual case-selection arguments.

Logs and start manifests: `data/local/final/<UTC timestamp>/`.
Negotiation snapshots: `data/local/runs/`. Final aggregate results are written to
the timestamped folder. Restart with the same command after a crash; existing final
sessions are recovered through the kit's 409 mechanism.

## Rehearsal and live pre-flight — not performed during preparation

When ready for a local HTTP rehearsal, in a separate terminal:

```powershell
.\.venv\Scripts\arena-sim.exe serve --final-rehearsal --data data/package
```

In the rehearsal terminal, set `ARENA_URL=http://127.0.0.1:8000`, register with
`.\.venv\Scripts\arena.exe register Rehearsal sim`, and put the returned simulator
key in that terminal's `ARENA_API_KEY`. Then use:

```powershell
.\scripts\final.ps1 -Rehearsal          # inspect only
.\scripts\final.ps1 -Rehearsal -Start   # two simulator waves; policy agent, no model calls
```

Rehearsal mode rejects non-loopback URLs. Use a fresh terminal with the production
environment for the real final; never overwrite production keys with simulator keys.

The organisers also request one paid practice case on the final machine and a short
pre-wave waiting check. Neither is automatically performed by this setup. Before the
actual final, stop practice loops, submit the repository and frozen commit through
the organisers' platform, and start the runner. During the final, no manual case or
message choices; program restarts are permitted. The runner follows API state, not
a hard-coded local start time. See the [official final FAQ](https://arena.tacto.ai/faq#final).

## Kit and verification

Official kit checked at `01b7d127597e84cfcddca303b0f999ee05dff74e`.
Compared with the previously vendored `75eecd1`, only its README quota statement
changed; runtime code/dependencies were already current. The vendored README and
attribution were updated, preserving our recording and economic-close extensions.

Thirty-two isolated tests passed for offline inspection, endpoint restrictions,
Retry-After, stable move IDs, 403 waiting, 409 recovery, duplicate-run locking,
durable recording, hosted-error handling and cap closure. These use MockTransport
or an in-memory simulator; no live runner or PowerShell start script was executed.
Live connectivity, provider availability, the paid pre-flight and an HTTP rehearsal
are still unverified. The earlier full-suite run also had five unrelated failing
strategy/score assertions; this setup does not claim an entirely green full suite.
