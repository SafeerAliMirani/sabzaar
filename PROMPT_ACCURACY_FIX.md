# Prompt for the Sabzaar chat: fix the false pins

Paste everything between the lines. (Or just tell the chat: "read
`Sabzaar/PROMPT_ACCURACY_FIX.md` and do it" — it has the folder.)

---
---

## PASTE FROM HERE

Sabzaar is putting tree pins and "plant here" suggestions on rooftops, bare
land and roads. I zoomed in manually and confirmed it. I assumed I needed
better satellite imagery and can't afford it. **That diagnosis was wrong, and
the answer is already in our own repo.** Please work through the following.

### 1. The root cause is a resolution and domain mismatch, not image quality

`DETECTION.md` states it plainly:

- DeepForest is a RetinaNet trained on **NEON airborne data at ~0.1 m**.
- We feed it **Esri World Imagery at ~0.6 m**, upsampled.

Upsampling adds no information. We are running the model **6x outside its
training resolution**, and on **arid South-Asian streetscapes** when it learned
**North American forests**. A model that far out of distribution hallucinates
crowns on exactly the things that look blob-like at 60 cm: rooftops, shadows,
roadside scrub. The false positives are predicted behaviour, not a bug.

So: stop treating "get sharper imagery" as the fix. It is not available to us,
and it is not the cheapest win.

### 2. Do the masking we already have the data for (highest priority)

A pin on a building is not a computer-vision problem. It is a spatial join we
are not doing.

- `DETECTION.md` line 62 says Overture **`_buildings.geojson`** must already be
  present in the repo dir. **Check whether we actually filter against it, and
  whether the filter is applied to the SUGGESTION layer as well as the
  detection layer.** My strong suspicion: detections are filtered and
  suggestions are not, or neither is.
- Add **OSM road centrelines buffered by a sensible width** as a negative mask.
- `DATA_SOURCES.md` already loads **ESA WorldCover**, where class **50 =
  built-up** and **80 = water**. Use both as negative masks.
- Never suggest planting inside a building footprint, on a road, or on water.
  Reject, do not down-rank.

Caveat to respect: WorldCover is **10 m** and the CHM is **~1 m**. A single
10 m "built-up" pixel covers 100 CHM pixels, so a coarse label must not
silently veto a fine one at edges. Prefer the vector footprints (Overture/OSM)
for hard rejects, and use WorldCover for soft context.

### 3. Turn our two independent layers into confidence tiers

We have two tree layers that fail in *different* ways:

- **Meta/WRI CHM v2** — ~1 m, measures **height**
- **DeepForest** — ~0.6 m, reads **RGB appearance**

`DETECTION.md` line 34 already says their disagreement "is the useful signal,
not a bug". So use agreement as confidence:

| CHM says tall | DeepForest says crown | In building/road/water mask | Result |
|---|---|---|---|
| yes | yes | no | **high confidence: tree** |
| yes | no | no | **maybe** (recent felling, or a tall non-tree) |
| no | yes | no | **maybe** (recent planting, or a false crown) |
| either | either | **yes** | **reject** |

A rooftop has height but no foliage signature. A green roof has neither at 1 m.
That is why combining them beats improving either one.

### 4. Show the uncertainty instead of hiding it

Three plain tiers on the map, no percentages, no jargon:

**Trees / Maybe / Needs eyes**

Pins stay marked **unverified** until a person confirms them. Use *tasdeeq*
(verification) as the Sindhi/Urdu word for that act. The framing for
non-technical users: **the satellite is a scout, the people of Sindh are the
judges.** Voice should recruit, not confess: not "our algorithm may be
inaccurate" but "we can't see this from space, can you check?"

Every confirmed or rejected pin becomes labelled training data for a future
local fine-tune. The weakness funds its own fix. This is the same pattern as
my other projects: Orrery measures and displays its own error and flags its bad
fits; Shajro routes every change through a human moderator. Sabzaar should do
both.

### 5. Label the staleness honestly

`DATA_SOURCES.md` cites CHM v2 source imagery as **(c) 2016 Vantor** and
WorldCover as **2021**. It is 2026. When our canopy layer disagrees with
present-day Esri imagery, **some of that is a decade of real change, not
model error.** Put the vintage of every layer in the UI. Do not let a viewer
think a 2016 canopy map is a 2026 census.

### 6. Drop the HPC, and understand why it failed

`DETECTION.md` line 59 admits compute nodes usually have **no internet**, and
our pipeline **fetches Esri tiles**. The documented workaround (pre-warm the
tile cache on a login node, then submit) is fighting the architecture rather
than fixing it.

The Larkana bbox is ~23 x 20 km; the source tiles are 29-75 MB.
`DATA_SOURCES.md` says "Machine ready: Python 3.12 on this PC". **This is a
laptop job, run in blocks overnight.** Galileo buys us nothing here and its
network restriction is precisely where the pipeline breaks. Unless we are
covering the whole district at once, remove the HPC path or clearly mark it
unsupported. Do not spend more time debugging it.

### 7. Kill "100%" as the goal

100% was never reachable, and chasing it is what produced confident wrong pins.
A pin needs things no satellite can see: who owns the land, whether there is
water, whether anyone will tend it. So a pin should mean **candidate**, not
**verdict**.

Replace the metric. The honest one is:

> **Of our top 50 suggested sites, how many pass a ground check?**

Report that number. It is defensible, it improves over time, and it is
something I can put on a CV. "100% accurate" never was.

### 8. Report back

1. Were Overture buildings actually being used as a filter? On detections,
   suggestions, both, or neither?
2. How many pins does the masking remove, and what does the map look like after?
3. Did the CHM x DeepForest agreement tiering work, and what is the split
   across Trees / Maybe / Needs eyes?
4. Anything in the above you think is wrong. If you disagree, say so with the
   evidence rather than just implementing it.

Do not add Sabzaar to my CV or portfolio yet. A live demo with confident pins
on rooftops contradicts the "honest by design" story my other projects tell.
Once the tiers and masks ship, current accuracy is fine to show.

## PASTE TO HERE
