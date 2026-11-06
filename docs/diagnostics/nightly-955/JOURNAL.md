# Retry adapter work log

- 2026-11-06: read task955, prior boundary66a7e33 and allowed947 metadata.
  Confirmed wrapper hash by an allowlisted local source lookup; no marker/status
  or graph read. Existing public night_gate already retries non-success.
- Created isolated base2106ed52 worktree. INPUTv1/v2 reviewed independently.
  Narrowed source contract around the real descriptor-retention limitation.
- Built process-lock and hash-bound private staging candidate. The installed
  source remained unchanged. First narrow execution stopped at quiet_guard3.
- OUTPUT source review C0 from both models identified inadequate before/after
  and staging tests. Added the actual public shell fixture and integration tests.
- Found UX would otherwise precede final-success admission. One bounded amendment
  review; implemented an advisory pre-UX probe while preserving the final source
  gate/telemetry/brief. No additional gate policy or dispatcher was introduced.
- Second narrow execution also stopped at quiet_guard3. No local test or mutant
  result is green. Static AST/JSON/privacy checks and layout source regeneration
  completed; these did not execute product/nightly code or synthetic processes.
- Moved tested logic into runtime modules so the existing critical-mutation CI
  can judge it; manual CLI scripts are outside that product mutation area.
- Next: commit reviewed bounded source, run normal CI at that exact SHA, inspect
  unit/mutation receipts. Private exact-wrapper proof and installed FD/orphan
  boundaries remain separate gated acceptance; do not install or close955.

## Follow-up after first CI

CI42204830475 on358efecc failed only the private-entry umask inventory test
(Linux11126passed, macOS11138passed). Both new CLIs now call the existing
harden_umask before main; no exemption was added. Under a fresh quiet/heavy
gate, the narrow regression and new synthetic cases passed55tests/5subtests
in4.82seconds. A real child started from mask022 produced a600file. Runtime
modules are imported directly by their tests so mutation selection remains
addressed. Source hashes and command are in targeted-result.json. Full CI on
the new commit and PR-event critical mutations remain pending; branch-push CI
skips mutation jobs. Installation/private full-wrapper/FD-chain proof is still
not complete. These new results supersede the earlier local-NOTRUN statement
for the public synthetic tests only.
