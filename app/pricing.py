"""
Application Diagnosis pricing — the approved one-time ladder.

Deliberately a pure function with no I/O: the ONLY place the ladder values
live, so a test can assert all nine tiers against this exact table rather
than against a re-typed copy of it. The actual qualifying-purchase count
this is called with always comes from Database.count_qualifying_ad_purchases
(or the equivalent locked read inside Database.begin_ad_purchase) — never
from anything the client supplies.
"""

# index i = price when the customer already has i qualifying purchases.
# Index 8 ("8+") is the floor price and applies to every count beyond it.
PRICING_LADDER_INR = [399, 339, 319, 299, 279, 259, 239, 219, 199]


def price_for_qualifying_count(qualifying_count: int) -> int:
    if qualifying_count < 0:
        raise ValueError("qualifying_count cannot be negative")
    index = min(qualifying_count, len(PRICING_LADDER_INR) - 1)
    return PRICING_LADDER_INR[index]
