---
layout: post
title: "Nearly perfect on synthetic audio, 0.6 on real meetings"
date: 2026-09-28 18:40:00 +0300
---

The first run of Nemotron 3 Diarization with real weights on Apple Silicon
(an M1 Max) landed today, and the docs no longer say "no numbers yet". No
behaviour changed in that PR; it is only measurements, and the measurements
disagree with each other in an instructive way.

On two real meetings with a reference (AMI ES2004a and IS1009a, mixed headset,
no collar) the model over the whole file gets a diarization error rate of 0.269
and 0.277, finds 4 of 4 voices, and runs at a real-time factor of 0.003. The
streaming mode is about the same on accuracy (0.273 on both) at a factor of
0.04. Then the same audio goes through our production post-meeting pass, which
slices it the way the daemon does: 0.606 and 0.453, with voice confusion jumping
from 0.004 and 0.071 to 0.393 and 0.266. Same model, same recordings, and the error
more than doubles on the first meeting. The difference is the seams: 64–74 % of
the gaps between pieces of one voice are exact chunk cuts, and with a zero gap
the streaming pass matches the whole-file pass. The model is not the weak link;
our slicing is.

The second lesson is about fixtures. On a synthetic fixture with crosstalk the
model is nearly perfect, an error rate of 0.039. Remove the overlaps and all
four macOS text-to-speech voices collapse into one slot, 0.628. Length is not
the cause; I checked. Synthetic speech without overlap is simply a kind of audio
the model has never needed to separate, and a green number on it would have told
me nothing about meetings.

Two smaller facts. First, nothing touches the disk: under a sandbox that denies
writes the whole path completes, the only write attempt is a symlink probe from
`filelock` at import time, and there is no network. Second, I got the durations wrong in the
first version of the docs. I wrote that the echo measurement was a 15-minute
call and that the 41-minute call channel took 6 seconds. A follow-up fixed it
the same day: the call was 8 minutes (474 s), and the 41-minute channel takes
5 seconds including process start and model load. Measured set: 8 minutes in
2 s, 20 minutes in 3 s, 41 minutes in 5 s, against a real-time factor of 0.35
for the sherpa pass we run today.

So the engine is fast enough that speed is not the question. The question is
how its answer is cut up afterwards, and that is the next entry.
