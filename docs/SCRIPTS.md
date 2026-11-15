# Writing scripts that survive production

Every film starts here: brand films, UGC ads and music videos. A script is a beat sheet (`beats.json`), one row per shot,
plus the narration, plus a signed promise. `script_check.py` checks it before the client sees it.
Templates: `templates/beats_brand_film.json`, `templates/beats_ugc_ad.json`, `templates/beats_music_video.json`.

## 1. The order (never skip a step)
1. **Brief in one paragraph**: who it is for, the one feeling the viewer leaves with, the one fact they remember, length,
   format, reference films.
2. **Delivery promise** (one block in `beats.json`, signed by the client before anything is generated). It names what the
   film will and will not do, in the client's words: "every shot once", "one person, one place", "no text in shot",
   "fast city, no faces". Every later decision is checked against it. Writing the promise next to the numbers is what stops
   a plan that quietly needs every clip three times.
3. **Three concepts that differ in kind**, not three variants of one idea. Each: a title, a logline (one sentence: a person
   wants something, something gets in the way, something changes), the hook, the one image people remember, and why it
   suits the video model (what the model draws well). The client picks or mixes.
4. **Beat sheet** of the chosen concept (rows below), then the narration, then the checks, then the client's gate.
5. **Music videos**: `audiomap.py` first, then `plan_check.py`, and the rows follow the song map, not a guess.

## 2. Every row answers seven questions
| Field | What it holds | Bad | Good |
|---|---|---|---|
| `subject` | who or what the shot is about | "the city" | "a small wooden ferry" |
| `subject_motion` | the ONE thing it does | "people walk and cars drive and lights flash" | "slides across the harbor, wake trailing behind it" |
| `scene` | where, when, light | "a harbor" | "a quiet harbor at 6 am, mist on the water, low sun behind the hills" |
| `framing` | shot size and angle | "nice angle" | "low, at water level, the ferry fills the left third" |
| `camera` | one move, or locked-off | "dynamic" | "slow push-in" |
| `intent` | what the viewer must understand or feel in this shot | (missing) | "the town wakes before anyone is up" |
| `role` | the row's job in the arc | | hook / setup / turn / build / payoff / breath / brand / cta |

Text and graphics added in the edit go in `onscreen_text`, never in `scene`: the model draws scene text as garbage.
An empty field is a decision not yet made, and an unmade decision is how invented business ends up on screen.

## 3. Hooks (the first 1 to 3 seconds)
- **Sensory**: a sound-led or texture-led close-up (rain on a window, a flame in a pan).
- **Scale shift**: from macro to the whole place, or the reverse, in one cut.
- **Contrast**: still and loud, empty and full, night and day back to back.
- **Question**: the viewer sees a result before the cause (a finished room, then the person who made it).
- **Problem (UGC)**: the pain in the viewer's own words in the first line, the product visible by second 3 to 5.

Write the hook last, once you know the payoff: the hook is a promise the payoff keeps.

## 4. Arcs by format
- **Narrated brand film**: a wish, the ordinary obstacle, a gentle magical turn the model can draw (light, weather,
  creatures), a quiet payoff on a face, the brand line. 55 to 65 s, 12 to 16 shots of 3 to 8 s, 100 to 130 words.
- **UGC ad**: hook (problem or claim), why it matters, the product in hand, one proof (a result, a number, a before and
  after), call to action with the offer. 15 to 30 s, lines of 1.5 to 6 s, about 2.5 words per second.
- **Music video without performers**: the song map sets the shape.
  - Intro: establish one place and one light state, long shots.
  - Drop: energy montage, beat cuts, the hero shot on the first bar.
  - Break: a held image that breathes, the emotional center.
  - Final drop: the payoff, the strongest material.
  - Outro: return to the opening image, changed (night to dawn, empty to lit).
  Build motifs that rhyme (gold light, water, one color) instead of reusing shots. Every shot appears once.
- **Music video with a story**: one line of plot per section, the turn on the break, lyrics landing on the picture they describe.

## 5. Narration that holds up
- About 2.3 words per second for calm brand narration, 2.5 to 2.8 for UGC. Measure it (`script_check.py`).
- One line per shot. A line is complete on its own ("Lena stayed behind to finish the order", not "stayed behind").
- Concrete nouns and verbs. One image per sentence.
- The line lands on the picture it describes. Split a line when its halves belong to different pictures.
- No repeated key words within three lines. Read it aloud: if a line needs a breath in the middle, cut it.
- Banned: filler phrases ("in a world where", "game changer"), em dashes, "not just X but Y", stacked adjectives.
- Rewrite example. Weak: "In a world that never sleeps, one city finds its rhythm in the most unexpected places."
  Strong: "Six in the morning. The harbor is already working."

## 6. Design against the model's weaknesses
- One subject, one action, one camera move per shot. Groups walking, object physics (papers flying, a book closing) and
  two people moving at once fail.
- No crowds, signs, menus, number plates or writing in any frame or motion prompt (`prompt_lint.py` blocks them).
- Traffic goes one direction or none; vehicles never cross.
- Magic the model can draw itself (light, weather, water, animals). Brand assets only on the end card or as a real insert.
- Count unique clips before writing more rows than you can make.
- Plan who walks where with `blocking.py` before any frame is made (see the README).

## 7. The checks before the client sees it
`python3 script_check.py beats.json [--audiomap audiomap.json]`

Fails: a missing field, two camera moves, a crowd or writing in a scene, an unsigned promise at the gate, a line that is
not a complete sentence, a banned phrase or an em dash, a music-video script whose rows do not cover the song.
Warns: narration too fast or too slow, repeated key words, a hook longer than the format allows, two actions in one row.
