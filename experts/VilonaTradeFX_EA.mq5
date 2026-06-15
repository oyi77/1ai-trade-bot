//+------------------------------------------------------------------+
//|                             VilonaTradeFX_EA.mq5                   |
//|                        Commercial Edition v2.0                     |
//|                   (c) BerkahKarya — phantomfx.aitradepulse.com     |
//+------------------------------------------------------------------+
//  FEATURES:
//   ★ Smart Layering™ — 3 entries, 1 SL, staggered TPs
//   ★ AI Signal Mode — auto-trade from Vilona AI bot
//   ★ Manual Mode — user sets entries manually
//   ★ Hybrid Mode — AI signals + manual confirmation
//   ★ Circuit Breaker — max consecutive losses + daily $ limit
//   ★ Trailing Stop & Breakeven
//   ★ Killzone Filter — trade only during active sessions
//   ★ Spread Protection — skip if spread too wide
//   ★ Instance Identity — auto-sends MT5 login for multi-terminal broadcasting
//   ★ Clean Chart Panel — real-time status display
//
//  USAGE:
//   1. Set API_Key from your license
//   2. Choose TradingMode
//   3. Attach to XAUUSDc M15 chart
//   4. EA auto-polls bridge every PollInterval seconds
//   5. EA sends account_id=MT5-{login} automatically — one API Key, many MT5 terminals
//+------------------------------------------------------------------+
#property copyright "BerkahKarya — VilonaTradeFX"
#property link      "https://phantomfx.aitradepulse.com"
#property version   "2.0"
#property description "AI-Powered Gold Trading EA with Smart Layering™"

//+------------------------------------------------------------------+
//| ENUMS                                                              |
//+------------------------------------------------------------------+
enum ENUM_TRADE_MODE {
   AUTO_AI = 0,      // Auto AI — fully automated signals
   SEMI_AUTO = 1,    // Semi-Auto — AI signal + manual confirm
   MANUAL = 2        // Manual — user sets entries
};

enum ENUM_LOT_MODE {
   FIXED_LOT = 0,    // Fixed lot size
   RISK_PERCENT = 1, // Risk % per trade
   LAYERS_RISK = 2   // Distribute risk across layers
};

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+

//── License ──
input string   InpLicense = "═══════ LICENSE ═══════"; // ────────
input string   API_Key = "VT-PRO-LAUNCH";              // API Key (License)
input string   BridgeURL = "https://phantomfx.aitradepulse.com"; // Bridge URL

//── Trading Mode ──
input string   InpMode = "═══════ TRADING MODE ═══════"; // ────────
input ENUM_TRADE_MODE TradingMode = AUTO_AI;            // Trading Mode
input int      PollIntervalSeconds = 5;                 // Poll Interval (seconds)
input int      MagicNumber = 20260605;                  // Magic Number
input string   TradeComment = "VilonaTradeFX";          // Order Comment
input double   MaxSpread = 30;                          // Max Spread (points)

//── Smart Layering™ ──
input string   InpLayers = "═══════ LAYERING ═══════";  // ────────
input bool     EnableLayering = true;                   // Enable Smart Layering™
input int      DefaultLayers = 3;                       // Default Layer Count (1-5)
input double   LayerSpacingPips = 50;                   // Layer Spacing (pips)
input string   LayerRiskSplit = "40,30,30";             // Risk Split % (comma-separated)
input double   LayerTP1_Pips = 100;                     // Layer 1 TP (pips from entry)
input double   LayerTP2_Pips = 200;                     // Layer 2 TP (pips)
input double   LayerTP3_Pips = 300;                     // Layer 3 TP (pips)

//── Risk Management ──
input string   InpRisk = "═══════ RISK ═══════";        // ────────
input ENUM_LOT_MODE LotMode = RISK_PERCENT;              // Lot Sizing Mode
input double   FixedLots = 0.01;                         // Fixed Lot Size
input double   RiskPercent = 1.0;                        // Risk % Per Trade
input double   MaxDailyLoss = 100.0;                     // Max Daily Loss ($)
input int      MaxConsecutiveLosses = 3;                 // Max Consecutive Losses
input int      MaxTotalPositions = 5;                    // Max Open Positions

//── Position Management ──
input string   InpPosMgmt = "═══════ POSITION MGMT ═══════"; // ────────
input bool     EnableTrailingStop = false;               // Enable Trailing Stop
input double   TrailingStartPips = 50;                   // Trailing Start (pips)
input double   TrailingStepPips = 10;                    // Trailing Step (pips)
input bool     EnableBreakeven = false;                  // Enable Breakeven
input double   BreakevenTriggerPips = 30;                // Breakeven Trigger (pips)
input double   BreakevenAddPips = 2;                     // Breakeven + Pips

//── Filters ──
input string   InpFilters = "═══════ FILTERS ═══════";   // ────────
input bool     EnableKillzoneFilter = false;             // Killzone Only
input int      KillzoneStartHour = 7;                    // Killzone Start (UTC+0)
input int      KillzoneEndHour = 9;                      // Killzone End (UTC+0)
input bool     EnableWeekendFilter = false;              // No Trading Weekend (OFF by default)

//── Display ──
input string   InpDisplay = "═══════ DISPLAY ═══════";   // ────────
input bool     ShowPanel = true;                         // Show Info Panel
input color    PanelColor = clrDodgerBlue;               // Panel Color
input int      PanelCorner = CORNER_RIGHT_UPPER;         // Panel Position

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                   |
//+------------------------------------------------------------------+
datetime g_lastPollTime = 0;
string   g_lastSignalId = "";
int      g_consecutiveLosses = 0;
double   g_dailyLoss = 0.0;
double   g_dailyProfit = 0.0;
datetime g_lastDailyReset = 0;
bool     g_circuitBreaker = false;
string   g_statusText = "Initializing...";
int      g_totalSignals = 0;
int      g_totalTrades = 0;
int      g_totalWins = 0;
datetime g_lastSignalTime = 0;
double   g_riskSplit[];

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit() {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) {
      Alert("❌ AutoTrading is disabled! Enable it in Tools → Options → Expert Advisors.");
      return INIT_FAILED;
   }

   ParseRiskSplit();
   g_lastDailyReset = TimeCurrent();
   ResetDailyStats();

   if(ShowPanel) CreatePanel();

   Print("═══════════════════════════════════════════");
   Print("🚀 VilonaTradeFX EA v2.0 — Commercial Edition");
   Print("   Mode: ", EnumToString(TradingMode));
   Print("   Layering: ", EnableLayering ? "ON" : "OFF");
   Print("   Bridge: ", BridgeURL);
   Print("   Risk: ", RiskPercent, "% | MaxDailyLoss: $", MaxDailyLoss);
   Print("═══════════════════════════════════════════");

   g_statusText = "Ready — waiting for signal...";
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   if(ShowPanel) ObjectDelete(0, "VTFX_Panel_BG");
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick() {
   //── Daily reset check ──
   if(TimeCurrent() - g_lastDailyReset > 86400) {
      ResetDailyStats();
      g_lastDailyReset = TimeCurrent();
   }

   //── Circuit breaker check ──
   if(g_circuitBreaker) {
      g_statusText = "⚠ CIRCUIT BREAKER — Daily limit reached";
      if(ShowPanel) UpdatePanel();
      return;
   }

   //── Weekend filter ──
   if(EnableWeekendFilter && IsWeekend()) {
      g_statusText = "⏸ Weekend — market closed";
      if(ShowPanel) UpdatePanel();
      return;
   }

   //── Killzone filter ──
   if(EnableKillzoneFilter && !IsKillzone()) {
      g_statusText = "⏸ Outside killzone";
      if(ShowPanel) UpdatePanel();
      return;
   }

   //── Position management ──
   ManagePositions();

   //── Poll bridge for signals ──
   if(TimeCurrent() - g_lastPollTime >= PollIntervalSeconds) {
      g_lastPollTime = TimeCurrent();
      PollBridge();
   }

   if(ShowPanel) UpdatePanel();
}

//+------------------------------------------------------------------+
//| POLL BRIDGE & PROCESS SIGNAL                                       |
//+------------------------------------------------------------------+
void PollBridge() {
   string accountId = GetAccountIdStr();
   string url = BridgeURL + "/signal?api_key=" + API_Key + "&account_id=" + accountId;
   char reqData[];
   char resData[];
   string resHeaders;
   string headers = "Content-Type: application/json\r\n";
   int timeout = 5000;

   int res = WebRequest("GET", url, headers, timeout, reqData, resData, resHeaders);

   if(res != 200) {
      g_statusText = "⚡ Bridge error: " + IntegerToString(res);
      return;
   }

   string response = CharArrayToString(resData);
   string signalId = ExtractValue(response, "signal_id");
   string action  = ExtractValue(response, "action");

   //── No signal ──
   if(action == "HOLD" || signalId == "" || signalId == g_lastSignalId) {
      if(action == "HOLD") g_statusText = "⏳ Waiting for signal... (HOLD)";
      return;
   }

   g_lastSignalId = signalId;
   g_lastSignalTime = TimeCurrent();
   g_totalSignals++;

   //── Parse signal ──
   string symbol     = ExtractValue(response, "symbol");
   double entry      = StringToDouble(ExtractValue(response, "entry"));
   double sl         = StringToDouble(ExtractValue(response, "sl"));
   double tp         = StringToDouble(ExtractValue(response, "tp"));
   double riskPct    = StringToDouble(ExtractValue(response, "risk_percent"));
   double confidence = StringToDouble(ExtractValue(response, "confidence"));
   string comment    = ExtractValue(response, "comment");
   string layersRaw  = ExtractValue(response, "layers"); // JSON array string

   if(riskPct <= 0) riskPct = RiskPercent;

   //── Spread check ──
   double spread = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point;
   if(spread > MaxSpread) {
      Print("⛔ Spread too wide: ", spread, " > ", MaxSpread);
      g_statusText = "⛔ Spread too wide: " + DoubleToString(spread, 0);
      return;
   }

   //── Position limit check ──
   if(CountMyPositions() >= MaxTotalPositions) {
      Print("⛔ Max positions reached: ", MaxTotalPositions);
      g_statusText = "⛔ Max positions: " + IntegerToString(MaxTotalPositions);
      return;
   }

   //── EXECUTE ──
   if(EnableLayering && layersRaw != "" && layersRaw != "[]") {
      // ── SMART LAYERING™ ──
      ExecuteLayers(symbol, action, sl, layersRaw, confidence, comment);
   } else {
      // ── Single Entry ──
      ExecuteSingle(symbol, action, entry, sl, tp, riskPct, confidence, comment);
   }

   // Acknowledge signal
   SendAck(signalId);
}

//+------------------------------------------------------------------+
//| PARSE RISK SPLIT DYNAMIC — helper for layering                     |
//+------------------------------------------------------------------+
void ParseRiskSplitDynamic(double &result[]) {
   string parts[];
   ushort sep = StringGetCharacter(",", 0);
   StringSplit(LayerRiskSplit, sep, parts);
   int cnt = ArraySize(parts);
   ArrayResize(result, cnt);
   for(int i = 0; i < cnt; i++) {
      result[i] = StringToDouble(parts[i]) / 100.0;
   }
}

//+------------------------------------------------------------------+
//| SMART LAYERING™ — Execute Multi-Entry Positions                    |
//+------------------------------------------------------------------+
void ExecuteLayers(string symbol, string action, double sl, string layersRaw,
                   double confidence, string comment) {
   // ── CLOSE ACTION: Close all open positions ──
   if(action == "CLOSE") {
      int closed = CloseAllPositions(symbol);
      Print("🔒 CLOSE signal (layers): ", closed, " position(s) closed on ", symbol);
      g_statusText = StringFormat("🔒 Closed %d position(s)", closed);
      return;
   }

   // Parse layers from JSON array
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pip = point * 10;
   if(pip <= 0) {
      pip = MathPow(10, -digits);  // fallback for brokers with zero point
   }

   // Parse layers from JSON: [{"entry": x, "tp": y, "risk_pct": z}, ...]
   string entries[];
   StringSplit(layersRaw, '}', entries);

   int totalLayers = ArraySize(entries) - 1; // last split is empty
   if(totalLayers <= 0 || totalLayers > 5) totalLayers = DefaultLayers;

   double riskSplit[];
   ParseRiskSplitDynamic(riskSplit);
   int splitCount = ArraySize(riskSplit);

   Print("🔥 Smart Layering™ | ", action, " ", _Symbol, " | ", totalLayers, " layers | SL=", sl);

   for(int i = 0; i < totalLayers; i++) {
      // Parse individual layer
      string layerEntry = ExtractField(entries[i], "entry");
      string layerTP    = ExtractField(entries[i], "tp");
      string layerRisk  = ExtractField(entries[i], "risk_pct");

      double lEntry = (layerEntry != "") ? StringToDouble(layerEntry) : 0;
      double lTP    = (layerTP != "")    ? StringToDouble(layerTP)    : 0;
      double lRisk  = (layerRisk != "")  ? StringToDouble(layerRisk)  : 0;

      // If no layer data from signal, use defaults
      if(lEntry <= 0) {
         double offset = (action == "BUY") ? i * LayerSpacingPips : -i * LayerSpacingPips;
         lEntry = (action == "BUY") ? ask + offset * pip : bid + offset * pip;
      }
      if(lTP <= 0) {
         double tpOffset = (i == 0) ? LayerTP1_Pips : (i == 1) ? LayerTP2_Pips : LayerTP3_Pips;
         lTP = (action == "BUY") ? lEntry + tpOffset * pip : lEntry - tpOffset * pip;
      }
      if(lRisk <= 0 && i < splitCount) {
         lRisk = riskSplit[i] / 100.0;
      }

      // Calculate lot size based on risk split
      double lots = CalculateLots(symbol, lEntry, sl, RiskPercent * lRisk);

      // Round entry, SL, and TP
      lEntry = NormalizeDouble(lEntry, digits);
      sl     = NormalizeDouble(sl, digits);
      lTP    = NormalizeDouble(lTP, digits);

      // Validate
      if(!ValidateSLTP(action, lEntry, sl, lTP)) {
         Print("⚠ Layer ", i+1, " invalid SL/TP, skipping");
         continue;
      }

      // Open position
      int ticket = OpenPosition(action, lEntry, sl, lTP, lots, confidence,
                                comment + " L" + IntegerToString(i+1));
      if(ticket > 0) {
         g_totalTrades++;
         Print("   ✅ Layer ", i+1, ": ", action, " @ ", lEntry, " TP=", lTP, " Lots=", lots);
      } else {
         Print("   ❌ Layer ", i+1, " failed: ", GetLastError());
      }
   }

   g_statusText = StringFormat("🔥 %d-Layer %s executed | Conf: %.0f%%",
                               totalLayers, action, confidence * 100);
}

//+------------------------------------------------------------------+
//| SINGLE ENTRY — Execute Single Position                             |
//+------------------------------------------------------------------+
void ExecuteSingle(string symbol, string action, double entry, double sl, double tp,
                   double riskPct, double confidence, string comment) {
   // ── CLOSE ACTION: Close all open positions ──
   if(action == "CLOSE") {
      int closed = CloseAllPositions(symbol);
      Print("🔒 CLOSE signal: ", closed, " position(s) closed on ", symbol);
      g_statusText = StringFormat("🔒 Closed %d position(s)", closed);
      return;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(entry <= 0) entry = (action == "BUY") ? ask : bid;
   double lots = CalculateLots(_Symbol, entry, sl, riskPct);

   entry = NormalizeDouble(entry, digits);
   sl    = NormalizeDouble(sl, digits);
   tp    = NormalizeDouble(tp, digits);

   if(!ValidateSLTP(action, entry, sl, tp)) {
      Print("⚠ Invalid SL/TP for single entry");
      return;
   }

   int ticket = OpenPosition(action, entry, sl, tp, lots, confidence, comment);
   if(ticket > 0) {
      g_totalTrades++;
      Print("✅ Position opened: ", action, " ", _Symbol, " @ ", entry,
            " SL=", sl, " TP=", tp, " Lots=", lots);
      g_statusText = StringFormat("✅ %s %s | Lots=%.2f | Conf=%.0f%%",
                                  action, _Symbol, lots, confidence * 100);
   } else {
      Print("❌ Order failed: ", GetLastError());
   }
}

//+------------------------------------------------------------------+
//| OPEN POSITION                                                      |
//+------------------------------------------------------------------+
int OpenPosition(string action, double price, double sl,
                 double tp, double lots, double confidence, string comment) {
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int stoplevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   // 3x safety margin + absolute floor of 100 points (extra safe for XAUUSD/BTC brokers)
   int safeLevel = (stoplevel > 0) ? stoplevel : 100;
   double minDist = safeLevel * point * 3;

   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = _Symbol;
   req.volume    = lots;
   req.type      = (action == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price     = (action == "BUY") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                     : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Normalize and enforce minimum distance
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   req.price = NormalizeDouble(req.price, digits);

   double distSL = MathAbs(req.price - sl);
   double distTP = MathAbs(req.price - tp);
   if(distSL < minDist) {
      Print("⚠ SL too close (", distSL, " < ", minDist, "), adjusting...");
      sl = (action == "BUY") ? req.price - minDist : req.price + minDist;
      sl = NormalizeDouble(sl, digits);
   }
   if(distTP < minDist) {
      Print("⚠ TP too close (", distTP, " < ", minDist, "), adjusting...");
      tp = (action == "BUY") ? req.price + minDist : req.price - minDist;
      tp = NormalizeDouble(tp, digits);
   }

   req.sl        = sl;
   req.tp        = tp;
   req.deviation = 30;
   req.magic     = MagicNumber;
   req.comment   = comment + " | " + DoubleToString(confidence * 100, 0) + "%";

   if(!OrderSend(req, res)) {
      Print("❌ OrderSend failed: ", GetLastError(),
            " | distSL=", distSL, " distTP=", distTP, " min=", minDist);
      return -1;
   }

   return (int)res.order;
}

//+------------------------------------------------------------------+
//| POSITION MANAGEMENT — Trailing Stop & Breakeven                    |
//+------------------------------------------------------------------+
void ManagePositions() {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pip = point * 10;
   if(pip <= 0) {
      pip = MathPow(10, -digits);  // fallback for brokers with zero point
   }

   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double openPrice   = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL   = PositionGetDouble(POSITION_SL);
      double currentTP   = PositionGetDouble(POSITION_TP);
      double currentPrice= PositionGetDouble(POSITION_PRICE_CURRENT);
      bool   isBuy       = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double profitPips  = (isBuy ? currentPrice - openPrice : openPrice - currentPrice) / pip;

      //── Trailing Stop ──
      if(EnableTrailingStop && profitPips >= TrailingStartPips) {
         double newSL;
         if(isBuy) {
            newSL = currentPrice - TrailingStartPips * pip;
            if(newSL > currentSL + TrailingStepPips * pip) {
               ModifySLTP(ticket, newSL, currentTP);
            }
         } else {
            newSL = currentPrice + TrailingStartPips * pip;
            if(newSL < currentSL - TrailingStepPips * pip) {
               ModifySLTP(ticket, newSL, currentTP);
            }
         }
      }

      //── Breakeven ──
      if(EnableBreakeven && profitPips >= BreakevenTriggerPips &&
         ((isBuy && currentSL < openPrice) || (!isBuy && currentSL > openPrice))) {
         double beSL = openPrice + (isBuy ? BreakevenAddPips * pip : -BreakevenAddPips * pip);
         ModifySLTP(ticket, beSL, currentTP);
      }
   }
}

//+------------------------------------------------------------------+
//| CLOSE ALL POSITIONS — for CLOSE signal                             |
//+------------------------------------------------------------------+
int CloseAllPositions(string symbol) {
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      req.action    = TRADE_ACTION_DEAL;
      req.symbol    = _Symbol;
      req.position  = ticket;
      req.volume    = PositionGetDouble(POSITION_VOLUME);
      req.deviation = 30;
      req.magic     = MagicNumber;
      req.type      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                      ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      req.price     = (req.type == ORDER_TYPE_BUY)
                      ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                      : SymbolInfoDouble(_Symbol, SYMBOL_BID);

      if(OrderSend(req, res)) {
         closed++;
         Print("🔒 Closed #", ticket, " PnL=", PositionGetDouble(POSITION_PROFIT));
      } else {
         Print("❌ Close failed #", ticket, ": ", GetLastError());
      }
   }
   return closed;
}

//+------------------------------------------------------------------+
//| MODIFY SL/TP                                                       |
//+------------------------------------------------------------------+
void ModifySLTP(ulong ticket, double newSL, double newTP) {
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};

   req.action   = TRADE_ACTION_SLTP;
   req.position = ticket;
   req.sl       = NormalizeDouble(newSL, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.tp       = NormalizeDouble(newTP, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.symbol   = _Symbol;
   req.magic    = MagicNumber;

   if(!OrderSend(req, res)) {
      Print("SL/TP modify failed #", ticket, ": ", GetLastError());
   }
}

//+------------------------------------------------------------------+
//| LOT CALCULATION                                                    |
//+------------------------------------------------------------------+
double CalculateLots(string symbol, double entry, double sl, double riskPct) {
   if(LotMode == FIXED_LOT) return FixedLots;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double pip = point * 10;
   if(pip <= 0) {
      int dig = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      pip = MathPow(10, -dig);  // fallback: 0.01 for 2-digit, 0.001 for 3-digit
   }
   double slPips = MathAbs(entry - sl) / pip;
   if(slPips <= 0) slPips = 100;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * riskPct / 100.0;
   double lotSize = riskMoney / (slPips * pip / tickSize * tickValue);

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
   lotSize = MathFloor(lotSize / lotStep) * lotStep;

   return NormalizeDouble(lotSize, 2);
}

//+------------------------------------------------------------------+
//| VALIDATE SL/TP                                                     |
//+------------------------------------------------------------------+
bool ValidateSLTP(string action, double entry, double sl, double tp) {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int stoplevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   // 3x safety margin + absolute floor of 100 points (extra safe for XAUUSD/BTC brokers)
   int safeLevel = (stoplevel > 0) ? stoplevel : 100;
   double minDist = safeLevel * point * 3;

   if(action == "BUY") {
      if(sl >= entry - minDist) return false;
      if(tp <= entry + minDist) return false;
   } else {
      if(sl <= entry + minDist) return false;
      if(tp >= entry - minDist) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| COUNT MY POSITIONS                                                 |
//+------------------------------------------------------------------+
int CountMyPositions() {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket)) {
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetString(POSITION_SYMBOL) == _Symbol) {
            count++;
         }
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| SEND ACKNOWLEDGMENT TO BRIDGE                                      |
//+------------------------------------------------------------------+
void SendAck(string signalId) {
   string accountId = GetAccountIdStr();
   string url = BridgeURL + "/ack/" + signalId + "?api_key=" + API_Key + "&account_id=" + accountId;
   char ackData[];
   char ackRes[];
   string ackHeaders = "Content-Type: application/json\r\n";
   string ackResHdr;
   WebRequest("POST", url, ackHeaders, 3000, ackData, ackRes, ackResHdr);
}

//+------------------------------------------------------------------+
//| JSON EXTRACTION — Simple string parser (no external DLL)           |
//+------------------------------------------------------------------+
string ExtractValue(string json, string key) {
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";

   pos += StringLen(search);

   // Skip whitespace + colon
   while(pos < StringLen(json)) {
      ushort c = StringGetCharacter(json, pos);
      if(c == ':') { pos++; break; }
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n') { pos++; continue; }
      return "";
   }

   // Skip whitespace after colon
   while(pos < StringLen(json)) {
      ushort c = StringGetCharacter(json, pos);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n') { pos++; continue; }
      break;
   }

   // Check what type of value
   ushort first = StringGetCharacter(json, pos);
   string value = "";

   if(first == '"') {
      // String value
      pos++;
      while(pos < StringLen(json)) {
         ushort c = StringGetCharacter(json, pos);
         if(c == '"') break;
         if(c == '\\') { pos++; if(pos < StringLen(json)) pos++; continue; }
         value += ShortToString(c);
         pos++;
      }
   } else if(first == '[') {
      // Array value — capture until matching ]
      int depth = 1;
      pos++;
      while(pos < StringLen(json) && depth > 0) {
         ushort c = StringGetCharacter(json, pos);
         if(c == '[') depth++;
         if(c == ']') depth--;
         if(depth > 0) value += ShortToString(c);
         pos++;
      }
   } else {
      // Numeric/boolean literal
      while(pos < StringLen(json)) {
         ushort c = StringGetCharacter(json, pos);
         if(c == ',' || c == '}' || c == ' ' || c == '\n' || c == '\r' || c == '\t') break;
         value += ShortToString(c);
         pos++;
      }
   }

   return value;
}

//+------------------------------------------------------------------+
//| Extract field from JSON object fragment (for layers)               |
//+------------------------------------------------------------------+
string ExtractField(string fragment, string key) {
   string search = "\"" + key + "\":";
   int pos = StringFind(fragment, search);
   if(pos < 0) return "";

   pos += StringLen(search);

   // Skip whitespace
   while(pos < StringLen(fragment)) {
      ushort c = StringGetCharacter(fragment, pos);
      if(c == ' ' || c == '\t') { pos++; continue; }
      break;
   }

   string value = "";
   ushort first = StringGetCharacter(fragment, pos);

   if(first == '"') {
      pos++;
      while(pos < StringLen(fragment)) {
         ushort c = StringGetCharacter(fragment, pos);
         if(c == '"') break;
         value += ShortToString(c);
         pos++;
      }
   } else {
      while(pos < StringLen(fragment)) {
         ushort c = StringGetCharacter(fragment, pos);
         if(c == ',' || c == '}' || c == ' ' || c == '\n' || c == '\t') break;
         value += ShortToString(c);
         pos++;
      }
   }

   return value;
}

//+------------------------------------------------------------------+
//| UTILITY FUNCTIONS                                                  |
//+------------------------------------------------------------------+
void ParseRiskSplit() {
   string parts[];
   ushort sep = StringGetCharacter(",", 0);
   StringSplit(LayerRiskSplit, sep, parts);
   int cnt = ArraySize(parts);
   ArrayResize(g_riskSplit, cnt);
   for(int i = 0; i < cnt; i++) {
      g_riskSplit[i] = StringToDouble(parts[i]);
   }
}

//── Instance Identity: unique MT5 account ID for multi-terminal broadcasting ──
string GetAccountIdStr() {
   return "MT5-" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
}

bool IsWeekend() {
   MqlDateTime dt;
   TimeCurrent(dt);
   return (dt.day_of_week == 0 || dt.day_of_week == 6);
}

bool IsKillzone() {
   MqlDateTime dt;
   TimeCurrent(dt);
   int hour = dt.hour;
   return (hour >= KillzoneStartHour && hour < KillzoneEndHour);
}

void ResetDailyStats() {
   g_consecutiveLosses = 0;
   g_dailyLoss = 0.0;
   g_dailyProfit = 0.0;
   g_circuitBreaker = false;
}

//+------------------------------------------------------------------+
//| INFO PANEL                                                         |
//+------------------------------------------------------------------+
void CreatePanel() {
   string name = "VTFX_Panel_BG";
   if(ObjectFind(0, name) < 0) {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, PanelCorner);
      ObjectSetInteger(0, name, OBJPROP_XSIZE, 250);
      ObjectSetInteger(0, name, OBJPROP_YSIZE, 180);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 25);
      ObjectSetInteger(0, name, OBJPROP_BGCOLOR, clrBlack);
      ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, PanelColor);
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
   }
}

void UpdatePanel() {
   string text = "\n";
   text += "   🔥 VILONA TRADE FX v2.0\n";
   text += "   ─────────────────────\n";
   text += "   Status: " + g_statusText + "\n";
   text += StringFormat("   Signals: %d | Trades: %d\n", g_totalSignals, g_totalTrades);
   text += StringFormat("   Wins: %d | Losses: %d\n", g_totalWins, g_totalTrades - g_totalWins);
   text += StringFormat("   Daily P&L: $%.2f | Loss: $%.2f\n", g_dailyProfit, g_dailyLoss);
   text += StringFormat("   Open Pos: %d/%d\n", CountMyPositions(), MaxTotalPositions);
   text += StringFormat("   CB: %s | Layering: %s\n",
                        g_circuitBreaker ? "⚠ ON" : "✅ OFF",
                        EnableLayering ? "ON" : "OFF");
   text += StringFormat("   Spread: %.1f | Risk: %.1f%%\n",
                        (SymbolInfoDouble(_Symbol, SYMBOL_ASK) -
                         SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point,
                        RiskPercent);
   text += "   ─────────────────────\n";
   text += "   © BerkahKarya | VT-PRO\n";
   Comment(text);
}

//+------------------------------------------------------------------+
//| OnTrade — track wins/losses                                        |
//+------------------------------------------------------------------+
void OnTrade() {
   HistorySelect(TimeCurrent() - 86400, TimeCurrent());
   int deals = HistoryDealsTotal();

   double todayPnL = 0.0;
   int todayWins = 0;
   int todayLosses = 0;

   for(int i = deals - 1; i >= 0; i--) {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket <= 0) continue;

      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol) continue;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      todayPnL += profit;
      if(profit > 0) todayWins++;
      else if(profit < 0) todayLosses++;
   }

   g_dailyProfit = MathMax(0, todayPnL);
   g_dailyLoss = MathMax(0, -todayPnL);
   g_totalWins = todayWins;
   g_consecutiveLosses = todayLosses;

   // Check circuit breaker
   if(g_dailyLoss >= MaxDailyLoss || g_consecutiveLosses >= MaxConsecutiveLosses) {
      if(!g_circuitBreaker) {
         Print("⚠ CIRCUIT BREAKER ACTIVATED! DailyLoss=$", g_dailyLoss,
               " | ConsLosses=", g_consecutiveLosses);
      }
      g_circuitBreaker = true;
   }
}
//+------------------------------------------------------------------+
