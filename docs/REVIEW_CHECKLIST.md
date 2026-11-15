# Clip review checklist

`studio.py takes` runs `clip_qa.py` on every take. Then a person (or a reviewer agent) watches each take with its
`.qa.json` flags and this list, zooms into every flagged time, and answers every line. `clip_qa.py` catches about two
thirds of the defects people reject (crossing streams, pop-ins, roads swapping); it misses slow fades, such as a car
slowly turning see-through. The human look is not optional.

## Defects that fail a clip
1. Vehicles drive through each other, or traffic in one lane moves both ways.
2. A road, rail track, bridge or lane appears, vanishes or swaps for another.
3. Anything pops in or out: a person, a vehicle, a pole, a cloud of smoke, headlights.
4. Anything morphs: a car going see-through, two boats merging, window slats rearranging, the moon changing phase.
5. A foot, hand or limb hitches, double-steps or bends wrong (a foot that lifts twice before one step).
6. A person anywhere when the shot promised none, including silhouettes and reflections.
7. Readable or garbled text, numbers, plates, logos, stickers.
8. Corrupt blocks or a flash in the first 0.5 s: trim it, do not fail the clip, if enough clean footage stays.
9. Motion that contradicts the row in a way the edit cannot use (a boat sailing backwards, an orbit where the cut needs a push-in).
10. A cartoon, waxy or over-smoothed look compared with the hero still (`look.py check`).

## For every clip, report
- PASS or FAIL, and the clean range (start to end in seconds).
- One line per defect, with its time.
- Whether the motion matches the row (direction, speed, camera move).

## Rules for the reviewer
- Assume a defect until the frames show none.
- Do not pass a clip because the defect is small: viewers see small things at full speed.
- Watch at full speed and frame by frame (8 fps sheets or more), never three stills.
