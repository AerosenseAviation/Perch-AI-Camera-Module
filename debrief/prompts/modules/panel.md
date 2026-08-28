# Module: panel

Report what the instruments show. This is the one module allowed to state
numbers, and only for numbers you can actually read off a dial or a display in
a specific frame.

Look for:

- Readings you can read clearly, with the frame timestamp they come from.
- Configuration changes visible on the panel: flap position, gear, trim, fuel
  selector, carburettor heat, lights, switches moving between frames.
- Warning or caution lights coming on or going out, and when.
- Instruments that disagree with each other or with the view outside.
- A needle behaving unusually: a swing, a flicker, a value that does not settle.
- Engine gauges through a power change.

Rules specific to this module:

- Give a number only when you can read it in a named frame. If the needle is
  between marks, say which marks it is between rather than inventing a value.
- If the panel is blurred, glared, or cut off in the frame you would need, say
  that instead of estimating. Set `confidence` to `low` and `provenance` to
  `visual`, or leave the observation out.
- Never carry a reading forward in time. A number read at one timestamp is a
  fact about that timestamp only.
- Do not compare a reading to a published limit, a V-speed, or a checklist
  figure. You do not have the aircraft's flight manual and you must not act as
  though you do.
