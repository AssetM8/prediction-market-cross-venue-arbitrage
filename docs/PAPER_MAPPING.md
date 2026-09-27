# Paper mapping

Source: Oriol Saguillo, Vahid Ghafouri, Lucianna Kiffer, Guillermo Suarez-Tangil,
*Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets*,
[arXiv:2508.03474](https://arxiv.org/abs/2508.03474) (v1, 5 Aug 2025).

The paper studies arbitrage **within Polymarket**: it defines market-rebalancing arbitrage
inside one market and combinatorial arbitrage between dependent markets. It reduces the
`O(2^(n+m))` dependency search with timeliness, topical-similarity and combinatorial
heuristics, uses an LLM (validated by experts) to find dependencies, and estimates about
40 million USD of realized arbitrage profit from on-chain order-book data.

This repository applies the same ideas **across two venues** (Kalshi and Polymarket). This
changes the problem in ways the paper did not need to handle, and the implementation is
stricter as a result.

## Definitions to modules

| Paper concept | What it says | Where it lives | Fidelity |
| --- | --- | --- | --- |
| Market as a set of conditions; MECE requirement | A market's conditions are mutually exclusive and collectively exhaustive, so prices should sum to 1 | `app/arbitrage/rebalancing.py` (`build_groups`) | **Faithful** for Polymarket negative-risk groups. Kalshi mutually exclusive events are treated as exclusive but **not** exhaustive unless verified |
| Definition 2: dependency via the set of possible joint resolution vectors `V ⊆ V1 × V2`; dependent iff `\|V\| < n·m` | Two markets are dependent when some combinations of outcomes cannot occur together | `app/arbitrage/combinatorial.py` (`is_dependent`, `dependent_subsets`), `app/arbitrage/payoff.py` (`ALLOWED_STATES`) | **Faithful** as exact enumeration. For two binary propositions, each relation maps to its allowed joint states |
| Dependent subsets `S ⊆ M1`, `S' ⊆ M2` with equal truth sums on every `v ∈ V` | Portfolios that must pay the same in every possible world | `combinatorial.dependent_subsets` | **Faithful** (brute force, bounded by `MAX_CONDITIONS = 8`) |
| Definition 3: market-rebalancing arbitrage (long if `Σ YES < 1`, short if `Σ YES > 1`) | Buy every YES for less than 1, or sell every YES (buy every NO) for more than its payout | `rebalancing.evaluate_group` | **Adapted**: displayed prices are replaced by depth-walked asks, taker fees, buffers and capital cost. Long needs verified exhaustiveness. Short needs only exclusivity (buying every NO pays `n − 1`). The paper's 0.02 detection threshold is kept as a reference constant |
| Definition 4: combinatorial arbitrage over dependent subsets | If `Σ_S price < Σ_S' price`, hold YES on `S` and YES on the complement of `S'` (and the mirror case) | `combinatorial.evaluate`; cross-venue form in `cross_venue.py` via `payoff.verified_constructions` | **Adapted across venues**: each pair relation yields the two-leg constructions whose *minimum* payout over allowed joint states is 1. Only `EQUIVALENT` and `COMPLEMENTARY` are executable |
| Heuristic reduction: timeliness, topical similarity, combinatorial relationships | Avoid comparing everything with everything | `app/matching/candidates.py` (inverted-index blocking, category and time filters, similarity ranking) | **Adapted**: deterministic blocking keys and hashing embeddings replace the paper's topic embeddings. The candidate reduction is printed by every scan (`possible pairs -> compared -> candidates -> approved`) |
| LLM dependency detection, validated by experts | An LLM proposes dependencies, humans check them | `app/matching/rules.py` (deterministic), `app/matching/adjudicator.py` (optional LLM) | **Deliberately different**: dependencies come from deterministic proposition extraction and interval algebra. The LLM is off by default and can only **veto** an approval, never create one |
| Realized-profit measurement from on-chain fills | Historical analysis of executed arbitrage | not implemented | Out of scope: this system is a forward-looking scanner with paper execution, not a historical study |
| Arbitrage profit on displayed prices | `1 − Σ prices` | `app/arbitrage/depth.py` | **Extended**: profit as a function of quantity (the profit curve), executable depth on every leg, per-fill fee rounding, buffers |

## Why cross-venue equivalence must be stricter than similarity

Inside one Polymarket market, the paper can rely on the venue's own MECE structure: one
resolver, one set of rules, one settlement. Across venues none of that holds:

1. **Different resolvers and rules.** Kalshi resolves from its contract terms and named
   sources. Polymarket resolves through its own resolution process and market description.
   Two markets titled "Fed cuts rates in December" can use different sources, deadlines,
   time zones or revision policies, and can resolve differently.
2. **Wording that looks equivalent often isn't.** "≥ 3.0%" vs "> 3.0%", "by December 31 ET"
   vs "by December 31" (time zone unstated), "first release" vs "final revised value",
   "declared winner by AP" vs "certified result", "if the event is cancelled the market
   resolves NO" vs "resolves 50/50". Each of these turns a hedge into a directional bet in
   some states of the world. The fixture set contains an example of each (see
   `fixtures/manifest.json`).
3. **Similarity is not a relation.** A high embedding or title similarity says two texts
   are *about* the same thing. It does not say which joint outcomes are possible. The
   arbitrage math needs the second: the guaranteed payout is the minimum over possible
   joint states, and one misclassified state removes the guarantee.
4. **Asymmetric cost of error.** A missed pair costs a missed opportunity. A wrongly
   approved pair creates an unhedged position that looks hedged.

So the matcher approves only when it can **show** the relation. Subject, metric and region
must match exactly under interval algebra, every resolution-criteria check must pass, and
no critical field may be unknown. Everything else is recorded with the reason and never
executed. Implications (`A_IMPLIES_B`, `B_IMPLIES_A`) and exclusivity are still computed
and priced as combinatorial analytics, because the paper shows they carry arbitrage. They
stay `CANDIDATE` in this MVP, because their relation is inferred from wording rather than
established by matching settlement rules.

## Adaptations summary

| Aspect | Paper | This implementation |
| --- | --- | --- |
| Venues | Polymarket only | Kalshi × Polymarket, plus intra-venue rebalancing analytics on each |
| Prices | Displayed prices / historical fills | Live or fixture order books, depth-walked, per-fill fees, buffers |
| Relation discovery | LLM + expert validation | Deterministic extraction + interval/state algebra; optional veto-only LLM |
| Executable set | All detected arbitrage | Only `EQUIVALENT` / `COMPLEMENTARY` pairs that pass contract- and quote-level validation |
| Execution | On-chain, atomic within one venue's settlement | Non-atomic across venues; simulated with leg risk, unwinds and a kill switch |
| Output | Historical profit estimate | Opportunities with assumptions, profit curve, audit trail |
