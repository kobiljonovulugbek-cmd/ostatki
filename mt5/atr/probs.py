"""
Историческая вероятность «TP раньше SL» для сигналов агента «Трейдер (демо)».

Для каждого инструмента: входы каждый час (каждая 12-я 5-минутная свеча, кроме ролловера и вечера пятницы),
по цене открытия следующей свечи, с фиксированными SL и TP:
  SL = S × дневной ATR (средний диапазон 5 прошлых полных дней), TP = RR × SL.
Исход: что раньше — TP или SL (в одной свече оба -> считаем SL). Не дошло за 3 суток — не считается.
Раскладка по «растяжению»: сколько дневных ATR цена уже прошла в сторону сделки от начала текущей ноги
(нога — как в Ulug.ipynb: от экстремума до отката на 0.5 ATR). Корзины: 0–0.5, 0.5–1, 1–1.5, 1.5–2, 2+.
Периоды: все годы и отдельно 2024–2026. Цены BID, спред не учтён (он немного снижает шансы).
"""
import numpy as np, json, sys
from numba import njit
import backtest as B

PAIRS = B.PAIRS + ["USDJPY", "XAUUSD"]
SS = [0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0]
RRS = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]
EDGES = np.array([0.5, 1.0, 1.5, 2.0])          # корзины растяжения
LEG_K = 0.5
MAXBARS = 1440


@njit(cache=True)
def extensions(h, l, c, atr, k):
    n = len(c); ext = np.zeros(n)
    L = l[0]; H = h[0]
    for i in range(n):
        A = atr[i] * k
        if A == A and A > 0:
            if l[i] < L:
                L = l[i]; H = l[i]
            else:
                if h[i] > H: H = h[i]
                if H - l[i] >= A:
                    L = l[i]; H = l[i]
            ext[i] = (c[i] - L) / atr[i]
    return ext


@njit(cache=True)
def sim(o, h, l, atr, ext, block, tmin, newt, s, rr, edges, maxbars):
    n = len(o); nb = len(edges) + 1
    res = np.zeros((2, nb, 2))              # период x корзина x [n, wins]
    for i in range(0, n - 2, 12):
        a = atr[i]
        if a != a or a <= 0 or block[i]:
            continue
        b = 0
        while b < len(edges) and ext[i] >= edges[b]:
            b += 1
        e = o[i + 1]; sl = e - s * a; tp = e + rr * s * a
        out = -1
        for j in range(i + 1, min(n, i + 1 + maxbars)):
            if l[j] <= sl:
                out = 0; break
            if h[j] >= tp:
                out = 1; break
        if out < 0:
            continue
        seg = 1 if tmin[i] >= newt else 0
        res[0, b, 0] += 1; res[0, b, 1] += out
        if seg == 1:
            res[1, b, 0] += 1; res[1, b, 1] += out
    return res


def main():
    table = {}
    for pair in PAIRS:
        t, o, h, l, c = B.load(pair, "_data")
        atr = B.daily_atr(t, h, l); block = B.no_entry_mask(t)
        table[pair] = {}
        for side, (oo, hh, ll, cc) in {"buy": (o, h, l, c), "sell": (-o, -l, -h, -c)}.items():
            ext = extensions(hh, ll, cc, atr, LEG_K)
            for s in SS:
                for rr in RRS:
                    r = sim(oo, hh, ll, atr, ext, block, t, B.SPLIT2, s, rr, EDGES, MAXBARS)
                    cell = {}
                    for per, name in ((0, "all"), (1, "new")):
                        tot = r[per].sum(axis=0)
                        cell[name] = {"n": int(tot[0]), "p": round(100 * tot[1] / max(tot[0], 1), 1),
                                      "by_ext": [[int(x[0]), round(100 * x[1] / max(x[0], 1), 1)] for x in r[per]]}
                    table[pair][f"{side}|{s}|{rr}"] = cell
        print(pair, file=sys.stderr)
    first = {p: str(np.datetime64(int(B.load(p, "_data")[0][0]), "m"))[:10] for p in PAIRS}
    meta = {"desc": __doc__.strip(), "S": SS, "RR": RRS, "ext_edges": EDGES.tolist(), "leg_k": LEG_K,
            "history_from": first, "history_to": "2026-11-25"}
    json.dump({"meta": meta, "table": table}, open("atr_prob_table.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
