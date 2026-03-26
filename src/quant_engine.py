from datetime import datetime

import numpy as np

from src.models.market import QuantSignals

WEIGHTS: dict[str, float] = {
    "rsi":          0.20,
    "macd":         0.20,
    "momentum_20d": 0.20,
    "volume":       0.15,
    "pe_vs_sector": 0.15,
    "week52":       0.10,
}


class QuantEngine:
    """Compute quantitative trading signals for TASE securities.

    Calculates RSI, MACD, momentum, volume anomaly, PE-vs-sector, and
    52-week position signals, then combines them into a single composite
    score using configurable weights.
    """

    def __init__(self, sector_pe_medians: dict[str, float] | None = None) -> None:
        """Initialise the engine with optional sector PE benchmarks.

        Args:
            sector_pe_medians: Map of sector name to median P/E ratio used
                for relative valuation scoring. Defaults to empty dict.
        """
        self.sector_pe_medians = sector_pe_medians or {}

    def compute_signals(
        self,
        ticker: str,
        ohlcv_bars: list[dict],
        sector: str = "Unknown",
        pe_ratio: float | None = None,
        week52_high: float | None = None,
        week52_low: float | None = None,
    ) -> QuantSignals:
        """Compute all quantitative signals for a single ticker.

        Requires at least 14 bars for RSI. Returns a mostly-empty
        QuantSignals object if fewer bars are provided.

        Args:
            ticker: TASE security identifier.
            ohlcv_bars: List of OHLCV dicts with 'close' and 'volume' keys.
            sector: Sector name used for PE-vs-sector scoring.
            pe_ratio: Current trailing P/E ratio, or None to skip.
            week52_high: 52-week high price, or None to skip.
            week52_low: 52-week low price, or None to skip.

        Returns:
            QuantSignals with all computable fields populated and a
            composite_score in the range [-1, 1].
        """
        if len(ohlcv_bars) < 14:
            return QuantSignals(ticker=ticker, signal_time=datetime.now())

        closes = np.array([float(b["close"]) for b in ohlcv_bars], dtype=float)
        volumes = np.array([float(b["volume"]) for b in ohlcv_bars], dtype=float)
        signals = QuantSignals(ticker=ticker, signal_time=datetime.now())
        scores: dict[str, float] = {}
        flags: list[str] = []

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi = self._rsi(closes)
        if rsi is not None:
            signals.rsi_14 = round(rsi, 2)
            if rsi <= 30:
                scores["rsi"] = 1.0
                flags.append(f"RSI oversold ({rsi:.1f})")
            elif rsi >= 70:
                scores["rsi"] = -1.0
                flags.append(f"RSI overbought ({rsi:.1f})")
            elif rsi <= 40:
                scores["rsi"] = 0.5
            elif rsi >= 60:
                scores["rsi"] = -0.5
            else:
                scores["rsi"] = 0.0

        # ── MACD ──────────────────────────────────────────────────────────────
        macd_line, signal_line = self._macd(closes)
        if macd_line is not None and signal_line is not None:
            signals.macd = round(float(macd_line), 6)
            signals.macd_signal = round(float(signal_line), 6)
            histogram = macd_line - signal_line
            if macd_line > signal_line:
                scores["macd"] = min(1.0, abs(histogram) * 10)
                if abs(histogram) > 0.05:
                    flags.append("MACD bullish")
            else:
                scores["macd"] = max(-1.0, -abs(histogram) * 10)

        # ── Momentum (20-day) ─────────────────────────────────────────────────
        if len(closes) >= 21:
            momentum_pct = (closes[-1] - closes[-21]) / closes[-21] * 100
            signals.momentum_20d = round(float(momentum_pct), 4)
            scores["momentum_20d"] = max(-1.0, min(1.0, momentum_pct / 20.0))
            if momentum_pct >= 10:
                flags.append(f"Strong momentum +{momentum_pct:.1f}%")
            elif momentum_pct <= -10:
                flags.append(f"Weak momentum {momentum_pct:.1f}%")

        # ── Volume anomaly ────────────────────────────────────────────────────
        if len(volumes) >= 20:
            vol_mean = np.mean(volumes[-20:])
            vol_std = np.std(volumes[-20:])
            if vol_std > 0:
                z_score = (volumes[-1] - vol_mean) / vol_std
                signals.volume_anomaly = round(float(z_score), 4)
                direction = 1.0 if closes[-1] >= closes[-2] else -1.0
                scores["volume"] = (
                    direction * min(1.0, abs(z_score) / 3.0)
                    if abs(z_score) > 1.5
                    else 0.0
                )
                if z_score > 2:
                    flags.append(f"Volume spike ({z_score:.1f}σ)")

        # ── PE vs sector ──────────────────────────────────────────────────────
        if pe_ratio is not None and sector in self.sector_pe_medians:
            sector_median = self.sector_pe_medians[sector]
            if sector_median > 0 and pe_ratio > 0:
                pe_ratio_vs_sector = pe_ratio / sector_median
                signals.pe_vs_sector = round(float(pe_ratio_vs_sector), 4)
                if pe_ratio_vs_sector < 0.75:
                    scores["pe_vs_sector"] = 0.8
                    flags.append(f"Cheap vs sector ({pe_ratio_vs_sector:.2f}x)")
                elif pe_ratio_vs_sector < 0.90:
                    scores["pe_vs_sector"] = 0.4
                elif pe_ratio_vs_sector > 1.50:
                    scores["pe_vs_sector"] = -0.8
                    flags.append(f"Expensive vs sector ({pe_ratio_vs_sector:.2f}x)")
                elif pe_ratio_vs_sector > 1.25:
                    scores["pe_vs_sector"] = -0.4
                else:
                    scores["pe_vs_sector"] = 0.0

        # ── 52-week position ──────────────────────────────────────────────────
        if week52_high is not None and week52_low is not None and week52_high > week52_low:
            week52_position = (float(closes[-1]) - week52_low) / (week52_high - week52_low)
            signals.week52_position = round(float(week52_position), 4)
            if week52_position < 0.15:
                scores["week52"] = 0.7
                flags.append("Near 52w low")
            elif week52_position > 0.85:
                scores["week52"] = 0.3
                flags.append("Near 52w high")

        # ── Composite score ───────────────────────────────────────────────────
        if scores:
            total_weight = sum(WEIGHTS[k] for k in scores)
            signals.composite_score = (
                round(sum(scores[k] * WEIGHTS[k] for k in scores) / total_weight, 4)
                if total_weight > 0
                else 0.0
            )
        signals.signal_summary = flags
        return signals

    def compute_all(self, tickers_data: dict[str, dict]) -> list[QuantSignals]:
        """Compute signals for every ticker in the universe and rank by score.

        Args:
            tickers_data: Dict mapping ticker → {'bars': list[dict], 'info': obj | None}.
                The 'info' object may expose .sector, .pe_ratio, .week52_high,
                and .week52_low attributes.

        Returns:
            List of QuantSignals sorted descending by composite_score.
        """
        results: list[QuantSignals] = []
        for ticker, data in tickers_data.items():
            info = data.get("info")
            bars = data.get("bars", [])
            ticker_signals = self.compute_signals(
                ticker,
                bars,
                sector=info.sector if info else "Unknown",
                pe_ratio=float(info.pe_ratio) if info and info.pe_ratio else None,
                week52_high=float(info.week52_high) if info and info.week52_high else None,
                week52_low=float(info.week52_low) if info and info.week52_low else None,
            )
            results.append(ticker_signals)
        results.sort(key=lambda s: s.composite_score or 0, reverse=True)
        return results

    def _rsi(self, closes: np.ndarray, period: int = 14) -> float | None:
        """Calculate the 14-period Relative Strength Index using Wilder smoothing.

        Args:
            closes: Array of closing prices, oldest first.
            period: RSI look-back period. Defaults to 14.

        Returns:
            RSI value in [0, 100], or None if insufficient data.
        """
        if len(closes) < period + 1:
            return None
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        return float(100 - (100 / (1 + avg_gain / avg_loss)))

    def _ema(self, values: np.ndarray, period: int) -> np.ndarray:
        """Calculate the Exponential Moving Average for a price series.

        Uses a standard EMA multiplier of 2 / (period + 1). The first
        EMA value is seeded with the simple average of the first `period` bars.

        Args:
            values: Input price array, oldest first.
            period: EMA smoothing period.

        Returns:
            Array of EMA values, same length as input (leading entries are zero).
        """
        ema = np.zeros_like(values)
        multiplier = 2.0 / (period + 1)
        ema[period - 1] = np.mean(values[:period])
        for i in range(period, len(values)):
            ema[i] = values[i] * multiplier + ema[i - 1] * (1 - multiplier)
        return ema

    def _macd(
        self,
        closes: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[float, float] | tuple[None, None]:
        """Calculate MACD line and signal line for a closing price series.

        Args:
            closes: Array of closing prices, oldest first.
            fast: Fast EMA period. Defaults to 12.
            slow: Slow EMA period. Defaults to 26.
            signal: Signal line EMA period. Defaults to 9.

        Returns:
            (macd_line, signal_line) tuple of floats, or (None, None) if
            there are insufficient bars (need at least slow + signal bars).
        """
        if len(closes) < slow + signal:
            return None, None
        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._ema(macd_line[slow - 1:], signal)
        return float(macd_line[-1]), float(signal_line[-1])
