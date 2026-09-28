# Dual CLI Harness

**API first. Browser second. Never the reverse.**

A pattern for giving an agent reliable access to ~70 SaaS integrations across a
fleet of remote VMs: every service gets an **API CLI as primary** and an
optional **browser-automation CLI as fallback**, behind one router that tells the
agent which lane is live.

Genericised write-up of a production system. No client names, no credentials, no
vendor-proprietary business data.

---

## The rule

| Priority | Surface | When |
|---|---|---|
| **1 — primary** | `<service>-pp-cli` (API / OAuth / service key) | The service has an API and a CLI exists or can be built |
| **2 — secondary** | `<service>-cdp-pp-cli` (headless browser via CDP) | API missing, API auth dead, or the data is genuinely web-UI-only |

**Never call the browser lane for work the API can already do while API auth is
healthy.**

## Why this ordering is not a style preference

| | API lane | Browser lane |
|---|---|---|
| Failure mode | auth expiry — detectable, states a reason | page redesign, selector rot, session loss |
| Cost per call | ~50 ms | ~2–6 s + a browser process |
| Determinism | schema-stable | layout-dependent |
| Blast radius of a UI change | **none** | every dependent workflow |
| Debuggability | status code + body | screenshot archaeology |

The browser lane is **strictly more expensive and strictly more fragile.** It is
a safety net, not a parallel implementation. Teams that reach for it first pay
that cost every single call, and discover the fragility at 3 a.m. when a vendor
ships a redesign.

## The router

One binary resolves the question *"which lane do I run?"* so the agent never has
to guess:

```bash
dual-cli list                      # every service + which lanes exist
dual-cli doctor                    # health across the whole fleet
dual-cli doctor <service> --json   # structured health for one service
dual-cli prefer <service>          # prints the binary to run, given live auth
```

`prefer` is the load-bearing verb. It inspects live auth state and prints the
correct executable — so the decision is data, not a prompt instruction the model
can drift away from. **Encoding a policy in a command is more reliable than
stating it in natural language and hoping.**

See [`src/dual_router.py`](src/dual_router.py) and
[`docs/doctrine.md`](docs/doctrine.md).

## Anti-patterns this pattern exists to prevent

Three failure modes were observed repeatedly across a real fleet and are worth
naming, because all three look healthy from the outside:

1. **False redundancy.** A binary named `*-dual-pp-cli` is not proof of two lanes.
   If both its API and CDP targets resolve to the same realpath or the same
   SHA-256, the "fallback" is a second call into the same code. Classify
   `PARTIAL`, do not count two lanes.

2. **Probe-contract failure reported as product failure.** Exit code `2` usually
   means *the probe passed an unsupported flag*, not *the service is down*. A
   monitor reporting `FAIL` on a healthy service is worse than no monitor.

3. **Inventory drift.** Building the expected set from an archived inventory doc
   silently omits lanes deployed since it was written. Always derive the expected
   inventory from a **live scan**.

Full write-up: [`docs/anti-patterns.md`](docs/anti-patterns.md).

## Definition of done — for a lane to be called connected

Not "it responds". All five:

1. The expected executable exists at the expected path.
2. `--help` exits zero and exposes the expected command surface.
3. A live `doctor` exits zero and reports the expected auth/transport.
4. A representative **read** exits zero.
5. The read contains **meaningful non-empty business data** — not HTTP 200, not a
   rendered login page, not static capability text.

Rule 5 is the one that catches the false green. HTTP 200 from a login page is a
200. Detail: [`docs/definition-of-done.md`](docs/definition-of-done.md).

## The monitoring epistemology

Two rules that changed how the fleet reported health:

> **An unavailable probe is `UNKNOWN`, not `UNHEALTHY`.**
> Never invent a failure to fill a gap in the data.

> **Staleness is a trigger to `PROBE`, never to `ACT`.**
> Never restart or repair a service because a metric is old.

A digest that cannot say "I don't know" will manufacture failures, and an
automated remediator acting on stale data will restart healthy services. Both
rules exist because both failures happened.

## Layout

```
src/dual_router.py          lane resolver: inventory, doctor, prefer
docs/doctrine.md            the lane policy, cost model, and probe contract
docs/definition-of-done.md  the five acceptance criteria + negative lanes
docs/anti-patterns.md       false redundancy, probe-contract failure, drift
```

## Provenance

The pattern was extracted from operating a fleet of ~70 SaaS CLIs across 11 remote
VMs, where the same service family frequently offered both an API and a
browser-automation surface and nothing reliably told the agent which to use.

## License

MIT — see [LICENSE](LICENSE).
