# Ondoway

Ondoway builds GPS-triggered audio walking tours: a person asks for a day in a
city and hears stories told at the places where they stand. One context covers
the whole product — planner, authoring, playback, workbench.

## Language

**Subject**:
An area of interest a traveller picks to steer their day. The
traveller-facing word; in code and data the same concept is called a lens.
_Avoid_: category, topic, genre, interest tag

**Lens**:
The code and corpus name for a subject. One concept with four roles: it
steers which places are picked, constrains which beats may speak at a stop,
colours the diction of the narration, and names the served subject on screen.
A parent lens stands for all of its children. The vocabulary is fixed — a
closed set, not free text.
_Avoid_: filter (it also orders and labels), register (one of its roles, not
the concept)

**Beat**:
One unit of corpus story, tied to a place and tagged with exactly one leaf
lens. Beats are the only source of a stop's words.

**Stop**:
One place on a day's route where the walker stands and a piece of the story
plays.

**Leg**:
The walk between two consecutive stops. A leg's narration belongs to that
specific pair; when the pair changes, the old words no longer apply.

**Footprint**:
The area whose entry makes a stop's piece play. Large places have large
footprints; words that claim "right here" are true only near the thing
itself, never at the footprint's edge.

**Anchor**:
A reviewed spot inside a large place's footprint where one chapter of the
story plays — a grave, a wall, a doorway.

**Replan**:
A mid-walk re-cut of the day: the same places kept and re-ordered, no new
audio owed. Every kept line is re-checked where it now lands; a line that is
no longer true is dropped — silence over wrongness — its script rewritten at
once and its audio caught up in the background.

## Doors and hours

**Door**:
A place the walker must enter to get its value — a museum, a church, a
walled garden, a market hall. A place with a door has hours; a street, a
bridge or an open square has none.
_Avoid_: gated (code word for the same thing)

**Hours source**:
Where a place's hours came from. Exactly one of three: the map, a guess, or
unknown. The source decides how the hours are spoken; nothing else does.
_Avoid_: verified, official, confirmed, tier, corroborated

**Map hours**:
Hours copied as-is from OpenStreetMap, the top level of trust. Spoken
plainly, with no hedge.
_Avoid_: dubious, unverified, moderate confidence

**Guess**:
Hours a model produced without a map entry to copy. Spoken as a guess. A
guess may say a place is closed but may never remove it from a day.

**Unknown**:
A door whose hours nobody holds. Never a reason to drop the place; the walker
hears that we could not confirm its hours.

**Closed report**:
A walker's mid-walk tap saying a door is shut. It replans that day and marks
the place's guess as contradicted for the next walker. One report never
rewrites hours.

**Standby**:
A backup place chosen, and its audio made, when a day is composed — one per
guessed stop. Offered as a one-sentence question when a closed report lands.
A standby always has map hours saying it is open on arrival.
_Avoid_: refill (adding places for any other reason, which is later work)
