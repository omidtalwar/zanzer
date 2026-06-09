//+------------------------------------------------------------------+
//|                                              ZanzerGuardian.mq5   |
//|                    Zanzer - AI Trading Guardian (V2 enforcement)  |
//|                                                                  |
//|  Enforces the lock state owned by the Trading Guardian API.      |
//|                                                                  |
//|  PLATFORM LIMITATION (important):                                 |
//|  MetaTrader 5 has no pre-trade hook for MANUAL orders, so an EA   |
//|  cannot reject a manual trade before it executes. This EA instead |
//|  detects a position opened WHILE TRADING IS LOCKED and closes it  |
//|  immediately (within PollSeconds, or instantly on the deal event).|
//|  Positions that existed BEFORE the lock are left alone -- "lock"  |
//|  means "no NEW trades", not "close my open trades".               |
//|                                                                  |
//|  It reads the lock via WebRequest GET {ApiUrl}/lock. You MUST     |
//|  whitelist the URL: Tools > Options > Expert Advisors >           |
//|  "Allow WebRequest for listed URL" -> add http://127.0.0.1:8000   |
//+------------------------------------------------------------------+
#property copyright "Zanzer"
#property version   "1.00"
#property strict
#property description "Closes any position opened while the Trading Guardian API reports trading is LOCKED. Whitelist the API URL under Tools>Options>Expert Advisors."

#include <Trade/Trade.mqh>

input string ApiUrl       = "http://127.0.0.1:8000"; // Trading Guardian API base URL
input int    PollSeconds  = 5;                        // How often to poll lock state (s)
input bool   EnforceClose = true;                     // Close positions opened while locked
input int    WebTimeoutMs = 5000;                     // WebRequest timeout (ms)
input bool   AllSymbols   = true;                     // Enforce across all symbols (not just chart)

CTrade   g_trade;
bool     g_locked   = false;     // last known lock state
string   g_reason   = "";        // last known lock reason
ulong    g_baseline[];           // tickets that existed when the lock engaged
bool     g_webOk    = false;     // was the last poll successful?

//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetTypeFillingBySymbol(_Symbol);
   EventSetTimer((PollSeconds < 1) ? 1 : PollSeconds);
   PollLock();
   UpdateComment();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   PollLock();
   if(g_locked && EnforceClose)
      CloseNewPositions();
   UpdateComment();
}

//+------------------------------------------------------------------+
//| React instantly to a newly opened position while locked          |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
   if(g_locked && EnforceClose && trans.type == TRADE_TRANSACTION_DEAL_ADD)
      CloseNewPositions();
}

//+------------------------------------------------------------------+
//| Poll the API for lock state                                      |
//+------------------------------------------------------------------+
void PollLock()
{
   string url = ApiUrl + "/lock";
   char   data[];           // empty body for GET
   char   resultArr[];
   string resultHeaders;
   string headers = "";

   ResetLastError();
   int status = WebRequest("GET", url, headers, WebTimeoutMs, data, resultArr, resultHeaders);

   if(status == -1)
   {
      int err = GetLastError();
      g_webOk = false;
      PrintFormat("Zanzer: WebRequest to %s failed (err=%d). "
                  "Whitelist it under Tools>Options>Expert Advisors.", url, err);
      return;
   }
   g_webOk = true;

   if(status != 200)
   {
      PrintFormat("Zanzer: API returned HTTP %d for %s", status, url);
      return;
   }

   string body = CharArrayToString(resultArr, 0, WHOLE_ARRAY, CP_UTF8);
   bool nowLocked = (StringFind(body, "\"locked\":true")  >= 0) ||
                    (StringFind(body, "\"locked\": true") >= 0);
   g_reason = ExtractJsonString(body, "reason");

   if(nowLocked && !g_locked)
   {
      CaptureBaseline();   // remember current positions; only NEW ones get closed
      PrintFormat("Zanzer: LOCK engaged (%s). New trades will be closed. "
                  "%d existing position(s) grandfathered.",
                  g_reason, ArraySize(g_baseline));
   }
   else if(!nowLocked && g_locked)
   {
      ArrayResize(g_baseline, 0);
      Print("Zanzer: lock cleared. Trading allowed.");
   }
   g_locked = nowLocked;
}

//+------------------------------------------------------------------+
//| Snapshot tickets that exist right now (the "grandfathered" set)  |
//+------------------------------------------------------------------+
void CaptureBaseline()
{
   ArrayResize(g_baseline, 0);
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         int n = ArraySize(g_baseline);
         ArrayResize(g_baseline, n + 1);
         g_baseline[n] = ticket;
      }
   }
}

//+------------------------------------------------------------------+
bool InBaseline(ulong ticket)
{
   for(int i = 0; i < ArraySize(g_baseline); i++)
      if(g_baseline[i] == ticket)
         return true;
   return false;
}

//+------------------------------------------------------------------+
//| Close any position opened after the lock engaged                 |
//+------------------------------------------------------------------+
void CloseNewPositions()
{
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(InBaseline(ticket))
         continue;  // existed before the lock -> leave it

      string sym = PositionGetString(POSITION_SYMBOL);
      if(!AllSymbols && sym != _Symbol)
         continue;

      if(g_trade.PositionClose(ticket))
         PrintFormat("Zanzer: BLOCKED new trade while locked -> closed position %I64u (%s).",
                     ticket, sym);
      else
         PrintFormat("Zanzer: failed to close %I64u (retcode=%d).",
                     ticket, g_trade.ResultRetcode());
   }
}

//+------------------------------------------------------------------+
//| Minimal JSON string-value extractor: "key":"value"               |
//+------------------------------------------------------------------+
string ExtractJsonString(const string body, const string key)
{
   string pat = "\"" + key + "\":";
   int p = StringFind(body, pat);
   if(p < 0)
      return "";
   p += StringLen(pat);
   // skip spaces
   while(p < StringLen(body) && StringGetCharacter(body, p) == ' ')
      p++;
   if(p >= StringLen(body) || StringGetCharacter(body, p) != '"')
      return "";  // null or non-string
   p++;
   int end = StringFind(body, "\"", p);
   if(end < 0)
      return "";
   return StringSubstr(body, p, end - p);
}

//+------------------------------------------------------------------+
void UpdateComment()
{
   string s = "Zanzer Guardian\n";
   if(!g_webOk)
      s += "API: UNREACHABLE (check it's running + URL whitelisted)\n";
   else
      s += "API: connected\n";

   if(g_locked)
   {
      s += "STATUS: LOCKED - new trades will be closed\n";
      if(g_reason != "")
         s += "Reason: " + g_reason + "\n";
      s += StringFormat("Grandfathered: %d existing position(s)\n", ArraySize(g_baseline));
   }
   else
   {
      s += "STATUS: unlocked - trading allowed\n";
   }
   Comment(s);
}
//+------------------------------------------------------------------+
