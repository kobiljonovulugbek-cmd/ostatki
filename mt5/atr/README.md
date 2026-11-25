# mt5/atr — вероятность сделки по истории (для агента «Трейдер (демо)»)

Положить в репозиторий `ostatki` в папку `mt5/atr/`:
- `atr_context.py` — инструмент для агента
- `atr_prob_table.json` — таблица вероятностей (9 инструментов: EURUSD, GBPUSD, USDCHF, USDCAD,
  AUDUSD, NZDUSD, EURGBP, USDJPY, XAUUSD; 5-мин история 2010/2012 — 25.11.2026)
- `probs.py` — как посчитана таблица (нужны данные и backtest.py из Downloads/backtest на Mac)

Запуск (нужно `pip install dukascopy-python pandas`):
```
python mt5/atr/atr_context.py                              # ATR и растяжение по всем инструментам
python mt5/atr/atr_context.py XAUUSD buy 3530 3510 3560     # P(TP раньше SL) для сделки
python mt5/atr/atr_context.py --signals mt5/signals.csv     # проверить все сигналы файла
```
P(TP раньше SL) — доля таких же сделок в истории: тот же SL в дневных ATR, тот же R:R,
то же «растяжение» движения. Почти везде ≈ безубыточности 1/(1+R:R): это реалистичная база,
а не сигнал. Цены BID, спред не учтён. Учебная торговля на демо-счёте, не инвестиционный совет.
