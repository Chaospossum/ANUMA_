# ANUMA — proposal draft (NeoSpace Challenge 2026)

DRAFT for the team. Structure follows the briefing: 10–15 pages, technology
~5 p, business model ~3 p, implementation plan ~4 p. Text marked **[TEAM]**
needs a decision or a number from the team; do not submit with it. Numbers
come from [results.md](results.md) and the stage records; keep their job ids.

Independent student project; not affiliated with or endorsed by ispace, ESA,
NASA or the MAGPIE mission team.

---

## Part A — Technology (~5 pages)

### A1. Problem and users

Landing-site requirements are easy to write and hard to combine. Stacked over
the lunar south pole (80–90°S, about 5 million 240 m cells) they still leave
hundreds of candidate points, many metres apart, and engineers check them one
by one. Several missions are heading to the same few areas, so early, fast and
explainable screening matters.

Users: mission-analysis engineers at lander and payload teams (primary), and
agency reviewers who need to see why a site was chosen (secondary).

### A2. Concept and operating scenario

`Requirements → one map layer per rule → score what is left → group nearby
points into sites → suggested order with reasons → a person checks`

- Each hard rule is its own map layer; the *Why excluded* layer shows what each
  rule removes (red: too steep; yellow: too little flat ground within 1 km).
- What survives is scored with user weights (sunlight, hydrogen, Earth view).
- Candidates within a chosen distance (default 10 km) count as one site.
- Every site states what its score is made of; the order is a suggestion and a
  person makes the decision.
- Every analysis can be shared as a link and exported (CSV, GeoJSON) so another
  team can reproduce it.

Operating scenario: an engineer sets the mission's rules and dates, steps
through the layers, compares the top sites and exports the shortlist for
detailed engineering tools. Requirements and dates change often, so every
setting is an input.

### A3. Technical architecture

- Data (public): NASA LOLA 20 m DEM and its height-error layer; ANUMA's sun
  model checked against NASA's average-illumination map; Lunar Prospector
  hydrogen; Earth visibility from the DEM; Artemis III candidate regions
  (approximate points, Goossens et al. 2025). Details: [SOURCES.md](../SOURCES.md).
- Compute: Python (NumPy) on a 240 m polar stereographic grid; full-cap
  studies on the MeluXina supercomputer (about 9 CPU node-hours in total).
- Delivery: FastAPI back end and a browser front end (Leaflet); works offline
  on a laptop.

### A4. Feasibility and maturity (evidence)

| Check | Result | Record |
| --- | --- | --- |
| Sun model vs NASA's illumination map | RMSE 0.0198 (limit 0.05 fixed before the run) | Stage A, job 5334265 |
| 240 m vs 20 m slope | 7.5 % of 240 m "landable" cells fail at 20 m → 20 m flat-ground rule added | Stage B, job 5334425 |
| Robustness to weights | #1 wins 479 of 500 draws (±0.1); ranks 1–6 always in the top 10 | Stage H, job 5345720 |
| Robustness to terrain error | 11 of the top 12 pass in every one of 200 noise draws | Stage H, job 5345720 |
| Agreement with expert choice | #1 lies 0.1 km from the approximate point (Goossens et al. 2025) of NASA's Peak Near Shackleton candidate region | Stage F, job 5345446 |

Suggested #1: 88.81°S 123.6°E, 66 % flat ground within 1 km, longest
continuous darkness 5.8 days (modelled 2026–2044) against about 22 days at a
typical polar spot.

Maturity **[TEAM: state the TRL you claim, e.g. a working prototype validated
against public reference data]**.

Limits we state openly: thresholds (8°, 50 %, 1 km) not yet varied; the 1 km
circle stands in for a landing ellipse; Earth view is a mean-libration proxy;
hydrogen is regional (0.5°); no real mission dates.

### A5. Regulation and standards

- Outer Space Treaty, Article II: no national appropriation; ANUMA never
  claims or "reserves" a site.
- Artemis Accords: safety zones and deconfliction; ANUMA's keep-out switch
  excludes or penalises areas around other missions' sites (today the Artemis
  III candidate regions, the only set with citable coordinates).
- Data licences: NASA PDS/PGDA products are public; citations are kept with
  every layer.
- **[TEAM: add relevant ESA/LSA guidance or space-resources law in Luxembourg
  if you want to cite it.]**

---

## Part B — Business model (~3 pages)

### B1. Market and customers

Demand is real and near-term:

- NASA's Commercial Lunar Payload Services contracts have a cumulative maximum
  value of $2.6 billion through 2028
  ([NASA](https://www.nasa.gov/reference/commercial-lunar-payload-services/)).
  In July 2026 NASA awarded about $590 million for four deliveries targeting
  late 2028 under its Moon Base Program near the south pole
  ([report](https://www.newsbytesapp.com/news/science/nasa-awards-590m-to-3-us-firms-for-moon-landings/story)).
- ESA awarded €862 million in January 2025 for the Argonaut lunar descent
  element; the first mission is projected for about 2030
  ([Space.com](https://space.com/the-universe/moon/europe-signs-contract-for-its-1st-moon-lander-argonaut),
  [European Spaceflight](https://europeanspaceflight.com/thales-alenia-space-finalises-industrial-team-for-esas-argonaut-lunar-lander/)).

Customers: mission-analysis teams at lander companies and payload providers in
these programmes; agencies as validation partners rather than first buyers.
**[TEAM: name 3–5 target organisations and why each.]**

Alternatives today: in-house scripts and GIS work per mission; agency tools
that are not available to commercial teams. **[TEAM: check and name.]**

### B2. Value proposition, offer and revenue

- Value: a validated first screen in hours instead of weeks, with every rule
  visible, every site explained and every analysis reproducible.
- Free: public map with default rules (trust, visibility, recruiting).
- Paid: private scenarios with the customer's own rules (ellipse, slope, rover
  reach, dates), a written screening report with the evidence trail, and HPC
  reruns for their rules. **[TEAM: pricing model — per study, annual licence,
  or service contract — and indicative prices.]**
- Why not rebuild it in a week: the value is the validated chain (frozen checks
  against NASA data, 20 m terrain, robustness studies), not the map.

### B3. Economics and assumptions

**[TEAM: cost per study (people hours + HPC), price, break-even. Keep it
simple: one table with assumptions you can defend in Q&A.]**

---

## Part C — Implementation plan (~4 pages)

### C1. R&D work, milestones and validation

| When | Milestone | Validation |
| --- | --- | --- |
| Months 0–3 | Customer ellipse and slope limit as inputs; precomputed flat-ground layers for 6–12° | Re-run Stage F/H per limit |
| Months 3–6 | Rover reach from each landing point | Compare with published traverse studies |
| Months 6–9 | Libration-resolved Earth contact windows; real mission dates | Against ephemeris-based visibility |
| Months 9–12 | Map beyond 80°S; comparison with hand-picked sites (e.g. ispace Mission 2 area, Apollo targets) | Overlap with expert choices |

### C2. Resources, budget and use of funds

**[TEAM: fill this table; it is the funding ask in the pitch.]**

| Item | Amount | Use |
| --- | --- | --- |
| People | [TEAM] FTE × 12 months | development, validation |
| Compute | [TEAM] node-hours | studies per customer rule set (current full study ≈ 9 node-hours) |
| Data and tools | [TEAM] | |
| Travel / customer workshops | [TEAM] | design-partner sessions |
| **Total ask** | **[TEAM] €** | |

### C3. Team, partners and delivery risks

- Team **[TEAM: roles of Nicole, Juliette, Daniel]**.
- Partners wanted: one lander team as design partner; MeluXina/LuxProvide for
  compute; a university group for validation.
- Risks: customers keep site selection in-house (mitigation: design partner,
  free tier); data gaps at 20 m near the pole (mitigation: state uncertainty,
  height-error studies); regulatory changes on safety zones (mitigation: rules
  as inputs, not hard-coded).

### C4. Next steps, feedback and references

- 1 year: the milestones above.
- 5 years: a shared map of declared exclusion zones from South Pole missions;
  use in early mission planning before detailed engineering tools.
- Expert feedback received: the ispace call of 16 October 2026 (concept and
  workflow first; grouping of nearby sites; per-rule layers; order with
  reasons; comparison with hand-picked sites; realistic next steps) —
  all reflected above.
- References: [SOURCES.md](../SOURCES.md), stage records in `docs/`.
