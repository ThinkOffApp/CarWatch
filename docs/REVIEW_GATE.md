# CarWatch review gate (blocking)

Today (Aug 12 2026) a change shipped that worked at home and failed the moment
the Pi went to the car: hardcoded location, an unvalidated OBD path, and
hand-copied deploys. Diff review caught none of it, because these are
product-level failures, not line bugs. @codexmb proposed this gate and it is
now binding: a PR that touches car behaviour does not merge until every box is
honestly checked or explicitly waived in the PR with a reason.

## Blocking checklist

- [ ] **No hardcoded state that the device should sense.** Location, network,
      engine, temperature, etc. are READ at runtime, never stubbed with a
      literal. A placeholder string for a real-world value is an automatic
      block. (What bit us: `location = "on Petrus desk"` on a device whose
      nature is to move.)
- [ ] **Car-specific behaviour is validated on/against the car**, not only at
      home. If it cannot be, the PR says so in one line at the TOP and is
      labelled `unverified-on-car`; it may merge as code but MUST NOT be
      described to petrus as working. A receipt (probe output, a real reading)
      is required before it is called ready.
- [ ] **Real deployment/update path**, not a manual copy. If a human has to
      rsync/scp/paste to make it live, that is a hack; the product path is the
      self-update mechanism (`update.sh` + `carwatch-update.timer`).
- [ ] **Reachable the way the user actually reaches it.** If it only works on
      one LAN / behind NAT / from the author's machine, that limitation is
      stated in the PR, loudly, so nobody plans a trip around it.
- [ ] **Stopgaps are labelled as stopgaps.** Any interim/demo-only behaviour
      is flagged in the PR and in what we tell petrus, with exactly what it
      will and will NOT do. petrus must never discover a limitation by hitting
      it.

## Why these five

They are the exact failure modes from Aug 12, turned into a gate so the same
class cannot ship silently again. "Build the product, not workarounds" is the
standing instruction; this is that instruction made checkable before merge.
