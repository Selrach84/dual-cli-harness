# Doctrine — Lane Policy and Probe Contract

## 1. The lane policy

### Create order

When a service needs an agent-accessible CLI:

1. **Discover the API first.** OpenAPI spec, service-to-service auth, an admin-issued key, a documented private-app token. If an API exists, this step succeeds.
2. **Build the API CLI.**
3. **Build the browser CLI second** — only as a safety net for the cases in step 1 that legitimately cannot be solved, or as a stopgap while an API credential is being provisioned.

The order is not arbitrary. A browser CLI built first becomes the primary lane by
accident, and the economic argument for the API is never revisited.

### Runtime order

```text
1. Resolve binary:          <service>-pp-cli   (or `dual-cli prefer <service>`)
2. doctor / auth status:    healthy  -> API commands ONLY
3. not healthy            -> CDP doctor + CDP commands; STATE "lane=cdp"
4. no API CLI but a public API exists -> build the API CLI first
```

**Step 3 has a reporting requirement.** When the agent falls back, it says so.
A silent fallback hides a degraded integration: the task succeeds, the API lane
is broken, and nobody learns for weeks. The fallback is a *symptom*; surfacing it
is what turns it into a signal.

### The honest-fallback rule

Preferring the API lane does **not** mean avoiding the browser lane. It means:

- Try the API lane when its auth is healthy.
- Fall back without hesitation when it is not.
- **Always name the lane you used.**

A team that treats "must use the API" as an absolute will refuse to work instead
of degrading gracefully. A team that treats CDP as equal will never invest in the
API. The rule resolves both.

## 2. The cost model

Concrete numbers from a real fleet, per call:

| | API lane | Browser lane |
|---|---|---|
| Latency | ~50 ms | 2–6 s |
| Process | none | a browser (~300 MB) |
| Tokens if output is fed to a model | compact JSON | HTML or screenshots |
| Failure on vendor UI change | no | yes |
| Failure on token expiry | yes, explicit | yes, silent render failure |

The browser lane costs roughly **40–100×** the time and orders of magnitude more
context. That is the argument for the ordering, stated numerically rather than as
taste.

## 3. The probe contract

A generic fleet monitor must not assume every CLI implements the same flags or
exit semantics.

### 3.1 Deduplicate correctly — two totals, not one

Keep **two** counts:

- **executable names** on disk, and
- **logical implementations**.

Resolve symlinks and compare SHA-256 hashes. Byte-identical copies and aliases
must remain visible in the artifact inventory but count **once** in the health
total. Otherwise a fleet's health percentage is inflated by duplicates.

### 3.2 A `dual` name is not proof of redundancy

Read the wrapper and resolve both targets. Requirement:

- distinct realpaths,
- distinct hashes,
- independently exercisable transports.

If both variables point at one binary, classify:

> **`PARTIAL (false redundancy)`** — do not count two lanes; state that its
> `doctor` tests the same implementation twice.

Force-probe the API and CDP paths **separately** before claiming fallback
coverage. The whole value of the fallback is that it is independent; a fallback
sharing code with the primary shares its failures too.

### 3.3 Inspect help before choosing a doctor invocation

`doctor --agent` is not universal. Exit code `2` commonly means **the probe passed
an unsupported flag**, not that auth or the product is unhealthy.

Classification rule:

> Usage/argument errors are **probe-contract failures**. Repair the probe before
> alerting.

Only a command shape proven by help output or source may become a health
assertion. A monitor that asserts on an unverified command shape will page
someone about a service it never actually queried.

### 3.4 Per-item evidence

For every command in a sweep, record:

- exit code,
- duration,
- stdout/stderr byte counts,
- whether structured output parsed,
- response shape and non-sensitive top-level keys,
- an item/field count or other non-empty-data assertion.

**Never print** tokens, cookies, credentials, raw email bodies, or customer
records. Evidence is the shape of the data, not the data.

### 3.5 Derive the expected inventory from a live scan

An archived inventory is a historical document, not a specification. Same-day
audits go stale: a CLI deployed at 18:15 was invisible to a sweep built from that
morning's audit reference, silently omitting an entire service family from the
report.

> **Scan fresh. Always.** Even against a same-day reference.

If asked "why is X not in the report", **check the live binary list for X first.**
It is more often a missing lane than a real absence.

### 3.6 Expected-negative lanes

An intentionally disconnected lane is not a failure. Define it explicitly up
front and give it a **negative acceptance test**: no configured credential or
session, and `doctor`/read fail closed with the expected status.

Without this, every deliberate gap is counted as a defect forever, and the fleet
report loses credibility with the people who would otherwise act on it.

## 4. Monitoring epistemology

### 4.1 `UNKNOWN` is a first-class outcome

> **An unavailable probe is `UNKNOWN`, not `UNHEALTHY`.**

If a probe cannot run — gateway timeout, host unreachable, credential absent — the
correct report is `UNKNOWN`. Inventing `FAIL` to fill the gap manufactures
incidents and destroys trust in the digest. A monitoring system that cannot
express uncertainty is not conservative; it is **wrong**, and confidently so.

### 4.2 Staleness triggers a probe, not an action

> **Staleness is a trigger to `PROBE`, never to `ACT`.**

A stale timestamp means *the data is old*. It does not mean the service is
broken. An automated remediator acting on staleness will restart healthy services
— and a restart during a batch operation causes the outage it was trying to
prevent.

Sequence: **stale → probe → then decide.** Never stale → act.

### 4.3 Escalate instead of papering over

A CLI with **670 consecutive `login_required` entries over 10 days** was reported
as **`NEEDS HUMAN 2FA`** — not auto-retried, not silently cleared. Repeated
identical failures across many cycles are an **escalation signal**, not a
transient. Auto-retry loops that hide a 10-day auth wall are worse than an honest
alert, because they consume the operator's trust budget while producing nothing.
