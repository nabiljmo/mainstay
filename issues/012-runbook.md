# Runbook — 012 · Kenya maize long-rains: publish + blind rate validation

**Owner:** The actuary (HITL) · **Blocked by:** 007 (Kenya zone map), 011 (publish) — both done.
**Goal:** publish the pilot product, then prove the engine by comparing its rates
against the company's withheld internal rates and attributing every gap to a cause.

---

## The one rule

> **The platform produces and records its rates *before* anyone looks at the
> benchmark.** Do not open, request, or glance at the company's internal rates until
> Phase C is signed off and timestamped. The whole point of the exercise is that
> the engine was not tuned to a known answer.

If the benchmark has already been seen for these zones, this validation is not
blind — note that honestly in the verdict rather than pretending otherwise.

---

## Pre-flight (all must be true before you start)

- [ ] Approved Kenya zone map exists and is the one you intend to price on
      (currently **Ken-v2**, 30 zones). Products → Zone map picker shows it.
- [ ] CHIRPS cached for the pricing years (2021–2025 shown under *Cached years*).
      More years = better tail; 5 years will flag **red** data quality — that is
      expected for the pilot and must be disclosed on the assumption sheet.
- [ ] Crop library: maize version is the one you want, and its **reviewed** flag
      reflects reality. It currently reads **UNREVIEWED** on the assumption
      sheet — either get an agronomist sign-off first, or accept and record that
      the pilot priced on FAO-typical (unreviewed) phenology.
- [ ] The three test publishes I made while building 011
      (`KEN-maize-long_rains-v1..v3`) are **throwaway**. The real pilot is simply
      the next version; note in the verdict which version is the official record.

---

## Phase A — Design & price the pilot (Products tab)

Do this per zone; the sticky bar shows the live rate as you work.

1. Open a fresh draft: Zone map **Ken-v2**, crop **maize**, season **long_rains**,
   sum insured **10,000**, → *Draft product*.
2. For **each zone** (step through the Zone dropdown):
   - [ ] Check each phase's **cover type** matches the agronomy (dry-spell for
         establishment/flowering, deficit for vegetative/grain-filling by default).
   - [ ] Review **strike/exit** against the FAO record and the *What these
         settings mean* plain-words panel. Edit in % or absolute.
   - [ ] Sanity-check the **Historical payouts** table — do the years that paid
         match seasons you know were bad?
   - [ ] Set **loadings** (uncertainty, admin, distribution, profit). Keep them
         identical across zones unless you have a reason not to — and record the
         reason if you do.
   - [ ] Hit **Recompute payouts**; the zone is now captured for publish.
3. Note anything you deliberately left as the auto-proposal vs. actively changed
   — this feeds the gap attribution later.

## Phase B — Publish & freeze

4. [ ] Click **Publish product** in the sticky bar → confirm.
5. [ ] Green banner shows `KEN-maize-long_rains-vN`, all 30 zones frozen. If it
       instead lists invalid zones, fix those zones (strike/exit ordering) and
       re-publish.
6. [ ] Open the **assumption sheet** (banner link) → browser **Print → Save as
       PDF**. This PDF is the immutable record of every input.

## Phase C — Record platform rates (STILL BLIND)

7. [ ] Export the frozen rate table (from the banner, or
       `GET /products/published/{id}`).
8. [ ] Save the rates + assumption-sheet PDF somewhere dated, and **write down
       the publish timestamp** (`published_at`). This is your "produced before
       benchmark" proof.
9. [ ] Only now, sign off Phase C. Everything above happened without the benchmark.

---

## Phase D — Blind comparison (now open the benchmark)

10. [ ] Retrieve the company's withheld internal rates for the same
        (crop, season, zones). Confirm the comparison is like-for-like: same sum
        insured basis, same "rate = premium ÷ SI" definition, gross vs. net.
11. [ ] Build the per-zone comparison (template below).

## Phase E — Gap attribution

For every material gap, attribute it to one cause. Do not "fix" a rate to match —
explain the difference. Use the assumption sheet as the evidence.

| Zone | Platform rate | Internal rate | Gap (pp) | Attributed cause | Evidence / note |
|-----:|--------------:|----------:|---------:|------------------|-----------------|
|  1   |               |           |          |                  |                 |
|  …   |               |           |          |                  |                 |

Cause must be one of:
- **Data years** — different/short CHIRPS window (pilot is 2021–2025, red flag).
- **Loadings** — different expense/margin assumptions.
- **Cover design** — phases, cover types, strike/exit, limits differ.
- **Method** — burning-cost-vs-fit, distribution choice, gross-up convention.

Summary stats to record: mean absolute gap (pp), max gap + which zone, count of
zones within ±X pp (agree on X, e.g. 3pp, up front).

---

## Phase F — Verdict & follow-ups

12. [ ] Record the verdict, one of:
    - **Engine trusted** — gaps small and fully explained by disclosed
      assumptions; no method errors.
    - **Issues found** — file a follow-up issue per problem (method bug, cover
      mis-design, data gap). Link them here.
13. [ ] Note the official pilot version id, the publish timestamp, and where the
        rates PDF + comparison sheet are stored.
14. [ ] Tick 012's acceptance boxes and close.

### Verdict record

- Official product version: `KEN-maize-long_rains-v___`
- Published at (blind cutoff): `__________`
- Mean abs gap: `___ pp` · Max gap: `___ pp` @ zone `__`
- Verdict: `trusted / issues found`
- Follow-up issues: `#___, #___`

---

## Appendix — endpoints used

```bash
# List published products (find the version id)
curl -s http://localhost:8000/products/published | python3 -m json.tool

# Frozen product incl. rate table + assumption data
curl -s http://localhost:8000/products/published/KEN-maize-long_rains-vN | python3 -m json.tool

# Assumption sheet (open in browser, Print → PDF)
open http://localhost:8000/products/published/KEN-maize-long_rains-vN/assumption-sheet

# Quote-source rate lookup (what distribution will use)
curl -s "http://localhost:8000/rates?country=KEN&crop=maize&season=long_rains&zone=5" | python3 -m json.tool
```
