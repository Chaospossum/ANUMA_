# ANUMA pitch — slide content and talking points

For Juliette and Daniel, who build the slides. One section per slide. Every
number has its source; do not round them differently on the slides.

Not affiliated with NASA, ESA or ispace. Screening tool, not a landing
recommendation.

Why this order: feedback from the ispace call (16 Oct) was that the concept
and how the tool is built matter more than the code. The demo shows the idea;
it is not the centrepiece. Keep the claims realistic (next 5 years).

---

## 1. The problem

**On the slide:** "Writing landing requirements is easy. Combining them and
narrowing hundreds of candidates down to a few is the hard part."

Talking points:
- A lander needs flat ground, sunlight for power, a view of Earth for radio,
  and something worth exploring nearby.
- Each rule is simple. Stacked together over the whole South Pole they leave
  hundreds of candidate points, many of them a few metres apart.
- Today the last step, choosing among them, is a lot of manual checking.

## 2. The idea: a transparent screening workflow (centrepiece)

**On the slide:** a left-to-right diagram

`Requirements → one mask per rule → score what is left → group nearby sites → human check`

Talking points:
- Every rule is its own map layer, so you can see what each rule removes
  ("too steep here", "too little sun here").
- What survives is scored with weights the user sets.
- Nearby candidates are grouped, so 20 points in one flat area count as one
  site.
- The tool suggests an order and says why. A person makes the final choice.
- Public data only: NASA LOLA terrain (20 m), NASA illumination map, Lunar
  Prospector hydrogen, Earth visibility computed from the terrain.

## 3. Demo (3 clicks, under 2 minutes)

1. Show single layer → **Why excluded**: red = too steep, yellow = too little
   flat ground within 1 km. Tick keep-out to add the Artemis III disks.
2. Change **Group sites closer than** from 10 to 2 km: more, closer sites
   appear, and the robustness badges switch to n/a (they were measured at
   10 km).
3. Open the #1 site card: the line under the score says what the score is
   made of and that it is a suggested order. Then **Copy scenario link** to
   show the analysis can be shared and reopened.

Have a screenshot of each step as a backup slide in case the laptop fails.

## 4. Result: an order, with reasons

**On the slide:** "Our suggested #1: 88.81°S 123.64°E, next to the Peak Near
Shackleton region."

| Fact | Number | Source |
| --- | --- | --- |
| Flat ground within 1 km (share of 20 m pixels ≤ 8°) | 66 % (rule: ≥ 50 %) | Stage F, job 5345446 |
| Longest continuous darkness, 2026–2044 (modelled) | 5.8 days; a typical polar spot ≈ 22 days | Stage G, job 5345264 |
| Stays #1 when the weights change (±0.1) | 479 of 500 draws (95.8 %) | Stage H, job 5345720 |
| Ranks 1–6 in the top 10 | in every one of the 500 draws | Stage H, job 5345720 |
| Survives NASA's published height error | 11 of the top 12 sites pass in every draw | Stage H, job 5345720 |

Talking points:
- Present it as "the order we suggest, and why". Do not say "97 % beats 96 %".
- Say what is uncertain: ranks 7–12 move when the weights change; one site
  sits exactly on the 50 % flat-ground line.
- Darkness is "any part of the Sun above the horizon", not a power or
  thermal result.

## 5. How we checked it

| Check | Result | Source |
| --- | --- | --- |
| Our sun model vs NASA's published illumination map | error (RMSE) 0.0198, limit set beforehand 0.05 | Stage A, job 5334265 |
| Coarse 240 m map vs fine 20 m terrain | 7.5 % of "landable" 240 m cells fail at 20 m, so we added the 20 m check | Stage B, job 5334425 |
| Our #1 vs NASA's Artemis III candidate regions | 0.1 km from the Peak Near Shackleton point | Stage F |
| Cost | ≈ 9 node-hours on the MeluXina supercomputer | myquota |

Not possible yet: ispace's Mission 2 targeted Mare Frigoris (about 60.5°N,
4.6°W; ispace, Sept 2024), outside ANUMA's 80–90°S map. Comparing against
manually chosen sites in other regions is a next step (slide 7).

## 6. Rules and law

**On the slide:** "Nobody can own a site on the Moon, but missions can declare
exclusion zones."

Talking points:
- Outer Space Treaty: no national appropriation. Artemis Accords: "safety
  zones" around operations.
- Only about half a dozen South Pole areas are really worth going to, and
  several missions are planned there in the next few years, so they will
  compete for the same sites.
- ANUMA already has a keep-out toggle: exclude or penalise areas around other
  missions' sites (today: the Artemis III candidate regions).

## 7. Next steps (realistic, 1 and 5 years)

**Within 1 year**
- Rover reach: from each landing point, the area a rover can actually drive to.
- Extend the map beyond 80°S and compare with sites chosen by hand (for
  example ispace's Mission 2 area in Mare Frigoris, Apollo target sites).
- Real mission dates instead of scenario windows.
- User-placed relay satellites for communication coverage (simple orbits).

**Within 5 years**
- A shared map of declared exclusion zones from all South Pole missions.
- Use in early mission planning: a fast, reproducible first screen before
  detailed engineering tools.

## Things not to say

- That any site is "safe" or "the best landing site".
- Anything implying ispace affiliation or endorsement. You may say "we
  discussed the tool with ispace and got feedback", but don't make it the
  centre of the pitch.
- That hydrogen means extractable water.
- That the 1 km circle is a real landing ellipse.
