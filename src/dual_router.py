#!/usr/bin/env python3
"""
Dual CLI router — resolves WHICH lane to run, so the agent never guesses.

Commands
--------
  dual-cli list                      services + which lanes exist
  dual-cli doctor [service] [--json] live health, honest about uncertainty
  dual-cli prefer <service>          prints the binary to run given live auth
  dual-cli audit                     duplicate/false-redundancy audit

Design rules enforced here (see docs/doctrine.md):
  1. API lane is primary; CDP lane is fallback. Never the reverse.
  2. Two totals: executable NAMES and logical IMPLEMENTATIONS.
     Dedupe by realpath + SHA-256 — a name is not an implementation.
  3. A `dual` name is not proof of two lanes. Verify distinct realpaths.
  4. A probe that cannot run returns UNKNOWN, never UNHEALTHY.
  5. A probe-contract failure (bad flag) is repaired, not alerted.
  6. Inventory comes from a LIVE scan, never an archive.

Stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Services the fleet is expected to expose. Populate from your own fleet;
# the router treats every discovered binary as a lane regardless of this list
# (rule 6), using it only to flag EXPECTED-but-MISSING services.
EXPECTED_SERVICES: tuple[str, ...] = ()

# Lanes that are intentionally disconnected. Declared up front so they are not
# counted as failures forever (see docs/definition-of-done.md).
EXPECTED_NEGATIVE: frozenset[str] = frozenset()

BIN_DIRS: tuple[str, ...] = (
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
    "/root/.local/bin",
)

NAME_HINTS = ("cli", "pp-cli", "cdp", "mcp", "proxy")
PROBE_TIMEOUT_S = 25

UNKNOWN = "UNKNOWN"
OK = "OK"
FAIL = "FAIL"
PARTIAL = "PARTIAL"


# --------------------------------------------------------------------------- #
# Discovery — live scan, always
# --------------------------------------------------------------------------- #

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def split_lane_name(name: str) -> tuple[str, str]:
    """
    '<svc>-cdp-pp-cli' -> ('<svc>', 'cdp')
    '<svc>-pp-cli'     -> ('<svc>', 'api')
    'dual-cli'         -> ('dual-cli', 'api')
    """
    stem = name
    for suffix in ("-pp-cli", "-cli"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem.endswith("-cdp"):
        return stem[: -len("-cdp")], "cdp"
    if stem.endswith("-headful") or stem.endswith("-headless"):
        stem = stem.rsplit("-", 1)[0]
        return stem, "cdp"
    return stem, "api"


@dataclass
class Lane:
    name: str
    path: str
    service: str
    transport: str
    size: int
    sha: str = ""
    realpath: str = ""

    def resolve(self) -> None:
        self.realpath = os.path.realpath(self.path)
        self.sha = _sha256(self.path)


@dataclass
class Service:
    name: str
    lanes: dict[str, Lane] = field(default_factory=dict)

    @property
    def primary(self) -> Lane | None:
        return self.lanes.get("api") or self.lanes.get("cdp")

    @property
    def fallback(self) -> Lane | None:
        return self.lanes.get("cdp") if "api" in self.lanes else None

    def redundant(self) -> bool:
        """True when the 'fallback' is the same implementation as the primary."""
        a, b = self.lanes.get("api"), self.lanes.get("cdp")
        if not a or not b:
            return False
        return a.realpath == b.realpath or (a.sha and a.sha == b.sha)


def discover() -> dict[str, Service]:
    """LIVE scan (rule 6). Never read an archive."""
    services: dict[str, Service] = {}
    for d in BIN_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not any(h in name for h in NAME_HINTS):
                continue
            if name == "dual-cli":
                continue
            path = os.path.join(d, name)
            if not (os.path.isfile(path) and os.access(path, os.X_OK)):
                continue
            svc_name, transport = split_lane_name(name)
            lane = Lane(name=name, path=path, service=svc_name,
                        transport=transport, size=os.path.getsize(path))
            lane.resolve()
            services.setdefault(svc_name, Service(svc_name)).lanes[transport] = lane
    return services


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #

@dataclass
class ProbeResult:
    status: str
    detail: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout_bytes: int = 0
    stderr_bytes: int = 0


def probe(binary: str, *args: str) -> ProbeResult:
    """
    Run a probe and classify HONESTLY.

    Exit 2 is a PROBE-CONTRACT FAILURE (unsupported flag), not an unhealthy
    service. It is reported as UNKNOWN with an explicit reason, never FAIL.
    """
    import time

    t0 = time.monotonic()
    try:
        cp = subprocess.run(
            [binary, *args],
            capture_output=True, timeout=PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # Could not observe the service. That is UNKNOWN, not unhealthy.
        return ProbeResult(UNKNOWN, f"probe timed out after {PROBE_TIMEOUT_S}s")
    except OSError as exc:
        return ProbeResult(UNKNOWN, f"probe could not execute: {exc}")

    dt = int((time.monotonic() - t0) * 1000)
    res = ProbeResult(
        status=OK if cp.returncode == 0 else FAIL,
        detail="",
        exit_code=cp.returncode,
        duration_ms=dt,
        stdout_bytes=len(cp.stdout or b""),
        stderr_bytes=len(cp.stderr or b""),
    )

    if cp.returncode == 2:
        # Usage error. The probe never reached the service.
        res.status = UNKNOWN
        res.detail = ("probe-contract failure (exit 2: unsupported flag) — "
                      "repair the probe; do NOT alert")
        return res

    if cp.returncode != 0:
        tail = (cp.stderr or b"").decode(errors="replace").strip().splitlines()
        res.detail = tail[-1][:200] if tail else f"exit {cp.returncode}"
        return res

    out = (cp.stdout or b"").decode(errors="replace").strip()
    if not out:
        # exit 0 with no output is NOT proof of health: the probe may have
        # parsed nothing. Assert on content, not on status alone.
        res.status = UNKNOWN
        res.detail = "exit 0 but empty output — no evidence of a live read"
        return res

    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        res.detail = "ok (non-JSON output)"
        return res

    # Criterion 5: assert on meaningful CONTENT, not on HTTP 200 / exit 0.
    if isinstance(payload, dict) and payload:
        res.detail = f"ok ({len(payload)} fields)"
    elif isinstance(payload, list):
        res.detail = f"ok ({len(payload)} items)" if payload else "EMPTY list — investigate, not a failure"
    else:
        res.detail = "ok"
    return res


def doctor_lane(lane: Lane) -> ProbeResult:
    """
    Probe a lane using a command shape verified against its OWN help output
    (rule 3 / probe contract). Falls back to `--help` when no doctor exists.
    """
    help_out = probe(lane.path, "--help")
    help_text = ""
    try:
        cp = subprocess.run([lane.path, "--help"], capture_output=True,
                            timeout=PROBE_TIMEOUT_S)
        help_text = (cp.stdout or b"").decode(errors="replace")
    except Exception:
        pass

    for candidate in (("doctor", "--json"), ("doctor",), ("auth", "status")):
        if all(c in help_text for c in candidate):
            return probe(lane.path, *candidate)

    return help_out if help_out.status == OK else ProbeResult(
        UNKNOWN, f"no verified doctor shape; help probe={help_out.status}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_list(services: dict[str, Service]) -> int:
    print(f"{'SERVICE':<28} {'API':<34} {'CDP':<34} FLAGS")
    print("-" * 108)
    for name in sorted(services):
        svc = services[name]
        api = svc.lanes.get("api")
        cdp = svc.lanes.get("cdp")
        flags = []
        if svc.redundant():
            flags.append("FALSE-REDUNDANCY")
        if cdp and not api:
            flags.append("cdp-only")
        print(f"{name:<28} "
              f"{(api.name if api else '-'):<34} "
              f"{(cdp.name if cdp else '-'):<34} "
              f"{','.join(flags)}")

    names = sum(len(s.lanes) for s in services.values())
    # Two totals (rule 2). Distinct by realpath+hash.
    impls = {(l.realpath, l.sha) for s in services.values() for l in s.lanes.values()}
    print("-" * 108)
    print(f"executable names: {names}   logical implementations: {len(impls)}"
          f"   services: {len(services)}")
    if names != len(impls):
        print(f"NOTE: {names - len(impls)} duplicate/aliased binaries counted once "
              f"in the health total.")
    return 0


def cmd_audit(services: dict[str, Service]) -> int:
    """Surface false redundancy and duplicate implementations."""
    problems = 0
    for name in sorted(services):
        svc = services[name]
        if svc.redundant():
            problems += 1
            print(f"{PARTIAL:<8} {name:<28} "
                  f"api={svc.lanes['api'].realpath} "
                  f"cdp={svc.lanes['cdp'].realpath} "
                  f"-> same implementation; NOT two lanes")
    seen: dict[tuple[str, str], str] = {}
    for name in sorted(services):
        for lane in services[name].lanes.values():
            key = (lane.realpath, lane.sha)
            if key in seen:
                print(f"{PARTIAL:<8} {lane.name:<28} byte-identical to {seen[key]}")
                problems += 1
            else:
                seen[key] = lane.name
    print(f"\n{problems} issue(s) found.")
    return 1 if problems else 0


def cmd_doctor(services: dict[str, Service], service: str | None,
               as_json: bool) -> int:
    targets = [service] if service else sorted(services)
    results: dict[str, dict] = {}
    counts = {OK: 0, FAIL: 0, UNKNOWN: 0, PARTIAL: 0}

    for name in targets:
        svc = services.get(name)
        if not svc:
            results[name] = {"status": UNKNOWN,
                             "detail": "not present in live inventory",
                             "expected_negative": name in EXPECTED_NEGATIVE}
            counts[UNKNOWN] += 1
            continue
        entry: dict = {"lanes": {}}
        if svc.redundant():
            entry["false_redundancy"] = True
        for transport, lane in svc.lanes.items():
            r = doctor_lane(lane)
            entry["lanes"][transport] = {"binary": lane.name,
                                         "status": r.status,
                                         "detail": r.detail,
                                         "exit_code": r.exit_code,
                                         "duration_ms": r.duration_ms}
            counts[r.status] = counts.get(r.status, 0) + 1
        statuses = {v["status"] for v in entry["lanes"].values()}
        entry["status"] = (OK if statuses == {OK}
                           else FAIL if FAIL in statuses
                           else UNKNOWN)
        if svc.redundant():
            counts[PARTIAL] += 1
        results[name] = entry

    if as_json:
        print(json.dumps({"results": results, "counts": counts}, indent=2))
        return 0

    print(f"{'SERVICE':<28} {'LANE':<6} {'STATUS':<9} DETAIL")
    print("-" * 96)
    for name in sorted(results):
        entry = results[name]
        if "lanes" not in entry:
            print(f"{name:<28} {'-':<6} {entry['status']:<9} {entry['detail']}")
            continue
        for transport, v in sorted(entry["lanes"].items()):
            note = v["detail"][:56]
            print(f"{name:<28} {transport:<6} {v['status']:<9} {note}")
        if entry.get("false_redundancy"):
            print(f"{name:<28} {'':<6} {PARTIAL:<9} "
                  f"fallback is the same implementation as primary")
    print("-" * 96)
    print(f"OK {counts[OK]} · FAIL {counts[FAIL]} · UNKNOWN {counts[UNKNOWN]} "
          f"· FALSE-REDUNDANCY {counts[PARTIAL]}")
    print("UNKNOWN means the probe could not observe the service — it is NOT a failure.")
    return 1 if counts[FAIL] else 0


def cmd_prefer(services: dict[str, Service], service: str) -> int:
    """
    The load-bearing verb: print the binary to run, decided from LIVE auth state.
    Policy as a command beats policy as a prompt instruction.
    """
    svc = services.get(service)
    if not svc:
        print(f"unknown service: {service}", file=sys.stderr)
        return 2

    api = svc.lanes.get("api")
    cdp = svc.lanes.get("cdp")

    if api:
        r = doctor_lane(api)
        if r.status == OK:
            print(api.path)
            print(f"# lane=api ({r.detail})", file=sys.stderr)
            return 0
        reason = r.detail or r.status
    else:
        reason = "no API lane present"

    if cdp:
        if svc.redundant():
            print(f"# WARNING: fallback shares an implementation with primary "
                  f"— it is not independent", file=sys.stderr)
        r = doctor_lane(cdp)
        print(cdp.path)
        print(f"# lane=cdp (api unavailable: {reason})", file=sys.stderr)
        return 0

    print(f"no usable lane for {service} (api: {reason})", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dual-cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="services + which lanes exist")
    d = sub.add_parser("doctor", help="live health")
    d.add_argument("service", nargs="?")
    d.add_argument("--json", action="store_true")
    pr = sub.add_parser("prefer", help="print the binary to run")
    pr.add_argument("service")
    sub.add_parser("audit", help="duplicate / false-redundancy audit")

    args = p.parse_args(argv)

    # Rule 6: live scan, every invocation. No cached inventory.
    services = discover()

    if args.cmd == "list":
        return cmd_list(services)
    if args.cmd == "doctor":
        return cmd_doctor(services, args.service, args.json)
    if args.cmd == "prefer":
        return cmd_prefer(services, args.service)
    if args.cmd == "audit":
        return cmd_audit(services)
    return 2


if __name__ == "__main__":
    sys.exit(main())
