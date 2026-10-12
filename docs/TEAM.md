# Team and Delivery Facts

- The submitted 28 September paper does not mention HPC because MeluXina access came later. The team is adding HPC now. It does not replace the paper. It checks whether the ranking described by the paper still holds at 20 m. The mission case remains open.
- Phases 1–6 are on this branch: ingest, score, API, map, static export, and README demo.
- The mission case is still open: mission requirements, landing ellipse, weights, and which comparison the demo will show.
- ispace is the customer the team is talking to, not a frozen mission scenario.
- Actual data used: `LDEM_80S_20MPP_ADJ`, `AVGVISIB_65S_240M_201608`, and Lunar Prospector `hydrogenhd`.
- NASA has not published Artemis III centres. Approximate points for the 2022 names are cited from Goossens et al. (2025) Table 1. Four 2024-only names stay null. See `docs/results.md`.
- MeluXina project `p201619` has 300 CPU node-hours this month (0 used when confirmed), plus 10 TB each at `/project/home/p201619` and `/project/scratch/p201619`; personal home is 100 GB. There is no GPU, FPGA, or large-memory allocation. The first guarded smoke test is capped at one CPU node and 30 minutes, with `myquota` captured before and after. Later one-node work remains separately reviewed and bounded; QOS limits are not a reason to claim 100-node scaling.
- No cluster job will run until accounts work.
- This is screening, not landing clearance.
- [Possible studies](hpc-use.md#possible-studies) are optional and unrun; the mission case remains open.
