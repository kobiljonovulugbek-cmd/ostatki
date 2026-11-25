# Сигналы агента «Трейдер (демо)» из ИИ-офиса

`signals.csv` — сигналы для советника OfficeTrader в MT5 (работает только на демо-счёте).
Формат строки: `id;symbol;side;type;entry;sl;tp;risk;expires_utc;comment`
- side: buy | sell, type: market | limit | stop
- risk — % от баланса (советник ограничивает своим RiskPercent, максимум 2)
- expires_utc — время истечения сигнала (unix, UTC)

Файл обновляет запланированная задача Claude «Трейдер (демо): сигналы».

## mt5/atr — историческая вероятность сделки
`python mt5/atr/atr_context.py --signals mt5/signals.csv` — для каждого сигнала P(TP раньше SL)
по истории инструмента (2010–2026), с учётом SL в дневных ATR, R:R и растяжения движения.
Подробно — `mt5/atr/README.md`. Сайт остатков эти файлы не использует.
