"""
Экспериментальная стратегия «ATR-тест» (из Ulug.ipynb) — сигналы для советника OfficeTrader на ДЕМО-счёте.

Правило (зафиксировано заранее, не подгонять), отдельно для каждого уровня ATR (как в ноутбуке):
  * ATR = средний дневной диапазон 5 последних ПОЛНЫХ дней (Нью-Йорк).
    Уровни: 25% (A = 0.25 × ATR), 50% (A = 0.5 × ATR), 100%D (A = 1.0 × ATR).
    Недельный/месячный уровни не используются: стоп 3–7.5 ATR, а советник закрывает всё в пятницу.
  * Нога вверх: от минимума после последнего отката на A; нога вниз — зеркально.
  * Цена (закрытие 5-мин свечи) прошла от начала ноги 1.0 × A → вход ПО движению (рынок).
  * Режим 90%+: SL = 3 × A, TP = 0.1–0.3 × A (свой для каждого инструмента и уровня, см. PARAMS) —
    по истории TP срабатывает раньше SL в 90–95% случаев, но один стоп съедает 10–30 тейков,
    и после спреда ожидание отрицательное (−0.01…−0.07R на сделку). Один вход на ногу.
  * Нет входов 16:30–18:30 Нью-Йорк (ролловер) и в пятницу после 16:00 НЙ.
История 2010–2026 (со спредом), PF по XAUUSD/EURUSD/GBPUSD/USDJPY:
  25% ~5 сделок/день, PF 0.75–0.88;  50% ~1.3/день, PF 0.84–0.98;  100%D ~0.3/день, PF 0.91–1.08.
Это эксперимент для проверки вживую, не прибыльная стратегия.

Запуск:  python mt5/atr/atr_strategy.py            -> строки для mt5/signals.csv (или пусто)
Скрипт не хранит состояние: сигнал выдаётся, только если вход сработал за последние WINDOW минут
(запуск раз в час -> окно 65 минут), поэтому одна нога не даст повторных сигналов.
"""
import sys, os, json, logging
from datetime import datetime, timezone
logging.disable(logging.INFO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atr_context import fetch, probability

SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
LEVELS = {"25": (0.25, 0.25), "50": (0.5, 0.5), "100D": (1.0, 0.5)}   # уровень: (доля ATR, риск %)
X = 1.0
# Режим «90%+» (по требованию владельца): SL = 3A, TP подобран по истории 2010–2026 так,
# чтобы TP срабатывал раньше SL не меньше чем в 90% случаев. (P, P 2024–26, n, ожидание после спреда в R)
PARAMS = {
    ("EURUSD", "25"): (3.0, 0.15, 90.7, 90.6, 25257, -0.060), ("EURUSD", "50"): (3.0, 0.20, 91.5, 92.5, 6163, -0.028),
    ("EURUSD", "100D"): (3.0, 0.15, 93.6, 91.5, 1695, -0.015),
    ("GBPUSD", "25"): (3.0, 0.15, 90.9, 91.5, 24897, -0.064), ("GBPUSD", "50"): (3.0, 0.10, 94.6, 94.9, 6540, -0.031),
    ("GBPUSD", "100D"): (3.0, 0.15, 94.3, 93.8, 1646, -0.015),
    ("USDJPY", "25"): (3.0, 0.10, 91.1, 90.8, 22016, -0.073), ("USDJPY", "50"): (3.0, 0.15, 92.6, 92.8, 5893, -0.031),
    ("USDJPY", "100D"): (3.0, 0.30, 90.3, 89.5, 1410, -0.008),
    ("XAUUSD", "25"): (3.0, 0.10, 91.2, 91.8, 25724, -0.069), ("XAUUSD", "50"): (3.0, 0.15, 91.4, 91.8, 6485, -0.045),
    ("XAUUSD", "100D"): (3.0, 0.20, 91.8, 92.0, 1662, -0.021),
}
LABEL = {"25": "25%", "50": "50%", "100D": "100%D"}
K = 0.5               # для обратной совместимости
WINDOW_MIN = 65
DIGITS = {"XAUUSD": 2, "USDJPY": 3}


def daily_atr(df):
    day = df.index.date
    g = df.groupby(day).agg(h=("high", "max"), l=("low", "min"), n=("open", "count"))
    full = g[g.n >= 100]
    if len(full) and full.index[-1] == day[-1]:
        full = full.iloc[:-1]
    return float((full.h - full.l).iloc[-5:].mean())


def blocked(ts):
    m = ts.hour * 60 + ts.minute
    return (16 * 60 + 30 <= m < 18 * 60 + 30) or (ts.weekday() == 4 and m >= 16 * 60)


def scan(symbol, df=None):
    df = fetch(symbol, days=20) if df is None else df
    atr = daily_atr(df)
    hi, lo, cl, idx = df["high"].values, df["low"].values, df["close"].values, df.index
    out = []
    for level, (k, risk) in LEVELS.items():
        A = k * atr
        for side in ("buy", "sell"):
            s = 1 if side == "buy" else -1
            h = hi if s == 1 else -lo; l = lo if s == 1 else -hi; c = cl * s
            L = l[0]; H = h[0]; used = False; trig = None
            for i in range(len(c)):
                if not used and c[i] >= L + X * A and not blocked(idx[i]):
                    used = True; trig = i
                if l[i] < L:
                    L = l[i]; H = l[i]; used = False; trig = None
                else:
                    H = max(H, h[i])
                    if H - l[i] >= A:
                        L = l[i]; H = l[i]; used = False; trig = None
            if trig is None:
                continue
            age = (idx[-1] - idx[trig]).total_seconds() / 60
            if age > WINDOW_MIN or blocked(idx[-1]):
                continue
            SL_A, TP_A, P, Pn, n, ev = PARAMS[(symbol, level)]
            if abs(cl[-1] - cl[trig]) > 0.5 * TP_A * A:      # опоздали больше чем на пол-цели — вероятность уже другая
                continue
            entry = float(cl[-1])
            sl = entry - s * SL_A * A; tp = entry + s * TP_A * A
            out.append((symbol, level, risk, side, entry, sl, tp, atr, str(idx[trig])[:16], P, Pn, n, ev, TP_A))
    return out


def main():
    now = datetime.now(timezone.utc)
    rows = []
    for sym in SYMBOLS:
        try:
            for symbol, level, risk, side, entry, sl, tp, atr, when, P, Pn, n, ev, TP_A in scan(sym):
                d = DIGITS.get(symbol, 5)
                sid = now.strftime("%Y%m%d-%H%M") + f"-{symbol}-{side[0].upper()}-ATR{level}"
                exp = int(now.timestamp()) + 20 * 60
                row = f"{sid};{symbol};{side};market;{entry:.{d}f};{sl:.{d}f};{tp:.{d}f};{risk};{exp};ATR90 {LABEL[level]} SL3 TP{TP_A}"
                rows.append({"row": row, "level": level, "trigger_bar_ny": when, "atr": atr,
                             "p": P, "p_2024_2026": Pn, "n": n, "breakeven": round(100 * 3.0 / (3.0 + TP_A), 1),
                             "expected_R_after_spread": ev})
        except Exception as ex:
            print(f"# {sym}: ошибка {type(ex).__name__}: {ex}", file=sys.stderr)
    for r in rows:
        print(r["row"])
    print(json.dumps(rows, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
