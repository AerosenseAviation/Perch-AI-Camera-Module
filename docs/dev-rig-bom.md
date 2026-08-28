# Perch dev rig — bill of materials

One buildable test rig. Prices are rough USD, pre-import, and worth re-checking.
Build two: one to fly, one to keep working on while the first is in an aircraft.

## The architecture decision this BOM assumes

**The Pi runs the pipeline, not the model.** Stages 1–5 — capture, frame
selection, audio features, telemetry, phase segmentation — are local and
model-free and a Pi 5 handles them. Stages 6–9 need a frontier vision model.
Reading an airspeed indicator to a number you would publish to a pilot is
exactly what a small on-device model gets wrong, and it gets it wrong by
inventing a plausible number rather than by erroring.

The upload stays small anyway: ~400 selected JPEGs plus a transcript and
telemetry is 30–80 MB, not 15 GB of video. The GoPro problem was never the
model, it was shipping raw footage around.

**WiFi for bulk, BLE for control.** BLE is ~1 Mbps in practice; 50 MB would
take hours. The Pi runs an access point, the phone joins it, transfer is over
HTTP. BLE does discovery, wake, start/stop, battery and status.

That also gives the feature that matters most: a **live MJPEG preview** so the
pilot aims the instrument sensor from their phone before taxi. A misaimed panel
sensor is how this product fails silently in the field, and BLE cannot carry a
preview.

## Parts

| Part | Pick | ~$ | Why |
|---|---|---|---|
| Compute | Raspberry Pi 5, 8 GB | 80 | Two MIPI CSI ports — the whole reason for the 5 over the 4. |
| Cooling | Active cooler | 10 | Non-optional; see thermal below. |
| Wide sensor | Camera Module 3 Wide (IMX708, 120°, AF, HDR) | 35 | HDR matters more than anything else here. |
| Instrument sensor | HQ Camera (IMX477) + C-mount lenses (6/8/16 mm) | 100 | Buy optical flexibility — the right focal length to frame a panel from 60 cm is unknown. |
| *cheap alt* | Camera Module 3 standard (75°) | 25 | Fine to start; the fixed FOV will be outgrown. |
| Cables | 2× CSI, 300–500 mm, **Pi 5 22-pin** | 10 | Pi 5 uses a different connector from the Pi 4. Easy mistake. |
| Motion | BNO085 or ICM-20948 IMU (I2C) | 20 | Bank, pitch, g, turn rate, touchdown. |
| Altitude | BMP390 barometer (I2C) | 10 | Replaces GPS altitude. No antenna, works in any unpressurised cabin. |
| ~~GPS~~ | — | 0 | **Dropped.** See below. |
| Audio | USB audio adapter + electret lav mic | 20 | Cabin mic only; an intercom tap is a wired connection to avionics. |
| Storage | 128 GB A2 microSD | 15 | Optionally NVMe HAT + 256 GB SSD (~55) — see power loss. |
| Power | 20,000 mAh USB-C PD bank | 40 | Pi 5 wants 5 V/5 A; ~8 W in use. Battery keeps it a portable device with zero aircraft input. |
| Mount | Suction/RAM-style arm + small ball head | 55 | The ball head gives the instrument sensor **independent aim** — the mechanical crux. |
| Bracket | 3D-printed twin-camera carrier | 0–30 | Iterate this; it is the real design work. |

**≈ $395 per rig.**

## Why no GPS

The rig mounts inside, usually against the headliner. No sky view worth relying
on, and only some airframes would ever give one.

The deeper reason: telemetry authorises numeric claims in the validator. An
intermittent GPS with multipath inside a metal cabin does not fail loudly — it
produces plausible bad fixes, which then license exactly the invented airspeed
the whole validator exists to prevent. A reliable no beats an unreliable yes.

A barometer replaces what GPS altitude was actually used for, at a quarter of
the price and with no antenna. Ground speed is the only real loss, and the ASI
is in frame — indicated airspeed is the number a pilot cares about anyway.

Side benefit: no position data means nothing that looks like tracking.

## Gotchas that will bite

**Pi 5 has no hardware H.264 encoder.** The 5 dropped what the 4 had. Software
encoding two 1080p streams is a stretch.

The way out is a design decision, not a workaround: **the instrument sensor
never needs video.** Nobody wants a two-hour recording of an airspeed
indicator. Stills every ~2 s and nothing else — which is all the pipeline
consumes. Continuous video on the wide camera only.

**Thermal.** A sealed enclosure on a glareshield in the sun, two cameras, two
hours. It will throttle. Active cooling, vented light-coloured enclosure, and
log die temperature from flight one.

**Power-loss corruption.** Master switch off, Pi loses power mid-write, SD card
dies, flight lost. Read-only rootfs with a small writable data partition, flush
aggressively, consider NVMe. Solve before the first field trial.

**Dynamic range is the hardest optical problem.** A dark panel against a
blinding windscreen. Get it wrong and the panel is a silhouette and the sky is
white — and it will look like an AI problem when it is not.

**Prove two-camera simultaneous capture on day one.** Riskiest assumption here.
Two sensors at full res on one Pi 5, both writing, plus audio — bench that
before designing a bracket.

## What the Pi retires

The audio cross-correlation sync in `perch/stages/probe.py` exists because two
separate cameras have two clocks. One Pi, one clock, one timebase — the offset
is always zero. Keep it for the taped-two-cameras interim test; the hardware
makes it dead weight, which is a good sign the boundary is in the right place.

## Before buying anything

The BOM should not block the experiment. Tape any narrow-FOV camera beside a
wide one on the glareshield, fly once, run the pipeline, and grade the `panel`
observations for wrong-rate. That answers *can a frontier model read my panel*
— the question the entire product rests on — for nothing. If steam gauges read
badly and glass reads well, that changes this BOM.
