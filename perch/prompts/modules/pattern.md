# Module: pattern

Report the shape the aircraft flew over the ground.

Look for:

- The shape of the circuit or the track: whether the legs look square, whether
  the downwind is displaced in or out, whether the base turn is early or late.
- Consistency between one circuit and the next when there is more than one.
- Turns: whether they are one continuous turn or a turn in stages, and whether
  the roll-out settles on a heading or drifts through it.
- Ground features the aircraft tracks along or over, and whether it holds them.
- Drift with the wind: a track that crabs, a leg that bows.

If position telemetry is supplied, you may use the positions and ground speeds
in it, and you must mark those observations `telemetry`. **The Perch rig has no
GPS**, so usually there is none: judge shape from what the ground does in the
frames and mark those observations `visual` or `inferred`. Never state a leg
length in distance units, or a groundspeed, unless position telemetry is in this
prompt. Bank angle and turn rate may come from the rig's motion sensor when that
is supplied.

Be honest that a circuit shape inferred from the view forward is a rough
impression. If you cannot tell, say so rather than describing a circuit you did
not see.

A tidy circuit is worth saying so. So is a leg flown a long way out, or a turn
that comes round beautifully.
