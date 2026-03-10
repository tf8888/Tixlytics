#!/usr/bin/env python3
"""
Tixlytics Bid Engine
====================
Reads events.json and outputs section-level bid decisions to maximize
risk-adjusted profit with $50,000 total capital.

Usage:
    python bid_engine.py [path/to/events.json]

Defaults to events.json in the current directory if no path is given.
"""

import json
import re
import sys
from dataclasses import dataclass, field

# ── Constants ────────────────────────────────────────────────────────────────
TOTAL_CAPITAL = 50_000.00
RESALE_FEE_PCT = 0.10          # 10% all-in resale costs
MIN_MARGIN_PCT = 0.10          # minimum net margin to justify a bid
MAX_CAPITAL_PER_EVENT_PCT = 0.50   # don't put >50% of capital in one event
MAX_CAPITAL_PER_SECTION_PCT = 0.25 # don't put >25% of capital in one section
MAX_TICKETS_PER_SECTION = 8        # max tickets per section bid

# ── Section name normalization ───────────────────────────────────────────────

def normalize_section(raw: str) -> str:
    """
    Normalize section names across sources.
    Examples:
        "FLOOR-A", "Floor A", "Fl A"       -> "Floor A"
        "SEC 101", "Section 101", "Sec 101" -> "Section 101"
        "ORCH C", "Orchestra C"             -> "Orchestra C"
        "Front Mezz", "Front Mezzanine"     -> "Front Mezzanine"
        "Rear Mezz", "Rear Mezzanine"       -> "Rear Mezzanine"
        "FLOOR", "Floor"                    -> "Floor"
    """
    s = raw.strip()

    # Floor with letter suffix: "FLOOR-A", "FL A", "Floor A" -> "Floor A"
    m = re.match(r'^(?:FLOOR[-\s]?|Fl(?:oor)?\s+)([A-Z])$', s, re.IGNORECASE)
    if m:
        return f"Floor {m.group(1).upper()}"

    # Bare "FLOOR" / "Floor"
    if re.match(r'^FLOOR$', s, re.IGNORECASE):
        return "Floor"

    # Numbered sections: "SEC 101", "Section 101", "Sec 101" -> "Section 101"
    m = re.match(r'^(?:SEC(?:TION)?)\s*(\d+)$', s, re.IGNORECASE)
    if m:
        return f"Section {m.group(1)}"

    # Orchestra: "ORCH C", "Orchestra C" etc.
    m = re.match(r'^(?:ORCH(?:ESTRA)?)\s+([A-Z])$', s, re.IGNORECASE)
    if m:
        return f"Orchestra {m.group(1).upper()}"

    # Mezzanine abbreviations
    m = re.match(r'^(Front|Rear)\s+Mezz(?:anine)?$', s, re.IGNORECASE)
    if m:
        prefix = m.group(1).capitalize()
        return f"{prefix} Mezzanine"

    # Fallback: title-case
    return s.title()


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class SectionAnalysis:
    section: str
    atp: float                  # average transaction price (realized sales)
    daily_volume: float
    total_listed_qty: int
    face_value: float
    pct_primary_available: float
    lowest_ask: float
    num_listings: int
    competitor_bid: float       # current highest competing bid (0 if none)
    competitor_split_qty: int

    # computed
    net_resale: float = 0.0     # ATP * (1 - fee)
    bid_price: float = 0.0
    expected_margin_pct: float = 0.0
    tickets_to_buy: int = 0
    capital_at_risk: float = 0.0
    expected_profit: float = 0.0
    should_bid: bool = False
    skip_reasons: list = field(default_factory=list)
    days_to_sell: float = 0.0
    liquidity_score: float = 0.0
    confidence: float = 0.0


@dataclass
class EventAnalysis:
    event_id: str
    name: str
    category: str
    venue: str
    date: str
    days_to_event: int
    event_atp: float
    event_daily_volume: float
    event_total_qty: int
    sections: list = field(default_factory=list)


# ── Core pricing logic ───────────────────────────────────────────────────────

def compute_bid(sa: SectionAnalysis, days_to_event: int, category: str) -> SectionAnalysis:
    """
    Determine whether and how much to bid for a section.

    Key principles:
    1. ATP is what tickets actually sell for — that's our expected revenue.
    2. Net revenue = ATP * (1 - 10% fees).
    3. Bid must be below net revenue to ensure profit.
    4. Primary availability suppresses secondary value — if fans can buy at
       face value, the ATP will trend down and our resale ceiling is the face value.
    5. More days to event = more price risk (prices can drop).
    6. Low daily volume = higher holding risk (may not sell in time).
    7. Competitor bids set a floor — we need to beat them to win tickets, but
       shouldn't overpay just to win.
    """
    reasons = []

    # ── Step 1: Net resale revenue after fees ────────────────────────────
    sa.net_resale = sa.atp * (1 - RESALE_FEE_PCT)

    # ── Step 2: Primary availability check ───────────────────────────────
    # If significant primary inventory remains, the effective resale ceiling
    # drops toward face value because fans have a cheaper alternative.
    # Even if face value > ATP, high primary availability signals weak demand
    # and puts downward pressure on secondary prices.
    if sa.pct_primary_available > 40:
        # Heavy primary availability — the section is still largely unsold.
        # Two effects: (1) ceiling from face value, (2) demand suppression.
        face_net = sa.face_value * (1 - RESALE_FEE_PCT)
        effective_ceiling = min(sa.net_resale, face_net)
        # Apply additional demand suppression — high primary avail means
        # secondary demand is weak. Discount proportional to availability.
        suppression = 0.10 + 0.05 * (sa.pct_primary_available - 40) / 60
        effective_ceiling *= (1 - suppression)
        reasons.append(
            f"Primary {sa.pct_primary_available:.0f}% available at "
            f"${sa.face_value:.0f} — heavy primary supply, capping + discounting resale"
        )
        sa.net_resale = effective_ceiling
    elif sa.pct_primary_available > 15:
        # Moderate primary availability — some downward pressure.
        face_net = sa.face_value * (1 - RESALE_FEE_PCT)
        blended = 0.6 * sa.net_resale + 0.4 * min(sa.net_resale, face_net)
        suppression = 0.03
        blended *= (1 - suppression)
        if blended < sa.net_resale:
            reasons.append(
                f"Primary {sa.pct_primary_available:.0f}% available — "
                f"blending resale estimate toward face value"
            )
            sa.net_resale = blended

    # ── Step 3: Liquidity & time-to-sell assessment ──────────────────────
    if sa.daily_volume > 0:
        sa.days_to_sell = sa.total_listed_qty / sa.daily_volume
    else:
        sa.days_to_sell = float('inf')

    # Liquidity score: how easily can we sell relative to time remaining?
    if sa.daily_volume > 0 and days_to_event > 0:
        sa.liquidity_score = min(sa.daily_volume * days_to_event / max(sa.total_listed_qty, 1), 5.0)
    else:
        sa.liquidity_score = 0.0

    # ── Step 4: Time-risk discount ───────────────────────────────────────
    # More days = more uncertainty. Short-dated events with high demand
    # are safer. Apply a discount to our expected resale.
    if days_to_event <= 3:
        time_discount = 0.0     # imminent event, price is well-established
    elif days_to_event <= 7:
        time_discount = 0.02    # slight discount for ~1 week out
    elif days_to_event <= 14:
        time_discount = 0.05
    elif days_to_event <= 30:
        time_discount = 0.08
    else:
        time_discount = 0.12

    sa.net_resale *= (1 - time_discount)

    # ── Step 5: Set bid price ────────────────────────────────────────────
    # Target margin: we want at least MIN_MARGIN_PCT net profit on each ticket.
    # Bid = what we're willing to pay per ticket.
    target_bid = sa.net_resale / (1 + MIN_MARGIN_PCT)

    # We need to beat the competitor bid to win tickets.
    if sa.competitor_bid > 0:
        # Bid $1 above competitor to win, but only if that's still profitable.
        min_winning_bid = sa.competitor_bid + 1.0
        if min_winning_bid > target_bid:
            # Winning requires overpaying our target. Check if margin is still
            # acceptable at a tighter spread.
            margin_at_winning = (sa.net_resale - min_winning_bid) / min_winning_bid
            if margin_at_winning >= 0.05:  # 5% floor when competing
                target_bid = min_winning_bid
                reasons.append(
                    f"Raised bid to ${min_winning_bid:.0f} to beat competitor "
                    f"at ${sa.competitor_bid:.0f} (margin {margin_at_winning:.1%})"
                )
            else:
                reasons.append(
                    f"Competitor bid ${sa.competitor_bid:.0f} is too high — "
                    f"margin only {margin_at_winning:.1%} if we beat it"
                )
                sa.skip_reasons = reasons
                sa.should_bid = False
                return sa

    sa.bid_price = round(target_bid, 2)

    # ── Step 6: Sanity checks ────────────────────────────────────────────

    # Don't bid above the lowest secondary ask — that would mean we're paying
    # more than what sellers are actively offering.
    if sa.bid_price >= sa.lowest_ask:
        reasons.append(
            f"Bid ${sa.bid_price:.0f} >= lowest ask ${sa.lowest_ask:.0f} — "
            f"would overpay; could buy from market instead"
        )
        # Cap bid just below lowest ask
        sa.bid_price = round(sa.lowest_ask * 0.85, 2)
        if sa.bid_price >= sa.net_resale:
            sa.skip_reasons = reasons + ["No margin after adjusting below ask"]
            sa.should_bid = False
            return sa

    # Absolute margin check
    sa.expected_margin_pct = (sa.net_resale - sa.bid_price) / sa.bid_price if sa.bid_price > 0 else 0
    if sa.expected_margin_pct < 0.05:
        reasons.append(f"Margin {sa.expected_margin_pct:.1%} too thin")
        sa.skip_reasons = reasons
        sa.should_bid = False
        return sa

    # Liquidity check — skip sections where we may not be able to sell in time
    if sa.liquidity_score < 0.3 and sa.daily_volume < 1:
        reasons.append(f"Liquidity too low (score={sa.liquidity_score:.2f}, vol={sa.daily_volume}/day)")
        sa.skip_reasons = reasons
        sa.should_bid = False
        return sa

    # ── Step 7: Confidence score ────────────────────────────────────────
    # Combine margin, liquidity, and primary risk into a 0-1 confidence score.
    # Computed before position sizing so it can inform ticket quantities.
    margin_component = min(sa.expected_margin_pct / 0.30, 1.0)  # 30% margin = max
    liquidity_component = min(sa.liquidity_score / 3.0, 1.0)
    primary_risk = 1.0 - (sa.pct_primary_available / 100.0)

    sa.confidence = (0.45 * margin_component + 0.35 * liquidity_component + 0.20 * primary_risk)

    # ── Step 8: Position sizing ──────────────────────────────────────────
    # How many tickets to buy? Based on daily volume and time remaining.
    # We don't want to hold more than we can sell before the event.
    if sa.daily_volume > 0 and days_to_event > 0:
        # Target: enough tickets that we can sell within ~30-50% of remaining days
        # This gives us buffer if sales are slower than average.
        sellable_window = max(days_to_event * 0.35, 1)
        sellable = max(int(sa.daily_volume * sellable_window), 2)
    else:
        sellable = 2

    sa.tickets_to_buy = min(sellable, MAX_TICKETS_PER_SECTION)
    # For very high-confidence + high-liquidity opportunities, allow
    # larger positions (but capital allocation will still constrain).
    if sa.confidence > 0.6 and sa.liquidity_score > 0.5:
        sa.tickets_to_buy = min(sellable, MAX_TICKETS_PER_SECTION * 2)
    sa.capital_at_risk = sa.tickets_to_buy * sa.bid_price
    sa.expected_profit = sa.tickets_to_buy * (sa.net_resale - sa.bid_price)

    sa.should_bid = True
    sa.skip_reasons = reasons
    return sa


# ── Data assembly ────────────────────────────────────────────────────────────

def build_section_map(event: dict) -> dict:
    """Assemble normalized section-level data from all event sources."""
    sections = {}

    # Sales data (ATP, volume, inventory) — the most reliable data.
    for sd in event.get("sales_data", {}).get("section_level", []):
        key = normalize_section(sd["section"])
        sections[key] = {
            "atp": sd["atp"],
            "daily_volume": sd["daily_volume"],
            "total_listed_qty": sd["total_qty"],
        }

    # Primary availability
    for pa in event.get("primary_availability", []):
        key = normalize_section(pa["section"])
        if key in sections:
            sections[key]["face_value"] = pa["face_value"]
            sections[key]["pct_primary_available"] = pa["pct_available"]

    # Secondary listings — find lowest ask and count per normalized section
    for listing in event.get("secondary_listings", []):
        key = normalize_section(listing["section"])
        if key not in sections:
            continue
        current_low = sections[key].get("lowest_ask", float('inf'))
        if listing["price"] < current_low:
            sections[key]["lowest_ask"] = listing["price"]
        sections[key]["num_listings"] = sections[key].get("num_listings", 0) + 1

    # Competitor bids
    for bid in event.get("current_highest_bids", []):
        key = normalize_section(bid["section"])
        if key in sections:
            sections[key]["competitor_bid"] = bid["bid_price"]
            sections[key]["competitor_split_qty"] = bid["split_qty"]

    return sections


def analyze_event(event: dict) -> EventAnalysis:
    """Run the full analysis pipeline for one event."""
    ea = EventAnalysis(
        event_id=event["event_id"],
        name=event["name"],
        category=event["category"],
        venue=event["venue"],
        date=event["date"],
        days_to_event=event["days_to_event"],
        event_atp=event["sales_data"]["event_level"]["atp"],
        event_daily_volume=event["sales_data"]["event_level"]["daily_volume"],
        event_total_qty=event["sales_data"]["event_level"]["total_market_qty"],
    )

    section_map = build_section_map(event)

    for name, data in section_map.items():
        sa = SectionAnalysis(
            section=name,
            atp=data["atp"],
            daily_volume=data["daily_volume"],
            total_listed_qty=data["total_listed_qty"],
            face_value=data.get("face_value", 0),
            pct_primary_available=data.get("pct_primary_available", 0),
            lowest_ask=data.get("lowest_ask", float('inf')),
            num_listings=data.get("num_listings", 0),
            competitor_bid=data.get("competitor_bid", 0),
            competitor_split_qty=data.get("competitor_split_qty", 0),
        )
        sa = compute_bid(sa, ea.days_to_event, ea.category)
        ea.sections.append(sa)

    # Sort: bid opportunities first (by confidence desc), then skips.
    ea.sections.sort(key=lambda s: (-s.should_bid, -s.confidence))
    return ea


# ── Capital allocation ───────────────────────────────────────────────────────

def allocate_capital(events: list[EventAnalysis]) -> list[EventAnalysis]:
    """
    Enforce portfolio-level capital limits.
    Rank all bid-worthy sections across events by confidence * expected_margin,
    then allocate capital greedily until budget exhausted.
    """
    # Collect all biddable sections with their parent event info.
    candidates = []
    for ea in events:
        for sa in ea.sections:
            if sa.should_bid:
                candidates.append((ea, sa))

    # Rank by confidence-weighted expected profit per dollar of capital.
    # This is effectively a Sharpe-like ranking.
    def sort_key(pair):
        _, sa = pair
        roi = sa.expected_profit / sa.capital_at_risk if sa.capital_at_risk > 0 else 0
        return sa.confidence * roi

    candidates.sort(key=sort_key, reverse=True)

    remaining_capital = TOTAL_CAPITAL
    capital_by_event = {}
    max_per_event = TOTAL_CAPITAL * MAX_CAPITAL_PER_EVENT_PCT
    max_per_section = TOTAL_CAPITAL * MAX_CAPITAL_PER_SECTION_PCT

    for ea, sa in candidates:
        event_used = capital_by_event.get(ea.event_id, 0)
        event_room = max_per_event - event_used
        section_room = max_per_section
        budget = min(remaining_capital, event_room, section_room)

        if budget < sa.bid_price * 2:
            # Not enough for even a pair — skip.
            sa.should_bid = False
            sa.skip_reasons.append("Insufficient remaining capital")
            continue

        # Maybe reduce ticket count to fit budget.
        affordable = int(budget // sa.bid_price)
        sa.tickets_to_buy = min(sa.tickets_to_buy, affordable)
        sa.capital_at_risk = sa.tickets_to_buy * sa.bid_price
        sa.expected_profit = sa.tickets_to_buy * (sa.net_resale - sa.bid_price)

        remaining_capital -= sa.capital_at_risk
        capital_by_event[ea.event_id] = event_used + sa.capital_at_risk

    return events


# ── Output formatting ────────────────────────────────────────────────────────

def print_results(events: list[EventAnalysis]):
    """Pretty-print bid decisions to stdout."""
    total_capital_deployed = 0
    total_expected_profit = 0
    bid_count = 0
    skip_count = 0

    print("=" * 80)
    print("TIXLYTICS BID ENGINE -- DECISIONS")
    print(f"Total Capital: ${TOTAL_CAPITAL:,.0f}")
    print("=" * 80)

    for ea in events:
        print(f"\n{'-' * 80}")
        print(f"EVENT: {ea.name}")
        print(f"  Venue: {ea.venue}")
        print(f"  Date: {ea.date}  |  Days to event: {ea.days_to_event}")
        print(f"  Market ATP: ${ea.event_atp:,.0f}  |  Daily volume: {ea.event_daily_volume}  |  Inventory: {ea.event_total_qty}")
        print(f"{'-' * 80}")

        has_bids = False
        for sa in ea.sections:
            if sa.should_bid:
                has_bids = True
                bid_count += 1
                total_capital_deployed += sa.capital_at_risk
                total_expected_profit += sa.expected_profit

                print(f"\n  [BID] {sa.section}")
                print(f"     Bid Price:       ${sa.bid_price:>8,.2f} / ticket")
                print(f"     Tickets:         {sa.tickets_to_buy}")
                print(f"     Capital at Risk: ${sa.capital_at_risk:>8,.2f}")
                print(f"     Expected Resale: ${sa.net_resale:>8,.2f} / ticket (ATP ${sa.atp:.0f} minus 10% fees, time-risk adjusted)")
                print(f"     Expected Margin: {sa.expected_margin_pct:>7.1%}")
                print(f"     Expected Profit: ${sa.expected_profit:>8,.2f}")
                print(f"     Confidence:      {sa.confidence:>7.2f}")
                print(f"     Liquidity:       {sa.daily_volume:.0f}/day, {sa.total_listed_qty} listed  (score: {sa.liquidity_score:.2f})")
                print(f"     Lowest Ask:      ${sa.lowest_ask:>8,.2f}")
                comp_str = f"${sa.competitor_bid:,.0f}" if sa.competitor_bid > 0 else "None"
                print(f"     Competitor Bid:  {comp_str}")
                print(f"     Primary Avail:   {sa.pct_primary_available:.0f}% at ${sa.face_value:,.0f}")
                if sa.skip_reasons:
                    for r in sa.skip_reasons:
                        print(f"     Note: {r}")
            else:
                skip_count += 1
                print(f"\n  [SKIP] {sa.section}")
                for r in sa.skip_reasons:
                    print(f"     Reason: {r}")
                print(f"     (ATP=${sa.atp:.0f}, Net resale=${sa.net_resale:.2f}, "
                      f"Lowest ask=${sa.lowest_ask:.0f}, "
                      f"Primary={sa.pct_primary_available:.0f}% avail)")

        if not has_bids:
            print("\n  ** No bids placed for this event.")

    # -- Portfolio summary
    print(f"\n{'=' * 80}")
    print("PORTFOLIO SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Bids placed:          {bid_count}")
    print(f"  Sections skipped:     {skip_count}")
    print(f"  Capital deployed:     ${total_capital_deployed:>10,.2f} of ${TOTAL_CAPITAL:,.0f} "
          f"({total_capital_deployed/TOTAL_CAPITAL:.0%})")
    print(f"  Capital reserved:     ${TOTAL_CAPITAL - total_capital_deployed:>10,.2f}")
    print(f"  Expected profit:      ${total_expected_profit:>10,.2f}")
    if total_capital_deployed > 0:
        print(f"  Expected ROI:         {total_expected_profit/total_capital_deployed:>10.1%}")
    print(f"{'=' * 80}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Ensure stdout handles UTF-8 on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)

    path = sys.argv[1] if len(sys.argv) > 1 else "events.json"
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {path} not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)

    events_raw = data.get("events", [])
    if not events_raw:
        print("No events found in data.", file=sys.stderr)
        sys.exit(1)

    events = [analyze_event(e) for e in events_raw]
    events = allocate_capital(events)
    print_results(events)


if __name__ == "__main__":
    main()
