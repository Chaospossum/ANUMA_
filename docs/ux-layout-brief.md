# UX layout brief: answer first, workflow as navigation

Branch `feat/ux-layout`. Front end only (`static/index.html`, `static/app.js`,
`static/style.css`); `anuma/api.py` unchanged.

## Users

| User | First question | Looks at first | Must find without help |
| --- | --- | --- | --- |
| Presenter (NeoSpace pitch, projector 1280×720 / 1920×1080, 5–10 m) | "Where is the answer, and where are my three demo clicks?" | The banner: #1 site and three numbers | *Why excluded*, *Group within 2 km*, *#1 site card*, *Copy scenario link* — each one click, never behind the drawer |
| Judge / ispace engineer (over the shoulder or screenshot) | "What does each rule remove, why is #1 first, and is this a verdict?" | The workflow row above the map, then the map and the top rows | Click through the rule steps; the honesty line under the score ("suggested order, not a verdict") |
| Mission-analysis engineer (laptop 1440×900) | "How do I set my own weights and rules, compare sites, and share?" | The rail, grouped by intent | Score, CSV/GeoJSON, Copy scenario link, pinned comparison cards |

## Design decisions

1. **Answer, then why, then controls.** Banner stays on top. In presentation
   mode its three facts become large chips (lit %, longest night, flat ground).
2. **The workflow is the navigation.** The old one-line "Requirements → … →
   a person checks" text becomes a clickable stepper above the map, shown in
   both modes: *1 Slope rule → 2 Flat ground → 3 Why excluded → 4 Score →
   5 Group → 6 Suggested order → 7 Human check*. Steps 1–4 switch the map
   layer (slope, 20 m flat ground, why-excluded, combined score); step 5 draws
   the grouping radius around every ranked site; step 6 opens the #1 card;
   step 7 highlights the check columns and opens the card. Each step's sub-line
   states the rule as set and what it removes (cells passing slope, cells removed
   by flat ground, keep-out state, weights, sites after grouping).
3. **Demo bar in presentation mode.** Four large buttons in the header, always
   visible with the drawer closed: *Why excluded*, *Group within 2 km* (toggles
   back to the default), *#1 site card*, *Copy scenario link*.
4. **Rail grouped by intent** with progressive disclosure (`<details>`):
   *What matters* (weights, open), *Hard rules* (slope, slope radius, 20 m flat
   ground, keep-out, open), *Grouping* (open), *Time window* (closed), *Show on
   map* (layer select, Artemis overlay, closed). *Share & export* (Score, CSV,
   GeoJSON, Copy link) stays pinned at the bottom of the rail.
5. **Accessibility.** Visible `:focus-visible` outlines everywhere; stepper
   buttons carry `aria-current="step"`; verdict marks keep shape + colour; the
   why-excluded layer keeps red/yellow (distinct for the common colour-vision
   deficiencies) and the legend names each colour.
6. **Nothing invented.** Every count comes from `/score`
   (`terrain_gate_detail.cells_passing_slope`, `cells_removed`, site count) or
   from the request. All honesty statements stay where they were.

## Wireframes

### Normal mode (1440×900), before

```
+--------------------------------------------------------------------------+
| anuma                       [Scenario ◂]  subtitle   [Presentation mode] |
| ▌Suggested #1 … — long sentence with every fact …                        |
| ▌checks line · scenario tags                                             |
+------------+-------------------------------------+-----------------------+
| Scenario   |                                     | ranked table          |
| sliders    |                map                  |                       |
| slope      |                                     |                       |
| group      |                                     | legend / filter       |
| flat rule  |                                     |                       |
| keep-out   |                           [legend]  | site cards            |
| window     |                                     |                       |
| layer ▾    |                                     |                       |
| artemis    |                                     |                       |
| [Score] CSV GeoJSON Link                         |                       |
+------------+-------------------------------------+-----------------------+
| not modelled · layers · disclaimer                                       |
+--------------------------------------------------------------------------+
```

### Normal mode, after

```
+--------------------------------------------------------------------------+
| anuma                       [Scenario ◂]  subtitle   [Presentation mode] |
| ▌[1 Slope ≤ 8°][2 Flat ground ≥50 %][3 Why excluded][4 Score 50/30/20]   |
| ▌[5 Group 10 km → 10 sites][6 Suggested order #1][7 Human check]         |
| ▌Suggested #1 near NASA's Peak Near Shackleton … (facts)                 |
| ▌checks line · scenario tags                                             |
+------------+-------------------------------------+-----------------------+
| ▾ What     |                                     | ranked table          |
|   matters  |                map                  |                       |
| ▾ Hard     |      (layer follows the step)       |                       |
|   rules    |                                     | legend / filter       |
| ▾ Grouping |                                     |                       |
| ▸ Time     |                           [legend]  | site cards            |
|   window   |                                     |                       |
| ▸ Show on  |                                     |                       |
|   map      |                                     |                       |
| [Score] CSV GeoJSON Copy link  (pinned)          |                       |
+------------+-------------------------------------+-----------------------+
| not modelled · layers · disclaimer                                       |
+--------------------------------------------------------------------------+
```

### Presentation mode (1280×720), before

```
+--------------------------------------------------------------------------+
| anuma                      [Scenario ▸] subtitle [Presentation mode]     |
| ▌Requirements → one map layer per rule → … → a person checks (text)      |
| ▌Suggested #1 … — lit 88 % · longest night 5.8 d · 66 % flat            |
| ▌checks line · scenario tags                                             |
+-----------------------------------+--------------------------------------+
|              map                  | ranked table (10 rows, one line each)|
|   (drawer opens over it)          |                                      |
|                          [legend] | legend row                           |
+-----------------------------------+--------------------------------------+
| footer                                                                   |
+--------------------------------------------------------------------------+
```

### Presentation mode, after

```
+--------------------------------------------------------------------------+
| anuma  Demo: [Why excluded] [Group within 2 km] [#1 site card] [Copy link]|
|                                  [Scenario ▸] subtitle [Presentation mode]|
| ▌[1 Slope][2 Flat ground][3 Why excluded][4 Score][5 Group][6 Order][7 ✓]|
| ▌Suggested #1 near NASA's Peak Near Shackleton    88 % lit | 5.8 d | 66 %|
| ▌checks line · scenario tags                                             |
+-----------------------------------+--------------------------------------+
|              map                  | ranked table (10 rows, one line each)|
|   (drawer opens over it)          |                                      |
|   [site card floats here]         |                                      |
|                          [legend] | legend row                           |
+-----------------------------------+--------------------------------------+
| footer                                                                   |
+--------------------------------------------------------------------------+
```

## What moves where

| Before | After |
| --- | --- |
| `#answer-steps` static text (presentation only) | `#answer-steps` clickable stepper (both modes) |
| Layer `<select>` buried in the rail | Still in *Show on map*, now also driven by the stepper (kept in sync both ways) |
| Three demo moves inside the drawer | `#demo-bar` in the header (presentation mode) |
| Flat rail of fields | Five `<details>` sections by intent, Share & export pinned |
| Presentation facts as a sentence | `#answer-chips`: three chips with the number first |

## Out of scope (deliberately)

- Marker placement algorithm (`placeLabels`) and place-name avoidance.
- Any API change; a "cells with data" total for the slope step would need one.
- File import, new keep-out sets, Earth-view model changes.
