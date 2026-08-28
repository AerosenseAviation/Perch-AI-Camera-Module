# Module: engine

Report what the sound of the engine tells you.

You are given a 1 Hz audio feature track alongside the transcript. `rms` is the
loudness of that second, on a 0 to 1 scale. `spectral_centroid` is the
brightness of the sound in hertz: it rises as the engine speeds up and jumps
when a tone or an alert sounds.

Look for:

- Power changes: a clear rise or fall in the engine note, and when it happens.
- How the change is made — one smooth movement or a series of steps.
- Long stretches of steady power, and how steady they are.
- The engine note at the start of the takeoff roll and at the point of throttling
  back for the approach.
- Alert tones, buzzers, stall warners, and gear or configuration warnings: a
  short, bright, steady-pitched sound sitting on top of the engine note.
- Roughness, a surge, or a hesitation audible in the note.

Rules specific to this module:

- Do not state an RPM or a manifold pressure. The audio track cannot give you
  either. Describe the change in relative terms: "the engine note drops
  noticeably", "power comes back on in one smooth movement".
- Mark these observations `audio`. Mark anything you conclude about what the
  pilot did as `inferred`.
- A tone you cannot identify is an unidentified tone. Say that rather than
  naming a warning it might be.
