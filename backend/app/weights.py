from dataclasses import dataclass


@dataclass(frozen=True)
class Weight:
    offer_id: int
    share: int
    pinned: bool


class InvalidWeights(ValueError):
    pass


def distribute_weights(active: list[Weight]) -> dict[int, int]:
    if not active:
        return {}

    shares = {row.offer_id: row.share for row in active if row.pinned}
    free = sorted(row.offer_id for row in active if not row.pinned)
    remaining = 100 - sum(shares.values())
    if remaining < 0 or (not free and remaining):
        raise InvalidWeights("Снимите закрепление, чтобы распределить ровно 100%.")

    if free:
        share, remainder = divmod(remaining, len(free))
        for index, offer_id in enumerate(free):
            shares[offer_id] = share + (index < remainder)
    return shares
