# Step 3 — Compute and power

## The decision this step makes

What runs the device, what feeds it, and what happens when it loses power
mid-flight. This is the step that turns two chosen sensors into a thing that can
be switched on in an aircraft and left alone for two hours.

**Not decided here:** enclosure, mount hardware, the production board. The
production device is almost certainly not what the PoC runs on, and designing it
now would be guessing ahead of the measurements this step produces.

## The device does no analysis

The PoC device captures and syncs. Nothing else.

Frames from both sensors, audio, IMU and barometer samples, and a timestamp
index. That is the whole job. Every stage of the pipeline — phases, capability,
analysis, composition — runs off-device, on a laptop or in the cloud, from the
synced flight.

This is a deliberate simplification and it is worth being explicit about, because
the obvious instinct is to push segmentation onto the device since it is cheap
and local. Resist it. Firmware is the slowest thing to change and the hardest to
debug in an aircraft, and every stage moved on-device is a stage that can only be
iterated between flights instead of between coffees. The pipeline already reads
artifacts from disk; the device's only contract is to produce that directory
correctly.

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

## The board

Two full-resolution camera sensors running simultaneously is the requirement that
picks the board, and it eliminates most of the field immediately: a Pi 4 or a Pi
Zero 2 W has one CSI connector. A Pi 5 has two.

| Board | Verdict |
|---|---|
| **Raspberry Pi 5 (8 GB)** | The PoC choice. Two CSI lanes, mature libcamera support for dual sensors, built-in 5 GHz wifi, headroom to spare. |
| Compute Module 5 + carrier | The production path. A carrier board is a PCB project and belongs after the PoC proves the concept. |
| Radxa / Orange Pi and similar | Some have two CSI connectors, but dual-camera software support is where these boards are weakest. Wrong risk to take on the critical path. |
| ESP32-class | Far too weak for two full-resolution sensors. |

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

A Pi 5 in a sealed enclosure, on a glareshield, in direct sun, is a genuinely
hard thermal case. Cockpit ambient can pass 50 °C on the ground before the engine
is even running, and a Pi 5 starts throttling in the low 80s. Throttling is not a
crash — it is worse, because it silently drops the capture rate and quietly fails
Step 1's completeness criterion instead of failing loudly.

Mitigations in order: the official active cooler; an aluminium enclosure used as
the heatsink; reducing clock speed, which we can afford because the workload is
light; and, if none of that is enough, a lower-power board.

Test it in a closed box in the sun running the real workload, with core
temperature logged for two hours. Do not test it on a bench in an air-conditioned
room.

## Getting the flight off the device

The device runs its own wifi access point. The phone connects directly to it and
pulls the flight — no hangar network, no internet, nothing to configure at an
unfamiliar airfield.

At the revised ~450 MB per flight, a realistic 25 Mbit/s over the Pi's 5 GHz
radio is about 2.5 minutes. That happens while the pilot packs up.

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
| 2 | Endurance | ≥3 h continuous capture on one charge |
| 3 | Power-loss integrity | 20 hard power cuts during capture: no unbootable card, no loss beyond the frame being written |
| 4 | Thermal | No throttling in a closed enclosure at 45 °C ambient under full workload for 2 h |
| 5 | Time to recording | ≤60 s from switch-on to first frame stored |
| 6 | Sync | A full flight transfers and verifies on the phone in ≤5 min |
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
A Pi 5 that throttles in a sealed box in the sun does not fail visibly — it
quietly captures fewer frames, and the first evidence is a completeness number
below 95% after a flight that cannot be repeated. Log core temperature on every
flight from here on, not just during the bench test, so the failure is always
visible in the data rather than inferred from a gap.

---

[← Step 2: The optical bench test](02-the-optical-bench-test.md) · [Contents](README.md)
