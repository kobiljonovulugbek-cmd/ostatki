"""
ATR-контекст и историческая вероятность для агента «Трейдер (демо)».

Примеры:
  python mt5/atr/atr_context.py                                  # контекст по всем инструментам
  python mt5/atr/atr_context.py XAUUSD buy 3530 3510 3560         # вероятность для одной сделки
  python mt5/atr/atr_context.py --signals mt5/signals.csv         # проверить все сигналы из файла

Что считает:
  * ATR = средний дневной диапазон (High-Low) 5 последних ПОЛНЫХ дней (дни по Нью-Йорку), по 5-мин свечам Dukascopy.
  * Растяжение = сколько ATR цена уже прошла в сторону сделки от начала текущей ноги
    (нога заканчивается откатом на 0.5 ATR, как в Ulug.ipynb).
  * P(TP раньше SL) — доля таких же сделок в истории инструмента (2010/2012–2026, входы каждый час):
    тот же SL в ATR, тот же R:R, та же корзина растяжения. Безубыточность = 1/(1+R:R).
Вывод бэктеста: перевеса у входа «по растяжению» нет; таблица — реалистичная база для оценки вероятности,
а не сигнал. Цены BID, спред не учтён. Это учебная торговля на демо-счёте, не инвестиционный совет.
"""
import sys, os, json, csv
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "atr_prob_table.json"), encoding="utf-8"))
META, TABLE = T["meta"], T["table"]
SYMBOLS = list(TABLE.keys())
LEG_K = META["leg_k"]
EDGES = META["ext_edges"]
BUCKETS = ["0–0.5", "0.5–1", "1–1.5", "1.5–2", "2+"]


def fetch(symbol, days=20):
    import pandas as pd
    import dukascopy_python as d
    end = datetime.now(timezone.utc)
    df = d.to_dataframe(d.fetch_candles(symbol[:3] + "-" + symbol[3:], 5, d.TimeUnit.MINUTE,
                                        d.OfferSide.BID, end - timedelta(days=days), end))
    df = df[df["volume"] > 0]
    df.index = df.index.tz_convert("America/New_York")
    return df


def context(symbol):
    df = fetch(symbol)
    day = df.index.date
    g = df.groupby(day).agg(h=("high", "max"), l=("low", "min"), n=("open", "count"))
    full = g[g.n >= 100]
    if len(full) and full.index[-1] == day[-1]:
        full = full.iloc[:-1]                       # текущий день не полный
    atr = float((full.h - full.l).iloc[-5:].mean())
    A = atr * LEG_K
    hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
    L = lo[0]; H = hi[0]                            # нога вверх (для покупок)
    Hd = hi[0]; Ld = lo[0]                          # нога вниз (для продаж)
    for i in range(len(cl)):
        if lo[i] < L: L = lo[i]; H = lo[i]
        else:
            H = max(H, hi[i])
            if H - lo[i] >= A: L = lo[i]; H = lo[i]
        if hi[i] > Hd: Hd = hi[i]; Ld = hi[i]
        else:
            Ld = min(Ld, lo[i])
            if hi[i] - Ld >= A: Hd = hi[i]; Ld = hi[i]
    price = float(cl[-1])
    return {"symbol": symbol, "time_ny": str(df.index[-1])[:16], "price": price, "atr": atr,
            "ext_buy": (price - L) / atr, "ext_sell": (Hd - price) / atr, "leg_low": float(L), "leg_high": float(Hd)}


def nearest(v, grid):
    return min(grid, key=lambda g: abs(g - v))


def bucket(ext):
    b = 0
    while b < len(EDGES) and ext >= EDGES[b]:
        b += 1
    return b


def probability(symbol, side, entry, sl, tp, ctx=None):
    symbol = symbol.upper()
    if symbol not in TABLE:
        return {"symbol": symbol, "error": "нет истории для этого инструмента (есть: " + ", ".join(SYMBOLS) + ")"}
    ctx = ctx or context(symbol)
    side = side.lower()
    risk = abs(entry - sl); reward = abs(tp - entry)
    if risk <= 0:
        return {"symbol": symbol, "error": "SL совпадает с входом"}
    ok_side = (side == "buy" and sl < entry < tp) or (side == "sell" and tp < entry < sl)
    s_atr = risk / ctx["atr"]; rr = reward / risk
    S = nearest(s_atr, META["S"]); R = nearest(rr, META["RR"])
    cell = TABLE[symbol][f"{side}|{S}|{R}"]
    ext = ctx["ext_buy"] if side == "buy" else ctx["ext_sell"]
    b = bucket(ext)
    n_b, p_b = cell["all"]["by_ext"][b]
    p_all, p_new = cell["all"]["p"], cell["new"]["p"]
    be = round(100 / (1 + R), 1)        # безубыточность для той же ячейки таблицы (R:R из сетки)
    p_use = p_b if n_b >= 300 else p_all
    edge = round(p_use - be, 1)
    if abs(edge) < 3:
        verdict = "на уровне безубыточности — исторического перевеса нет"
    elif edge > 0:
        verdict = f"выше безубыточности на {edge} п.п."
    else:
        verdict = f"НИЖЕ безубыточности на {-edge} п.п. — сделка исторически убыточна"
    warn = []
    if not ok_side: warn.append("SL/TP не с той стороны от входа")
    if s_atr < META["S"][0] * 0.7 or s_atr > META["S"][-1] * 1.3:
        warn.append(f"SL = {s_atr:.2f} ATR вне сетки таблицы ({META['S'][0]}–{META['S'][-1]}), оценка грубая")
    if n_b < 300: warn.append(f"мало истории в корзине растяжения {BUCKETS[b]} (n={n_b}), взята общая цифра")
    return {"symbol": symbol, "side": side, "entry": entry, "sl": sl, "tp": tp,
            "atr": ctx["atr"], "sl_atr": round(s_atr, 2), "rr": round(rr, 2), "grid": [S, R],
            "ext": round(ext, 2), "ext_bucket": BUCKETS[b],
            "p": p_use, "p_all_years": p_all, "p_2024_2026": p_new, "n": cell["all"]["n"],
            "p_bucket": p_b, "n_bucket": n_b, "breakeven": be, "edge_pp": edge,
            "verdict": verdict, "warnings": warn}


def fmt_ctx(c):
    return (f"| {c['symbol']} | {c['price']:.5g} | {c['atr']:.5g} | {c['ext_buy']:.2f} | {c['ext_sell']:.2f} | {c['time_ny']} NY |")


def fmt_prob(r):
    if "error" in r:
        return f"- {r['symbol']}: {r['error']}"
    w = ("; ⚠️ " + "; ".join(r["warnings"])) if r["warnings"] else ""
    return (f"- {r['symbol']} {r['side']} {r['entry']} SL {r['sl']} TP {r['tp']}: SL = {r['sl_atr']} ATR, R:R {r['rr']}, "
            f"растяжение {r['ext']} ATR ({r['ext_bucket']}) → **P(TP раньше SL) ≈ {r['p']}%** "
            f"(все годы {r['p_all_years']}%, 2024–26 {r['p_2024_2026']}%, n={r['n']}); безубыточность при R:R {r['grid'][1]} — {r['breakeven']}% — {r['verdict']}{w}")


def main(argv):
    if len(argv) >= 6 and argv[1] != "--signals":
        _, sym, side, e, sl, tp = argv[:6]
        r = probability(sym, side, float(e), float(sl), float(tp))
        print(fmt_prob(r)); print(json.dumps(r, ensure_ascii=False)); return
    if len(argv) >= 3 and argv[1] == "--signals":
        rows = list(csv.DictReader(open(argv[2], encoding="utf-8"), delimiter=";"))
        now = datetime.now(timezone.utc).timestamp()
        cache = {}
        for row in rows:
            try:
                if float(row.get("expires_utc") or 0) < now - 86400: continue
                sym = row["symbol"].upper()
                if sym in TABLE and sym not in cache: cache[sym] = context(sym)
                print(row["id"], fmt_prob(probability(sym, row["side"], float(row["entry"]),
                                                      float(row["sl"]), float(row["tp"]), cache.get(sym))))
            except Exception as ex:
                print(row.get("id"), "ошибка:", ex)
        return
    print("| Инструмент | Цена | ATR (день) | Растяжение вверх, ATR | Растяжение вниз, ATR | Свеча |")
    print("|---|---|---|---|---|---|")
    for s in SYMBOLS:
        try:
            print(fmt_ctx(context(s)))
        except Exception as ex:
            print(f"| {s} | нет данных: {type(ex).__name__} | | | | |")


if __name__ == "__main__":
    import logging; logging.disable(logging.INFO)
    main(sys.argv)
