# Definition of Done — Calling a Lane "Connected"

> "It responds" is not connected. "HTTP 200" is not connected.
> A lane is connected when it returns **real business data on every criterion
> below.**

## The five criteria

For every intended connected lane:

| # | Criterion | Evidence |
|---|---|---|
| 1 | Expected executable exists at the expected path | `test -x`, record size + SHA-256 |
| 2 | `--help` exits **0** and exposes the expected command surface | captured help text |
| 3 | Live `doctor` exits **0** and reports the expected auth/transport | structured doctor output |
| 4 | A representative **read** exits **0** | exit code + duration |
| 5 | The read contains **meaningful non-empty business data** | item/field count |

### Criterion 5 is the one that matters

The false green it prevents:

| Observed | Naive verdict | Correct verdict |
|---|---|---|
| `HTTP 200` returning a **login page** | ✅ healthy | ❌ **not connected** |
| `HTTP 200` returning static capability text | ✅ healthy | ❌ not connected |
| `[]` — valid JSON, zero records | ✅ healthy | ⚠️ **investigate**: empty ≠ error, but not proof either |
| Valid payload, correct shape, N items | ✅ healthy | ✅ **connected** |

A web session that has silently logged out returns **200 with a login page**.
Every naive health check in existence calls that a success. Criterion 5 is what
makes the digest trustworthy.

**Empty is not failure.** `[]` may be legitimately correct for a quiet account.
It is a trigger to look at *why* it is empty — never an automatic `FAIL`.

## Negative acceptance for intentional gaps

For a lane that is deliberately disconnected, define the expectation explicitly
before the sweep:

> No configured credential or session; `doctor` and read **fail closed** with the
> expected status.

Expected-negative lanes **do not count as fleet failures.**

### Why this is mandatory, not optional

Without a declared negative set, every deliberate gap is a permanent defect in
every report. Three consequences follow:

1. The failure count never reaches zero, so it stops being informative.
2. Real failures hide inside the expected noise.
3. Operators learn to ignore the digest — the one outcome that makes monitoring
   worse than nothing.

**A metric that cannot reach a passing state is not a metric.**

## Worked example — the false-negative that reshaped a report

A digest read `69 CLIs · 9 VMs · 52 ok, 11 warn, 6 FAIL`.

Before touching anything, each `FAIL` was triaged to a **distinct root cause**:

| `FAIL` | Actual root cause | Real fix |
|---|---|---|
| 1 | **Flaky timeout in the test harness** | Fix the harness, not the service |
| 2 | **Discovery double-counted** the same binary under two paths | Dedupe by realpath + hash |
| 3 | **Genuinely logged-out sessions** | Re-authenticate (needs a human) |

Only **three** were real, and only one class needed a human. Two of the six were
**monitoring bugs** — the probe was broken, not the service.

The naive response — restart everything red — would have **restarted two healthy
services** and introduced two real outages while "fixing" two non-problems.

Verification was deliberately adversarial:

- one verifier per affected VM, each **proving auth with real business data**,
  never a session ping;
- first-hand reproduction of every `FAIL`;
- a multi-model adversarial panel asked to falsify the conclusions.

Final digest: **`65 CLIs · 52 OK, 13 WARN, 0 FAIL, 0 UNKNOWN`** — with every WARN
carrying a stated cause and every UNKNOWN carrying a stated reason.

## The recurring defect class worth naming

Across one sweep, **five of nine tickets were the same bug**:

> **The monitor reports OK for a genuinely broken state.**

This is the most expensive defect class in monitoring, because it is invisible.
A monitor that fails loudly is annoying. A monitor that succeeds falsely is a
**liability** — it converts an unmonitored system into a system you *believe* is
monitored, and the belief is what stops anyone from looking.

Mitigation: every health assertion must be tied to a command shape **proven by
help output or source**, and every success must assert on **data**, not status.
