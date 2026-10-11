# Team and Delivery Facts

- The submitted 28 September paper does not mention HPC because MeluXina access came later. The team is adding HPC now. It does not replace the paper. It checks whether the ranking described by the paper still holds at 20 m. The mission case remains open.
- Phases 1–6 are on this branch: ingest, score, API, map, static export, and README demo.
- The mission case is still open: mission requirements, landing ellipse, weights, and which comparison the demo will show.
- ispace is the customer the team is talking to, not a frozen mission scenario.
- Actual data used: `LDEM_80S_20MPP_ADJ`, `AVGVISIB_65S_240M_201608`, and Lunar Prospector `hydrogenhd`.
- Artemis III geometries are null.
- MeluXina has 1 TB reserved. 300 CPU node-hours were accepted as enough; planned use is about 80 node-hours: a 2-hour smoke test, a 40-hour 20 m vs 240 m check, and 40-hour statistical tests. About 220 node-hours are held.
- No cluster job will run until accounts work.
- This is screening, not landing clearance.
- [HPC use plan](hpc-use.md)

The mission case is still open. A bounded Monte Carlo is a possible use of the held node-hours, not a way to spend them for show. The proposed simulation would draw many weight sets and slope limits on the cheap 240 m grid and record how often each site remains in the top ten. It would rerun at 20 m only for sites that are unstable. That is the simulation. It would show ispace which sites survive requirement changes. This is proposed and has not been done; it becomes a useful portfolio line only after jobs actually run.
