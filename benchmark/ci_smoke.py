# -*- coding: utf-8 -*-
"""
CI smoke test: import the installed wheel and exercise the Rust-backed indicators on
synthetic OHLCV (no committed data needed). Confirms the extension loads, numba is not
imported, and a few representative indicators produce correct, finite output.
"""
import sys
from pathlib import Path

# Use the in-repo build only when the source tree actually contains the compiled
# extension (local dev copies `_oaindicators` into ./openalgo/). In CI the extension
# is not committed, so fall through to the installed wheel - the artifact we ship -
# instead of shadowing it with an extension-less source package.
_root = Path(__file__).resolve().parent.parent
if list((_root / "openalgo").glob("_oaindicators*")):
    sys.path.insert(0, str(_root))

import numpy as np
import pandas as pd

import openalgo
from openalgo import ta
import openalgo._oaindicators as _rs  # noqa: F401 -- must import (extension present)

FAILS = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def nan_contract(close, high, low, vol):
    """The NaN contract of issue #2029, on both backends.

    Rolling kernels are window-local (a NaN blanks only the windows containing it,
    exactly like pandas `.rolling`); recursive kernels seed at the first finite
    value. Both matter because indicators are chained: every indicator emits
    warm-up NaNs, so a kernel that lets one NaN poison its accumulator turns the
    whole downstream series into NaN - which is what made `ta.crossover` report
    zero crossings over 1600 bars.
    """
    from openalgo.indicators import _backend

    n = close.size
    holed = close.copy()
    holed[:3] = np.nan          # an upstream indicator's warm-up
    holed[n // 2] = np.nan      # a gap in the middle

    def nan_idx(a):
        return set(np.flatnonzero(np.isnan(np.asarray(a, dtype=float))).tolist())

    ref = pd.Series(holed)
    check("sma window-local", nan_idx(ta.sma(holed, 20)) == nan_idx(ref.rolling(20).mean()))
    check("stdev window-local",
          nan_idx(ta.stdev(holed, 20)) == nan_idx(ref.rolling(20).std(ddof=0)))
    check("wma window-local", nan_idx(ta.wma(holed, 20)) == nan_idx(ref.rolling(20).mean()))
    # A poisoned stdev accumulator surfaced as a silent 0.0, not a NaN, because
    # `f64::max(NaN, 0.0)` returns 0.0 - so assert the values, not just the mask.
    sd = np.asarray(ta.stdev(holed, 20), dtype=float)
    check("stdev not collapsed to zero", np.nanmin(sd) > 0)
    # RSI over a warm-up prefix used to report a confident, flat 100.
    check("rsi skips upstream warm-up",
          np.isnan(np.asarray(ta.rsi(holed, 14), dtype=float)[:4]).all())
    check("median survives a NaN window", np.isnan(np.asarray(ta.median(holed, 5))).any())

    # The reported reproduction: chaining must not blank the series out.
    base = ta.wma(close, 55)
    fast = ta.rsi(base, 14)
    slow = ta.wma(fast, 9)
    f, sl = pd.Series(fast), pd.Series(slow)
    expected = int((((f.shift(1) <= sl.shift(1)) & (f > sl)).sum()))
    check("chained wma/rsi/wma yields crossovers", expected > 0)
    check("crossover matches pandas", int(np.asarray(ta.crossover(fast, slow)).sum()) == expected)

    # Rust and the pure-NumPy fallback must agree - the two diverged silently, so
    # the same script gave different answers from a wheel and a source checkout.
    if not _backend.HAVE_RUST:
        return
    cases = {
        "sma": lambda: _backend.sma(holed, 20),
        "wma": lambda: _backend.wma(holed, 20),
        "ema": lambda: _backend.ema(holed, 20),
        "stdev": lambda: _backend.stdev(holed, 20),
        "hma": lambda: _backend.hma(holed, 20),
        "rsi": lambda: _backend.rsi(holed, 14),
        "vwma": lambda: _backend.vwma(holed, vol, 20),
        "atr_wilder": lambda: _backend.atr_wilder(high, low, holed, 14),
    }
    rust = {k: np.asarray(f(), dtype=float) for k, f in cases.items()}
    _backend.HAVE_RUST = False
    try:
        numpy_ = {k: np.asarray(f(), dtype=float) for k, f in cases.items()}
    finally:
        _backend.HAVE_RUST = True
    for k in cases:
        a, b = rust[k], numpy_[k]
        same = np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(
            a, b, rtol=1e-9, atol=1e-9, equal_nan=True)
        check(f"backends agree: {k}", same)


def main():
    print(f"openalgo {openalgo.__version__}; rust core {_rs.__version__}")
    check("numba not imported", "numba" not in sys.modules)

    n = 2000
    # deterministic synthetic random walk OHLCV
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.standard_normal(n))
    high = close + np.abs(rng.standard_normal(n))
    low = close - np.abs(rng.standard_normal(n))
    open_ = close + rng.standard_normal(n) * 0.1
    vol = rng.integers(1_000, 10_000, n).astype(float)

    sma = ta.sma(close, 20)
    check("sma matches pandas", np.allclose(
        sma[19:], pd.Series(close).rolling(20).mean().to_numpy()[19:], atol=1e-9))

    ema = ta.ema(close, 20)
    check("ema finite", np.isfinite(ema).all())

    rsi = ta.rsi(close, 14)
    check("rsi in 0..100", np.nanmin(rsi) >= 0 and np.nanmax(rsi) <= 100)

    up, mid, lo = ta.bbands(close, 20, 2.0)
    check("bbands ordered", np.all(up[19:] >= mid[19:]) and np.all(mid[19:] >= lo[19:]))

    macd, sig, hist = ta.macd(close)
    check("macd finite", np.isfinite(macd).all())

    st, dirn = ta.supertrend(high, low, close, 10, 3.0)
    check("supertrend direction set", set(np.unique(dirn[~np.isnan(dirn)])) <= {-1.0, 1.0})

    obv = ta.obv(close, vol)
    check("obv finite", np.isfinite(obv).all())

    adx_di_p, adx_di_m, adx = ta.adx(high, low, close, 14)
    check("adx finite tail", np.isfinite(adx[-1]))

    # Series in -> Series out preserved
    s = pd.Series(close)
    check("series typing", isinstance(ta.sma(s, 10), pd.Series))

    nan_contract(close, high, low, vol)

    print("RESULT:", "SMOKE PASS" if not FAILS else f"FAIL: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
