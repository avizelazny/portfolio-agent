from datetime import datetime
from typing import Optional
import numpy as np
from src.models.market import QuantSignals

WEIGHTS = {"rsi":0.20,"macd":0.20,"momentum_20d":0.20,"volume":0.15,"pe_vs_sector":0.15,"week52":0.10}

class QuantEngine:
    def __init__(self, sector_pe_medians=None):
        self.sector_pe_medians = sector_pe_medians or {}

    def compute_signals(self, ticker, ohlcv_bars, sector="Unknown", pe_ratio=None, week52_high=None, week52_low=None):
        if len(ohlcv_bars) < 14:
            return QuantSignals(ticker=ticker, signal_time=datetime.now())
        closes = np.array([float(b["close"]) for b in ohlcv_bars], dtype=float)
        volumes = np.array([float(b["volume"]) for b in ohlcv_bars], dtype=float)
        signals = QuantSignals(ticker=ticker, signal_time=datetime.now())
        scores = {}; flags = []
        rsi = self._rsi(closes)
        if rsi:
            signals.rsi_14 = round(rsi,2)
            if rsi<=30: scores["rsi"]=1.0; flags.append(f"RSI oversold ({rsi:.1f})")
            elif rsi>=70: scores["rsi"]=-1.0; flags.append(f"RSI overbought ({rsi:.1f})")
            elif rsi<=40: scores["rsi"]=0.5
            elif rsi>=60: scores["rsi"]=-0.5
            else: scores["rsi"]=0.0
        macd_l, sig_l = self._macd(closes)
        if macd_l and sig_l:
            signals.macd=round(float(macd_l),6); signals.macd_signal=round(float(sig_l),6)
            h=macd_l-sig_l
            if macd_l>sig_l: scores["macd"]=min(1.0,abs(h)*10); flags.append("MACD bullish") if abs(h)>0.05 else None
            else: scores["macd"]=max(-1.0,-abs(h)*10)
        if len(closes)>=21:
            m=(closes[-1]-closes[-21])/closes[-21]*100; signals.momentum_20d=round(float(m),4)
            scores["momentum_20d"]=max(-1.0,min(1.0,m/20.0))
            if m>=10: flags.append(f"Strong momentum +{m:.1f}%")
            elif m<=-10: flags.append(f"Weak momentum {m:.1f}%")
        if len(volumes)>=20:
            vm=np.mean(volumes[-20:]); vs=np.std(volumes[-20:])
            if vs>0:
                z=(volumes[-1]-vm)/vs; signals.volume_anomaly=round(float(z),4)
                d=1.0 if closes[-1]>=closes[-2] else -1.0
                scores["volume"]=d*min(1.0,abs(z)/3.0) if abs(z)>1.5 else 0.0
                if z>2: flags.append(f"Volume spike ({z:.1f}σ)")
        if pe_ratio and sector in self.sector_pe_medians:
            sm=self.sector_pe_medians[sector]
            if sm>0 and pe_ratio>0:
                r=pe_ratio/sm; signals.pe_vs_sector=round(float(r),4)
                if r<0.75: scores["pe_vs_sector"]=0.8; flags.append(f"Cheap vs sector ({r:.2f}x)")
                elif r<0.90: scores["pe_vs_sector"]=0.4
                elif r>1.50: scores["pe_vs_sector"]=-0.8; flags.append(f"Expensive vs sector ({r:.2f}x)")
                elif r>1.25: scores["pe_vs_sector"]=-0.4
                else: scores["pe_vs_sector"]=0.0
        if week52_high and week52_low and week52_high>week52_low:
            pos=(float(closes[-1])-week52_low)/(week52_high-week52_low)
            signals.week52_position=round(float(pos),4)
            if pos<0.15: scores["week52"]=0.7; flags.append(f"Near 52w low")
            elif pos>0.85: scores["week52"]=0.3; flags.append(f"Near 52w high")
        if scores:
            tw=sum(WEIGHTS[k] for k in scores)
            signals.composite_score=round(sum(scores[k]*WEIGHTS[k] for k in scores)/tw,4) if tw>0 else 0.0
        signals.signal_summary=flags
        return signals

    def compute_all(self, tickers_data):
        results=[]
        for ticker,data in tickers_data.items():
            info=data.get("info"); bars=data.get("bars",[])
            s=self.compute_signals(ticker,bars,
                sector=info.sector if info else "Unknown",
                pe_ratio=float(info.pe_ratio) if info and info.pe_ratio else None,
                week52_high=float(info.week52_high) if info and info.week52_high else None,
                week52_low=float(info.week52_low) if info and info.week52_low else None)
            results.append(s)
        results.sort(key=lambda s:s.composite_score or 0,reverse=True)
        return results

    def _rsi(self, closes, period=14):
        if len(closes)<period+1: return None
        d=np.diff(closes); g=np.where(d>0,d,0.0); l=np.where(d<0,-d,0.0)
        ag=np.mean(g[:period]); al=np.mean(l[:period])
        for i in range(period,len(g)):
            ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
        if al==0: return 100.0
        return float(100-(100/(1+ag/al)))

    def _ema(self, v, p):
        e=np.zeros_like(v); k=2.0/(p+1); e[p-1]=np.mean(v[:p])
        for i in range(p,len(v)): e[i]=v[i]*k+e[i-1]*(1-k)
        return e

    def _macd(self, closes, fast=12, slow=26, signal=9):
        if len(closes)<slow+signal: return None,None
        ef=self._ema(closes,fast); es=self._ema(closes,slow)
        ml=ef-es; sl=self._ema(ml[slow-1:],signal)
        return float(ml[-1]),float(sl[-1])
