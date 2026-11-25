"""
Экспериментальная стратегия «ATR-тест» (из Ulug.ipynb) — сигналы для советника OfficeTrader на ДЕМО-счёте.

Правило (зафиксировано заранее, не подгонять):
  * ATR = средний дневной диапазон 5 последних ПОЛНЫХ дней (Нью-Йорк), A = 0.5 × ATR.
  * Нога вверх: от минимума после последнего отката на A; нога вниз — зеркально.
  * Цена (закрытие 5-мин свечи) прошла от начала ноги 1.0 × A → вход ПО движению (рынок).
  * SL = 1.0 × A, TP = 1.5 × A (R:R 1.5). Один вход на ногу.
  * Нет входов 16:30–18:30 Нью-Йорк (ролловер) и в пятницу после 16:00 НЙ.
История 2010–2026 (со спредом): ~1 сделка в день на инструмент, винрейт ~40%, в среднем ≈ −0.04R на сделку.
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
K, X, SL_A, TP_A = 0.5, 1.0, 1.0, 1.5
RISK = 0.5
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


def scan(symbol):
    df = fetch(symbol, days=20)
    atr = daily_atr(df); A = K * atr
    hi, lo, cl, idx = df["high"].values, df["low"].values, df["close"].values, df.index
    out = []
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
        entry = float(cl[-1])
        sl = entry - s * SL_A * A; tp = entry + s * TP_A * A
        out.append((symbol, side, entry, sl, tp, atr, str(idx[trig])[:16]))
    return out


def main():
    now = datetime.now(timezone.utc)
    rows = []
    for sym in SYMBOLS:
        try:
            for symbol, side, entry, sl, tp, atr, when in scan(sym):
                d = DIGITS.get(symbol, 5)
                p = probability(symbol, side, entry, sl, tp)
                sid = now.strftime("%Y%m%d-%H%M") + f"-{symbol}-ATR"
                exp = int(now.timestamp()) + 20 * 60
                row = f"{sid};{symbol};{side};market;{entry:.{d}f};{sl:.{d}f};{tp:.{d}f};{RISK};{exp};ATR-test X1 SL1 TP1.5"
                rows.append({"row": row, "trigger_bar_ny": when, "atr": atr,
                             "p": p.get("p"), "breakeven": p.get("breakeven"), "n": p.get("n")})
        except Exception as ex:
            print(f"# {sym}: ошибка {type(ex).__name__}: {ex}", file=sys.stderr)
    for r in rows:
        print(r["row"])
    print(json.dumps(rows, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
