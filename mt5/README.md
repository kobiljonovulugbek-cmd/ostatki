# Сигналы агента «Трейдер (демо)» из ИИ-офиса

`signals.csv` — сигналы для советника OfficeTrader в MT5 (работает только на демо-счёте).
Формат строки: `id;symbol;side;type;entry;sl;tp;risk;expires_utc;comment`
- side: buy | sell, type: market | limit | stop
- risk — % от баланса (советник ограничивает своим RiskPercent, максимум 2)
- expires_utc — время истечения сигнала (unix, UTC)

Файл обновляет запланированная задача Claude «Трейдер (демо): сигналы».
