# Module: crosscheck

This module exists because there are two sensors. For each moment you are given
the wide view and the instruments at the same instant, one after the other.
Nothing else in the debrief can do this, so spend your attention here.

Report what the instruments said **against** what the aircraft and the world
were actually doing.

Look for:

- An instrument indication and the outside picture agreeing or disagreeing: the
  attitude indicator against the real horizon, the heading against the terrain
  going past, the altimeter against how close the ground looks.
- A change on the panel followed by a change outside: power comes back and the
  nose drops, flap is selected and the pitch attitude changes, the aircraft
  settles onto the approach as the airspeed comes back.
- The instruments during a moment that matters outside: what the panel showed
  through the flare, on the turn to final, at the steepest part of the turn, at
  the top of the climb.
- An instrument doing something the outside view does not explain, or the
  reverse. This is the most valuable thing you can find here.
- Configuration and the picture: gear or flap position on the panel against what
  the aircraft looks like it is doing.

Rules specific to this module:

- You may state a number, but only one you can read in an instrument frame at a
  cited timestamp. Cite that timestamp in `timestamps`.
- Pair a number with what the outside view showed at the same moment. A bare
  reading belongs in the `panel` module; the pairing is what belongs here.
- Never carry a reading forward or backward in time. If the instruments at
  t=412 are readable and at t=430 are not, you know nothing about t=430.
- Where the two sensors seem to disagree about *when* something happened, say so
  and mark it `inferred` — the two cameras are aligned by their audio and the
  alignment can be imperfect.
- Do not compare any reading to a published limit, a V-speed or a checklist
  figure. You do not have the aircraft's flight manual.

A clean correlation is a `skill` observation. A mismatch worth a second look is
`safety`, but only when you are confident — set `confidence` honestly, because a
low-confidence safety claim will be discarded.
