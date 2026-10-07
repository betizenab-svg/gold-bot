//+------------------------------------------------------------------+
//| SignalCopier.mq5  -  OPTIONAL, UNTESTED.                         |
//| Copies the bot's open signals into an MT5 account, so a demo     |
//| account linked to Myfxbook can prove the results independently.  |
//| Use it on a DEMO account first. It has not been run by the       |
//| developer (no MT5 on Linux); check every order it places.        |
//+------------------------------------------------------------------+
#property copyright "Gold Signals"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

input string DataUrl        = "https://raw.githubusercontent.com/betizenab-svg/gold-bot/main/site/data/public.json";
input double RiskPercent    = 1.0;      // risk per signal, % of balance
input string SymbolSuffix   = "";       // e.g. "m" if your broker names gold "XAUUSDm"
input int    PollSeconds    = 60;
input long   MagicNumber    = 260142;

CTrade trade;

//--- tiny JSON helpers (the file is written by our own bot, one known shape)
string JsonString(const string obj, const string key)
{
   string pattern = "\"" + key + "\": ";
   int start = StringFind(obj, pattern);
   if(start < 0) return "";
   start += StringLen(pattern);
   if(StringGetCharacter(obj, start) == '"')
   {
      int end = StringFind(obj, "\"", start + 1);
      return StringSubstr(obj, start + 1, end - start - 1);
   }
   int end = start;
   while(end < StringLen(obj))
   {
      ushort c = StringGetCharacter(obj, end);
      if(c == ',' || c == '}' || c == '\n' || c == '\r') break;
      end++;
   }
   string value = StringSubstr(obj, start, end - start);
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

bool OpenSignals(string &objects[])
{
   char post[], result[];
   string headers;
   ResetLastError();
   int status = WebRequest("GET", DataUrl, "", 10000, post, result, headers);
   if(status != 200)
   {
      PrintFormat("SignalCopier: could not read the signals (HTTP %d, error %d). Add the address to Tools > Options > Expert Advisors > Allow WebRequest.", status, GetLastError());
      return false;
   }
   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   int start = StringFind(body, "\"open\": [");
   if(start < 0) return true;
   int end = StringFind(body, "]", start);
   string list = StringSubstr(body, start, end - start);
   int count = 0;
   int pos = StringFind(list, "{");
   while(pos >= 0)
   {
      int close = StringFind(list, "}", pos);
      ArrayResize(objects, count + 1);
      objects[count++] = StringSubstr(list, pos, close - pos + 1);
      pos = StringFind(list, "{", close);
   }
   return true;
}

double LotFor(const string symbol, double entry, double stop)
{
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double step      = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double minLot    = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   if(tickValue <= 0 || tickSize <= 0 || step <= 0) return 0;
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0;
   double lossPerLot = MathAbs(entry - stop) / tickSize * tickValue;
   if(lossPerLot <= 0) return 0;
   double lots = MathFloor(riskMoney / lossPerLot / step) * step;
   return MathMax(lots, minLot);
}

bool AlreadyPlaced(const string code)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
      if(OrderGetTicket(i) > 0 && StringFind(OrderGetString(ORDER_COMMENT), code) == 0) return true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && StringFind(PositionGetString(POSITION_COMMENT), code) == 0) return true;
   return false;
}

void Place(const string obj)
{
   string code   = JsonString(obj, "code");
   string status = JsonString(obj, "status");
   if(code == "" || status != "PENDING" || AlreadyPlaced(code)) return;
   string symbol = JsonString(obj, "symbol") + SymbolSuffix;
   if(!SymbolSelect(symbol, true)) { PrintFormat("SignalCopier: %s not offered by this broker", symbol); return; }
   bool buy      = JsonString(obj, "direction") == "LONG";
   string order  = JsonString(obj, "order");
   double entry  = StringToDouble(JsonString(obj, "entry"));
   double stop   = StringToDouble(JsonString(obj, "stop"));
   double tp1    = StringToDouble(JsonString(obj, "tp1"));
   double tp2    = StringToDouble(JsonString(obj, "tp2"));
   double lots   = LotFor(symbol, entry, stop);
   double step   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double half   = MathMax(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), MathFloor(lots / 2.0 / step) * step);
   if(lots <= 0) return;
   trade.SetExpertMagicNumber(MagicNumber);
   datetime expiry = TimeCurrent() + 90 * 60;
   for(int part = 0; part < 2; part++)
   {
      double target = part == 0 ? tp1 : tp2;
      string comment = code + (part == 0 ? " T1" : " T2");
      bool ok;
      if(order == "STOP")
         ok = buy ? trade.BuyStop(half, entry, symbol, stop, target, ORDER_TIME_SPECIFIED, expiry, comment)
                  : trade.SellStop(half, entry, symbol, stop, target, ORDER_TIME_SPECIFIED, expiry, comment);
      else
         ok = buy ? trade.BuyLimit(half, entry, symbol, stop, target, ORDER_TIME_SPECIFIED, expiry, comment)
                  : trade.SellLimit(half, entry, symbol, stop, target, ORDER_TIME_SPECIFIED, expiry, comment);
      PrintFormat("SignalCopier: %s %s %.2f lots -> %s", comment, buy ? "buy" : "sell", half, ok ? "placed" : trade.ResultComment());
   }
}

// After target 1 the bot moves the stop to entry: do the same for the second half.
void ProtectRunners(string &objects[])
{
   for(int i = 0; i < ArraySize(objects); i++)
   {
      if(JsonString(objects[i], "status") != "PARTIAL_TP1") continue;
      string code  = JsonString(objects[i], "code");
      double entry = StringToDouble(JsonString(objects[i], "entry"));
      for(int p = PositionsTotal() - 1; p >= 0; p--)
      {
         ulong ticket = PositionGetTicket(p);
         if(ticket == 0 || PositionGetString(POSITION_COMMENT) != code + " T2") continue;
         if(MathAbs(PositionGetDouble(POSITION_SL) - entry) > SymbolInfoDouble(PositionGetString(POSITION_SYMBOL), SYMBOL_POINT))
            trade.PositionModify(ticket, entry, PositionGetDouble(POSITION_TP));
      }
   }
}

int OnInit()
{
   EventSetTimer(MathMax(15, PollSeconds));
   Print("SignalCopier started (UNTESTED - use a demo account).");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
{
   string objects[];
   if(!OpenSignals(objects)) return;
   for(int i = 0; i < ArraySize(objects); i++) Place(objects[i]);
   ProtectRunners(objects);
}
