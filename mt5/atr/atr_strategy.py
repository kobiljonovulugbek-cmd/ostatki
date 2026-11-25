"""
Экспериментальная стратегия «ATR-тест» (из Ulug.ipynb) — сигналы для советника OfficeTrader на ДЕМО-счёте.

Правило (зафиксировано заранее, не подгонять), отдельно для каждого уровня ATR (как в ноутбуке):
  * ATR = средний дневной диапазон 5 последних ПОЛНЫХ дней (Нью-Йорк).
    Уровни: 50% (A = 0.5 × ATR) и 100%D (A = 1.0 × ATR). 25% убран (спред съедает маленькую цель).
    Недельный/месячный уровни не используются: стоп 3–7.5 ATR, а советник закрывает всё в пятницу.
  * Нога вверх: от минимума после последнего отката на A; нога вниз — зеркально.
  * Цена (закрытие 5-мин свечи) прошла от начала ноги 1.0 × A → вход ПО движению (рынок).
  * С 25.11.2026 (вечер, решение владельца — схема HorizonX): SL = 3 × A, TP = 1 × A (выход при откате на A),
    лестница 6 ордеров с шагом 0.5A и весами лота 1,1,2,2,3,3 (ladder.py). Правило 90% отменено: P(TP раньше SL)
    для L1 ≈ 75–80% и примерно равна безубыточности 1/(1+R:R) = 75%; печатается в stderr.
  * Прежний режим 90%+ (до 25.11.2026 вечер): SL = 3 × A, TP = 0.1–0.3 × A (свой для каждого инструмента и уровня, см. PARAMS) —
    по истории TP срабатывает раньше SL в 90–95% случаев, но один стоп съедает 10–30 тейков,
    и после спреда ожидание отрицательное (−0.01…−0.07R на сделку). Один вход на ногу.
  * Нет входов 16:30–18:30 Нью-Йорк (ролловер) и в пятницу после 16:00 НЙ.
  * ФИЛЬТР «тренд + сессия» (с 25.11.2026): покупки — только если вчерашнее дневное закрытие выше SMA50 дневных
    закрытий, продажи — только если ниже; вход только 03:00–12:00 по Нью-Йорку (Лондон + начало НЙ).
    На истории 2019–2026 PF вырос с 0.59 до 0.67 (2010–2018: 0.56 → 0.58), сделок вдвое меньше.
История 2010–2026 (со спредом), PF по XAUUSD/EURUSD/GBPUSD/USDJPY:
  25% ~5 сделок/день, PF 0.75–0.88;  50% ~1.3/день, PF 0.84–0.98;  100%D ~0.3/день, PF 0.91–1.08.
Это эксперимент для проверки вживую, не прибыльная стратегия.

Запуск:  python mt5/atr/atr_strategy.py            -> строки для mt5/signals.csv (или пусто)
Каждая сделка = 6 ордеров лестницей (ladder.py): L1 по рынку, L2–L6 лимитки против движения с шагом
1/10 расстояния до стопа; стоп и цель общие; риск сделки делится на 6. id: ГГММДД-ЧЧММ-СИМВОЛ-L1…L6-A50|A100 (≤ 27 символов: в MT5 комментарий «AIO <id>» ≤ 31).
Скрипт не хранит состояние: сигнал выдаётся, только если вход сработал за последние WINDOW минут
(запуск раз в час -> окно 65 минут), поэтому одна нога не даст повторных сигналов.
"""
import sys, os, json, logging
from datetime import datetime, timezone
logging.disable(logging.INFO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atr_context import fetch, probability

SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
LEVELS = {"50": (0.5, 0.5), "100D": (1.0, 0.5)}   # уровень: (доля ATR, риск %); 25% убран владельцем 25.11.2026 (PF 0.13–0.34)
X = 1.0
TP_HORIZON = 1.0      # цель в долях A, как HorizonX (выход при откате на CorrectionRange); прежние TP_A из PARAMS не используются
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
SUFFIX = {"25": "-A25", "50": "-A50", "100D": "-A100"}   # окончание id ордера (уровень ATR)
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


def trend_by_day(df, n=50):
    """+1/-1 для каждого дня: закрытие ПРОШЛОГО дня (Нью-Йорк) выше/ниже SMA(n) прошлых дневных закрытий."""
    import pandas as pd
    dc = df["close"].groupby(df.index.date).last()
    sma = dc.rolling(n).mean()
    tr = ((dc > sma).astype(int) - (dc < sma).astype(int)).where(sma.notna(), 0).shift(1).fillna(0)
    return tr.to_dict()


def in_session(ts):
    """Лондон + начало Нью-Йорка: 03:00–12:00 по Нью-Йорку (13:00–22:00 Ташкента зимой)."""
    m = ts.hour * 60 + ts.minute
    return 3 * 60 <= m < 12 * 60


def blocked(ts):
    m = ts.hour * 60 + ts.minute
    return (16 * 60 + 30 <= m < 18 * 60 + 30) or (ts.weekday() == 4 and m >= 16 * 60)


def scan(symbol, df=None):
    df = fetch(symbol, days=100) if df is None else df     # 100 дней: нужны 50 дневных закрытий для тренда
    atr = daily_atr(df)
    trend = trend_by_day(df)
    ctx = None                                    # контекст для вероятности — только если есть сигнал
    hi, lo, cl, idx = df["high"].values, df["low"].values, df["close"].values, df.index
    out = []
    for level, (k, risk) in LEVELS.items():
        A = k * atr
        for side in ("buy", "sell"):
            s = 1 if side == "buy" else -1
            h = hi if s == 1 else -lo; l = lo if s == 1 else -hi; c = cl * s
            L = l[0]; H = h[0]; used = False; trig = None
            for i in range(len(c)):
                ok = (not blocked(idx[i])) and in_session(idx[i]) and trend.get(idx[i].date(), 0) == s
                if not used and c[i] >= L + X * A and ok:
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
            if age > WINDOW_MIN or blocked(idx[-1]) or not in_session(idx[-1]):
                continue
            SL_A = PARAMS[(symbol, level)][0]
            TP_A = TP_HORIZON                                 # цель HorizonX: откат 1A (с 25.11.2026 вечер)
            if abs(cl[-1] - cl[trig]) > 0.5 * TP_A * A:      # опоздали больше чем на пол-цели — вероятность уже другая
                continue
            entry = float(cl[-1])
            sl = entry - s * SL_A * A; tp = entry + s * TP_A * A
            try:
                from atr_context import context
                ctx = ctx or context(symbol)
                pr = probability(symbol, side, entry, sl, tp, ctx)
                P, n, be = pr.get("p"), pr.get("n"), pr.get("breakeven")
            except Exception:
                P, n, be = None, None, None
            out.append((symbol, level, risk, side, entry, sl, tp, atr, str(idx[trig])[:16], P, be, n, None, TP_A))
    return out


def main():
    from ladder import build as ladder
    now = datetime.now(timezone.utc)
    rows = []
    for sym in SYMBOLS:
        try:
            for symbol, level, risk, side, entry, sl, tp, atr, when, P, Pn, n, ev, TP_A in scan(sym):
                base = now.strftime("%y%m%d-%H%M") + f"-{symbol}"      # короткий id: в MT5 комментарий ≤ 31 символа
                # 6 ордеров лестницей (L1 по рынку, L2–L6 лимитки), общий риск сделки делится на 6
                for row in ladder(base, symbol, side, entry, sl, tp, risk, "market",
                                  f"ATR HX {LABEL[level]}", now=now.timestamp(), id_suffix=SUFFIX[level]):
                    rows.append({"row": row, "level": level, "trigger_bar_ny": when, "atr": atr,
                                 "p_L1": P, "breakeven_L1": Pn, "n": n})
        except Exception as ex:
            print(f"# {sym}: ошибка {type(ex).__name__}: {ex}", file=sys.stderr)
    for r in rows:
        print(r["row"])
    print(json.dumps(rows, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
