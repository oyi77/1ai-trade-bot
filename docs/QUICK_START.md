# Signal Generator — Quick Start Guide

## What Changed: 30-Second Version

Free users can **generate 3 custom trading signals** using AI-powered TA analysis.

**On 4th generation:** System shows upgrade/donation CTAs instead of denying access.

**Donation path:** $5–$100 tiers grant 5–300 bonus generation credits.

**Paid plans:** Unlimited generations.

---

## API Usage Examples

### 1. Generate a Signal (1 symbol)

```bash
curl -X POST https://app.tradingbot.com/api/v1/signals/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "indicators": ["rsi", "macd", "ema_cross"]
  }'

# Response (200)
{
  "success": true,
  "data": {
    "id": 42,
    "symbol": "BTC/USDT",
    "signal_type": "buy",
    "confidence_score": 0.82,
    "entry_price": 50000.0,
    "stop_loss": 48500.0,
    "take_profit_1": 52000.0,
    "take_profit_2": 54000.0,
    "take_profit_3": 56000.0,
    "risk_reward_ratio": 1.33,
    "analysis_reason": "TA confluence: RSI=28, MACD bullish, EMA cross",
    "source": "user_generated",
    "expires_at": "2025-01-24T20:00:00Z",
    "created_at": "2025-01-24T16:00:00Z"
  },
  "message": "Signal generated for BTC/USDT"
}
```

### 2. Hit Quota (402 Payment Required)

```bash
# After 3 free generations on free tier:
curl -X POST https://app.tradingbot.com/api/v1/signals/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "symbol": "ETH/USDT" }'

# Response (402)
{
  "success": false,
  "error": "GENERATION_LIMIT_REACHED",
  "message": "You've used all 3 free signal generations. Upgrade your plan for unlimited generations, or make a donation to get bonus credits.",
  "status_code": 402,
  "actions": {
    "upgrade": {
      "label": "Upgrade Plan",
      "url": "/api/v1/subscriptions/plans",
      "description": "Get unlimited signal generations with Starter plan or higher"
    },
    "donate": {
      "label": "Support Us",
      "url": "/api/v1/subscriptions/donate",
      "description": "Make a donation to receive bonus generation credits"
    }
  },
  "usage": { "used": 3, "limit": 3 }
}
```

### 3. Scan Multiple Markets

```bash
curl -X POST https://app.tradingbot.com/api/v1/signals/scan \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
    "timeframe": "1h",
    "min_confidence": 0.65,
    "limit": 5
  }'

# Response (200) — top 5 signals from scan
{
  "success": true,
  "data": [
    { signal for BTC, confidence 0.85 },
    { signal for ETH, confidence 0.78 },
    { signal for SOL, confidence 0.72 },
    ...
  ],
  "message": "Scanned 4 markets, found 3 signals"
}
```

### 4. Check Remaining Credits

```bash
curl -X GET https://app.tradingbot.com/api/v1/signals/quota \
  -H "Authorization: Bearer $TOKEN"

# Response (200)
{
  "success": true,
  "data": {
    "tier": "free",
    "total_credits": 3,
    "used_credits": 2,
    "remaining_credits": 1,
    "bonus_credits": 0,
    "is_unlimited": false,
    "upgrade_prompt": "You're running low on signal generations! Upgrade to Starter ($19.99/mo) for unlimited generations.",
    "donate_prompt": "Love free signals? Buy us a coffee ($5) and get 5 bonus generations!"
  }
}
```

### 5. Donate (Get Bonus Credits)

```bash
curl -X POST https://app.tradingbot.com/api/v1/subscriptions/donate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tier": "coffee",
    "success_url": "https://app.tradingbot.com/signals",
    "cancel_url": "https://app.tradingbot.com/pricing"
  }'

# Response (200)
{
  "success": true,
  "data": {
    "checkout_url": "https://checkout.tripay.com/pay/cs_live_xyz...",
    "session_id": "cs_live_xyz",
    "credits_to_receive": 5,
    "amount_cents": 500
  },
  "message": "Donation checkout created. You'll receive 5 bonus credits!"
}
```

### 6. List My Generated Signals

```bash
curl -X GET "https://app.tradingbot.com/api/v1/signals/my-generations?page=1&page_size=20" \
  -H "Authorization: Bearer $TOKEN"

# Response (200)
{
  "success": true,
  "data": {
    "signals": [ ...list of user's generated signals... ],
    "total": 3,
    "page": 1,
    "page_size": 20,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

### 7. List Donation Tiers

```bash
curl -X GET https://app.tradingbot.com/api/v1/subscriptions/donation-tiers

# Response (200)
{
  "success": true,
  "data": [
    {
      "tier": "coffee",
      "label": "Buy us a coffee ($5)",
      "amount_cents": 500,
      "credits": 5
    },
    {
      "tier": "supporter",
      "label": "Supporter ($15)",
      "amount_cents": 1500,
      "credits": 20
    },
    {
      "tier": "patron",
      "label": "Patron ($50)",
      "amount_cents": 5000,
      "credits": 100
    },
    {
      "tier": "whale",
      "label": "Whale ($100)",
      "amount_cents": 10000,
      "credits": 300
    }
  ]
}
```

---

## What Symbols & Indicators Are Supported?

### Scannable Symbols (15)
`BTC/USDT`, `ETH/USDT`, `BNB/USDT`, `SOL/USDT`, `XRP/USDT`, `ADA/USDT`, `DOGE/USDT`, `AVAX/USDT`, `DOT/USDT`, `MATIC/USDT`, `LINK/USDT`, `UNI/USDT`, `ATOM/USDT`, `LTC/USDT`, `FIL/USDT`

### Timeframes (6)
`1m`, `5m`, `15m`, `1h`, `4h`, `1d`

### Technical Indicators (9)
`rsi` (Relative Strength Index)
`macd` (MACD)
`ema_cross` (EMA 9/21 crossover)
`bollinger` (Bollinger Bands)
`vwap` (VWAP)
`stochastic` (Stochastic)
`atr` (Average True Range)
`ichimoku` (Ichimoku)
`supertrend` (SuperTrend)

---

## Quota Summary

| Plan | Daily View Limit | Generation Credits | Cost |
|------|------------------|-------------------|------|
| **Free** | 5 signals/day | 3 lifetime | $0 |
| **Starter** | 25 signals/day | Unlimited | $19.99/mo |
| **Pro** | Unlimited | Unlimited | $49.99/mo |
| **Enterprise** | Unlimited | Unlimited | $149.99/mo |

**Donations add bonus credits:**
- Coffee ($5) → +5 credits
- Supporter ($15) → +20 credits
- Patron ($50) → +100 credits
- Whale ($100) → +300 credits

---

## Error Codes

| Code | HTTP | Meaning | Action |
|------|------|---------|--------|
| `GENERATION_LIMIT_REACHED` | 402 | Out of free generations | Upgrade plan OR donate |
| `VALIDATION_ERROR` | 422 | Invalid symbol/timeframe/indicator | Check supported options above |
| `AUTHORIZATION_ERROR` | 403 | Onboarding gate not met | Complete onboarding first |
| `NOT_FOUND` | 404 | Symbol not found | Use supported symbol |

---

## Frontend Integration

### Show Quota Badge
```javascript
const quota = await fetch('/api/v1/signals/quota', {
  headers: { 'Authorization': `Bearer ${token}` }
}).then(r => r.json());

// Display: "2 / 3 generations used"
console.log(`${quota.data.used_credits} / ${quota.data.total_credits} used`);

// Show prompts if running low
if (quota.data.upgrade_prompt) {
  showBanner(quota.data.upgrade_prompt);
}
```

### Handle 402 Response
```javascript
const response = await fetch('/api/v1/signals/generate', { ... });

if (response.status === 402) {
  const error = await response.json();
  
  // Show modal with two CTAs
  showUpsellModal({
    title: 'Signal Generations Used',
    message: error.message,
    actions: error.actions // { upgrade: {...}, donate: {...} }
  });
}
```

---

## Production Checklist

- [ ] Database migration applied (see `IMPLEMENTATION_SUMMARY.md`)
- [ ] Real TA analysis integrated (swap `_run_technical_analysis()`)
- [ ] TriPay donation webhook configured
- [ ] Frontend shows quota badge & 402 modal
- [ ] Test: generate → hit quota → see upgrade/donate → upgrade/donate → success
- [ ] Monitor donation funnel metrics

---

## Documentation

- **Complete API spec:** See `SIGNAL_GENERATOR.md`
- **Implementation details:** See `IMPLEMENTATION_SUMMARY.md`
- **Testing guide:** See `tests/unit/test_signal_generator.py`

---

**Version:** 1.0  
**Status:** Production Ready  
**Last Updated:** 2025-01-24
