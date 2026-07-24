---
id: 010
title: Pricing Engine — burning cost, fitted EL, live loadings
type: AFK
labels: [needs-triage]
blocked_by: [009]
user_stories: [16, 17, 18]
phase: 2
---

## What to build

Per-zone pricing on a drafted product: burning cost over the chosen window; distribution fit (gamma default, normal/lognormal options) with Q-Q plot; simulated expected loss; technical EL = max(burning cost, modelled EL). Loadings editor: recommended default set, freely add/edit/remove named lines, each with basis (% of EL, % of gross, flat per policy); premium rate updates live as loadings change; gross-up maths correct (gross = EL ÷ (1 − Σ %-of-gross)). Result: a per-zone rate table on screen.

## Acceptance criteria

- [ ] Burning cost and fitted EL shown per zone with Q-Q plot; technical EL takes the max
- [ ] Distribution choice switchable; fit updates
- [ ] Loadings editor with three bases; live premium recomputation; gross-up verified against hand-calculated cases
- [ ] Data-quality flag from the pricing year range displayed and stored
- [ ] Golden tests: hand-computed burning cost, EL, and loading chains
- [ ] Fit sanity test: synthetic data from known gamma recovers parameters within tolerance

## Blocked by

- 009-index-engine-product-draft
