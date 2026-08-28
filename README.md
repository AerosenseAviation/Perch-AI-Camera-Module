# Perch

An AI cockpit camera for pilots, and the pipeline behind it.

The rig sits above or between the pilots and carries two sensors. A **wide**
sensor sees the cockpit, the pilots' hands and the world through the windscreen.
A **narrow** sensor is framed on the instruments and does nothing else. After the
flight, the pipeline turns both into a debrief: what happened, what was worth
seeing, and — because the second sensor can actually read a dial — what the
instruments said while it happened.

This repository is the software. It runs today against any two video files, so
the concept can be proven on a taped-up pair of cameras long before there is a
product to put them in.

The second sensor is the whole bet. Everything defensive in this codebase exists
because a wide shot cannot resolve a needle: the `panel` module used to be
disabled on most flights, and the validator threw away every claim containing an
airspeed. With instruments in frame, numbers stop being forbidden and become the
product — so the rules change from *never state a number* to *only state one you
read at a moment the sensor actually covered*.

## Install

Needs Python 3.11+, `ffmpeg` and `ffprobe`. `exiftool` is optional and only used
for GoPro telemetry.

```bash
sudo apt install ffmpeg libimage-exiftool-perl     # or: brew install ffmpeg exiftool

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Optional: local speech-to-text. Everything works without it.
pip install -e '.[transcribe]'

export ANTHROPIC_API_KEY=sk-ant-...
```

## Use

```bash
# One camera, any mount — the original single-stream behaviour.
perch run flight.mp4

# The rig: wide sensor plus instrument sensor.
perch run scene.mp4 --panel instruments.mp4 --rig cockpit_dual

# Check the alignment before committing to a run.
perch probe scene.mp4 --panel instruments.mp4

perch run scene.mp4 --panel instruments.mp4 --dry-run    # no model calls
perch run scene.mp4 --max-cost 0.50                      # stop at a ceiling
perch run scene.mp4 --modules panel,crosscheck           # only these

# A folder. flight01.mp4 automatically picks up flight01-panel.mp4.
perch batch ~/footage --rig cockpit_dual --max-cost-total 20.00

perch stage analyse runs/flight-20260828-101500          # re-run one stage

perch eval export runs/ -o grades.csv                    # grade by hand
perch eval report grades.csv                             # useful/obvious/wrong
perch eval rejections runs/                              # validator rate
```

Each run writes to `runs/<video-stem>-<timestamp>/`. Nothing leaves the machine
except the sampled frames and the audio transcript.

## Two cameras, one timeline

Two independent cameras start at different moments, and every observation in the
debrief cites a timestamp — so a bad alignment corrupts everything downstream.

The scene stream defines t=0. The panel stream carries an offset into scene
time, recovered from the audio: both cameras hear the same engine and the same
radio calls, so cross-correlating their loudness envelopes finds the shift.

```
  probe: scene 420s  1280x720 @ 10.00fps  audio=yes  telemetry=no
         panel 396s  960x960  offset +8.00s via audio (confidence 0.91)
```

Three things make it reliable, each of which broke a real case during
development:

- **The envelope is high-passed.** Engine power drifts over minutes and that slow
  component smears the correlation across tens of seconds. Removing it lets the
  transients — radio calls, a power change — pin the alignment.
- **The rolling mean is edge-corrected.** A zero-padded one leaves an artefact of
  identical shape at both ends of *both* envelopes, and those artefacts correlate
  almost perfectly at zero lag, silently beating the true peak.
- **Confidence is a correlation coefficient, not a heuristic.** Measured, a true
  alignment scores above 0.9 and unrelated audio about 0.12. Below the threshold
  the pipeline assumes a common start and says so, because a wrong offset is far
  worse than no offset.

`--panel-offset 8.0` skips the measurement when you already know it. On real
hardware with one clock, none of this is needed — it exists so a prototype made
of two separate cameras produces trustworthy timestamps.

## The nine stages

Each stage writes its output to disk and reads only what the stage before it
wrote, so any one can be re-run alone with `debrief stage`.

| # | Stage | Writes | Model |
|---|-------|--------|-------|
| 1 | probe | `probe.json` — both streams and the measured offset | — |
| 2 | telemetry | `telemetry.csv` | — |
| 3 | sample | `frames/scene/`, `frames/panel/`, `frames.json` | — |
| 4 | audio | `audio.wav`, `transcript.json`, `audio_features.csv` | local |
| 5 | segment | `phases.json` | fast, only without telemetry |
| 6 | capability | `viewpoint.json`, `panel_aim.json`, `modules.json` | fast |
| 7 | analyse | `observations.json`, `rejected.json` | strong |
| 8 | compose | `debrief.json` | strong |
| 9 | render | `debrief.html` | — |

`--dry-run` runs stages 1 to 5 and skips the rest.

### Stage 3 — the two streams are sampled differently

The scene changes slowly: terrain, weather, attitude. Three seconds is plenty,
at 768 px and quality 80.

The panel changes in a moment: a switch moves, a needle swings, a light comes on.
It is sampled every two seconds at 1024 px and quality 92, because a needle has
to survive JPEG to be readable.

Filenames carry the timestamp **on the run timeline**, so `frames/scene/f_000102.00.jpg`
and `frames/panel/f_000102.00.jpg` are the same moment of the flight.

### Stage 6 — capability, and the install check

Two jobs, and on a known rig the second matters more.

**What can the wide sensor see?** On an unknown camera this is inferred from
eight frames. With `--rig cockpit_dual` the answer is already known, so the
profile is used and the model call is skipped — an exact answer instead of a paid
approximate one.

**Is the instrument sensor aimed and readable?** This always costs a call,
because it is a property of *this* installation on *this* flight. It writes
`panel_aim.json`:

```json
{
  "in_frame": "full",
  "legible": "clear",
  "panel_type": "analog",
  "instruments": ["airspeed", "altimeter", "attitude", "tachometer"],
  "aim_hint": "",
  "glare": false
}
```

A sensor pointing at the glareshield is the most likely way this product fails in
the field, and it fails silently unless something checks. `aim_hint` is written
for the pilot — "tilt down slightly, the top of the panel is cut off" — and
appears at the top of the debrief. It belongs in the app as a pre-flight check.

An unreadable panel, or an aim check that could not run, disables `panel` and
`crosscheck`. **It fails closed:** no verified sensor, no numbers.

## The module table

| Module | Reads | Requires | Reports |
|---|---|---|---|
| `attitude` | scene | horizon visible | pitch and bank changes, steepest bank, wings-level quality |
| `pattern` | scene | outside terrain or telemetry | circuit shape, leg lengths, turn consistency |
| `landing` | scene | forward view and runway visible | flare, float, drift, bounce, touchdown character |
| `panel` | **panel** | instruments in frame and legible | readings, configuration changes, warning lights |
| `crosscheck` | **both** | both streams, legible instruments | what the instruments said against what the world was doing |
| `hands` | scene | pilot hands visible | control inputs, throttle, flap and gear selections |
| `scan` | scene | pilot face visible | lookout pattern, head movement, instrument dwell |
| `radio` | audio | audio present and transcribed | calls made, phraseology, readbacks |
| `callouts` | audio | audio present and transcribed | verbal callouts, checklist discipline |
| `engine` | audio | audio present | power changes by engine note, alert tones |
| `environment` | scene | outside terrain visible | weather, cloud, light, terrain, visible traffic |
| `highlights` | scene | always | the best frames of the flight |
| `story` | scene | always | the narrative |

`crosscheck` is the module that only exists because there are two sensors. It
receives the same moment from both, adjacent, and reports the panel against the
world: power coming back as the runway picture stops rising, the attitude
indicator against the real horizon, an instrument doing something the outside
view does not explain. Nothing else in the debrief can do this.

Two deliberate refinements to the original spec's table: "audio present" splits
in two (`engine` reads the waveform and needs only a track; `radio` and
`callouts` read words and also need a transcript), and `panel` is gated on the
aim check rather than on the wide view happening to resolve the instruments.

`character` and `trivia` observations still matter as much as `safety` — the
steepest bank, the light on the ridge, the smoothest of three touchdowns.

## Anti-hallucination

Enforced twice: once in the prompt, once in code. The five prompt rules live in
`perch/prompts/rules.md` and are spliced into the stage 7 and 8 system prompts.

The validator (`perch/validate.py`) rejects an observation when:

1. it cites a timestamp outside the flight duration;
2. it cites no timestamp at all;
3. it states a number with no readable panel and no telemetry;
4. its confidence is `low` and its interest is `safety`;
5. **it states a number at a moment the instrument sensor did not cover.**

Rule 5 is new with the second sensor and it is the important one. Readable
instruments are permission to report what was on the glass at a given instant —
not permission to report a number for a moment the sensor was blurred, glared or
looking elsewhere. Without it, "the airspeed reads 75 knots" silently becomes a
claim about the whole flight. The tolerance is `[validate] panel_frame_tolerance`,
five seconds by default.

Every rejection is written to `rejected.json` with the rule it broke. The
rejection rate is a quality metric: a prompt change that raises it has made the
model less careful.

The negative tests are in `tests/test_pipeline.py` — a flight with no readable
panel produces no observation stating an airspeed, and an *illegible* instrument
sensor produces none either, even though the hardware was there.

## Cost control

- Every model response is cached under `runs/.cache`, keyed on a hash of the
  model, prompt, schema and image bytes. Prompt iteration never re-pays.
- The estimate is printed after the probe stage, before any model call.
- `--max-cost` is checked against each call's projected tokens; the run stops
  cleanly and keeps everything written so far.
- `--dry-run` runs every local stage and skips the model entirely.

**The second sensor roughly doubles the cost.** A 7-minute flight on a dual rig
with 10 modules enabled is ~82 calls and around **$13** at Opus 5 prices; the same
flight on one camera was ~$5. Frames dominate the input tokens, and `crosscheck`
sends two images per moment. Levers, in the order worth reaching for:

1. `--modules panel,crosscheck,story` — run what earns its keep on your footage.
2. `strong = "claude-sonnet-5"` — 2.5x cheaper input, and worth measuring against
   Opus on graded flights before assuming it is worse.
3. `[sample.panel] interval_seconds` — the panel stream is sampled fastest, so it
   is the biggest single lever.

Always `--dry-run` a new library first: it prints the estimate per flight with no
spend.

## Evaluation

This is the instrument that decides whether the product works.

```bash
perch batch ~/footage --rig cockpit_dual
perch eval export runs/ -o grades.csv
# fill `verdict` with useful / obvious / wrong
perch eval report grades.csv
```

The report breaks verdicts down by mount, module, phase **and stream**, and
prints one extra line:

```
Instrument reading (panel + crosscheck): 1 wrong of 24 graded (4.2%).
A wrong number is the one failure that loses a pilot for good.
```

That number is the go/no-go for the whole idea. Reading analog steam gauges from
video is the hard part — glass cockpits are much easier — and it is unproven
until it is graded on real footage. A missed observation costs nothing; a
confident wrong airspeed costs the customer.

## Layout

```
perch/
  cli.py            command line
  pipeline.py       drives the nine stages
  config.py         TOML configuration, rig profiles
  models.py         pydantic schemas for every artifact
  modules.py        the module table
  validate.py       the five rejection rules
  cache.py          content-addressed response cache
  cost.py           estimation and accounting
  llm.py            the single model-call path
  runs.py           run directories and RunContext
  evaluate.py       the grading harness
  stages/           one file per stage
  prompts/          every prompt, in Markdown
    rules.md          the five anti-hallucination rules
    capability.md     the viewpoint probe
    panel_aim.md      the instrument-sensor install check
    segment.md        the phase labeller
    system_analyse.md stage 7 system prompt
    compose.md        stage 8 system prompt
    modules/*.md      one instruction per module
  templates/        the HTML debrief
tests/
runs/
```

Prompt iteration is the main work of this project, so no prompt lives in a Python
string literal. Editing a prompt file changes the cache key, so the next run
re-asks the model rather than serving the old answer.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

168 tests. The suite generates its own clips with ffmpeg and stubs the model, so
it runs in CI with no API key and no spend. Tests needing ffmpeg skip themselves
when it is not installed.

## What is not built here

By design, in this phase: no flight-data monitoring, no precise geometry, no
V-speeds or checklists from any source, no instruction, no mobile app, no server,
no accounts, no cloud storage. The tool reports what the footage shows.
