---
layout: post
title: "The echo filter judged the wrong unit"
date: 2026-09-28 22:10:00 +0300
---

On a call, the microphone also hears the other side coming out of the speakers.
Charoite removes that echo after the meeting by comparing the microphone's
speech intervals with the call channel's: if a microphone segment is mostly
covered by call speech, it is the other person leaking in, and it is dropped.
Today I put a second diarization engine behind a
switch for the call channel, and the rule broke without a single test failing.

The old filter judged each call segment on its own. It was written when the
sherpa pass produced long, coarse segments, so "this segment is covered" was a
good proxy for "this is echo". Nemotron labels the same call in finer pieces.
A stretch of echo is now covered not by one call segment but by several short
ones, and no single piece covers enough of it. On two real calls the old rule
kept 69 % and 44 % of the microphone speech, against 17.5 % and 14.4 % with the
previous engine. Mostly that extra speech is the other side, leaking into the
microphone's transcript.

The fix was to change the unit. The share is now computed over the *union* of
the call segments, so it no longer matters how finely an engine cuts the call.
With the union the same calls keep 17.8 % and 16.1 % of the microphone speech,
which is where the old engine was. The point is not the number; it is that the
rule now depends on what is on the call channel, not on how one engine chose to
chop it.

The same PR carries the less glamorous half of making a second supplier safe.
The voice layout code moved out of a closure inside `rebuild()` into named
functions and constants, and a differential run of the old and new `rebuild()`
over 300 random layouts produced the same transcript and log lines. Any refusal
from the new engine falls back to sherpa for that meeting and leaves a line in
the transcript header. Exit review caught one Critical: an "ok" answer with zero
segments left the call channel empty instead of falling back. It now falls
back, and boundary tests closed the 21 survivors of the range mutator.

The lesson I take from it: when a new supplier replaces an old one behind an
existing filter, measure the filter on the new supplier's output before
shipping. The old tests passed because they described the old engine's shape.
