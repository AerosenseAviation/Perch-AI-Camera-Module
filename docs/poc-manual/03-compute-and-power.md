# Step 3 — Compute and power

## The decision this step makes

What runs the device, what feeds it, and what happens when it loses power
mid-flight. This is the step that turns two chosen sensors into a thing that can
be switched on in an aircraft and left alone for two hours.

**Not decided here:** enclosure, mount hardware, the production board. The
production device is almost certainly not what the PoC runs on, and designing it
now would be guessing ahead of the measurements this step produces.

## Selection on the device, inference off it

The line is not "the device does no analysis". It is drawn by what each side
costs in watts and in API spend, and it lands in a specific place: **the device
decides which moments are worth keeping. It never decides what they mean.**

Three architectures were considered.

**A — Capture everything at a fixed interval, decide nothing.** A 2-hour flight
is ~3,600 panel frames. Everything is stored, everything syncs, and a cheap scan
pass at 1024 px then finds the moments worth reading at full resolution.

**B — Deterministic selection on the device.** Frame-to-frame difference on a
downscaled panel crop, plus IMU triggers (attitude and g excursions, the vertical
spike of a touchdown, altitude rate changes) and audio triggers (power changes,
gear and flap, alert tones). Only moments that changed are kept at full quality.

**C — A vision model on the device.** Classify or read the panel locally, upload
conclusions.

**C is rejected, and not narrowly.** The Pi 5 has no NPU, so a model runs on CPU,
and CPU is exactly the budget we cannot spend — every watt burnt is battery gone
and heat added in a box we are already struggling to cool. It also duplicates,
worse, what the frontier model does well. On-device ML is a production
optimisation for a device with silicon designed for it, not a PoC decision.

**B wins over A**, and the interesting part is that it wins on all three axes:

| | A: capture everything | B: select on device |
|---|---|---|
| Frames stored per flight | ~3,600 | ~900 |
| Storage per flight | ~390 MB | ~110 MB |
| Sync time | ~2.5 min | ~45 s |
| Frames sent to the model | 500 scan @ 1024 + ~100 read @ 1900 | ~250 read @ 1900 |
| API cost per flight, panel only | ~$5.90 | ~$6.00 |
| Frames encoded on device | 3,600 | ~900 |

API cost comes out roughly level — B sends fewer frames but every one at full
resolution, so the two effects cancel. What B actually buys is that **every frame
sent is a frame worth sending**, the scan pass disappears as a moving part, sync
drops to well under a minute, and two thirds of the full-resolution JPEG encoding
never happens. That last one is the battery argument: encoding a 12 MP frame is
the single most expensive thing the CPU does in flight, and B does it a third as
often.

The selection itself is nearly free. A mean-absolute-difference on a downscaled
greyscale crop is microseconds, and the IMU and audio triggers are threshold
comparisons on data we are already reading. This is classical signal processing,
not inference — deterministic, debuggable, and cheap enough to be invisible in
the power budget.

**One rule constrains it, and it comes from Step 1.** The device must never
create a blind window. Selection raises the sampling rate at interesting moments;
it must never drop below a **guaranteed baseline of one frame every 10 seconds**,
whatever the difference metric says. Criterion 4 of the charter is zero false
omissions, and the omission rule only holds if the pipeline can distinguish "the
pilot did not do this" from "the device was not looking". A device that discards
a quiet stretch entirely destroys that distinction. Boring stretches get sampled
sparsely. They are never skipped.

Everything past selection — phases, capability, analysis, composition — runs
off-device from the synced flight. The device's contract is to produce a
directory of frames, audio, telemetry and an index. Nothing more.

## Power: a battery, not the aircraft

**The PoC is self-powered.** It does not connect to the aircraft's electrical
system at all.

The engineering reason is that aircraft power is hostile: a 14 V bus sags hard
during cranking, throws transients on load changes, and needs a wide-input
converter with proper transient protection to feed anything sensitive. That is a
solvable problem and it belongs in the production design.

The practical reason is stronger and it decides the matter now. The RV8 is
amateur-built and wiring into it is the owner's call. The Huey and the Stearman
are not, and the Stearman may have close to no electrical system to tap. Asking
an owner to let you into their aircraft's wiring to test a prototype is the
fastest way to lose access to the aircraft. A device that arrives, sticks on,
switches on and comes off again is a device people will let you fly.

So: a USB-C power bank, chosen after the draw is measured rather than before.
A working estimate is 5–8 W average, which over a 3-hour endurance target is
15–25 Wh — a 20,000 mAh bank is roughly 74 Wh and leaves large margin. Measure
first, then size down.

The battery earns its place twice. It is also the UPS — see below.

### Where the watts go

Estimates to be replaced by measurements. The point of the table is that the two
biggest levers are things we choose, not things the board imposes.

| Item | Estimate |
|---|---|
| Pi 5, underclocked, capture and selection | 4–6 W |
| Two camera sensors | 0.5–1 W |
| IMU, barometer, microphone | <0.1 W |
| Wifi — **off in flight**, on for sync only | 0 W / ~1 W |
| Fan — thermostatic, duty-cycled | 0–1 W |
| **In flight** | **~5–7 W** |

Three levers, in order of size:

- **Wifi off in flight.** The access point is for aiming and for sync, and both
  happen on the ground. Leaving it up costs around a sixth of the total budget
  for nothing.
- **Underclock.** The workload is light and the deadline is a frame every two
  seconds. Clock speed buys us nothing and costs watts and heat.
- **Encode fewer frames**, which is what on-device selection already does.

At 6 W, a 3-hour target needs 18 Wh. A 20,000 mAh bank is ~74 Wh nominal and well
over 50 Wh usable — several times the margin needed. Once the real draw is
measured, a 10,000 mAh bank is likely enough and halves the weight, which matters
for a device hanging off a ball joint.


## The board

Two full-resolution camera sensors running simultaneously is the requirement that
picks the board, and it eliminates most of the field immediately: a Pi 4 or a Pi
Zero 2 W has one CSI connector. A Pi 5 has two.

| Board | Verdict |
|---|---|
| **Raspberry Pi 5 (8 GB)** | The PoC choice. Two CSI lanes, mature libcamera support for dual sensors, built-in 5 GHz wifi, headroom to spare. |
| Compute Module 5 + carrier | The production path, and the natural step once the PoC has produced the measurements a board should be designed against. |
| Radxa / Orange Pi and similar | Some have two CSI connectors, but dual-camera software support is where these boards are weakest. Wrong risk to take on the critical path. |
| ESP32-class | Far too weak for two full-resolution sensors. |

The Pi 5 is a development platform, not a commitment. The step after the PoC is
our own PCB — either a carrier that a Compute Module 5 drops into, or a base
board that takes a Pi 5 directly and adds the sensors, power and connectors as
one part instead of seven. Either way that board gets designed from the measured
power, thermal and capture-rate numbers this step produces, which is the whole
reason for measuring them.

Note what removed the hardest constraint: **there is no video encoding.** The Pi
5 dropped the hardware H.264 encoder its predecessors had, which would have been
disqualifying for a camera product. Capturing a still every two or three seconds
does not touch it.

The rate is still worth measuring rather than assuming. A full-resolution still
through libcamera is not instant, and two sensors at their target intervals for
two hours with nothing dropped is a criterion, not a given.

## The other sensors

| Part | Choice | Why |
|---|---|---|
| IMU | BNO085 | On-chip sensor fusion. It outputs attitude directly, so there is no fusion filter to write, tune or get subtly wrong. |
| Barometer | BMP390 | Better altitude resolution than a BMP280, ~$12, and altitude is what the segmenter runs on now that there is no GPS. |
| Microphone | I²S MEMS (ICS-43434 or SPH0645) | Digital straight into the board, no analog noise pickup, no USB audio stack. Engine and acoustic events only — no pilot voice, no radio. |

All three are I²C or I²S, all three are a few dollars, and none of them needs a
driver written from scratch.

## Losing power mid-flight

The classic Raspberry Pi failure is a corrupted card from a write interrupted by
a power cut, and it does not announce itself — the device seems fine until the
flight you needed is unreadable.

Four defences, in order of how much they buy:

1. **The battery is the UPS.** Self-powering already removes the master-switch
   case entirely, which is the main way this happens in an aircraft.
2. **Read-only root.** The operating system on an overlay, writes confined to a
   separate data partition. A cut cannot corrupt a filesystem nothing is writing
   to.
3. **One file per frame, fsynced.** No container format, no single large file
   holding a whole flight. A cut mid-write costs exactly one frame.
4. **An append-only manifest.** The index of what was captured is appended and
   flushed as it goes, never rewritten. A truncated manifest is still a valid
   manifest of everything before the cut.

Together these mean the worst case of an abrupt power loss is one lost frame, and
that is what criterion 3 tests — deliberately and repeatedly.

The pilot still needs a way to switch it off cleanly: a physical button that
triggers a proper shutdown, and an LED that says when it is safe.

## Thermal

**This is the risk most likely to force a board change**, so test it first,
before anything else in this step.

The mount is the roof or a side post, not a glareshield, so the device is not
sitting in the worst of the greenhouse. That helps, and it is not a solution:
a canopy puts sun on most of a cockpit at some point in most flights, and ground
time before start is the hottest part of the day with no airflow at all.

Throttling is the failure mode, and it is quiet. A Pi 5 starts backing off in the
low 80s °C, and cockpit ambient can pass 50 °C on the ground. It does not crash —
it drops the capture rate, and the first evidence is a completeness number below
95% after a flight that cannot be repeated.

The order to attack it, cheapest first:

1. **A light-coloured enclosure.** Solar gain on a white or pale grey box is
   dramatically lower than on a black one, it costs nothing, and it is the single
   highest-value thermal decision available. It goes in the Step 4 spec as a
   requirement, not a preference.
2. **Aluminium as the heatsink.** The chassis conducts heat out rather than
   trapping it. No power, no moving parts.
3. **Underclock.** Already in the power budget, and it cuts heat at source.
   Cheaper than removing heat afterwards.
4. **A thermostatic fan.** Off below a threshold, so it costs nothing on a cool
   day and only draws current when it is actually earning it.

Note the loop this closes, and why the order matters: **active cooling costs
power, and power is battery.** Every watt of fan is a watt off endurance and, on
a hot day, a watt that also has to be carried. That is the argument for spending
the effort on colour, conduction and clock speed first, and treating the fan as
the last resort rather than the design.

Test it in a closed box in the sun running the real workload, with core
temperature logged for two hours. Not on a bench in an air-conditioned room.

## Getting the flight off the device

The device runs its own wifi access point. The phone connects directly to it and
pulls the flight — no hangar network, no internet, nothing to configure at an
unfamiliar airfield.

On-device selection cuts a flight to roughly 110 MB, so at a realistic
25 Mbit/s over the Pi's 5 GHz radio a sync takes about 45 seconds — down from the
~2.5 minutes Step 2 budgeted for storing everything. It finishes before the
pilot has finished packing up, and a 10-flight buffer now fits in about 1.1 GB.

The same access point carries the **live view for aiming** from Step 2: an MJPEG
stream to the phone's browser, install-time only.

## When it starts recording

Step 1 requires ≥95% capture completeness against the time the aircraft was
moving, so being switched on is not sufficient — it has to be recording *before*
anything happens.

Two things make that hold. Capture starts automatically on boot, with no
interaction. And the IMU is already watching: once the airframe starts vibrating,
the device knows the flight has begun and marks it, which also means the sync
step can hand the pipeline a flight rather than a recording session.

Switching on is part of the pilot's walk-round, not part of the start-up flow.

## Bill of materials

On top of the sensors chosen in Step 2:

| Part | Approx. |
|---|---|
| Raspberry Pi 5, 8 GB | $80 |
| Active cooler | $5 |
| 256 GB A2 microSD | $25 |
| USB-C power bank, 20,000 mAh | $40 |
| BNO085 IMU | $25 |
| BMP390 barometer | $12 |
| I²S MEMS microphone | $8 |
| Cables, headers, button, LED | $20 |
| **Total** | **~$215** |

## Pass criteria

| # | Criterion | Threshold |
|---|---|---|
| 1 | Sustained capture | Both sensors at their target intervals, full resolution, 2 h, zero dropped frames |
| 1b | No blind window | Baseline coverage holds at ≥1 frame per 10 s for the whole flight, whatever selection decides |
| 2 | Endurance | ≥3 h continuous capture on one charge |
| 3 | Power-loss integrity | 20 hard power cuts during capture: no unbootable card, no loss beyond the frame being written |
| 4 | Thermal | No throttling in a closed light-coloured enclosure at 45 °C ambient under full workload for 2 h, in sun |
| 5 | Time to recording | ≤60 s from switch-on to first frame stored |
| 6 | Sync | A full flight transfers and verifies on the phone in ≤2 min |
| 7 | Draw measured | Average and peak watts logged, so the production supply can be sized from data |

Criterion 7 is an output, not a gate — it is what the production power design
gets built against.

## Deliverables

1. A device that switches on, records a flight unattended, and syncs to a phone.
2. Measured power draw, thermal profile and capture rate under real conditions.
3. The power-loss test results.
4. A written statement of what the production board actually has to do, derived
   from those measurements rather than from a datasheet.

Roughly two weeks: a week for parts and assembly, a few days of bench work, and
the thermal soak running alongside.

## The risk worth naming

**Thermal, and it is not close.** Everything else in this step has a known fix.
A Pi 5 that throttles does not fail visibly — it quietly captures fewer frames,
and the evidence arrives as a completeness number after a flight nobody can fly
again.

What makes it awkward rather than merely hard is that the obvious remedy costs
the resource we are also trying to protect: cooling draws power, power is
battery, and a bigger battery is more weight on a ball joint. The cheap
mitigations — a light enclosure, an aluminium chassis, a lower clock — are cheap
precisely because they break that loop instead of feeding it.

Log core temperature on every flight from here on, not just during the bench
test, so the failure is always visible in the data rather than inferred from a
gap.

---

[← Step 2: The optical bench test](02-the-optical-bench-test.md) · [Contents](README.md)
