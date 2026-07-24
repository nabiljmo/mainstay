# PRD: AEZ Creator & Weather Index Insurance Platform

*Status: needs-triage (no issue tracker connected — tracked as a document alongside SPEC.md)*
*Companion document: SPEC.md (full agreed specification, design interview closed 24 July 2026)*

## Problem Statement

the company designs weather index insurance for smallholder farmers across Africa. Today the workflow is fragmented and manual: agro-ecological zones are drawn by a standalone R script running on a retired package stack that no longer installs on modern machines; crop phenology knowledge lives in actuaries' heads and ad-hoc spreadsheets; index design and pricing happen in an external third-party tool (Swiss Re's Twister Re) that has no concept of zones or crops; and settlement is computed separately from pricing, opening the door to inconsistency between what was priced and what pays out.

The consequences: creating a product for a new country takes weeks of manual work across disconnected tools; nothing is versioned or auditable end-to-end; premium quotes cannot be produced instantly in the field; and the zoning method (3 years of data, mean rainfall only) is weaker than the data allows.

## Solution

A single web-based platform that runs the entire lifecycle of a weather index insurance product:

An actuary opens the platform, picks any African country, and the system fetches open-source CHIRPS rainfall data and generates agro-ecological zones by clustering, which the actuary reviews on a map and approves as a frozen version. They pick a crop; the system already knows its phenology (growth stages, water-stress sensitivity, per-country planting windows, seeded from FAO sources) and proposes a phased index product with suggested triggers. The actuary adjusts, prices it (burning cost + fitted distribution, flexible loadings), and publishes — freezing a rate table.

From that moment, anyone authorised can get an instant quote: a GPS pin or village name plus a crop and sum insured returns a premium in under a second, because quoting is a lookup against the published product, never a live computation. Sales are bound into a two-level policy register (master policy + farmer schedule). At season's end, the same engine that priced the product computes actual payouts from actual CHIRPS data and produces a payout file for release — pricing and settlement can never disagree.

Pilot: Kenya, maize, long rains. Validation is blind: the company holds existing premium rates back and compares them against the platform's independently computed rates.

## User Stories

1. As an actuary, I want the system to fetch CHIRPS rainfall data directly from the official open source for any African country, so that I never have to prepare or upload weather data manually.
2. As an actuary, I want fetched weather data cached locally, so that repeat zoning and pricing runs take seconds instead of hours and we do not hammer a free academic server.
3. As an actuary, I want to choose how many years of history to use (minimum 5 recommended, back to 1981 available), so that I control the trade-off between data depth and relevance.
4. As an actuary, I want a data-quality flag (green/amber/red by history depth) shown on screen and stored on the product, so that thin-data pricing is visible rather than silent.
5. As an actuary, I want to generate agro-ecological zones for a country by setting cluster count, rainfall sensitivity, and years, so that zoning takes minutes instead of a manual R workflow.
6. As an actuary, I want to see per-zone homogeneity scores on an interactive map after a zoning run, so that I can judge whether the zones keep their promise before approving them.
7. As an actuary, I want to re-run zoning with different parameters as many times as I like before approving, so that the final map reflects my judgement, not the first attempt.
8. As an actuary, I want to approve a zone map as a frozen, named version, so that every product built on it references an immutable artefact.
9. As an actuary, I want an optional "align to admin level 2" toggle, so that zones can follow district lines when distribution or regulation requires it.
10. As an agronomist, I want to create and edit crop records (growth stages, durations, water-stress weights, per-country planting windows) in the app, so that adding a crop is data entry, not a software release.
11. As an agronomist, I want crop records versioned with every edit attributed, so that we always know whose knowledge a product was built on.
12. As an actuary, I want the system to propose index phases, cover types, and starting triggers automatically from the crop's phenology and the zone's rainfall history, so that I start from a sensible draft instead of a blank form.
13. As an actuary, I want to adjust every proposed phase, trigger, exit, and limit before pricing, so that automation informs my judgement but never replaces it.
14. As an actuary, I want deficit, excess, and continuous dry-day cover types with linear strike/exit payouts, so that I can express the company's core drought and flood products.
15. As an actuary, I want phase limits weighted by each growth stage's water-stress sensitivity, so that the money follows the agronomy.
16. As an actuary, I want burning cost and fitted-distribution expected loss computed per zone, with Q-Q plots and the higher of the two taken as technical EL, so that pricing is prudent and I can see what the model sees.
17. As an actuary, I want to add, edit, and remove named loading line items (as % of EL, % of gross premium, or flat amount) on top of a recommended default set, so that commercial pricing reflects each deal.
18. As an actuary, I want the premium rate to update live as I change loadings, so that I see the commercial consequence of every choice immediately.
19. As an actuary, I want to publish a product myself behind a confirmation step, so that I can move fast without waiting on an approval chain.
20. As an actuary, I want publishing to freeze the product as a read-only version with a full audit record (who, when, every parameter, data and library versions), so that any rate ever quoted can be traced and defended.
21. As an actuary, I want the pricing output to print every assumption (dataset, years, zone map version, crop library version, triggers, distribution, loadings), so that a blind comparison against internal benchmark rates can attribute any gap to a cause.
22. As a field agent, I want to enter a GPS pin or pick a village, a crop, and a sum insured on a lightweight phone-friendly page, so that I can quote a farmer on the spot over a weak connection.
23. As a field agent, I want the quote returned in under a second with a reference number, premium, and plain-language cover summary, so that the farmer can decide while we are still talking.
24. As a partner system, I want a REST API that takes location + crop + sum insured and returns a quote, so that insurance can be embedded in our own loan or input-sales flow.
25. As a product manager, I want quote requests that land where no product is published logged as demand signals, so that we know where to build next.
26. As a field agent, I want to convert a quote into a bound policy capturing minimal farmer details (name, phone, gender, optional national ID), so that a sale takes a minute and we hold no more personal data than necessary.
27. As a partner, I want a master policy with a schedule of farmers beneath it, so that group deals and individual sales live in one register.
28. As an operations manager, I want to record premium receipt against a policy and see its status change to active, so that the register reflects commercial reality without the platform touching money.
29. As an operations manager, I want a season dashboard showing each zone's phase-by-phase index progress (with provisional estimates clearly labelled), so that I can see payouts building before the season closes.
30. As an operations manager, I want settlement computed only on final CHIRPS data by the same engine that priced the product, so that what pays out is exactly what was priced.
31. As an operations manager, I want the season's payout run presented for review — totals, farmer counts, largest amounts, per-zone table, anomaly flags — behind a single "Release file" action, so that one glitch is caught by a click rather than a recovery exercise.
32. As an operations manager, I want a payout file (phone, amount, policy, index evidence) exported for existing payment rails, so that disbursement uses proven channels.
33. As an administrator, I want role-based access (actuary, agronomist, agent, operations, admin) with agents scoped to their own book, so that farmer data is protected and duties are separated.
34. As an actuary, I want long-running jobs (fetching, zoning, pricing, settlement) to run in the background with visible progress, so that the app never freezes mid-task.
35. As the platform owner, I want the whole system to run locally via Docker Compose and deploy identically to my own server later, so that moving to production is configuration, not rebuilding.

## Implementation Decisions

The platform is organised around deep modules — engines with small, stable, testable interfaces — wrapped by a thin web layer:

- **Weather Store** — single interface for "give me rainfall series for these pixels and dates." Encapsulates CHIRPS discovery, download, local caching, and dataset versioning. Nothing else in the system knows the data comes from files on a remote server.
- **Zoning Engine** — pure computation: rainfall cube + parameters in, zone polygons + homogeneity scores out. Features go beyond the R script: full-depth history and inter-annual variability, not just mean totals. Admin-snap is a post-processing option.
- **Crop Library** — versioned records of stages, durations, stress weights, planting windows. FAO-seeded, agronomist-owned.
- **Index Engine** — the heart. Product definition + rainfall series in; per-phase, per-year index values and payouts out. Deliberately date-agnostic: runs on historical data (pricing) and current-season data (settlement) identically. Phase start is a swappable rule (fixed calendar in v1, dynamic rainfall-triggered onset in v2 without rework).
- **Pricing Engine** — index history + payout structures + loadings in; burning cost, fitted expected loss (gamma default; normal/lognormal options), technical rate, and gross premium out. Correct gross-up for %-of-gross loadings: gross = EL ÷ (1 − Σ %-of-gross).
- **Product Registry** — versioning, publish/freeze semantics, audit records, published rate tables keyed (country, crop, season, zone).
- **Quote Service** — point-in-polygon zone lookup (PostGIS) + rate-table lookup. Read-only by construction.
- **Policy Register** — two-level model (master policy + farmer schedule); minimal PII, encrypted at rest, role-scoped. Records money, never moves it.
- **Settlement Service** — orchestrates the Index Engine over live final CHIRPS per closed phase; season dashboard; payout-run review and release gate; payout file export.

Architecture: Python backend (FastAPI), PostgreSQL + PostGIS as the single database, Celery background workers for all slow jobs, React + MapLibre frontend with a separate ultra-light agent quote page, all composed via Docker. Runs locally first; deploys unchanged to the owner's server.

Key product decisions carried in from the specification: CHIRPS is both pricing and settlement dataset (no reinsurer mandate); one zone map per country shared across crops and seasons; quoting is lookup-only against published products; no maker-checker on publishing (confirmation + audit instead); one payment per farmer at season end; settlement on final CHIRPS only (~3-week lag, printed into terms); no integration with external the company systems (API-only boundary).

## Testing Decisions

Good tests here assert external behaviour through each module's public interface — given these inputs, this output — never internal structure. The engines are pure functions over data, which makes them ideally testable; the web layer stays thin precisely so the things that matter are testable without a browser.

Priority order (money first):

- **Index Engine** — the highest-stakes module: it computes both prices and payouts. Golden tests: hand-computed index values and payouts for small synthetic rainfall series across all three cover types, phase boundaries, and edge cases (exactly-at-strike, exactly-at-exit, missing days). Property test: payout is monotonic in rainfall deficit and never exceeds the phase limit.
- **Pricing Engine** — golden tests against hand-calculated burning cost and loading gross-up cases; distribution-fitting sanity checks on synthetic data with known parameters.
- **Zoning Engine** — reproducibility test (same inputs + seed → identical zones) and a cross-check against the original R method's logic on identical inputs, replacing the lost Nigeria benchmark.
- **Quote Service** — integration tests: known pin inside a known zone returns the published rate; pin outside any product returns "not available" and logs the demand signal.
- **Settlement Service** — end-to-end season simulation on synthetic data: bound policies + a season of rainfall → expected payout file, including the anomaly-flag path.

No prior art exists in this repository (greenfield); these tests establish the house style.

## Out of Scope

- Farmer-direct quoting channels (USSD, WhatsApp) — v1 serves agents and partner APIs.
- Moving money — premium collection and payout disbursement stay on existing rails; the platform records and computes only.
- Dynamic rainfall-triggered phase onset — designed for, built in v2.
- Temperature data and degree-day covers (ERA5) — v2.
- Additional rainfall datasets alongside CHIRPS — v2.
- Detrending and tail blow-up factors in pricing — v2 candidates.
- Manual zone-boundary editing — approval gate and admin-snap only in v1.
- Staggered/step payout structures and stacked structures per phase.
- In-season or per-phase payouts — one payment per farmer at season end.
- Integration with existing the company internal systems — explicitly excluded by the owner; the API is the boundary.
- Photos, biometrics, household surveys in the policy register.

## Further Notes

- Pilot: Kenya, maize, long rains (March–June). Build sequence and milestones live in SPEC.md: data + zoning → crop library + index + pricing → quoting → binding → settlement (calendar-aligned to the first bound season's end).
- Blind validation is a hard constraint: the company's internal Kenya maize rates exist but are withheld until the platform has priced independently. Do not request them; make every pricing assumption visible instead.
- The Twister Re v1.5 User Guide (this folder) is the reference for pricing conventions (relevant-BC = max of burning cost and modelled EL; distribution options) — used for guidance, not copied.
- CHIRPS cache needs tens of GB per country at full depth; cache location must be configurable (external drive).
- The original R script's known defects (retired packages, O(n²) capping loop, path bugs, unsafe NA handling, independent x/y scaling) are documented in this conversation's history and must not be ported.
