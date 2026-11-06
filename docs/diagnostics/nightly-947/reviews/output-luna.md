# OUTPUT / finalrisk review — №947

- Reviewer: `gpt-6-luna` (independent static review; no execution or model/API calls).
- Source base: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`.
- Before source: `f0a3ce57568efb5e2246fd25e627fa76114e152f`.
- Packet: `/tmp/charoite947-output-packet`, 27 files; `manifest.json` SHA256 `b7a73b576ca6e24e2d24cc0a0b6248efa2ffac1453b08903f8d84176cde44a0d` (verified).
- Product changes: 0.

## Critical

None.

## Important

- `diagnostics/causal-control.py:87-94` — **The paired receipts do not themselves prove the source trees were clean.** `source_sha` records only `git rev-parse HEAD`, with no expected-SHA assertion or dirty-tree check. The hash list also omits `src/dossier_incremental.py`, which `nightly_dossier.py:145` invokes for the measured generation boundary. If either worktree had a local edit to that module, the recorded commit and listed dependency hashes could still look bound while the measured refusal/admission behavior came from unrecorded code. This weakens attribution of the before/after synthetic delta, though the report correctly limits it to the combined source/admission change and does not claim it caused the real incident. **Minimal fix:** have the control fail unless HEAD equals the expected SHA and the worktree/index are clean; include hashes for all imported behavior-bearing modules, especially `src/dossier_incremental.py`, in both receipts. Alternatively retain equivalent reproducible cleanliness and full dependency-hash receipts with the packet.

## Minor

None.

## Scope and conclusion

The synthetic cases and 213-test result are described with appropriate limits: the adapter is a double, there was no owner graph scan/publication or real model request, and the actual incident cause remains unknown. The before/after table and prompt-size change support a combined full-source/admission boundary delta, not a guard-only causal claim. The control replaces the adapter before generation and prohibits socket connections, so I found no path in the documented synthetic run that could issue a real model/API request. The report preserves the failed first test invocation and its corrected rerun transparently. №947 should remain open; this review does not support declaring the incident cause established or the root card complete.
