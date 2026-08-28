You are labelling the phase of flight shown in individual frames from one
flight. Each frame is preceded by its timestamp in seconds.

Use exactly one of these labels per frame:

- `ground` — stationary on the ground, engine running or not.
- `taxi` — moving on the ground at walking or running pace.
- `takeoff` — the takeoff roll and the first moments after leaving the ground.
- `climb` — climbing away with the ground receding.
- `cruise` — steady flight, no obvious climb, descent or manoeuvring.
- `manoeuvre` — deliberate turning, wingovers, stalls, aerobatics, steep turns.
- `circuit` — flying a pattern near an airfield at low level.
- `approach` — descending toward a landing surface, runway ahead or below.
- `landing` — the flare, touchdown and rollout.
- `shutdown` — stationary after the flight, propeller stopping or stopped.

Judge each frame on what it shows. Height above ground, the size of features on
the ground, the attitude of the horizon, and whether a runway is ahead are the
useful cues. When a frame is genuinely ambiguous, pick the label that fits the
frames around it in time; a flight moves through these phases in a sensible
order.

Return one label for every frame you were given. Echo each timestamp exactly as
it appears in the prompt.
