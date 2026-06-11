# Tripay Callback Enforcement
## Target: `/home/openclaw/projects/1ai-trade-bot/`

This is not an auto-apply patch set. It is an exact, verifiable change contract for 4 fixes.

---

## 1) Enforce non-empty `TRIPAY_CALLBACK_URL` + `DUITKU_CALLBACK_URL`

**Why:** silent empty callbacks mean no Conversions API and no donor-state transition from webhook.

Change in `tradebot/config/settings.py`:

Current:
```python
TRIPAY_CALLBACK_URL: str = ""
DUITKU_CALLBACK_URL: str = ""
```

Target:
```python
TRIPAY_CALLBACK_URL: str = ""  # MUST be https://phantomfx.aitradepulse.com/webhook/tripay in prod
DUITKU_CALLBACK_URL: str = ""
```

Process rule:
- On startup (not in settings file), assert both URLs are set in `.env` before binding `tradebot.web.server`.
- If empty, fail startup with error naming the missing key.
- This is safer than a default, because an empty callback URL is worse than no service.

File to edit:
- `tradebot/config/settings.py` for defaults only
- Add one assert block in `tradebot/app.py` or in the web server startup path.

---

## 2) Tripay callback signature verify + idempotent donor transition

**Current behavior (from code scan):**
`payment.py` exposes `verify_tripay_callback()` but does not show an idempotency guard in the webhook handler.  
**Required behavior:**

- Verify `X-Callback-Signature` with `verify_tripay_callback(raw_body, signature)` using the raw body bytes. Do NOT re-encode JSON because canonicalization drift breaks HMAC.
- Reject non `PAID` status without transition.
- Insert into `payments.json` state machine only once using `merchant_order_id` or `reference` as idempotency key.
- Log every callback attempt with outcome: `verified/paid`, `verified/unpaid`, `rejected`.

Implementation contract:
- File: `tradebot/services/payment.py`
- Public helpers to keep or add:
  - `PaymentService.verify_tripay_callback(callback_data, callback_signature)` keep
  - `PaymentService.handle_tripay_callback(payload: dict, signature: str) -> dict` new
- Return shape:
  ```json
  {"ok": true, "status": "paid", "donor_state": "activated"}
  ```
- Failure shape:
  ```json
  {"ok": false, "error": "signature_mismatch"}
  ```

---

## 3) Bridge authz firewall + admin-only route rule

**Where:** `tradebot/web/server.py`
**Rules:**

- `/api/bridge/*` must not be exposed without auth.
- Current code shows bridge endpoints but no auth guard in the file scan.
- Add `_check_auth(request)` to:
  - `/api/bridge/signal`
  - `/api/bridge/status`
  - `/api/bridge/balance`
- If an internal component needs bridge data without session cookie, use a loopback header enforcement rule:
  - Header `X-Internal-Bridge: 1` allowed only from `127.0.0.1` or `10.0.0.0/8`
  - Otherwise require admin login.

This prevents attackers from pulling bridge state blindly.

---

## 4) CAPI / Pixel firing on LP view + checkout events

**Why:** conversion tracking feeds scaling decisions.

Where to fire:
- `tradebot/web/public_dashboard.py` and server templates for the LP.

Events to fire server-side best-effort (do not block on network errors):
1. `PageView` on `/landing`, `/dashboard/*`
2. `ViewContent` when tripay form rendered
3. `InitiateCheckout` on `/api/fuel/create` submission start
4. `Purchase` when payment transitions to paid via callback

Implementation contract:
- Use `httpx.AsyncClient` with fire-and-forget POST.
- Catch and log all errors.
- Do NOT fail the web request if CAPI POST fails.
- Add feature flag: `META_CAPI_ENABLED: bool = True` in `settings.py`.

Data flow diagram:
```
LP render -> public_dashboard.py middleware issue PageView
/api/fuel/create -> queue InitiateCheckout
callback verified as paid -> queue Purchase + sync donations ledger
```

---

## Verification checklist after applying

- [ ] `python -m tradebot` fails fast if `TRIPAY_CALLBACK_URL` is empty
- [ ] `curl` with bad signature returns `{"ok": false, "error": "signature_mismatch"}`
- [ ] `/api/bridge/*` returns 302 `/login` when unauthenticated
- [ ] CAPI POST is attempted on LP view; failure does not break page load
- [ ] No `except Exception: pass` introduced
- [ ] No `# type: ignore` added without commit note
- [ ] Tests pass locally: `python -m pytest tests/ -q`

---

## Commit message template

```
fix: enforce tripay callback setup and bridge authz — detail

- startup guard for TRIPAY_CALLBACK_URL and DUITKU_CALLBACK_URL
- idempotent webhook handler with strict signature check
- /api/bridge/* now admin-auth or loopback-only
- fire-and-forget Meta CAPI events from LP + paid webhook

Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
```

---

*Anchored to: tradebot/services/payment.py, tradebot/config/settings.py, tradebot/web/server.py*
