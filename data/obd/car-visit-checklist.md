# Pre-visit checklist - what must work at the next plug-in

Protocol (petrus, Aug 15): everything for a car visit is committed, deployed,
and rehearsed against tests/fake_elm327.py BEFORE the visit. This file holds
the current rehearsal status. No green here, no visit.

## Checklist and desk-rehearsal status (updated 2026-08-15 13:20 UTC)

1. **Adapter detected -> engine read posts to the room.**
   PASS (desk): posting path proven against a mock room API; key fix on the
   car verified by claudemm.
2. **Deep probe auto-fires on the update pass, only with adapter present.**
   Logic committed (stamp burns only when an adapter exists). Test lands in
   claudemm's probe self-test PR.
3. **Deep sweep end to end: launch -> PID sweep -> VIN -> raw fault frames
   -> mode-22 -> DONE, each step posting.**
   PASS (desk): full chain against the fake adapter, 5/5 posts received in
   order with correct content, twice (direct and update.sh launch shape).
   Fake coverage gaps -> claudemm's PR: 0902 VIN frames, mode-22 answers,
   multi-range 0120/0140 bitmaps, multi-module empty mode-03.
4. **Phantom-code regression: multi-module "no faults" decodes as ZERO.**
   PASS: unit tests (test_carwatch.py TestDtcDecode) + probe verdict wording
   verified in rehearsal output.
5. **Persistent results log always holds the data.**
   PARTIAL: log file is created at $HOME/.carwatch/obd-probe.log (survives
   power cuts, the Aug 15 loss cannot repeat) BUT a fully successful run
   leaves it empty - the probe only prints on post-failure. FINDING for the
   probe self-test PR: print every result line to stdout as well as posting,
   so the log is a complete record regardless of room reachability.
6. **Results committed to the repo.** OPEN: manual today; automate later.
7. **Undeskable caveat:** real-key posting from the real car can only be
   proven at the next actual plug-in. Say this out loud before any visit.
