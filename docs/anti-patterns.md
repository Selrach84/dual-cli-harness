# Anti-Patterns

Three failure modes observed repeatedly across a real fleet. All three produce
reports that **look healthy** while being wrong, which is why they are worth
naming.

---

## 1. False redundancy

### The shape

A binary named `<service>-dual-pp-cli` is assumed to provide two independent
lanes: an API path and a browser fallback. The name is treated as evidence.

### Why it is wrong

The name proves nothing about the implementation. Common real cases:

| Case | What the wrapper does | Real lane count |
|---|---|---|
| Both vars point to one binary | Same code path twice | **1** |
| Two names, same SHA-256 | Byte-identical copies | **1** |
| Symlink to the same realpath | Alias | **1** |
| Distinct realpaths, distinct hashes, distinct transports | Genuine fallback | **2** |

### Why it is dangerous

The fallback's entire value is that it **fails differently** from the primary. If
both lanes share code, they share failure modes — and the fleet believes it has
resilience it does not have. This is worse than having no fallback, because no
fallback is a known gap.

### Detection

```python
# Dedupe by resolved realpath AND content hash.
# A name is not an implementation.
key = (os.path.realpath(binary), sha256(binary))
```

Then **force-probe both paths separately** before claiming coverage.

### Classification

If both targets collapse to one implementation:

> **`PARTIAL (false redundancy)`** — counts as one lane; state explicitly that
> `doctor` tests the same implementation twice.

---

## 2. Probe-contract failure reported as product failure

### The shape

A generic monitor calls `doctor --agent` against every CLI in the fleet. Some
CLIs do not implement `--agent` and exit `2`. The monitor reports those lanes as
**unhealthy**.

### Why it is wrong

Exit `2` from an argument parser means *"I did not understand this invocation"* —
it says nothing about the service. The probe never reached the service.

### Why it is dangerous

A monitor that reports `FAIL` on a healthy service **trains its operators to
ignore it**. Once people learn the digest cries wolf, real failures are missed.
This is the mechanism by which monitoring quietly dies.

### Detection and fix

1. **Inspect each CLI's actual help before selecting a doctor invocation.**
2. Classify usage/argument errors as **probe-contract failures**.
3. **Repair the probe before alerting.**
4. Only a command shape proven by help output or source may become a health
   assertion.

> **Rule: a monitor may never assert on a command shape it has not verified.**

### The related trap — a probe that is too clever

An exit code of `0` with empty output is also not a pass. If the probe's own
parsing failed silently, it reports a healthy service it never read. Assert on
**parsed content**, not on exit status alone.

---

## 3. Inventory drift

### The shape

The expected lane set is built from an archived inventory document — often one
written by a previous audit. The sweep reports on exactly the lanes in the
document and looks complete.

### Why it is wrong

Archived inventories are **historical documents, not specifications.** Concrete
observed case:

| | Count |
|---|---|
| Lanes listed in the archived inventory | **4** |
| Lanes found by a live deduped `ls` across all bin paths | **11** |

A whole service family — the MLS suite, a Google Sheets lane, a comps provider,
the knowledge-graph binary — was **absent from the archive.**

Second case, same fleet: an audit run in the morning **missed a set of CLIs
deployed at 18:15 that same day.** The report was accurate when generated and
wrong within nine hours. It silently omitted a complete service family.

### Why it is dangerous

The report is not *wrong about the lanes it lists* — it is wrong about the
**denominator**. A green report over an incomplete inventory reads as "the fleet
is healthy" when the truth is "the fleet I looked at is healthy."

### Detection and fix

> **Derive the expected inventory from a LIVE scan. Never from an archive.**
> **Scan fresh even against a same-day audit reference.**

```bash
# Live discovery — dedupe, then treat every executable as an intended lane.
find /usr/local/bin "$HOME/.local/bin" /root/.local/bin -maxdepth 1 -type f \
  -perm -u+x 2>/dev/null \
  | grep -E 'cli|pp-cli|cdp|mcp|proxy' \
  | while read -r f; do
      printf '%s  %s  %s\n' "$(sha256sum "$f" | cut -c1-12)" \
                            "$(stat -c %s "$f")" "$(realpath "$f")"
    done | sort -u
```

**Operational corollary:** if someone asks *"why is X not in the report?"* —
**check the live binary list for X first.** It is more often a missing lane than a
genuine absence, and answering from the report you are defending will be wrong.

---

## 4. The meta-anti-pattern

All three above share one root cause:

> **The report describes the probe, and is read as describing the service.**

Every mitigation in this document is the same mitigation: make the probe's own
limitations **visible in its output** — `UNKNOWN` for a probe that could not run,
`PARTIAL` for a lane that is not independent, a stated inventory source for the
denominator.

A monitoring system that cannot express its own uncertainty will express it as
someone else's outage.
