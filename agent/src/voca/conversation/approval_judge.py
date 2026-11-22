from dataclasses import dataclass

from voca.delegation.danger import Danger


@dataclass(frozen=True)
class Rules:
    low: float = 0.45
    medium: float = 0.50
    high: float = 0.55
    deny: float = 0.45
    clarify_limit: int = 2

    def threshold(self, danger: Danger) -> float:
        return {
            Danger.LOW: self.low,
            Danger.MEDIUM: self.medium,
            Danger.HIGH: self.high,
        }[danger]
