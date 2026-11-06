# INPUT v2 review — task 955

Reviewed only `/tmp/n955-input-neutral-v2-20261106`. Manifest SHA256 `950f6b337be8c497e1ab1926a20d344e6a741c0879845c1e2bf5784696058fe3` matches; base is `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`. No code, tests, installation, or other reviewer material was accessed.

## Critical

None.

## Important

None for the explicitly staged-source scope. The proposal now states the real lifetime boundary: flock protection applies only while a process retains the inherited descriptor, and a `close_fds` child can outlive that protection. It limits lock-file safety to a stable trusted directory/inode, requires `O_NOFOLLOW` and `fstat`, adds exact wrapper hash checks with mismatch-stop, and explicitly leaves installation blocked until actual child/UX boundaries are proved or covered by an independent orphan-work exclusion gate. An intentionally inheriting fake child is correctly insufficient evidence. Therefore staged source work can proceed, provided its output is described only as a source candidate and task 955 remains incomplete pending the stated installation acceptance.

## Minor

None.

## OUTPUT review checks

Verify that code and test claims stay within this contract, both FD-retaining and `close_fds` cases are demonstrated only with isolated synthetic processes, the rc-zero failed status retries, and wrapper hash mismatch prevents transformation/staging. Do not infer installed protection from synthetic children or from the staged manifest.
