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

Revised upward in Step 2 to ~450 MB, once cropping moved into processing and the
device had to store frames with re-framing margin.

Sync to the phone takes roughly 80 seconds per flight. Because no video is
recorded — only frames — there is no H.264 encoding requirement, which removes
the single largest hardware constraint on the compute choice.

### Deliverables from the PoC

1. A working device that can be handed to a pilot who has not seen it before.
2. 12–14 graded flights with the grading sheets.
3. A written go/no-go against the six criteria above.
4. A bill of materials for a hundred-unit run.

---

## Step 2 — The optical bench test

### The decision this step makes

Which sensor, lens, preprocessing and delivery resolution go on each channel,
chosen by pointing candidates at a real panel and grading what comes back — not
by reading datasheets. Everything downstream (enclosure depth, mount geometry,
compute load, power, API cost) depends on this, so it comes before any hardware
is built.

**Not decided here:** compute board, enclosure, microphone, mounting hardware,
power. Those are later steps and several depend on this answer.

### Aiming is mechanical, not digital

The pilot aims the device by hand, through a ball joint, using a live view in the
app. There is no digital aim and no digital zoom on the device. Cropping happens
in processing.

Three consequences follow:

- **The app needs a live view.** Aiming by hand requires the pilot to see what the
  camera sees. This is the one place Perch shows video to a human, it is
  install-time only, and it is a firmware requirement nobody has costed yet.
- **The ball joint must hold aim under vibration for a whole flight.** Aim drift
  in degrees over a flight becomes a measured criterion, and the aim tolerance
  from this bench becomes the spec the joint is designed against.
- **The device must store more than it delivers**, or there is nothing left to
  re-crop. See storage below.

### Pixels on the gauge

The metric is not megapixels. It is **pixels across an instrument**, and the
arithmetic decides the whole optical design.

A standard 3⅛" instrument is 80 mm. At a 1.0 m mount distance through a 66° HFOV
lens, the frame covers 1.30 m of panel:

```
80 mm / 1300 mm × delivered width in px = pixels across the gauge
```

| | Delivered at 1024 px | Delivered at 1900 px |
|---|---|---|
| Full frame, no crop | 63 px | 117 px |
| Cropped to the cluster (0.68 m) | 120 px | 222 px |

Two independent things cap this, and neither is sensor resolution:

- **Lens resolving power.** A cheap lens on a 64 MP sensor gives 64 megapixels of
  blur. Past a point the glass is the limit and the extra pixels are empty.
- **Pixel pitch.** More pixels on the same die means smaller ones, which costs
  low light and dynamic range — exactly the two conditions that were already
  hardest. A 1/2.3" 12 MP with good glass will likely beat a 1/4" 48 MP.

One free win: **capture at full sensor resolution, always.** A frame every two
seconds means no H.264 encode, no sustained bandwidth and no thermal ceiling. A
camera product could not afford this; a debrief device can.

**The panel channel cannot cover a whole panel and stay readable.** It must be
aimed at the cluster that matters — the six-pack, or the PFD. That is what
"aimed correctly" means, and it is what the app's live view has to guide.

### Delivery resolution

The pipeline currently delivers panel frames to the model at 1024 px
(`[sample.panel] long_edge`). That was a cost choice, not a limit.

Claude Opus 5 and Sonnet 5 accept **2576 px on the long edge**, with an effective
budget of about **3.6 megapixels** (~4,800 image tokens), against 1.15 MP and
~1,570 tokens on earlier models. For a square-ish panel crop that is roughly
**1900 × 1900**. At 1024 we are using 29% of the pixels available to us.

The catch is cost: images bill at about (w × h) / 750 tokens, so 1024² → 1900² is
**3.4× the tokens on every panel frame** — roughly $0.024 per frame instead of
$0.007 at Opus 5 input pricing, or ~$12 instead of ~$3.50 over a 500-frame pass.

So the answer is not "turn it up", it is **turn it up where it matters**:

1. **Scan pass** — panel frames at 1024 across the whole flight. Its only job is
   to find the moments worth reading: a configuration change, a power change, a
   warning light, the approach.
2. **Read pass** — those moments only, re-sent at ~1900 for the actual numbers.

Most of a flight is a cruise where the panel does not change and resolution buys
nothing. This is a change to the `analyse` stage and can be built before any
hardware exists.

One caveat today: the `panel_aim` install check runs on the fast model
(`claude-haiku-4-5`), which predates high-resolution vision and is capped at
1568 px. Either accept that the aim check is coarser than the read — "is the
panel in frame and legible" is not a precision task — or run it on Sonnet 5.

### Preprocessing

The raw JPEG is not assumed to be the best input. Grade the ladder on the same
bench frames, at near-zero cost:

| | Preprocessing | What it tests |
|---|---|---|
| A | Raw JPEG | Baseline |
| B | CLAHE + unsharp mask | Local contrast, colour intact |
| C | B + highlight and glare recovery | The direct-sun case |
| D | Edge map composited *over* the image | Edges as an addition |
| E | Pure edge map | Edges as a replacement |

The reasoning for testing rather than assuming: edge extraction makes needles and
text pop, but it deletes colour — the green arc and red line on an ASI, a red
warning light, EFIS colour coding — and it pushes the image out of the
distribution vision models are trained on. Glare produces strong edges too, so it
can amplify the artefact it is meant to defeat. Local contrast enhancement gets
most of the benefit while keeping colour and staying in-distribution.

**Order matters:** crop → enhance → downsample. Downsample first and the pixels
are already gone; enhance before cropping and the histogram is dominated by a
bright windscreen the panel channel does not care about.

Whatever wins becomes a per-stream config knob — the panel gets it, the scene
probably does not.

### Candidates

Assuming a Pi-class CSI interface, per the dev rig BOM.

**Panel channel**

| Candidate | Why it is in |
|---|---|
| Pi Camera Module 3 (IMX708, 66° HFOV, AF) | The crop-from-wide baseline |
| Pi HQ Camera (IMX477) + 12 mm and 16 mm C-mount | Larger sensor, lens as a free variable |
| Arducam IMX519 16 MP AF | More pixels to crop from |

**Scene channel**

| Candidate | Why it is in |
|---|---|
| Pi Camera Module 3 Wide (IMX708, ~102° HFOV) | The default |
| Arducam IMX462 (Starvis, low light) | The dusk and night case |
| A 120°+ module | Whether extra coverage survives edge distortion |

About $300 for the set. Buy them all — a second procurement round costs more than
the parts.

### The bench

In the RV8, engine off, all candidates in one session so lighting is comparable.
Mount them on a single bar, same tripod point per condition.

Distances: **0.6 m, 0.9 m, 1.2 m** — the realistic span of roof, glareshield and
side-post mounts.

Four conditions, and the third is the one that kills designs:

1. Hangar shade — the easy case
2. Overcast outside — the normal case
3. **Direct sun through the canopy** — bright sky and dark panel in one frame
4. Dusk, panel lighting only

### Procedure

1. Capture stills from each camera at each distance in each condition.
2. Process each capture through the full matrix: 3 delivery resolutions
   (1024 / 1568 / 1900) × 5 preprocessing options.
3. Run them through the pipeline — `perch stage capability` for the aim check,
   then a panel read.
4. Grade against the real panel using the Step 1 tolerance table.
5. Record exposure behaviour separately: does auto-exposure hunt between sky and
   panel?

Grading the model's reads rather than measuring MTF is deliberate. The question
is not how sharp the lens is, it is whether the thing that has to read it, can.

### Pass criteria

| # | Criterion | Threshold |
|---|---|---|
| 1 | Panel readable | ≥90% of instruments legible by eye are read correctly, at 0.9 m, conditions 1–2 |
| 2 | Survives sun | ≥70% in condition 3 — degradation is acceptable, blindness is not |
| 3 | Scene coverage | From every candidate mount, the frame contains the pilot's hands, the panel area and the view forward |
| 4 | Exposure stable | No visible hunting between sky and panel across a 30 s capture |
| 5 | Aim tolerance measured | The angular error at which criterion 1 fails |
| 6 | Resolution knee found | The delivery resolution above which criterion 1 stops improving |

Criteria 5 and 6 are outputs, not gates. 5 is the number the ball joint and the
app's install check are designed against; 6 is what `long_edge` gets set to,
instead of a number picked by guess.

### Storage, revised

Cropping in processing means the device stores more than the 1024 px it
ultimately delivers. Storing full 12 MP frames every two seconds is about 12 GB
per flight and is not viable. The workable middle is a **generous crop on-device**
— region set once at install from the app's aim step, stored with margin so
processing can still re-frame within it.

At 1.5× linear margin (1536 px stored):

| Stream | Step 1 budget | Revised |
|---|---|---|
| Panel frames | 175 MB | ~390 MB |
| Scene, audio, telemetry | 63 MB | 63 MB |
| **Per 2-hour flight** | **~250 MB** | **~450 MB** |
| 10-flight buffer | 2.5 GB | 4.5 GB |
| Sync per flight | ~80 s | ~2.5 min |

A 256 GB card is about $20, so the card is not the constraint — sync time is, and
2.5 minutes while the pilot packs up is acceptable.

### Deliverables

1. A chosen sensor and lens for each channel, with the graded evidence.
2. The preprocessing option that wins, as a pipeline config.
3. The delivery resolution knee, as `[sample.panel] long_edge`.
4. Maximum usable mount distance.
5. Aim tolerance in degrees, feeding the ball joint spec and the app's install check.
6. Whether autofocus is needed, or fixed focus at the mount distance is more
   robust under vibration.

Roughly a week: a few days for parts, one session in the aircraft, a day grading.

### The risk worth naming

**Auto-exposure metering.** Sky through the windscreen is several stops brighter
than an unlit panel, and a single metering region will pick one and lose the
other. The two-sensor design is an advantage a single camera does not have — each
sensor can meter its own scene, the scene channel for the outside world and the
panel channel for the panel. Confirm that is actually configurable on the chosen
modules during the bench. If it is not, condition 3 fails and the design changes.

---
