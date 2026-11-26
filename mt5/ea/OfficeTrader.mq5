//+------------------------------------------------------------------+
//|  OfficeTrader.mq5 — исполнитель сигналов агента «Трейдер (демо)»  |
//|  из ИИ-офиса. Работает ТОЛЬКО на демо-счёте.                      |
//|                                                                  |
//|  Сигналы: текстовый файл signals.csv (через «;»):                |
//|  id;symbol;side;type;entry;sl;tp;risk;expires_utc;comment        |
//|  side = buy|sell, type = market|limit|stop, expires_utc = unix   |
//|                                                                  |
//|  1.12: лесенка до 6 ордеров на сделку (MaxOpenOrders = 30);      |
//|        TP от экстремума: если цена идёт против сделки, TP всех   |
//|        её ордеров = новый максимум − цель (sell) /               |
//|        новый минимум + цель (buy). Сделка = символ+направление+SL.|
//|  1.13: торговые часы как в HorizonX — новые сигналы принимаются  |
//|        только с SessionStartHour до SessionEndHour (по часовому  |
//|        поясу SessionGMTOffset, по умолчанию Ташкент 13–22).      |
//|        Уже выставленные лимитки и открытые позиции работают и вне|
//|        окна.                                                     |
//|  1.14: время «сейчас по UTC» берётся по котировкам сервера, а не |
//|        по часам компьютера (часы ПК спешили ~15 мин, и L1 по     |
//|        рынку «просрочивался» сразу). Когда все позиции сделки    |
//|        закрыты, её неисполненные лимитки снимаются (раньше они   |
//|        наливались после TP и уходили в стоп).                    |
//+------------------------------------------------------------------+
#property copyright "AI Office"
#property version   "1.14"
#property description "Открывает сделки по сигналам ИИ-офиса. Только демо-счёт."
#include <Trade/Trade.mqh>

input string SignalsURL           = "https://raw.githubusercontent.com/kobiljonovulugbek-cmd/ostatki/main/mt5/signals.csv";
input int    PollSeconds          = 60;     // как часто проверять сигналы, сек (мин. 30)
input double RiskPercent          = 1.0;    // риск на сделку, % от баланса (не больше 2)
input int    MaxOpenOrders        = 30;     // максимум открытых позиций и ордеров советника (лесенка = 6 на сделку)
input double MaxPriceDeviationPct = 0.30;   // если цена ушла от цены сигнала больше, сигнал пропускается
input string SymbolSuffix         = "";     // суффикс брокера, например "m" (XAUUSDm) или ".r"
input string SymbolMap            = "";     // замены имён: US500=SPX500,NAS100=USTEC,AAPL=AAPL.US
input long   Magic                = 20261123;
input double MaxDailyLossPct      = 3.0;    // дневной лимит убытка, % (после него — без новых сделок до завтра)
input double BreakevenAtR         = 1.0;    // перенос стопа в безубыток при прибыли N×риск (0 = выкл.)
input int    CloseFridayHour      = 22;     // в пятницу с этого часа (время сервера) закрыть всё (-1 = выкл.)
input bool   TPFromExtreme        = true;   // если цена идёт против сделки — TP всех её ордеров от нового экстремума
input int    SessionStartHour     = 13;     // торговые часы: новые сигналы с этого часа (-1 = без ограничения)
input int    SessionEndHour       = 22;     // ... и до этого часа (не включая)
input int    SessionGMTOffset     = 5;      // часовой пояс торговых часов, ч от UTC (Ташкент = 5)
input bool   CancelRestAfterClose = true;   // все позиции сделки закрыты -> снять её оставшиеся лимитки

CTrade trade;
const string DONE_FILE = "office_trader_done.txt";
const string LOG_FILE  = "office_trader_log.csv";
const string TRADES_FILE = "office_trader_trades.csv";
const string SYMBOLS_FILE = "office_trader_symbols.txt";
const string LTP = "OTLTP_";               // префикс глобальных переменных TP от экстремума
int lastLimitDay = -1;
datetime lastPoll = 0;

bool IsDemo() { return AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO; }

int OnInit()
{
   if(!IsDemo())
   {
      Alert("OfficeTrader работает только на ДЕМО-счёте. На этом счёте советник не запустится.");
      return(INIT_FAILED);
   }
   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(50);
   EventSetTimer(1);                          // раз в секунду: TP от экстремума; сигналы — раз в PollSeconds
   Print("OfficeTrader 1.14 запущен (демо). Сигналы: ", SignalsURL, ". Лесенка до ", MaxOpenOrders,
         " ордеров, TP от экстремума: ", (TPFromExtreme ? "вкл" : "выкл"),
         StringFormat(", торговые часы %d–%d (UTC%+d)", SessionStartHour, SessionEndHour, SessionGMTOffset));
   DumpSymbols();
   ClockCheck();
   Manage();
   Poll();
   lastPoll = TimeLocal();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTick() { if(TPFromExtreme && IsDemo()) LadderTP(); }

void OnTimer()
{
   if(!IsDemo()) { Print("OfficeTrader: счёт не демо — советник отключается."); ExpertRemove(); return; }
   if(TPFromExtreme) LadderTP();
   if(TimeLocal() - lastPoll >= MathMax(30, PollSeconds))
   {
      lastPoll = TimeLocal();
      Manage();
      Poll();
   }
}

//--- загрузка и разбор файла сигналов
void Poll()
{
   char   post[], res[];
   string headers;
   string url = SignalsURL + "?t=" + IntegerToString((long)TimeGMT() / 60);
   ResetLastError();
   int code = WebRequest("GET", url, "", 5000, post, res, headers);
   if(code == -1)
   {
      Print("OfficeTrader: WebRequest ошибка ", GetLastError(),
            ". Добавьте https://raw.githubusercontent.com в Сервис → Настройки → Советники → «Разрешить WebRequest».");
      return;
   }
   if(code != 200) { Print("OfficeTrader: файл сигналов недоступен, HTTP ", code); return; }

   string text = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
   string lines[];
   int n = StringSplit(text, '\n', lines);
   for(int i = 0; i < n; i++)
   {
      string ln = lines[i];
      StringReplace(ln, "\r", "");
      StringTrimLeft(ln);
      StringTrimRight(ln);
      if(ln == "" || StringGetCharacter(ln, 0) == '#' || StringFind(ln, "id;") == 0) continue;
      ProcessLine(ln);
   }
}

string Trim(string s) { StringTrimLeft(s); StringTrimRight(s); return s; }

string Resolve(string s)
{
   if(SymbolMap != "")
   {
      string pairs[];
      int k = StringSplit(SymbolMap, ',', pairs);
      for(int i = 0; i < k; i++)
      {
         string kv[];
         if(StringSplit(pairs[i], '=', kv) == 2 && Trim(kv[0]) == s) return Trim(kv[1]);
      }
   }
   string cand = s + SymbolSuffix;
   if(SymbolExist(cand)) return cand;
   // известные варианты имён у брокеров
   string aliases[][2] = {{"NAS100","USTECH100M"},{"NAS100","USTEC"},{"NAS100","US100"},{"NAS100","NDX100"},
                          {"US500","SPX500"},{"US500","US500M"},{"US500","SP500"},{"XAUUSD","GOLD"}};
   for(int i = 0; i < ArrayRange(aliases, 0); i++)
      if(aliases[i][0] == s && SymbolExist(aliases[i][1] + SymbolSuffix)) return aliases[i][1] + SymbolSuffix;
   return cand;
}
bool SymbolExist(string name) { bool custom = false; return SymbolExist(name, custom); }

void ProcessLine(string ln)
{
   string f[];
   if(StringSplit(ln, ';', f) < 9) return;
   string id    = Trim(f[0]);
   string sym   = Trim(f[1]);
   string side  = Trim(f[2]);
   string type  = Trim(f[3]);
   double entry = StringToDouble(Trim(f[4]));
   double sl    = StringToDouble(Trim(f[5]));
   double tp    = StringToDouble(Trim(f[6]));
   double risk  = StringToDouble(Trim(f[7]));
   long   expUtc = StringToInteger(Trim(f[8]));
   if(id == "" || IsDone(id)) return;

   string symbol = Resolve(sym);
   if(expUtc > 0 && (long)NowUTC() > expUtc) { MarkDone(id); Log(id, symbol, "сигнал просрочен — пропущен"); return; }
   if(side != "buy" && side != "sell") { MarkDone(id); Log(id, symbol, "неизвестное направление: " + side); return; }
   if(type != "market" && type != "limit" && type != "stop") { MarkDone(id); Log(id, symbol, "неизвестный тип: " + type); return; }
   if(!SymbolSelect(symbol, true)) { MarkDone(id); Log(id, symbol, "символ не найден у брокера (проверьте SymbolSuffix/SymbolMap)"); return; }
   if(CountOpen() >= MaxOpenOrders) return; // попробуем при следующей проверке
   if(DailyLimitHit()) { MarkDone(id); Log(id, symbol, StringFormat("дневной лимит убытка %.1f%% достигнут — сигнал пропущен", MaxDailyLossPct)); return; }
   if(FridayClose())   { MarkDone(id); Log(id, symbol, "пятница вечер — новые сделки не открываются"); return; }
   if(!InSession())    { MarkDone(id); Log(id, symbol, StringFormat("вне торговых часов %02d:00–%02d:00 (UTC%+d) — пропущен", SessionStartHour, SessionEndHour, SessionGMTOffset)); return; }

   bool   buy = (side == "buy");
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0) return; // нет котировок (рынок закрыт) — позже
   double price = buy ? ask : bid;
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   if(type == "market" && entry > 0 && MathAbs(price - entry) / entry * 100.0 > MaxPriceDeviationPct)
   { MarkDone(id); Log(id, symbol, StringFormat("цена %.*f ушла от цены сигнала %.*f — пропущен", digits, price, digits, entry)); return; }

   double ref = (type == "market") ? price : entry;
   if(ref <= 0 || (buy && !(sl < ref && tp > ref)) || (!buy && !(sl > ref && tp < ref)))
   { MarkDone(id); Log(id, symbol, "стоп/тейк не с той стороны от цены — пропущен"); return; }

   double r = (risk > 0 ? risk : RiskPercent);
   r = MathMin(r, MathMin(RiskPercent, 2.0));
   double lots = CalcLots(symbol, MathAbs(ref - sl), r);
   if(lots <= 0) { MarkDone(id); Log(id, symbol, "минимальный лот даёт риск больше допустимого — пропущен"); return; }

   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   string cmt = StringSubstr("AIO " + id, 0, 31);
   bool ok = false;
   if(type == "market")
      ok = buy ? trade.Buy(lots, symbol, 0.0, sl, tp, cmt) : trade.Sell(lots, symbol, 0.0, sl, tp, cmt);
   else
   {
      entry = NormalizeDouble(entry, digits);
      datetime exp = 0;
      ENUM_ORDER_TYPE_TIME tt = ORDER_TIME_GTC;
      if(expUtc > 0)
      {
         exp = (datetime)(expUtc + ServerOffset()); tt = ORDER_TIME_SPECIFIED;
         if(TimeCurrent() > 0 && exp <= TimeCurrent() + 60) { MarkDone(id); Log(id, symbol, "сигнал просрочен — пропущен"); return; }
      }
      if(type == "limit")
         ok = buy ? trade.BuyLimit(lots, entry, symbol, sl, tp, tt, exp, cmt) : trade.SellLimit(lots, entry, symbol, sl, tp, tt, exp, cmt);
      else
         ok = buy ? trade.BuyStop(lots, entry, symbol, sl, tp, tt, exp, cmt) : trade.SellStop(lots, entry, symbol, sl, tp, tt, exp, cmt);
   }
   uint rc = trade.ResultRetcode();
   if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL))
   {
      MarkDone(id);
      Log(id, symbol, StringFormat("ОТКРЫТО %s %s %.2f лот, вход %.*f, SL %.*f, TP %.*f, риск %.3f%%",
          side, type, lots, digits, (type == "market" ? trade.ResultPrice() : entry), digits, sl, digits, tp, r));
   }
   else if(rc == TRADE_RETCODE_MARKET_CLOSED) Log(id, symbol, "рынок закрыт — повторю позже");
   else { MarkDone(id); Log(id, symbol, StringFormat("ошибка %u: %s", rc, trade.ResultRetcodeDescription())); }
}

double CalcLots(string s, double dist, double riskPct)
{
   if(dist <= 0) return 0;
   double tv = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tv <= 0) tv = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0) return 0;
   double money = AccountInfoDouble(ACCOUNT_BALANCE) * riskPct / 100.0;
   double lots  = money / (dist / ts * tv);
   double step = SymbolInfoDouble(s, SYMBOL_VOLUME_STEP);
   double mn   = SymbolInfoDouble(s, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(s, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   lots = MathFloor(lots / step) * step;
   if(lots < mn) return 0;
   if(lots > mx) lots = mx;
   int vd = (int)MathMax(0, MathCeil(-MathLog10(step)));
   return NormalizeDouble(lots, vd);
}

int CountOpen()
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == Magic) c++;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
      if(OrderGetTicket(i) > 0 && OrderGetInteger(ORDER_MAGIC) == Magic) c++;
   return c;
}

bool IsDone(string id)
{
   int h = FileOpen(DONE_FILE, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   bool found = false;
   while(!FileIsEnding(h)) { if(FileReadString(h) == id) { found = true; break; } }
   FileClose(h);
   return found;
}

void MarkDone(string id)
{
   int h = FileOpen(DONE_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, id + "\n");
   FileClose(h);
}

void Log(string id, string sym, string msg)
{
   Print("OfficeTrader [", id, "] ", sym, ": ", msg);
   int h = FileOpen(LOG_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_UNICODE);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES) + ";" + id + ";" + sym + ";" + msg + "\n");
   FileClose(h);
}

//--- сопровождение: безубыток и закрытие перед выходными
void Manage()
{
   bool friday = FridayClose();
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(friday) { if(trade.PositionClose(t)) Log("-", sym, "закрыто перед выходными"); continue; }
      if(BreakevenAtR <= 0) continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
      if(sl <= 0) continue;
      bool buy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      if((buy && sl >= open) || (!buy && sl <= open)) continue; // уже в безубытке
      double R = MathAbs(open - sl);
      double cur = buy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
      double gain = buy ? cur - open : open - cur;
      if(R > 0 && gain >= BreakevenAtR * R)
      {
         int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
         double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
         double be = NormalizeDouble(buy ? open + 2 * pt : open - 2 * pt, dg);
         if(trade.PositionModify(t, be, tp)) Log("-", sym, StringFormat("стоп перенесён в безубыток %.*f", dg, be));
      }
   }
   if(friday)
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong o = OrderGetTicket(i);
         if(o > 0 && OrderGetInteger(ORDER_MAGIC) == Magic && trade.OrderDelete(o)) Log("-", OrderGetString(ORDER_SYMBOL), "отложенный ордер удалён перед выходными");
      }
}

//--- TP от экстремума: цена идёт против сделки -> TP всех её ордеров = экстремум ∓ исходная цель
string LtpKey(const string sym, const int dir, const double sl)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   return LTP + sym + "_" + (string)dir + "_" + DoubleToString(sl, dg);
}

void LadderTP()
{
   string keys[]; string syms[]; int dirs[]; double tps[]; double dmin[]; double oext[];
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      double sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP), op = PositionGetDouble(POSITION_PRICE_OPEN);
      if(sl <= 0 || tp <= 0) continue;
      int dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      if((dir > 0 && sl >= op) || (dir < 0 && sl <= op)) continue;   // уже в безубытке — не трогаем
      string k = LtpKey(sym, dir, sl);
      int g = -1;
      for(int j = 0; j < n; j++) if(keys[j] == k) { g = j; break; }
      if(g < 0)
      {
         n++;
         ArrayResize(keys, n); ArrayResize(syms, n); ArrayResize(dirs, n);
         ArrayResize(tps, n); ArrayResize(dmin, n); ArrayResize(oext, n);
         g = n - 1; keys[g] = k; syms[g] = sym; dirs[g] = dir; tps[g] = tp; dmin[g] = MathAbs(tp - op); oext[g] = op;
      }
      else
      {
         dmin[g] = MathMin(dmin[g], MathAbs(tp - op));
         tps[g]  = (dir < 0) ? MathMax(tps[g], tp) : MathMin(tps[g], tp);
         oext[g] = (dir < 0) ? MathMax(oext[g], op) : MathMin(oext[g], op);
      }
   }

   for(int g = 0; g < n; g++)
   {
      string sym = syms[g]; int dir = dirs[g];
      double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK), bid = SymbolInfoDouble(sym, SYMBOL_BID);
      if(ask <= 0 || bid <= 0) continue;
      double stops = MathMax((double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL), 1.0) * pt;
      string kd = keys[g] + "_d", kx = keys[g] + "_x";
      if(!GlobalVariableCheck(kd)) GlobalVariableSet(kd, dmin[g]);                 // цель запоминается один раз
      if(!GlobalVariableCheck(kx)) GlobalVariableSet(kx, (dir < 0) ? MathMax(oext[g], ask) : MathMin(oext[g], bid));
      double d = GlobalVariableGet(kd);
      double x = GlobalVariableGet(kx);
      double px = (dir < 0) ? ask : bid;
      if((dir < 0 && px > x) || (dir > 0 && px < x)) { x = px; GlobalVariableSet(kx, x); }

      double newTP = NormalizeDouble((dir < 0) ? x - d : x + d, dg);
      bool worse = (dir < 0) ? (newTP > tps[g] + pt / 2) : (newTP < tps[g] - pt / 2);
      if(!worse) continue;
      bool tpValid = (dir < 0) ? (newTP < ask - stops) : (newTP > bid + stops);

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
         int pdir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
         if(PositionGetString(POSITION_SYMBOL) != sym || pdir != dir) continue;
         double psl = PositionGetDouble(POSITION_SL);
         if(LtpKey(sym, pdir, psl) != keys[g]) continue;
         if(tpValid)
         {
            if(trade.PositionModify(tk, psl, newTP))
               Log("-", sym, StringFormat("TP от экстремума %.*f: #%I64u TP -> %.*f", dg, x, tk, dg, newTP));
         }
         else if(trade.PositionClose(tk))
            Log("-", sym, StringFormat("#%I64u закрыт: цена у цели от экстремума %.*f", tk, dg, x));
      }
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong ot = OrderGetTicket(i);
         if(ot == 0 || OrderGetInteger(ORDER_MAGIC) != Magic || OrderGetString(ORDER_SYMBOL) != sym) continue;
         ENUM_ORDER_TYPE t = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         int odir = (t == ORDER_TYPE_BUY_LIMIT || t == ORDER_TYPE_BUY_STOP) ? 1 :
                    (t == ORDER_TYPE_SELL_LIMIT || t == ORDER_TYPE_SELL_STOP) ? -1 : 0;
         if(odir != dir) continue;
         double osl = OrderGetDouble(ORDER_SL), oprice = OrderGetDouble(ORDER_PRICE_OPEN);
         if(LtpKey(sym, odir, osl) != keys[g]) continue;
         if((dir < 0 && newTP < oprice - stops) || (dir > 0 && newTP > oprice + stops))
            trade.OrderModify(ot, oprice, osl, newTP, (ENUM_ORDER_TYPE_TIME)OrderGetInteger(ORDER_TYPE_TIME),
                              (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION));
      }
   }

   // уборка памяти о закрытых сделках
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      if(StringFind(name, LTP) != 0) continue;
      string base = StringSubstr(name, 0, StringLen(name) - 2);
      bool alive = false;
      for(int j = 0; j < n; j++) if(keys[j] == base) { alive = true; break; }
      if(!alive) GlobalVariableDel(name);
   }
}

//--- торговые часы (как StartTime/EndTime в HorizonX)
bool InSession()
{
   if(SessionStartHour < 0 || SessionEndHour < 0 || SessionStartHour == SessionEndHour) return true;
   MqlDateTime d; TimeToStruct(NowUTC() + SessionGMTOffset * 3600, d);
   if(SessionStartHour < SessionEndHour) return d.hour >= SessionStartHour && d.hour < SessionEndHour;
   return d.hour >= SessionStartHour || d.hour < SessionEndHour;      // окно через полночь
}

bool FridayClose()
{
   if(CloseFridayHour < 0) return false;
   MqlDateTime d; TimeToStruct(TimeTradeServer(), d);
   return d.day_of_week == 5 && d.hour >= CloseFridayHour;
}

//--- дневной результат советника: закрытые сделки сегодня + плавающий
bool DailyLimitHit()
{
   if(MaxDailyLossPct <= 0) return false;
   MqlDateTime d; TimeToStruct(TimeTradeServer(), d);
   d.hour = 0; d.min = 0; d.sec = 0;
   datetime dayStart = StructToTime(d);
   double closed = 0;
   if(HistorySelect(dayStart, TimeTradeServer() + 60))
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
      {
         ulong dl = HistoryDealGetTicket(i);
         if(dl == 0 || HistoryDealGetInteger(dl, DEAL_MAGIC) != Magic) continue;
         closed += HistoryDealGetDouble(dl, DEAL_PROFIT) + HistoryDealGetDouble(dl, DEAL_SWAP) + HistoryDealGetDouble(dl, DEAL_COMMISSION);
      }
   double floating = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == Magic)
         floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   double startBal = AccountInfoDouble(ACCOUNT_BALANCE) - closed;
   if(startBal <= 0) return false;
   double pct = -(closed + floating) / startBal * 100.0;
   bool hit = pct >= MaxDailyLossPct;
   if(hit && lastLimitDay != d.day_of_year) { lastLimitDay = d.day_of_year; Log("-", "-", StringFormat("ДНЕВНОЙ ЛИМИТ: убыток %.2f%% — новые сделки до завтра не открываются", pct)); }
   return hit;
}

//--- журнал закрытых сделок для разбора агентом
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &req, const MqlTradeResult &res)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != Magic) return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT) return;
   long posId = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   string sym = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT), swap = HistoryDealGetDouble(trans.deal, DEAL_SWAP), comm = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
   double vol = HistoryDealGetDouble(trans.deal, DEAL_VOLUME), closePx = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   long reason = HistoryDealGetInteger(trans.deal, DEAL_REASON);
   string why = reason == DEAL_REASON_SL ? "SL" : reason == DEAL_REASON_TP ? "TP" : reason == DEAL_REASON_SO ? "stopout" : (reason == DEAL_REASON_EXPERT ? "советник" : "вручную");
   string sigId = "", side = ""; double openPx = 0; datetime openT = 0;
   if(HistorySelectByPosition(posId))
      for(int i = 0; i < HistoryDealsTotal(); i++)
      {
         ulong dl = HistoryDealGetTicket(i);
         if(HistoryDealGetInteger(dl, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            sigId = HistoryDealGetString(dl, DEAL_COMMENT);
            StringReplace(sigId, "AIO ", "");
            side = HistoryDealGetInteger(dl, DEAL_TYPE) == DEAL_TYPE_BUY ? "buy" : "sell";
            openPx = HistoryDealGetDouble(dl, DEAL_PRICE);
            openT = (datetime)HistoryDealGetInteger(dl, DEAL_TIME);
            break;
         }
      }
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   int h = FileOpen(TRADES_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_UNICODE);
   if(h == INVALID_HANDLE) return;
   if(FileSize(h) == 0) FileWriteString(h, "close_time;open_time;signal_id;symbol;side;volume;open;close;profit;swap;commission;reason;balance\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, StringFormat("%s;%s;%s;%s;%s;%.2f;%.*f;%.*f;%.2f;%.2f;%.2f;%s;%.2f\n",
      TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES), TimeToString(openT, TIME_DATE | TIME_MINUTES), sigId, sym, side, vol,
      dg, openPx, dg, closePx, profit, swap, comm, why, AccountInfoDouble(ACCOUNT_BALANCE)));
   FileClose(h);
   Log(sigId, sym, StringFormat("ЗАКРЫТО по %s: %+.2f", why, profit + swap + comm));
   if(CancelRestAfterClose) CancelRest(sigId, sym, (ulong)posId);
}

//--- id сделки = id ордера без части «-L1…-L9»: 261126-1311-XAUUSD-L3-A50 -> 261126-1311-XAUUSD-A50
string DealBase(string id)
{
   int n = StringLen(id);
   for(int i = 0; i + 2 < n; i++)
      if(StringGetCharacter(id, i) == '-' && StringGetCharacter(id, i + 1) == 'L')
      {
         ushort c = StringGetCharacter(id, i + 2);
         if(c >= '1' && c <= '9' && (i + 3 == n || StringGetCharacter(id, i + 3) == '-'))
            return StringSubstr(id, 0, i) + StringSubstr(id, i + 3);
      }
   return id;
}

//--- все позиции сделки закрыты -> снять её неисполненные лимитки (иначе они нальются позже без сделки)
void CancelRest(string sigId, string sym, ulong closedPos)
{
   if(sigId == "") return;
   string base = DealBase(sigId);
   if(base == sigId) return;                                   // не лесенка
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || t == closedPos || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      string c = PositionGetString(POSITION_COMMENT); StringReplace(c, "AIO ", "");
      if(DealBase(c) == base) return;                          // сделка ещё жива
   }
   int removed = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong o = OrderGetTicket(i);
      if(o == 0 || OrderGetInteger(ORDER_MAGIC) != Magic) continue;
      string c = OrderGetString(ORDER_COMMENT); StringReplace(c, "AIO ", "");
      if(DealBase(c) == base && trade.OrderDelete(o)) removed++;
   }
   if(removed > 0) Log(base, sym, StringFormat("сделка закрыта — снято неисполненных лимиток: %d", removed));
}

//--- смещение времени сервера от UTC (кратно 15 мин; часы ПК сокращаются)
long ServerOffset()
{
   long off = (long)(TimeTradeServer() - TimeGMT());
   return (long)MathRound(off / 900.0) * 900;
}

//--- «сейчас» по UTC по котировкам сервера, а не по часам компьютера
datetime NowUTC()
{
   datetime srv = TimeCurrent();
   if(srv <= 0) return TimeGMT();
   datetime u = (datetime)((long)srv - ServerOffset());
   return u;
}

void ClockCheck()
{
   datetime srv = TimeCurrent();
   if(srv <= 0) return;
   long drift = (long)(TimeTradeServer() - srv);              // > 0: часы ПК спешат
   if(MathAbs(drift) >= 120 && MathAbs(drift) <= 7200)      // больше 2 ч — скорее нет свежих котировок
      Log("-", "-", StringFormat("часы компьютера расходятся с сервером на %+d мин — советник считает время по серверу; лучше синхронизировать время Windows", (int)(drift / 60)));
}

//--- список подходящих символов брокера (для настройки имён)
void DumpSymbols()
{
   int h = FileOpen(SYMBOLS_FILE, FILE_WRITE | FILE_TXT | FILE_UNICODE);
   if(h == INVALID_HANDLE) return;
   string keys[] = {"XAU", "GOLD", "EURUSD", "GBPUSD", "USDJPY", "500", "SPX", "NAS", "NDX", "100", "US30", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOG", "AMD"};
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
   {
      string s = SymbolName(i, false);
      string up = s; StringToUpper(up);
      for(int k = 0; k < ArraySize(keys); k++)
         if(StringFind(up, keys[k]) >= 0)
         {
            FileWriteString(h, s + ";" + SymbolInfoString(s, SYMBOL_DESCRIPTION) + ";" + SymbolInfoString(s, SYMBOL_PATH) + "\n");
            break;
         }
   }
   FileClose(h);
}
