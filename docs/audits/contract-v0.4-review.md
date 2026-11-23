# Contract v0.4: `fault_confirmed` reworded for experiment 008, and what it changes

Written 23 November 2026, when the change was numbered version 0.4. The wording itself dates from 21 November 2026, when experiment 008 was written up.

## The change

- **Section 3, `fault_confirmed`.**
  - **Before:** whether the fault is known to have fired after the commit.
  - **Now:** whether the fault fired where its fault point says.
  - The one function that decides this for every connection drop is `DropRecord.landed_as_intended`. The field checks the test setup, not the system under test, and never feeds a verdict.
- **Section 4.** The K2 row also mentions experiment 007's drop after the priming event. A new row lists experiment 008's two cuts, before the request and mid-stream before the commit, and says why they are not K1: no process is killed, and the client has already sent the call.

## What it changes in earlier results

**Nothing.** For experiments 001 to 007, every fault point is after the commit, so "fired where its fault point says" and "fired after the commit" mean the same thing. Every recorded value of `fault_confirmed` keeps its meaning. No verdict depends on the field. No results file was re-judged, because none could change.

## Not changed

- The verdicts, `classify`, and property RA with `report_matches_sink` (added in version 0.3).
