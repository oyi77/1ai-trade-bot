def format_smc_analysis(ohlcv_bars: list, symbol: str = "XAUUSD", current_price: float = 0, 
                        action: str = "HOLD", pip_size: float = 0.10) -> str:
    """Generate compact SMC/ICT analysis section for signal posts.
    
    Uses real ICT methodology:
    - Liquidity Sweep (Asia/London/Previous Day levels)
    - Market Structure (BOS / CHoCH)
    - Order Blocks (nearest unmitigated OB)
    - Fair Value Gaps (nearest FVG zone)
    - Target Liquidity (next pool to target)
    
    Returns empty string if insufficient data or tools unavailable.
    """
    if not ohlcv_bars or len(ohlcv_bars) < 20 or action == "HOLD":
        return ""
    
    try:
        from session_levels import calculate_all_levels
        from sweep_detector import detect_sweep
        from liquidity_zones import map_zones
        
        levels = calculate_all_levels(ohlcv_bars)
        sweep = detect_sweep(ohlcv_bars[-24:], levels)
        sweep_dir = "BEARISH" if action == "SELL" else "BULLISH"
        zones = map_zones(ohlcv_bars[-24:], levels, current_price, sweep_dir)
        
        lines = ["", "━━━━━━━━━━━━━━━━━━━━━━", "🧬 <b>SMC / ICT ANALYSIS</b>", ""]
        
        # 1. Liquidity Sweep
        if sweep and sweep.confidence > 0.35:
            lines.append(
                f"🧹 <b>Liquidity Sweep:</b> {sweep.direction} "
                f"— {sweep.level_name} @ ${sweep.level_price:.2f} "
                f"(conf {sweep.confidence:.0%})"
            )
        
        # 2. Market Structure
        bos_high = getattr(levels, 'bos_high', 0) or 0
        bos_low = getattr(levels, 'bos_low', 0) or 0
        if bos_high or bos_low:
            struct_type = "BOS ↑" if action == "BUY" else "BOS ↓" if action == "SELL" else "RANGE"
            lines.append(f"🏗 <b>Structure:</b> {struct_type} | H: ${bos_high:.2f} L: ${bos_low:.2f}")
        
        # 3. Order Block
        ob_zones = [z for z in zones.zones if z.zone_type == "OB" and not z.mitigated]
        if ob_zones:
            ob_zones.sort(key=lambda z: abs(z.midpoint - current_price))
            nearest_ob = ob_zones[0]
            ob_dist = abs(current_price - nearest_ob.midpoint) / pip_size
            ob_dir = nearest_ob.direction
            ob_label = "Bullish OB" if ob_dir == "BULLISH" else "Bearish OB"
            lines.append(
                f"📦 <b>Order Block:</b> {ob_label} "
                f"@ ${nearest_ob.bottom:.2f}–${nearest_ob.top:.2f} "
                f"| {ob_dist:.0f} pip {'↓ below' if nearest_ob.midpoint < current_price else '↑ above'}"
            )
        
        # 4. Fair Value Gap
        fvg_zones = [z for z in zones.zones if z.zone_type == "FVG"]
        if fvg_zones:
            fvg_zones.sort(key=lambda z: abs(z.midpoint - current_price))
            nearest_fvg = fvg_zones[0]
            fvg_dist = abs(current_price - nearest_fvg.midpoint) / pip_size
            status = "✅ filled" if nearest_fvg.mitigated else "🕳 open"
            lines.append(
                f"🕳 <b>FVG:</b> ${nearest_fvg.bottom:.2f}–${nearest_fvg.top:.2f} "
                f"| {fvg_dist:.0f} pip | {status}"
            )
        
        # 5. Target Liquidity Pool
        if zones.nearest_target:
            tp_zone = zones.nearest_target
            tp_dist = abs(current_price - tp_zone.midpoint) / pip_size
            lines.append(
                f"🎯 <b>Target Liq:</b> {tp_zone.zone_type} "
                f"@ ${tp_zone.midpoint:.2f} | +{tp_dist:.0f} pip"
            )
        
        # 6. Session Range
        asia_hi = getattr(levels, 'asia_high', 0) or 0
        asia_lo = getattr(levels, 'asia_low', 0) or 0
        london_hi = getattr(levels, 'london_high', 0) or 0
        london_lo = getattr(levels, 'london_low', 0) or 0
        if asia_hi and asia_lo:
            lines.append(
                f"🌏 <b>Asia Range:</b> ${asia_lo:.2f}–${asia_hi:.2f} | "
                f"London: ${london_lo:.2f}–${london_hi:.2f}" if london_hi else
                f"🌏 <b>Asia Range:</b> ${asia_lo:.2f}–${asia_hi:.2f}"
            )
        
        return "\n".join(lines)
    except Exception:
        return ""
