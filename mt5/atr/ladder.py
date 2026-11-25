"""
Лестница входов: одна сделка = 6 ордеров (решение владельца 25.11.2026, схема HorizonX с 25.11.2026 вечер).

  L1 — по рынку (или по цене сигнала), L2…L6 — лимитные ордера ПРОТИВ движения,
  каждый следующий на 1/6 расстояния «вход → стоп» дальше (как HorizonX: шаг 0.5 отката при стопе 3 отката;
  L6 — на 5/6 пути к стопу). Стоп и цель у всех ордеров общие.
  Лот растёт как в HorizonX: веса 1, 1, 2, 2, 3, 3. Общий риск сделки НЕ растёт — он делится по весам
  (1% -> 0.083/0.083/0.167/0.167/0.25/0.25%): если стоп заберёт все шесть, убыток = риск сделки.
  Цель как в HorizonX: 1/3 расстояния «вход → стоп» (откат на CorrectionRange = 1A при стопе 3A);
  советник (TPFromExtreme) переносит её от нового экстремума.

Запуск (для своих сигналов агента):
  python mt5/atr/ladder.py BASE_ID SYMBOL buy|sell ENTRY SL TP|auto TOTAL_RISK [market|limit|stop] [COMMENT]
  (TP = auto -> цель HorizonX: ENTRY ± |ENTRY − SL| / 3)
печатает 6 строк для mt5/signals.csv (id = BASE_ID-L1 … BASE_ID-L6) и вероятность по каждому ордеру.
"""
import sys, os, time, json, logging
logging.disable(logging.INFO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

N_ORDERS = 6
STEP = 1 / 6           # шаг лесенки в долях расстояния вход → стоп (HorizonX: 0.5 отката при стопе 3 отката)
WEIGHTS = [1, 1, 2, 2, 3, 3]   # рост лота как в HorizonX; общий риск сделки делится по весам
TP_FRACTION = 1 / 3    # цель HorizonX: откат 1A при стопе 3A


def horizon_tp(side, entry, sl):
    s = 1 if side == "buy" else -1
    return entry + s * abs(entry - sl) * TP_FRACTION


DIGITS = {"XAUUSD": 2, "USDJPY": 3}


def build(base_id, symbol, side, entry, sl, tp, total_risk, first_type="market", comment="",
          now=None, market_ttl=20 * 60, limit_ttl=4 * 3600, id_suffix=""):
    """Возвращает список строк signals.csv. id: <base_id>-L<i><id_suffix>."""
    now = int(now or time.time())
    d = DIGITS.get(symbol, 5)
    s = 1 if side == "buy" else -1
    dist = abs(entry - sl)
    rows = []
    for i in range(N_ORDERS):
        e = entry - s * i * STEP * dist                      # против движения: для buy ниже, для sell выше
        typ = first_type if i == 0 else "limit"
        exp = now + (market_ttl if typ == "market" else limit_ttl)
        risk_each = round(total_risk * WEIGHTS[i] / sum(WEIGHTS), 3)
        cm = (f"{comment} L{i+1}/6")[:40].replace(";", ",")
        rows.append(f"{base_id}-L{i+1}{id_suffix};{symbol};{side};{typ};{e:.{d}f};{sl:.{d}f};{tp:.{d}f};"
                    f"{risk_each};{exp};{cm}")
    return rows


def with_prob(rows, ctx=None):
    from atr_context import probability, context
    out = []
    for r in rows:
        f = r.split(";")
        sym, side, e, sl, tp = f[1], f[2], float(f[4]), float(f[5]), float(f[6])
        try:
            ctx = ctx or context(sym)
            p = probability(sym, side, e, sl, tp, ctx)
            out.append((r, p.get("p"), p.get("breakeven")))
        except Exception as ex:
            out.append((r, None, None))
    return out


if __name__ == "__main__":
    a = sys.argv
    if len(a) < 8:
        print(__doc__); sys.exit(1)
    base, sym, side, e, sl, tp, risk = a[1], a[2].upper(), a[3].lower(), float(a[4]), float(a[5]), a[6], float(a[7])
    tp = horizon_tp(side, e, sl) if tp.lower() == "auto" else float(tp)
    typ = a[8] if len(a) > 8 else "market"
    cm = a[9] if len(a) > 9 else ""
    rows = build(base, sym, side, e, sl, tp, risk, typ, cm)
    for r in rows:
        print(r)
    try:
        info = [{"id": r.split(";")[0], "p": p, "breakeven": be} for r, p, be in with_prob(rows)]
        print(json.dumps(info, ensure_ascii=False), file=sys.stderr)
    except Exception as ex:
        print(f"# вероятность не посчитана: {ex}", file=sys.stderr)
