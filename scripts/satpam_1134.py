#!/usr/bin/env python3
"""SATPAM PATROL 1134 — Glowscent act_2125021885010866"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

ACT_ID = "2125021885010866"
API = "https://graph.facebook.com/v22.0"
ENV_PATH = "/home/openclaw/projects/1ai-ads/.env"

def load_token():
    for line in open(ENV_PATH).read().splitlines():
        if not line or line.startswith("#"):
            continue
        if line.split("=", 1)[0] == "META_ACCESS_TOKEN":
            return line.split("=", 1)[1].strip()
    raise RuntimeError("token missing")

TOKEN = load_token()

def api_get(path, params=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                body = e.read().decode()
                print(f"API 400: {body[:300]}")
                return {}
            if e.code == 403:
                print("API 403 forbidden")
                return {}
            if e.code == 429 or e.code == 400:
                wait = (attempt + 1) * 5
                print(f"Rate limit hit, waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            print(f"API error: {e}")
            time.sleep(2)
    return {}

def api_post(path, data):
    data["access_token"] = TOKEN
    for k in list(data.keys()):
        if isinstance(data[k], (list, dict)):
            data[k] = json.dumps(data[k])
    qs = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{API}/{path}", data=qs, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 429):
                wait = (attempt + 1) * 5
                print(f"POST rate limit, waiting {wait}s")
                time.sleep(wait)
                continue
            raise
    return {}

def api_delete(path):
    url = f"{API}/{path}?access_token={TOKEN}"
    req = urllib.request.Request(url, method="DELETE")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 429):
                wait = (attempt + 1) * 5
                time.sleep(wait)
                continue
            raise
    return {}

def main():
    print("=== SATPAM 1134 GLOWSCENT PATROL ===")
    
    # Verify account access
    account_check = api_get(f"act_{ACT_ID}", {"fields": "account_name"})
    if not account_check or "account_name" not in account_check:
        print(f"BLOCKER: Cannot access act_{ACT_ID}. Token valid? Account still exists?")
        print(f"Token length: {len(TOKEN)}")
        print("Stopping patrol — cannot classify without account access.")
        sys.exit(1)
    
    print(f"Account verified: {account_check.get('account_name')}")
    
    time.sleep(1.5)
    
    # Fetch campaigns
    campaigns_raw = api_get(f"act_{ACT_ID}/campaigns", {
        "fields": "id,name,status,effective_status,daily_budget,lifetime_budget,spend",
        "limit": 200
    })
    
    campaigns = campaigns_raw.get("data", [])
    print(f"Campaigns found: {len(campaigns)}")
    
    if not campaigns:
        print("EMPTY ACCOUNT: 0 campaigns found. Stopping policy execution.")
        print(f"ACTIVE: 0 | OFF_: 0 | 🌟: 0 | Spend 7d: Rp 0")
        sys.exit(0)
    
    time.sleep(1.5)
    
    # Fetch 7-day insights
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    until = datetime.now().strftime("%Y-%m-%d")
    
    insights = api_get(f"act_{ACT_ID}/insights", {
        "fields": "campaign_id,campaign_name,spend,cpc,clicks,ctr,impressions",
        "level": "campaign",
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": 200
    })
    
    insight_map = {}
    for row in insights.get("data", []):
        cid = row.get("campaign_id")
        if cid:
            insight_map[cid] = row
    
    print(f"Insights rows: {len(insight_map)}")
    time.sleep(1.5)
    
    # Taglinks tracked for Glowscent
    TRACKED_TAGLINKS = ["abera", "pintulipatgeser", "hijab"]
    
    # Thresholds for 1134 Glowscent
    CPC_KILL = 400
    CPC_DANGER_CBO = 140
    CPC_DANGER_ABO = 250
    SPEND_KILL = 2000
    SPEND_WATCH = 5000
    SPEND_WATCH_CTR = 1000
    CTR_WATCH = 1.0
    
    active_campaigns = [c for c in campaigns if c.get("status") == "ACTIVE"]
    off_campaigns = [c for c in campaigns if c.get("name", "").startswith("OFF_")]
    star_campaigns = [c for c in campaigns if c.get("name", "").startswith("🌟_")]
    
    print(f"ACTIVE: {len(active_campaigns)} | OFF_: {len(off_campaigns)} | 🌟: {len(star_campaigns)}")
    
    kills = []
    watches = []
    winners = []
    total_spend = 0
    
    for camp in active_campaigns:
        cid = camp["id"]
        name = camp.get("name", "")
        cdata = insight_map.get(cid, {})
        
        spend = float(cdata.get("spend", 0) or 0)
        cpc = float(cdata.get("cpc", 0) or 0)
        clicks = int(cdata.get("clicks", 0) or 0)
        ctr = float(cdata.get("ctr", 0) or 0)
        impr = int(cdata.get("impressions", 0) or 0)
        
        total_spend += spend
        
        # Detect campaign type
        name_upper = name.upper()
        if "TEST" in name_upper or "TESTING" in name_upper:
            camp_type = "TEST"
        elif name_upper.startswith("ABO") or name_upper.startswith("BIDCAP"):
            camp_type = "ABO"
        elif name_upper.startswith(("CBO", "BC_", "LC_", "TC_", "GLW", "ON_LC_")):
            camp_type = "CBO"
        else:
            camp_type = "CBO"  # Default to CBO for Glowscent
        
        # Layer 1: CPC check
        cpc_kill = False
        cpc_watch = False
        
        if spend > SPEND_KILL and cpc > CPC_KILL:
            cpc_kill = True
        elif spend > SPEND_WATCH:
            if camp_type == "CBO" and cpc > CPC_DANGER_CBO:
                cpc_watch = True
            elif camp_type in ("ABO", "TEST", "BIDCAP") and cpc > CPC_DANGER_ABO:
                cpc_watch = True
        
        # Layer 2: CTR check
        ctr_watch = False
        if impr > SPEND_WATCH_CTR and ctr < CTR_WATCH:
            ctr_watch = True
        
        # Layer 3: Taglink check
        has_tracked_tag = any(tag in name.lower() for tag in TRACKED_TAGLINKS)
        
        # Decision
        if cpc_kill:
            kills.append(f"{name} (CPC Rp{cpc:.0f}, spend Rp{spend:.0f})")
        elif cpc_watch or ctr_watch:
            reason = []
            if cpc_watch:
                reason.append(f"CPC Rp{cpc:.0f}")
            if ctr_watch:
                reason.append(f"CTR {ctr:.2f}%")
            watches.append(f"{name} ({', '.join(reason)})")
        elif has_tracked_tag and spend > 1000 and clicks > 0:
            winners.append(f"{name} (spend Rp{spend:.0f}, CPC Rp{cpc:.0f}, clicks {clicks})")
    
    # Execute kills (OFF_ + PAUSE)
    for kill_name in kills:
        base_name = kill_name.split(" (")[0]
        for camp in campaigns:
            if camp.get("name") == base_name and camp.get("status") == "ACTIVE":
                print(f"KILL: {base_name} — pausing...")
                api_post(camp["id"], {"status": "PAUSED"})
                time.sleep(1.5)
                # Rename to OFF_
                new_name = f"OFF_{base_name}"
                api_post(camp["id"], {"name": new_name})
                time.sleep(1.5)
                print(f"  → PAUSED + renamed OFF_{base_name}")
                break
    
    # Report format
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
    report = f"🛡️ SATPAM 1134 — {timestamp}\n"
    report += f"ACTIVE: {len(active_campaigns)} | OFF_: {len(off_campaigns)} | 🌟: {len(star_campaigns)}\n"
    report += f"💰 Spend 7d: Rp{total_spend:,.0f}\n"
    
    if kills:
        report += f"\n⚠️ KILL ({len(kills)}):\n"
        for k in kills:
            report += f"  • {k}\n"
    else:
        report += f"\n⚠️ KILL: None\n"
    
    if watches:
        report += f"\n👀 WATCH ({len(watches)}):\n"
        for w in watches:
            report += f"  • {w}\n"
    else:
        report += f"\n👀 WATCH: None\n"
    
    if winners:
        report += f"\n🌟 WINNERS ({len(winners)}):\n"
        for w in winners:
            report += f"  • {w}\n"
    else:
        report += f"\n🌟 WINNERS: None\n"
    
    print("\n" + report)
    print("\n[SATPAM 1134 COMPLETE]")

if __name__ == "__main__":
    main()
