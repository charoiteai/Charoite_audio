# Deep Audit — 2026-10-10

Branch: `audit/2026-10-10-code-review`  
Base: `main` @ `5149770552b2b49c8b48dec12ad44a8351028cbf`

## Executive summary

The codebase is **exceptionally well-hardened**. Previous audit rounds (explicitly referenced in comments as DS, GLM, Opus, Sonnet, DeepSeek, etc.) have already closed most classic failure modes around:

- Atomic graph writes (`safe_write`)
- Live-meeting priority (`live_gate` + flock)
- Privacy / cloud boundaries (`privacy.py` as single authority)
- Prompt-injection isolation for headless Claude calls
- iCloud / network FS quirks
- Resource contention (model leases, busy retries)

**No critical or high-severity bugs** were identified in the deep pass over the highest-risk modules.

## Modules examined in depth

| Module | Assessment | Notes |
|--------|------------|-------|
| `src/charoite_graph/safe_write.py` | Strong | Atomic tmp+replace, expect gates, symlink resolution, metadata carry-over, explicit avoidance of `copystat` time theft. Well-commented race windows. |
| `src/file_locks.py` + `live_gate.py` | Strong | Clear distinction BlockingIOError vs OSError (no-flock FS). Shared graph lock. Night caps. Finite-cap validation. |
| `src/privacy.py` | Strong | Single source of truth. Fail-closed defaults (`is True`). Kill-switch overrides everything. Address policy for remote LLM. |
| `src/cloud.py` | Strong | Text-only isolation flags, CLI probing with retries, effort defaults, proxy handling without leaking non-proxy keys. |
| `src/llm.py` | Strong | Centralized transport, busy-status retries, strict-JSON detection, fit-cache with TTL/sweep. |
| `src/graph_updater.py` | Good | Chunked extraction with overlap, live-meeting yield, health probe before work. `post_meeting_hook` with `shell=True` is intentional and documented. |
| `src/charoite_graph/graph_search.py` | Complex but careful | Hybrid BM25 + embeddings, RRF, honesty gate, chunking strategy. Large but heavily commented. |
| `SECURITY.md` / `PRIVACY.md` | Excellent | Threat model is explicit and matches the code. |

## Residual risks (design trade-offs, not bugs)

These are accepted risks that the project has already documented:

1. **`sufler.post_meeting_hook` (shell=True)**  
   Intentional feature. Documented in SECURITY.md as a trust boundary: anything that can write to the data folder can run a command with the app's TCC permissions. Mitigation is “leave the hook empty if you don't use it” + folder permissions (`0700`/`0600`).

2. **Broad `except Exception` in auxiliary loops**  
   By design so a single failure (name resolution, déjà vu, markup) does not kill the live meeting. Status is emitted on change. Risk: a new critical path could accidentally inherit this pattern.

3. **Daemon size and concurrency**  
   `daemon.py` is very large. Many concurrent loops share the model and transcript under locks. The priority rules (hint outranks thread, live outranks background) are explicit and tested, but the surface area remains high.

4. **iCloud Drive as graph location**  
   Many safeguards exist (atomic writes, copy_if_changed, no in-place overwrite). Still a non-POSIX environment; residual risk of delayed visibility or quota exhaustion remains.

5. **Decision gate is still shadow-only**  
   Correct approach (measure first). Until promoted, every “?” still wakes the model.

6. **Cloud sandbox for graph edits**  
   Cloud edits a copy; only verified changes are transferred. Residual risk is in the transfer/check logic itself (not re-audited line-by-line in this pass).

## Recommended focus for further review (Claude or human)

1. **`src/graph_updater.py`** — full read of the merge logic for multi-chunk extraction and the post-meeting hook invocation path.
2. **Cloud graph-edit transfer path** — the code that copies approved changes from the sandbox back into the real graph (search for sandbox / cloud_edit_graph handling).
3. **`src/daemon.py` concurrency** — specifically the interaction between the hint lock, thread lock, and live_gate under heavy load.
4. **Companion apps** (`app-ios/`, `app-android/`) — recording queue, crash recovery, and graph-file writes (they share the same markdown files).
5. **Nightly dossier revision** — incremental rebuild + cloud edit path under the graph lock.

## Conclusion

The project shows the marks of repeated, serious external and internal review. The residual risk is mostly complexity and accepted design trade-offs rather than obvious defects. Safe to continue development; the items above are the highest-value places for the next deep pass.

---
*Deep audit performed via GitHub API. Intended for review with Claude.*
