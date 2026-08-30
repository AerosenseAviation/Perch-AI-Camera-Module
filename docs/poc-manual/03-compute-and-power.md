# Step 3 — Compute and power

## The decision this step makes

What runs the device, what feeds it, and what happens when it loses power
mid-flight. This is the step that turns two chosen sensors into a thing that can
be switched on in an aircraft and left alone for two hours.

**Not decided here:** enclosure, mount hardware, the production board. The
production device is almost certainly not what the PoC runs on, and designing it
now would be guessing ahead of the measurements this step produces.

## The device captures. The cloud decides.

**The device never judges what matters.** It captures as much as it can, at the
best resolution it can, and hands all of it over. Every decision about what is
interesting happens off the device.

The obvious argument is the NPU — there isn't one, so anything clever costs CPU,
watts and heat we cannot spare. The real argument is worse than that: **a
judgement made in the air cannot be revisited.** A frame the device discards is
gone. No amount of cloud software recovers it, the flight cannot be flown again,
and the thing we discarded is exactly the kind of thing we do not yet know how to
recognise — which is the entire point of the project. Cloud software can get
smarter every week against footage already captured. Firmware that threw the
footage away has permanently capped how smart the product can become.

So the hardware's job is narrow and absolute: **capture everything, lose
nothing.**

### What that costs

At full sensor resolution on both channels, a 2-hour flight is roughly:

| Channel | Interval | Frames | Size |
|---|---|---|---|
| Panel, 11.9 MP | 2 s | 3,600 | ~10.8 GB |
| Scene, 11.9 MP | 3 s | 2,400 | ~7.2 GB |
| Audio + telemetry | — | — | ~31 MB |
| **Total** | | **6,000** | **~18 GB** |

**Storage is not the constraint.** A 512 GB card is about $40 and holds 25
flights — well past the 10-flight buffer. Cards are the cheapest component in the
device and the last place to economise.

**Moving the data is the constraint.** 18 GB over wifi at a realistic 25 Mbit/s
is an hour and a half. That is not a sync, and no pilot will wait for it. See
below for how the flight actually gets off the device.

### Discarding by judgement is banned. Reducing by config is not.

There is a distinction worth being precise about, because it is the difference
between a rule and a straitjacket.

- **Discarding by judgement** — "this frame looks boring, drop it" — is banned
  outright, on the device, forever. It is content-dependent, unrepeatable, and
  destroys information.
- **Reducing uniformly by configuration** — a capture interval, a resolution, a
  crop region the pilot set once when aiming — is not a judgement. It applies
  identically to every frame, it is a number in a config file, and it is
  reviewable and changeable between flights.

`interval_seconds`, `long_edge` and the install crop are all already the second
kind. They are dials we set deliberately, not calls the device makes.

The rule that follows: **the card holds everything, at full resolution, always.**
Any reduction happens on the way to the cloud, never on the way to the card. The
card is the archive, and the archive is what lets a smarter pipeline be re-run
against flight 3 a year after flight 3 happened.

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
| Pi 5, capture and full-resolution encode | 5–8 W |
| Two camera sensors | 0.5–1 W |
| IMU, barometer, microphone | <0.1 W |
| Wifi — **off in flight**, on for sync only | 0 W / ~1 W |
| Fan — thermostatic, duty-cycled | 0–1 W |
| **In flight** | **~6–9 W** |

Three levers, in order of size:

- **Wifi off in flight.** The access point is for aiming and for sync, and both
  happen on the ground. Leaving it up costs around a sixth of the total budget
  for nothing.
- **Underclock, within reason.** The deadline is a frame every two seconds, not
  a millisecond. Trim the clock to whatever still meets criterion 1 with margin —
  but capture rate wins any argument with power, because a dropped frame is the
  one thing this design refuses to do.
- **Nothing else.** Encoding every frame at full resolution is the CPU load,
  and it is not negotiable — it is the job. Six thousand full-resolution JPEGs
  over two hours is the workload the board, the battery and the enclosure all
  have to be sized against, which is why criterion 7 measures it rather than
  estimating it.

At 8 W, a 3-hour target needs 24 Wh. A 20,000 mAh bank is ~74 Wh nominal and well
over 50 Wh usable — twice the margin needed. Size it down only once the real
draw is measured, since weight on a ball joint is its own constraint.


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

18 GB does not go over wifi, so for the PoC it does not try to.

**Bulk data comes off over USB-C, or by pulling the card.** The Pi 5 has USB 3,
so a flight moves to a laptop in about three minutes at realistic speeds, and a
card reader is faster still. The pipeline runs on that laptop anyway during the
PoC, so this is the short path, not a workaround.

**The device's wifi access point stays**, and it does the jobs that need to
happen at the aircraft: the **live view for aiming** from Step 2, device status
and storage remaining, and delivering the finished debrief back to the phone.
None of those move much data.

Bulk sync to a phone is a **production problem, deferred deliberately.** It
depends on how much data the cloud turns out to need, which is one of the things
the PoC exists to find out. Designing a phone-sync path now would mean guessing
that number and building firmware around the guess.

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
| 512 GB A2 microSD | $40 |
| USB-C power bank, 20,000 mAh | $40 |
| BNO085 IMU | $25 |
| BMP390 barometer | $12 |
| I²S MEMS microphone | $8 |
| Cables, headers, button, LED | $20 |
| **Total** | **~$230** |

## Pass criteria

| # | Criterion | Threshold |
|---|---|---|
| 1 | Sustained capture | Both sensors at their target intervals, full resolution, 2 h, zero dropped frames |
| 1b | Nothing dropped | Every scheduled frame reaches the card, at full resolution, for the whole flight |
| 2 | Endurance | ≥3 h continuous capture on one charge |
| 3 | Power-loss integrity | 20 hard power cuts during capture: no unbootable card, no loss beyond the frame being written |
| 4 | Thermal | No throttling in a closed light-coloured enclosure at 45 °C ambient under full workload for 2 h, in sun |
| 5 | Time to recording | ≤60 s from switch-on to first frame stored |
| 6 | Offload | A full flight (~18 GB) transfers and verifies to a laptop in ≤5 min over USB-C |
| 6b | Buffer | 10 flights fit on the card with room to spare, and the oldest is dropped, not the newest refused |
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

Capturing everything makes this harder, not easier — six thousand
full-resolution encodes is real sustained CPU load, and it is load we have chosen
not to reduce. That is the right trade, and it means the thermal budget has to
absorb it rather than the capture rate giving way.

Log core temperature on every flight from here on, not just during the bench
test, so the failure is always visible in the data rather than inferred from a
gap.

---

[← Step 2: The optical bench test](02-the-optical-bench-test.md) · [Contents](README.md)
