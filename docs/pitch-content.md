# ANUMA pitch — slide content and talking points

For Juliette and Daniel, who build the slides. Not affiliated with NASA, ESA or
ispace. Screening tool, not a landing recommendation.

## What the ispace feedback asked for (16 Oct call), and where it is

| They said | Where in the pitch |
| --- | --- |
| The idea and the presentation matter more than the code; the code demonstrates it | Slides 2–3 carry the pitch; the demo is one slide |
| Show how the tool is logically built and how a user would use it | Slides 2 and 3 |
| The hard part is stacking rules and narrowing hundreds of candidates | Slide 1 |
| Group candidates that are metres apart; don't treat them as separate sites | Slide 2, demo step 2 |
| One layer per rule, clickable, showing why an area is excluded | Slide 2, demo step 1 |
| Ranking with uncertainty, but "the order we suggest, and why", with a human check | Slide 4 |
| Compare with sites chosen by hand | Slide 5 |
| Legal: no ownership, but exclusion zones; few good South Pole sites | Slide 6 |
| Import/export so others can reproduce results | Slide 3 (copy link, CSV/GeoJSON) |
| (NeoSpace briefing) value, plan and funding needs | Slide 6 |
| End with next steps: 1 and 5 years, realistic, regulatory/business/industry/technical | Slide 7 |

Keep numbers few. They like numbers, but the idea comes first.

## Format and timing (from the NeoSpace briefing)

**5-minute pitch + 5-minute Q&A.** The pitch must pick the strongest evidence,
explain the solution and its value, and show the plan and funding needs.
There is also a 10–15 page proposal (technology ~5 p, business model ~3 p,
implementation plan ~4 p).

| Slide | Time |
| --- | --- |
| 1 Problem | 0:30 |
| 2 Idea (workflow) | 1:00 |
| 3 Demo | 1:00 |
| 4 Result + how we checked it | 0:45 |
| 5 Rules and law | 0:30 |
| 6 Business and funding | 0:45 |
| 7 Next steps and the ask | 0:30 |
| **Total** | **5:00** |

Section 4b ("How we checked it") is folded into slide 4 when presenting;
keep its full table for the proposal and the Q&A.

---

## 1. The problem

**On the slide:** "Writing landing requirements is easy. Combining them and
narrowing hundreds of candidate points down to a few sites is the hard part."

- A lander needs flat ground, sunlight for power and a view of Earth for radio.
  Each rule is simple.
- Stacked over the whole South Pole they still leave hundreds of points, many
  only metres apart, and someone checks them one by one.
- ANUMA makes that last step faster and explainable, not automatic.

## 2. The idea: a transparent workflow (centrepiece)

**On the slide:** one left-to-right diagram

`Requirements → one map layer per rule → score what is left → group nearby points → suggested order + reasons → human check`

- **One layer per rule.** Each rule is its own map, so you can click through
  them and see what each removes: "too steep here", "too little flat ground".
- **Score what survives** with weights the user sets (sunlight, hydrogen, Earth
  view).
- **Group nearby points.** Points within a chosen distance (default 10 km)
  count as one site, so the list shows places, not pixels.
- **Suggested order with reasons.** Every site says what its score is made of.
  A person makes the final choice.
- Public data only: NASA LOLA terrain (20 m), NASA illumination map, Lunar
  Prospector hydrogen, Earth view computed from the terrain.

## 3. How a mission planner would use it (demo, 1 minute)

**On the slide:** three screenshots in a row (backup if the laptop fails).

1. **Why excluded:** Show single layer → *Why excluded*. Red = too steep,
   yellow = too little flat ground within 1 km. Tick keep-out to add zones
   around other missions.
2. **Group sites:** change *Group sites closer than* from 10 to 2 km. More,
   closer sites appear. The robustness badges switch to n/a, because those
   studies used 10 km: the tool says when its own checks no longer apply.
3. **Reasons and sharing:** open the #1 site card. The line under the score
   says what the score is made of and that it is a suggested order. Then
   *Copy scenario link*: a colleague opens the same analysis on their own
   computer (CSV and GeoJSON downloads too).

Talking point: requirements and dates change all the time in a real mission,
so every setting is an input you can change and share.

## 4. The result: an order, with reasons

**On the slide:** "Suggested #1: 88.81°S 123.64°E, next to the Peak Near
Shackleton region — and why."

Three reasons, one number each:
- **Flat enough:** 66 % of the ground within 1 km is flat at 20 m (rule ≥ 50 %).
  Source: Stage F, job 5345446.
- **Short nights:** longest continuous darkness 5.8 days, against about
  22 days at a typical polar spot (modelled 2026–2044). Source: Stage G,
  job 5345264.
- **Stable:** stays #1 in 479 of 500 runs with changed weights (95.8 %).
  Source: Stage H, job 5345720.

Say the uncertainty out loud:
- Ranks 7–12 move when the weights change.
- One site sits exactly on the 50 % flat-ground line.
- "This is the order we suggest and why. A person still checks it."

## 4b. How we checked it (fold into slide 4; full table for the proposal)

**On the slide:** two checks and one honest gap.
- Our sunlight model matches NASA's published illumination map (error 0.0198,
  limit set beforehand 0.05). Source: Stage A, job 5334265.
- Our #1 lies 0.1 km from NASA's hand-picked Artemis III "Peak Near
  Shackleton" candidate region: the same kind of place experts choose.
  Source: Stage F.
- Gap: ispace's own Mission 2 sites (Mare Frigoris, about 60.5°N) are outside
  our 80–90°S map, so we could not compare with them yet (slide 7).

## 5. Rules and law

**On the slide:** "Nobody can own a site on the Moon, but missions can protect
their area with exclusion zones."

- Outer Space Treaty: no national appropriation. Artemis Accords: "safety
  zones" around operations.
- Only about half a dozen South Pole areas are really worth going to, and
  several missions are planned there in the next few years, so they will want
  the same places.
- ANUMA already has a keep-out switch: exclude or penalise areas around other
  missions' sites (today: the Artemis III candidate regions).

## 6. Business and funding (DRAFT — team to confirm every number)

**On the slide:** "Who pays: lander and payload mission-analysis teams. What they
get: a validated first screen in hours instead of weeks."

- **Customers:** mission-analysis teams at lunar lander companies and payload
  providers (e.g. in agency programmes like CLPS or ESA's Argonaut); agencies
  as validation partners, not first buyers.
- **Free vs paid:**
  - Free: the public map with default rules (builds trust and visibility).
  - Paid: private scenarios with the customer's own rules (ellipse size,
    slope limit, rover reach), a written screening report with the evidence
    trail, and reruns on the supercomputer for their rules.
- **Why not rebuild it in a week:** the value is the validated chain (frozen
  checks against NASA data, 20 m terrain, robustness studies), not the map.
- **Ask (placeholder, decide as a team):** 12 months, 2 people, a small HPC
  allocation (the full study so far used about 9 node-hours), and **one
  lander team as a design partner** to set real requirements.
  Put a € figure here only once the team has costed it.

## 7. Next steps: 1 year and 5 years

**Within 1 year**
- *Technical:* rover reach — from each landing point, the area a rover can
  actually drive to (a long narrow corridor is not the same as an open area).
- *Technical:* extend the map beyond 80°S and compare with sites chosen by
  hand (ispace Mission 2 area, Apollo target sites).
- *Industry:* real mission dates and user-placed relay satellites as inputs.

**Within 5 years**
- *Regulatory:* a shared map of declared exclusion zones from all South Pole
  missions, so planners see where others will be.
- *Business:* a fast, reproducible first screen that mission teams use in early
  planning, before detailed engineering tools.

Close with: "A transparent first screen: every rule visible, every site
explained, every analysis shareable."

## Hard Q&A (prepare these)

1. "Your #1 is next to a NASA region — did you tune it?" Same public data and
   physics; rules were frozen before each run; change the slope limit and the
   #1 changes, so nothing is hard-coded.
2. "Earth view near the pole?" It is Earth's elevation at mean libration; the
   Moon's wobble (about ±7°) makes contact intermittent there. Real contact
   windows are a 1-year step.
3. "A 1 km circle is not a landing ellipse." Agreed; it stands in for "most of
   the ellipse must be flat". The ellipse becomes a customer input.
4. "Who pays, and why can't they rebuild it?" See slide 6.
5. "Other missions near Shackleton?" The keep-out switch takes any
   coordinates; we only loaded sets with published coordinates. That gap is
   why a shared exclusion-zone register is the 5-year step.

## Things not to say

- That any site is "safe" or "the best landing site".
- Anything implying ispace affiliation or endorsement. You may say "we
  discussed the tool with ispace and got feedback", but don't make it the
  centre of the pitch.
- That hydrogen means extractable water.
- That the 1 km circle is a real landing ellipse.
- Far-future assumptions ("a GPS constellation around the Moon"). Keep it to
  the next 5 years.
