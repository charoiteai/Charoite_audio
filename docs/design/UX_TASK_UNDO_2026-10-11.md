# Task status undo

## Problem and behavior

Marking a task completed writes directly to the meeting's Markdown file. The row can immediately disappear behind the Completed filter, leaving no visible way to correct a mistaken click.

After a successful status change, a persistent notice shows the task text and an explicit Undo status change button. It is outside the filtered list and works when the completed row has disappeared. Reopening a completed task has the same recovery path. There is one undo slot, replaced by the next successful status change; failed writes do not replace it. No timer dismisses the notice. State is shared across task views, kept in memory for this app session, and not a durable history. The control uses RU/EN/ZH labels and a keyboard-focusable button. It deliberately has no global Command-Z binding that could interfere with editing the search field.

## Storage guarantees and limits

- A change receipt is published only after a successful file write.
- Undo restores the exact original task line, including indentation and an uppercase X marker. Other lines are read from the current file and preserved, including unrelated external edits and inserted lines.
- Undo requires exactly one line matching the recorded changed line. A changed/deleted task or ambiguous duplicate causes a visible error and a rescan, with no write. Undo cannot recreate a deleted file.
- Task writes through the service are serialized, including different rows in one file. Row controls and undo are disabled during a write.
- Status changes affect only the regex-captured checkbox marker, preserving literal checkbox examples in the task text.
- A second snapshot read catches external changes before attempting the atomic write. This is not cross-process locking: another program can still write in the interval after that check. Exact-line matching also cannot establish durable identity if an external editor deletes and recreates an identical task. A durable identity or coordinated file store would be a separate change.
- Undo storage is one level. Redo and a multi-action undo stack are not implemented.

## Practices consulted

Apple recommends giving people ways to reverse actions, and NN/G's user control and freedom heuristic supports an obvious recovery path. NN/G's microinteraction article illustrates task completion feedback with an undo action. Our choice of a persistent single-action notice is specific to Charoite's file-backed tasks, not a prescribed timeout or layout from those sources.

- https://developer.apple.com/design/human-interface-guidelines/undo-and-redo
- https://www.nngroup.com/articles/user-control-and-freedom/
- https://www.nngroup.com/articles/microinteractions/

## Validation

Nine regression tests use temporary Markdown files. They cover exact-byte round trips, literal checkbox content, shifted rows and unrelated edits, edited task rejection, ambiguous duplicates, deleted files, repeated undo, real asynchronous service publication and serialized clicks, and preservation of the prior undo slot after a rejected write. Mac CI compiles the UI and runs the tests. Native graphical QA has not been performed in this Linux environment.

### Mac QA

1. Mark a task completed with Completed hidden; undo from the notice and verify the row returns.
2. Reopen a task with Completed shown; undo and verify the original completed state.
3. Change search/grouping/meeting filters before undo. The notice should still identify the affected task.
4. At the 640-point workspace minimum, test long Russian and English task names and VoiceOver/button keyboard navigation. Confirm the Undo button remains visible.
5. Edit another line in Obsidian before undo; that edit should remain. Edit the task itself or duplicate its completed line; undo should report a conflict instead of writing.
6. Click two rows rapidly, including rows in one file; verify only the accepted change is stored. Open a second task view and verify the shared undo notice reflects the same action.
