from __future__ import annotations

from dataclasses import dataclass

from .models import Movement, Prediction, SettledTrade


@dataclass(slots=True)
class OpenTrade:
    prediction: Prediction
    entry: Movement
    notional: float


class PaperBook:
    def __init__(self, starting_cash: float = 100000.0) -> None:
        self.starting_cash = starting_cash
        self.cash_balance = starting_cash
        self.realized_pnl = 0.0

    @staticmethod
    def trade_notional(prediction: Prediction) -> float:
        scale = 10 ** (prediction.magnitude - 1)
        return 100.0 * float(scale)

    def open_trade(self, prediction: Prediction, entry: Movement) -> OpenTrade:
        return OpenTrade(prediction=prediction, entry=entry, notional=self.trade_notional(prediction))

    def settle_trade(self, trade: OpenTrade, exit_move: Movement) -> SettledTrade:
        price_delta = exit_move.price - trade.entry.price
        direction_sign = 1 if trade.prediction.direction == "up" else -1
        pnl = direction_sign * price_delta * (trade.notional / trade.entry.price)
        flat_epsilon = max(1e-8, trade.entry.price * 1e-6)
        if abs(price_delta) <= flat_epsilon:
            correct_direction: bool | None = None
        else:
            correct_direction = (price_delta > 0 and trade.prediction.direction == "up") or (
                price_delta < 0 and trade.prediction.direction == "down"
            )
        self.realized_pnl += pnl
        self.cash_balance += pnl
        return SettledTrade(
            prediction=trade.prediction,
            entry=trade.entry,
            exit=exit_move,
            notional=trade.notional,
            pnl=pnl,
            correct_direction=correct_direction,
        )
