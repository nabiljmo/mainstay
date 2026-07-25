# AEZ Creator & Weather Index Insurance Platform — Agreed Specification

*Product of a structured design interview, 24 July 2026. Every decision below was explicitly agreed. Reference material: the Nigeria K-means zoning R script and the Swiss Re Twister Re v1.5 User Guide (in this folder).*

## 1. Product shape

- **Core:** an expert actuarial workbench. Actuaries create zones, design indices, price, and publish products.
- **Quoting is a read-only lookup** against published products — never on-the-fly computation. Flow: expert designs → publishes → anyone quotes.
- **Audiences in order:** Company actuaries (workbench) → field agents + partner APIs (quoting) → farmer-direct (later; USSD/WhatsApp out of scope for v1).
- **Scope: full lifecycle** — quote, bind, and settle — built in that sequence.

## 2. Weather data

- **CHIRPS only** (rainfall; the station-blended product, not CHIRP), fetched by the system from the official UCSB/Climate Hazards Center source. No manual uploads; no bundled dataset.
- **Local cache** of fetched data (source remains the single source of truth; cache is wipeable/refetchable). Cache location configurable (external drive OK); a full country at depth is tens of GB.
- **Actuary chooses history depth.** Minimum 5 years recommended; up to 1981 available; never forced.
- **Data-quality flag** on the pricing screen and product record: green ≥15 yrs, amber 10–14, red <10 ("short history: tail risk may be underestimated"). Advisory only.
- Every published product permanently records: dataset + version, years used, fetch date.
- v2 candidates: ERA5 temperature (degree-day covers), second rainfall source.

## 3. Zoning (AEZ creator)

- **One zone map per country**, shared across all crops and seasons.
- Clustering (k-means family) on location + rainfall features; parameters exposed: country, years, **cluster count**, rainfall sensitivity.
- Workflow: **algorithm draws → actuary reviews (map + per-zone homogeneity scores) → re-runs as needed → Approve** freezes a named, versioned zone map (e.g. `KEN-v1`).
- No hand-editing of boundaries in v1. Optional **"align to admin level 2"** toggle (each district assigned to its majority cluster).
- Engine improvements over the R script: full-depth CHIRPS, richer features (rainfall variability across years, not just mean totals), safe scaling, modern stack.

## 4. Crop knowledge library

- **In-app, versioned, editable library** — first-class product data, not code.
- Per crop: growth stages, durations, water-stress sensitivity weights. Per country: planting window(s).
- **Seeded from FAO sources** (crop calendars, stage/water-requirement literature); reviewed by company agronomists before first use; every edit attributed.
- Products record the crop-library version they were priced with.
- **Fixed calendar phases in v1.** Phase start is implemented as a swappable rule so **dynamic (rainfall-triggered) onset arrives in v2** without rework.

## 5. Index builder

- **Three cover types:** Deficit cover (put on cumulative phase rainfall), Excess cover (call), Continuous dry-day cover (longest run of days below threshold).
- **Linear payouts only:** strike, exit, phase limit; 0% at strike → 100% of phase limit at exit. No staggered/step payouts, no stacked structures.
- Up to 5 phases per product; **phase limits split the sum insured weighted by stage water-stress sensitivity** from the crop library.
- System **auto-proposes** phases (from planting window + stage durations), cover type per phase, and starting triggers (percentile-based, e.g. strike at 30th percentile of zone phase-rainfall history, exit at 5th). Actuary can override everything. Auto-proposed, never auto-final.

## 6. Pricing & publishing

- Per zone: index computed for each historical year → **burning cost** (over chosen window) and **modelled expected loss** (fitted distribution: gamma default; normal/lognormal options; Q-Q plot shown; simulation). **Technical EL = max(BC, model)** (Twister convention).
- No detrending, no tail blow-up factors in v1 (both shown as v2 candidates).
- **Loadings: flexible itemised list.** Recommended default set offered; actuary freely adds/edits/removes named loadings. Each line has a basis: % of EL, % of gross premium, or flat per policy. System handles gross-up math correctly: gross = EL ÷ (1 − Σ %-of-gross loadings).
- **No maker-checker.** The building actuary publishes, via an explicit "make this quotable" confirmation. Publishing writes a full audit record (who, when, every parameter) and **freezes the product as a read-only version**. Changes = new version.
- Published rate table: `(country, crop, season, zone) → premium rate + terms`.

## 7. Quoting

- Inputs: **GPS pin or village/admin-area pick** (fallback matters), crop, season, sum insured.
- Engine: PostGIS point-in-polygon → zone → published product → rate × sum insured. Sub-second.
- Output: quote with reference number — premium, cover summary in plain language, validity date. Quote references trace to the exact product version.
- Pin lands where no product is published → "not yet available here," **logged as a demand signal** for the product team.
- Surfaces: lightweight phone-friendly agent page (cheap Androids, 3G) + partner REST API.

## 8. Binding (policy register)

- **Two-level register from day one:** master policy (e.g. partner/bank group deal) + schedule of farmers beneath it. An individual sale = master policy with one farmer. Both cases confirmed real at the company.
- **Minimal PII:** name, phone (identity + payment address), gender, optional national ID per country KYC. Location/crop come from the quote. Encrypted at rest; role-scoped access (agents see only their own book).
- **Money is recorded, never moved.** Premium collection happens in existing company/partner channels; system records status and activates the policy. Same at payout: system produces the file; existing rails disburse.

## 9. Settlement

- Same engine that priced the product computes each phase's actual index from CHIRPS as the season progresses — **pricing and settlement can never disagree**.
- **Dashboard** shows phase-by-phase progress in-season (provisional estimates from preliminary CHIRPS, clearly labelled).
- **Settlement on CHIRPS final data only** (~3-week lag after month-end — printed into product terms).
- **One payment per farmer, end of season.** No in-season payouts in v1.
- Payout run presented for review (totals, farmer count, largest amounts, zone table, index evidence; anomaly banner if any zone pays >3× its priced EL) → one human clicks **Release file** → payout file (phone, amount, policy, evidence) exported to payment rails.

## 10. Technology

- **Python** backend: FastAPI; xarray/rioxarray (CHIRPS cubes), scikit-learn (clustering), scipy (fitting), geopandas/shapely (zones). Actuarial logic ported from R, not reused.
- **PostgreSQL + PostGIS** — single database for products, policies, farmers, audit, zone lookup.
- **Celery workers** for slow jobs (fetching, zoning, pricing, settlement) — kick off, watch progress, get notified.
- **React + MapLibre** frontend; separate ultra-light agent quote page.
- **Docker Compose from day one.** Runs locally (user's Mac) now; identical stack deploys later to the user's personal server (guided walkthrough promised: Docker + compose file + env file + reverse proxy with HTTPS).

## 11. Pilot

- **Kenya, maize.** Assumed **long rains** season unless stated otherwise (Kenya products are per season; the zone map is per country and unaffected).
- Engine validation: run ported clustering vs the R method on identical inputs; confirm agreement before trusting downstream.
- Then: full-history Kenya zone map → FAO Kenya maize phenology into crop library → design + price a long-rains drought product → compare rates against an existing internal Kenya product if available.

## Build sequence

1. **Data + zoning** — CHIRPS fetch/cache, clustering engine, zone review/approve UI. *Milestone: approved Kenya zone map.*
2. **Crop library + index builder + pricing** — FAO-seeded library, auto-proposed products, pricing engine, publish flow. *Milestone: published Kenya maize product with rates.*
3. **Quoting** — agent page + partner API. *Milestone: GPS pin → premium in under a second.*
4. **Binding** — two-level register, PII handling, premium status. *Milestone: bound schedule under a master policy.*
5. **Settlement** — phase computation, dashboard, payout file. *Milestone: season closed with a released payout file.* (Calendar-aligned: needed only after the first bound season ends.)

## Open items — all resolved (24 July 2026)

1. **Settlement dataset:** no reinsurer mandate. CHIRPS confirmed as both pricing and settlement dataset.
2. **Handoff to existing internal systems:** out of scope — not applicable. API-only interface stands.
3. **In-season payouts:** none promised anywhere. One payment per farmer at season end, confirmed.
4. **Pricing benchmark:** exists, but deliberately withheld — **blind validation**. The system prices Kenya maize long rains independently; the company compares against internal rates afterwards. Consequence for the build: the pricing output must print every assumption (dataset, years, zone map version, crop-library version, triggers, distribution, loadings) so any gap found in comparison is traceable to a cause.
5. **Pilot season: long rains** (roughly March–June), Kenya, maize. Confirmed.
