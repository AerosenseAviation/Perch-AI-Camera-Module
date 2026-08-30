# Perch — Proof of Concept Build Manual

A step-by-step guide to getting a physical Perch prototype into an aircraft and
producing debriefs that are good enough to decide whether the product is real.

This is a build manual, not a product spec. It assumes nothing exists yet except
the software in this repository.

---

## Step 1 — The charter

### What Perch is

A fixed-mount device in the cockpit with two camera sensors and a microphone. It
does not record video for the pilot. It captures frames and audio purely as
context for an AI, and produces a written debrief after the flight.

The pilot never watches the footage. If they want footage, they have a GoPro.
Perch is a debrief device that happens to use cameras.

### The thesis being tested

> A camera and a microphone in a cockpit, mounted wherever it physically fits,
> produce a post-flight debrief that helps the pilot fly better and safer — and
> the value compounds across flights, because the system remembers what this
> pilot usually does.

Read that carefully, because it sets what does and does not gate the PoC.

**It is not gated on reading instruments.** Airspeed, RPM, manifold pressure and
engine parameters are a bonus. When the narrow sensor can see the panel, the
debrief gets sharper and more specific. When it cannot — bad mount, night, glare,
a Huey — the debrief must still be worth reading. If Perch is only useful when
it can read a panel, it is a dashcam with OCR, and that is a different, worse
product.

**It is gated on cross-flight memory.** "You usually do X, and this time I didn't
see it" is the sentence that makes this smart rather than merely novel. A
single-flight debrief is a party trick. The tenth-flight debrief is the product.

**It is gated on being useful from any mount.** Roof, glareshield, side post,
behind the seats. The mount changes what Perch sees. It must not change whether
Perch is worth having.

### What Perch does not do

- No FOQA, no exceedance reporting, no instruction, no certification claims.
- No precise geometry: no glidepath angles, no bank angles from a camera image.
- No pilot voice, no radio audio. The microphone is for engine and airframe
  acoustics only — power changes, rough running, gear and flap noise, stall
  warning horn, the sound of a touchdown. This is a deliberate scope limit, not
  a technical one, and it survives to production.
- No GPS. The device mounts inside a metal or composite fuselage with no sky
  view. A flaky GPS is worse than none, because a fix that comes and goes
  licenses the AI to invent numbers between the gaps.

### Sensors on the PoC device

| Sensor | Purpose | Notes |
|---|---|---|
| Wide "scene" camera | The whole cockpit and what is out the window | The primary input. Everything falls back to this. |
| Narrow "panel" camera | Instruments only | Bonus channel. Aimed at install time; fails closed if the aim check fails. |
| Microphone | Engine and acoustic events | Never pilot voice or radio. |
| IMU | Attitude, g, turn rate, vibration | Also drives motion detection and touchdown. |
| Barometer | Altitude, climb and descent | Drives flight phase segmentation with no GPS. |

### Exit criteria

The PoC passes if all six hold across the test flights.

**1. Capture completeness ≥ 95%.**
Of the wall-clock time the aircraft was moving, at least 95% is represented by
frames, audio and telemetry on the card. A device that stops recording halfway
through a flight fails everything downstream, so this is measured first and
separately.

**2. The debrief is useful ≥ 50% of the time, regardless of mount.**
Every observation in every debrief is graded `useful` / `neutral` / `wrong` by a
pilot who flew that flight. At least half must be `useful`. This is measured
per-mount as well as overall: if the roof mount scores 70% and the side post
scores 20%, the product is mount-dependent and that is a finding.

**3. Longitudinal claims ≥ 80% verified true on flights 5–8.**
Once a baseline exists, the system starts making cross-flight claims. Each one is
checked against the earlier footage. At least four in five must be true. This is
the criterion that actually tests the thesis.

**4. Zero false omissions. Binary fail.**
If Perch says the pilot did not do something and they did, the PoC has failed
that flight outright, no matter how good the rest of the debrief was. One false
"you forgot to check the fuel selector" destroys trust permanently and correctly.
See the omission rule below.

**5. Instruments ≤ 10% wrong — measured, not gating.**
When the debrief states a number, it is checked against the panel. Tolerance:

| Quantity | Tolerance |
|---|---|
| Airspeed | ±5 kt |
| Altitude | ±100 ft |
| RPM | ±100 rpm |
| Manifold pressure | ±1 inHg |
| Angles (flap, attitude indicator) | ±10° |

A number inside tolerance that supports a **true** observation is `useful`. A
number supporting a **false** observation is `wrong`, however accurate the digits
were. Fewer than 10% of stated numbers may be `wrong`. Missing this criterion is
a finding to fix, not a reason to kill the product — see the thesis.

**6. It survives the environment.**
Nothing comes loose, nothing overheats to shutdown, nothing corrupts the card on
power loss, and the mount does not become a hazard. Recorded per flight as pass
or fail with notes.

### The omission rule

Perch never claims an omission. It reports an absence of evidence.

- Not allowed: *"You forgot the pre-landing check."*
- Allowed: *"I didn't see the pre-landing flow on this flight, though it's on the
  last four."*

The difference is not politeness. Perch sees a fraction of the cockpit at a
sample interval; a check it did not observe may well have happened off-frame or
between frames. Claiming an omission asserts knowledge the device does not have,
and criterion 4 exists to enforce this.

The word "usually" is not permitted until there are at least **four prior
flights** in that aircraft with that pilot. Below four, there is no baseline —
only a small number of anecdotes.

### Study design

Two kinds of test flight, answering two different questions.

**Longitudinal (depth) — the primary study.**
Same aircraft, same pilot, repeat flights. This is the only way to build a
baseline, and therefore the only way to test criterion 3 at all.

- Aircraft: **RV8**
- Flights: **6–8**
- Mount: fixed for the whole series, so what changes between flights is the
  flying, not the install.

**Breadth (spread) — the install sanity check.**
One flight each in as many different cockpits and mount positions as can be
arranged. Tests that the device and the debrief hold up somewhere other than the
aircraft they were tuned in.

- Aircraft: **Huey**, **Stearman**, plus **2–3 side-by-side** types from friends
- Flights: **1 each**, ~5 flights total
- Mount: whatever physically works in that aircraft, documented per flight.

Roughly 12–14 flights in total. Note that the RV8, Huey and Stearman are all
tandem; the borrowed side-by-sides are the only test of a cockpit where two
people sit abreast, which changes both what a roof mount sees and where a device
can go at all.

### Storage and sync

The device buffers **10 flights or 80% of the card, whichever comes first**.

Perch never refuses to record. When the buffer is full it drops the oldest
unsynced flight and tells the pilot it did so. A device that declines to start
because its card is full is a device that misses the flight the pilot most wanted
debriefed.

Budget per 2-hour flight, approximately:

| Stream | Size |
|---|---|
| Panel frames | 175 MB |
| Scene frames | 32 MB |
| Audio (Opus) | 29 MB |
| Telemetry | 2 MB |
| **Total** | **~250 MB** |

Sync to the phone takes roughly 80 seconds per flight. Because no video is
recorded — only frames — there is no H.264 encoding requirement, which removes
the single largest hardware constraint on the compute choice.

### Deliverables from the PoC

1. A working device that can be handed to a pilot who has not seen it before.
2. 12–14 graded flights with the grading sheets.
3. A written go/no-go against the six criteria above.
4. A bill of materials for a hundred-unit run.

---
