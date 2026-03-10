# Tixlytics Take-Home: Bid Engine Write-Up

## Approach

### Core problem
Given market data for three live events, decide where to deploy $50K in capital to buy tickets from fans (via a primary marketplace buyback program) and resell on secondary exchanges, maximizing risk-adjusted profit.

### Key insight: ATP is the anchor, not ask prices
The single most important number in this dataset is *ATP* (average transaction price) — what tickets actually sell for. Ask prices on secondary exchanges are aspirational; ATP reflects reality. The entire pricing model is built around this distinction:

```
Expected revenue per ticket = ATP × (1 - 10% fees) × (1 - time_risk_discount)
```

### Pricing pipeline (per section)

1. **Start with ATP** as the expected resale price.
2. **Primary availability discount**: If significant primary inventory remains (>40%), the secondary market ceiling drops — fans can still buy at face value, so secondary demand is suppressed. For 15-40% availability, I apply a blended estimate; above 40%, a hard cap plus demand suppression discount.
3. **Time-risk discount**: More days to event means more uncertainty. 2-12% discount depending on days out. This is conservative — a production system would use historical volatility.
4. **Set bid price** to achieve ≥10% net margin after all costs and discounts. If a competitor bid exists, raise to beat it, but only if margin stays ≥5%.
5. **Sanity checks**: cap bid below the lowest secondary ask (at 85% of ask), skip if margin is too thin, skip if liquidity is too low to sell in time.
6. **Position sizing**: based on daily volume × time window, capped by portfolio limits (50% per event, 25% per section). High-confidence + high-liquidity sections get 2x position sizes.
7. **Capital allocation**: rank all biddable sections by confidence-weighted ROI, allocate greedily until budget exhausted.

### Event-level decisions

**NBA ECF Game 5 (EVT-004) — Best opportunity**
- 5 days out, primary completely sold out, massive demand (155 tickets/day).
- Section 3 ($552.50 bid, 39.7% expected margin) and Section 210 ($233.75, 34%) are the strongest trades. Short time horizon = low uncertainty, and daily volume means we can sell quickly.
- Floor skipped: competitor bid at $2,500 already pushes margin negative after fees + risk.
- This event gets the most capital because the short duration minimizes holding risk.

**Billie Eilish (EVT-001) — Solid, longer duration**
- 30 days out, primary sold out everywhere. Good daily volume (42/day).
- Floor A is the best section (8/day volume, strong ATP).
- All sections get bids, but the 30-day holding period means higher time-risk discount (8%).
- Section 220 is marginal (5.8% margin) — only bid because competitor at $35 is beatable.

**Hamilton (EVT-002) — Most complex**
- 14 days out, but significant primary inventory: Orchestra L 60%, Rear Mezzanine 52%.
- Low daily volume (5/day total) means liquidity risk.
- No competitor bids — we're price-setters, which is good for margin but may signal a thinner market.
- Orchestra C is the cleanest bid (primary sold out, ATP well above face value).
- Orchestra L and Rear Mezzanine carry primary-supply risk but still offer margin after suppression discount.
- Front Mezzanine is the best Hamilton section — only 15% primary, decent volume, strong margin.

### Section name normalization
Different sources use inconsistent names: "SEC 101", "Section 101", "Sec 101", "FLOOR-A", "Floor A", "Fl A", "ORCH C", "Orchestra C", "Front Mezz", "Front Mezzanine". The normalizer uses regex patterns to canonicalize these to a standard format (e.g., "Section 101", "Floor A", "Orchestra C", "Front Mezzanine").

## Trade-offs

1. **Conservative position sizing vs. capital utilization**: I deploy ~38% of capital ($19K of $50K). A more aggressive engine would put more to work, but keeping capital in reserve is prudent — we're buying from a program that may send tickets unpredictably, and cash reserves let us bid on future opportunities. In a production system, utilization targets would be dynamic based on pipeline.

2. **ATP as point estimate vs. distribution**: ATP is a single number. In reality, resale prices have a distribution — some tickets sell above ATP, some below. A production system would model this as a range with confidence intervals rather than a single expected value.

3. **Static time discount vs. dynamic decay**: I use fixed buckets (≤3 days: 0%, 7 days: 2%, etc.). Real price decay is nonlinear and event-dependent — sports playoffs may appreciate as the event approaches if teams advance, while concerts tend to depreciate.

4. **No row-level pricing**: The bid is section-level, but row matters for resale value. Front-row tickets in a section sell for much more than back-row. Our bid price reflects the section average, which means we'll overpay for bad rows and underpay for good ones. Net effect should be neutral if we get a random mix, but there's adverse selection risk — motivated sellers may be more likely to sell worse seats.

5. **Bid-to-beat strategy**: When a competitor bid exists, I beat it by $1. This is a simple strategy. In production you'd want to consider the competitor's behavior patterns, bid timing, and whether winning is even desirable at that price point.

## What data I'd want for a production system

- **Historical ATP time series**: How does ATP change over the days leading to an event? This would replace static time-risk discounts with empirical decay curves.
- **Bid fill rates**: What percentage of bids at different price points actually get filled? This informs the trade-off between bid price (lower = more profit if filled, but less likely to fill).
- **Row-level resale data**: ATP broken down by row, not just section, to improve bid accuracy and avoid adverse selection.
- **Comparable event data**: How did similar events (same artist/team, similar venue, same day of week) perform historically? This calibrates expectations for new events.
- **Real-time velocity**: Not just daily average volume but intraday velocity and trend direction — is demand accelerating or decelerating?
- **Competitor bid history**: Patterns in how competitors adjust bids over time would allow anticipatory positioning.
- **Primary release schedule**: When new primary inventory drops (e.g., additional rows released), it impacts secondary demand. Tracking this avoids being caught with inventory when primary floods the market.
- **Event-specific signals**: Team playoff probabilities (for contingent events), artist social media buzz, local weather forecasts, competing events in the same market.

## Where AI helped and where I corrected it

**AI was useful for:**
- Initial scaffolding of the data structures, file I/O, and output formatting
- Generating the regex patterns for section name normalization
- Structuring the multi-step pricing pipeline

**Where I corrected the AI:**
- **Primary availability logic**: The AI's initial approach simply capped resale at face value when primary was available. This missed two things: (1) when face value is *higher* than ATP (Hamilton Orchestra L: face $277 > ATP $270), the cap doesn't trigger but the demand suppression effect still applies; (2) the mere availability of primary inventory depresses demand regardless of price direction. I rewrote this to apply a demand suppression multiplier in addition to the price ceiling.
- **Confidence/position ordering**: The AI computed the confidence score *after* using it for position sizing, which meant the confidence-based boost never triggered (confidence was still 0). Had to reorder the steps.
- **Capital deployment**: Initial version was far too conservative — $8K deployed on a $50K budget. Adjusted position sizing to be more aggressive for high-confidence opportunities.
- **Unicode on Windows**: The AI used box-drawing characters and emoji that Windows cp1252 encoding can't handle. Replaced with ASCII equivalents.
- **Bid vs. ask confusion**: Had to ensure the engine understood that bids are placed to *buy from fans* (below market), not to *list on exchanges*. The spread between our bid and the ATP is where profit lives.
