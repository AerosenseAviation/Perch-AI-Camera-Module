# Flight Debrief

A command-line tool that reads a video of a flight and returns a post-flight
debrief for the pilot.

The debrief is what the footage shows: what happened, what was interesting, what
was well flown, and what the light was doing over the ridge at 14 minutes. It is
not a flight-data-monitoring report, it is not instruction, and it does not tell
the pilot what they should have done.

The camera position is unknown before the run. It might be on the panel, on the
nose, on the pilot's chest, on their head, on a wing strut, or on the tail. The
tool works out what the camera can see, then runs only the analyses that
viewpoint supports. A wing-cam flight gets a real debrief; it just does not get
one that quotes an airspeed.

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
debrief probe flight.mp4                       # what is in the file
debrief run flight.mp4                         # the whole pipeline
debrief run flight.mp4 --dry-run               # local stages only, no model calls
debrief run flight.mp4 --max-cost 0.50         # stop before crossing a dollar ceiling
debrief run flight.mp4 --modules landing,story # only these modules
debrief run flight.mp4 --no-audio              # skip audio entirely

debrief batch ~/footage --max-cost-total 20.00

debrief stage analyse runs/flight-20260828-101500    # re-run one stage

debrief eval export runs/ -o grades.csv        # one row per observation, to grade
debrief eval report grades.csv                 # useful / obvious / wrong rates
debrief eval rejections runs/                  # what the validator threw away
```

Each run writes to `runs/<video-stem>-<timestamp>/`. Nothing leaves the machine
except the sampled frames and the audio transcript.

## The nine stages

Each stage writes its output to disk and reads only what the stage before it
wrote, so any one of them can be re-run alone with `debrief stage`.

| # | Stage | Writes | Model |
|---|-------|--------|-------|
| 1 | probe | `probe.json` | — |
| 2 | telemetry | `telemetry.csv` | — |
| 3 | sample | `frames/`, `frames.json` | — |
| 4 | audio | `audio.wav`, `transcript.json`, `audio_features.csv` | local |
| 5 | segment | `phases.json` | fast, only without telemetry |
| 6 | capability | `viewpoint.json`, `modules.json` | fast |
| 7 | analyse | `observations.json`, `rejected.json` | strong |
| 8 | compose | `debrief.json` | strong |
| 9 | render | `debrief.html` | — |

`--dry-run` runs stages 1 to 5 and skips the rest. Stage 5 falls back to a
single honest span when it has neither telemetry nor a model.

### Stage 3 — the frame budget

Frames are the cost. One every 3 seconds by default, scaled to 768 px on the
long edge, JPEG quality 80, capped at 400 per flight. Past about 20 minutes the
interval stretches to hold the cap: a 3-hour flight is sampled every 27 seconds.

Filenames carry the timestamp (`f_000123.45.jpg`), so the frames folder is the
source of truth and no index file is required to interpret it.

### Stage 6 — capability

Eight frames spread across the flight go to the fast vision model in one call.
It returns a viewpoint descriptor: the mount, what is visible, and the image
quality. That descriptor decides which modules run.

The result is cached against a hash of those eight frames, so the second flight
from the same mount costs nothing.

## The module table

| Module | Requires | Reports |
|---|---|---|
| `attitude` | horizon visible | pitch and bank changes, steepest bank, wings-level quality |
| `pattern` | outside terrain or telemetry | circuit shape, leg lengths, turn consistency |
| `landing` | forward view and runway visible | flare, float, drift, bounce, touchdown character |
| `panel` | instrument panel clear | readings, configuration changes, warning lights |
| `hands` | pilot hands visible | control inputs, throttle, flap and gear selections |
| `scan` | pilot face visible | lookout pattern, head movement, instrument dwell |
| `radio` | audio present and transcribed | calls made, phraseology, readbacks |
| `callouts` | audio present and transcribed | verbal callouts, checklist discipline |
| `engine` | audio present | power changes by engine note, alert tones |
| `environment` | outside terrain visible | weather, cloud, light, terrain, visible traffic |
| `highlights` | always | the best frames of the flight |
| `story` | always | the narrative |

One deliberate refinement to the specification: "audio present" splits in two.
`engine` reads the waveform and needs only an audio track. `radio` and
`callouts` read words, so they also need a transcript — enabling them without
one buys a round of model calls that can only come back empty, and it makes
"What I could not see" say something true and specific instead.

`character` and `trivia` observations matter as much as `safety`. The steepest
bank of the flight, the longest wings-level run, the smoothest of three
touchdowns — those are what the pilot enjoys and shows other people, and the
module prompts ask for them explicitly.

## Anti-hallucination

A confident wrong statement destroys pilot trust, so the rules are enforced
twice: once in the prompt, once in code.

The five prompt rules live in `debrief/prompts/rules.md` and are spliced into
the system prompts for stages 7 and 8.

The validator (`debrief/validate.py`) runs after stage 7 and rejects an
observation when:

- it cites a timestamp outside the flight duration;
- it cites no timestamp at all;
- it states a numeric airspeed or altitude while the `panel` module is disabled
  and no telemetry exists;
- its confidence is `low` and its interest is `safety`.

Every rejection is written to `rejected.json` with the rule it broke. The
rejection rate is a quality metric: a prompt change that raises it has made the
model less careful.

`tests/test_pipeline.py::test_a_flight_with_no_visible_panel_states_no_airspeed_or_altitude`
is the negative test — a wing-cam flight where the model is fed claims about
airspeed and altitude, asserting that none survive into `observations.json`.

## Cost control

- Every model response is cached under `runs/.cache`, keyed on a hash of the
  model, the prompt, the schema and the image bytes. Prompt iteration never
  re-pays for the same frames.
- `debrief run` prints an estimate after the probe stage, before any model call.
- `--max-cost` is checked before each call, using that call's projected tokens.
  The run stops cleanly and keeps everything written so far.
- `--dry-run` runs every local stage and skips the model entirely.
- The true cost is printed after the run and written to `run.json`.

Model names and prices live in `debrief/debrief.default.toml`. Override them by
putting a `debrief.toml` in the working directory — it is merged over the
defaults, so it only needs the keys it changes.

```toml
[models]
fast = "claude-haiku-4-5"     # the probe passes, stages 5 and 6
strong = "claude-opus-5"      # analysis and compose, stages 7 and 8
```

**Watch the analyse stage.** It fans out to one call per module per phase per
batch of 20 frames, and the frames dominate the input tokens. A 7-minute flight
from a panel mount with 9 modules enabled is around 68 calls and roughly $5 at
Opus 5 prices; a 3-hour flight is capped at 400 frames but not at modules or
phases. Three levers, in the order worth reaching for:

1. `--modules` — run the two or three that earn their keep on your footage.
2. `strong = "claude-sonnet-5"` — 2.5x cheaper input, and worth measuring
   against Opus on graded flights before assuming it is worse.
3. `[sample] interval_seconds` — fewer frames is a linear saving, and the first
   thing to try on long cross-countries where little changes minute to minute.

Always run `--dry-run` first on a new library: it prints the estimate per flight
with no spend.

## Evaluation

This is the instrument that decides whether the product works.

```bash
debrief batch ~/footage
debrief eval export runs/ -o grades.csv
# open grades.csv, fill `verdict` with useful / obvious / wrong
debrief eval report grades.csv
```

The report breaks the verdicts down by mount, by module and by phase. The useful
rate by mount and by module is the go/no-go instrument: a module that cannot
clear the bar on real footage does not survive.

The export carries a `clock` column (`14:32`) alongside the raw `timestamp`, so
a grader can scrub straight to the moment in the video.

## Layout

```
debrief/
  cli.py            command line
  pipeline.py       drives the nine stages
  config.py         TOML configuration
  models.py         pydantic schemas for every artifact
  modules.py        the module table
  validate.py       the four rejection rules
  cache.py          content-addressed response cache
  cost.py           estimation and accounting
  llm.py            the single model-call path
  runs.py           run directories and RunContext
  evaluate.py       the grading harness
  stages/           one file per stage
  prompts/          every prompt, in Markdown
    rules.md          the five anti-hallucination rules
    capability.md     the viewpoint probe
    segment.md        the phase labeller
    system_analyse.md stage 7 system prompt
    compose.md        stage 8 system prompt
    modules/*.md      one instruction per module
  templates/        the HTML debrief
tests/
runs/
```

Prompt iteration is the main work of this project, so no prompt lives in a
Python string literal. Editing a prompt file changes the cache key, so the next
run re-asks the model rather than serving the old answer.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

The suite generates its own short clips with ffmpeg and stubs the model, so it
runs in CI with no API key and no spend. Tests that need ffmpeg skip themselves
when it is not installed.

## What is not built here

By design, in this phase: no flight-data monitoring, no precise geometry, no
V-speeds or checklists from any source, no instruction, no mobile app, no web
page, no server, no accounts, no payment, no cloud storage.
