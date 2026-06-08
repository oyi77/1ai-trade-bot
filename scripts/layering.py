#!/usr/bin/env python3
"""Vilona Trade FX — Smart Layering Signal Generator.
Dipanggil dari vilona_tradefx_handler.py sebelum push sinyal ke bridge.
Generate multi-layer entry dari satu sinyal AI.

Layer logic:
  - Layer 1: 40% risk — entry di harga saat ini, TP standar
  - Layer 2: 30% risk — entry +LayerSpacing pips (jauh dari harga), TP lebih lebar
  - Layer 3: 30% risk — entry +2x LayerSpacing, TP paling lebar
  - Semua layer share SL yang sama
"""


def generate_layers(action, entry, sl, tp, symbol="XAUUSD",
                    layer_count=3, layer_spacing_pips=50,
                    risk_split=None, tp_pips=None):
    """
    Generate layered entries dari satu sinyal.

    Args:
        action: "BUY" atau "SELL"
        entry: harga entry utama
        sl: stop loss (shared across all layers)
        tp: take profit utama
        symbol: simbol (default XAUUSD)
        layer_count: jumlah layer (1-5)
        layer_spacing_pips: jarak antar layer dalam pips
        risk_split: list persentase risk per layer, e.g. [0.4, 0.3, 0.3]
        tp_pips: custom TP pips per layer, e.g. [100, 200, 300]
                 Kalau None, auto dari spacing × multiplier

    Returns:
        list of dict: [{"entry": float, "tp": float, "risk_pct": float}, ...]
    """
    if risk_split is None:
        if layer_count == 1:
            risk_split = [1.0]
        elif layer_count == 2:
            risk_split = [0.5, 0.5]
        elif layer_count == 3:
            risk_split = [0.4, 0.3, 0.3]
        elif layer_count == 4:
            risk_split = [0.35, 0.25, 0.25, 0.15]
        else:
            risk_split = [0.3, 0.2, 0.2, 0.15, 0.15]

    if tp_pips is None:
        # Default TP: semakin jauh entry, semakin lebar TP
        tp_pips = [(i + 1) * layer_spacing_pips * 2 for i in range(layer_count)]

    # ── Symbol-aware pip size ──
    sym = symbol.upper()
    if sym in ("XAUUSD", "GOLD"):
        pip_value = 0.1
    elif sym in ("BTCUSD", "BTC"):
        pip_value = 1.0
    elif sym.endswith("JPY"):
        pip_value = 0.01
    elif sym in ("USOIL", "OIL", "CL"):
        pip_value = 0.01
    elif sym.startswith(("BBCA", "BBRI", "TLKM", "ASII", "IHSG")):
        pip_value = 1.0
    else:
        pip_value = 0.0001  # standard forex

    layers = []
    is_buy = action.upper() == "BUY"

    for i in range(layer_count):
        # Entry: semakin jauh dari harga utama
        offset = i * layer_spacing_pips * pip_value
        if is_buy:
            layer_entry = entry + offset
        else:
            layer_entry = entry - offset

        # TP: berdasarkan tp_pips dari entry layer
        layer_tp_pips = tp_pips[i] if i < len(tp_pips) else tp_pips[-1]
        if is_buy:
            layer_tp = layer_entry + (layer_tp_pips * pip_value)
        else:
            layer_tp = layer_entry - (layer_tp_pips * pip_value)

        risk = risk_split[i] if i < len(risk_split) else risk_split[-1]

        layers.append({
            "entry": round(layer_entry, 2),
            "tp": round(layer_tp, 2),
            "risk_pct": round(risk, 2),
        })

    return layers


def enrich_signal_with_layers(sig, layer_count=3, layer_spacing=50):
    """
    Ambil sinyal existing, tambah layers array.
    Dipanggil sebelum post_signal_to_bridge().

    Args:
        sig: dict sinyal dari detect_mechanical_signal() atau AI consensus
        layer_count: jumlah layer (default 3 untuk Pro tier)
        layer_spacing: spacing pips (default 50)

    Returns:
        dict sinyal dengan key 'layers' added
    """
    action = sig.get("action", "HOLD")
    if action == "HOLD":
        return sig

    entry = sig.get("entry", 0)
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    symbol = sig.get("symbol", "XAUUSD")

    if entry <= 0 or sl <= 0:
        return sig  # can't layer without valid entry/sl

    layers = generate_layers(
        action=action,
        entry=entry,
        sl=sl,
        tp=tp,
        symbol=symbol,
        layer_count=layer_count,
        layer_spacing_pips=layer_spacing,
    )

    sig["layers"] = layers
    sig["risk_percent"] = sig.get("risk_percent", 1.0)

    # Update comment
    source = sig.get("source", "vtfx")
    sig["comment"] = f"VTFX/{source}/{layer_count}L"

    return sig


# ── Test ──
if __name__ == "__main__":
    # Test BUY signal
    sig = {
        "action": "BUY",
        "entry": 4380.00,
        "sl": 4365.00,
        "tp": 4410.00,
        "confidence": 0.78,
        "source": "mechanical",
        "symbol": "XAUUSD",
    }

    layered = enrich_signal_with_layers(sig, layer_count=3, layer_spacing=50)
    print("=== LAYERED SIGNAL ===")
    for k, v in layered.items():
        if k == "layers":
            print(f"  {k}:")
            for i, l in enumerate(v):
                print(f"    Layer {i+1}: entry={l['entry']} tp={l['tp']} risk={l['risk_pct']}")
        else:
            print(f"  {k}: {v}")

    print()
    print("=== SELL SIGNAL ===")
    sig2 = {
        "action": "SELL",
        "entry": 4380.00,
        "sl": 4395.00,
        "tp": 4355.00,
        "confidence": 0.72,
        "source": "ai_dual",
        "symbol": "XAUUSDc",
    }
    layered2 = enrich_signal_with_layers(sig2, layer_count=3, layer_spacing=50)
    for k, v in layered2.items():
        if k == "layers":
            print(f"  {k}:")
            for i, l in enumerate(v):
                print(f"    Layer {i+1}: entry={l['entry']} tp={l['tp']} risk={l['risk_pct']}")
        else:
            print(f"  {k}: {v}")
