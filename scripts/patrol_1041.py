import urllib.request, urllib.parse, json, time, os, sys
from datetime import datetime

def load_token():
    env_path = "/home/openclaw/projects/1ai-ads/.env"
    for line in open(env_path).read().splitlines():
        if not line or line.startswith("#"):
            continue
        if line.split("=", 1)[0] == "META_ACCESS_TOKEN":
            return line.split("=", 1)[1].strip()
    raise RuntimeError("token missing")

TOKEN = load_token()
API = "https://graph.facebook.com/v22.0"
ACT_ID = "380721031313330"
ACT = f"act_{ACT_ID}"

def api_get(endpoint, params=None):
    url = f"{API}/{endpoint}"
    qs = dict(params or {})
    qs["access_token"] = TOKEN
    url += "?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def api_post(endpoint, payload):
    url = f"{API}/{endpoint}?access_token={TOKEN}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        print(f"POST error on {endpoint}: {err}", file=sys.stderr)
        raise

def detect_type(name):
    n = name.upper()
    if "TEST" in n or "TESTING" in n:
        return "ABO"
    if n.startswith("ABO"):
        return "ABO"
    if n.startswith("BIDCAP"):
        return "ABO"
    if n.startswith(("CBO", "BC_", "LC_", "TC_", "🌟_", "ON_LC_", "ON_BC")):
        return "CBO"
    return "CBO"

def rebuild_next(next_url):
    parsed = urllib.parse.urlparse(next_url)
    qs = urllib.parse.parse_qs(parsed.query)
    qs["access_token"] = [TOKEN]
    new_qs = urllib.parse.urlencode(qs, doseq=True)
    path = parsed.path
    if path.startswith("/v22.0/"):
        path = path[len("/v22.0/"):]
    return f"{API}/{path}?{new_qs}"

def fetch_all(endpoint, params=None):
    results = []
    data = api_get(endpoint, params)
    results.extend(data.get("data", []))
    while "paging" in data and "next" in data.get("paging", {}):
        time.sleep(1.5)
        new_url = rebuild_next(data["paging"]["next"])
        with urllib.request.urlopen(new_url, timeout=30) as resp:
            data = json.loads(resp.read())
        results.extend(data.get("data", []))
    return results

def main():
    since = "2026-06-05"
    until = "2026-06-11"
    time_range = json.dumps({"since": since, "until": until})

    campaigns = fetch_all(f"{ACT}/campaigns", {"fields":"id,name,status", "limit":"200"})
    insights = fetch_all(f"{ACT}/insights", {
        "fields":"campaign_id,campaign_name,spend,cpc,clicks,ctr,impressions",
        "time_range": time_range,
        "level":"campaign",
        "limit":"200"
    })
    ins_by_id = {}
    for i in insights:
        cid = i.get("campaign_id")
        if cid:
            ins_by_id[cid] = i

    actions = []
    kill_list = []
    watch_list = []
    winner_list = []
    SKIP_PREFIXES = ("OFF_", "DEAD_")

    for c in campaigns:
        cid = c["id"]
        name = c["name"]
        if any(name.startswith(p) for p in SKIP_PREFIXES):
            continue
        ins = ins_by_id.get(cid, {})
        spend = float(ins.get("spend", 0))
        cpc = float(ins.get("cpc", 0))
        clicks = int(ins.get("clicks", 0))
        ctr = float(ins.get("ctr", 0))
        impr = int(ins.get("impressions", 0))
        camp_type = detect_type(name)
        cpc_danger = 120 if camp_type == "CBO" else 250

        reason = None
        if cpc > 200 and spend > 2000:
            reason = "kill"
        elif cpc > cpc_danger and spend > 5000:
            reason = "watch_cpc"
        elif ctr < 1.0 and impr > 1000:
            reason = "watch_ctr"

        if reason == "kill":
            actions.append((cid, name, "kill"))
            kill_list.append(f"{name} (CPC {cpc:.0f}, spend {spend:.0f})")
        elif reason == "watch_cpc":
            actions.append((cid, name, "pause"))
            watch_list.append(f"{name} (CPC {cpc:.0f}, spend {spend:.0f})")
        elif reason == "watch_ctr":
            actions.append((cid, name, "pause"))
            watch_list.append(f"{name} (CTR {ctr:.2f}%)")

    # Winner candidates not already targeted
    targeted_ids = {a[0] for a in actions}
    for c in campaigns:
        cid = c["id"]
        name = c["name"]
        if any(name.startswith(p) for p in SKIP_PREFIXES):
            continue
        if cid in targeted_ids:
            continue
        ins = ins_by_id.get(cid, {})
        spend = float(ins.get("spend", 0))
        cpc = float(ins.get("cpc", 0))
        clicks = int(ins.get("clicks", 0))
        if cpc < 120 and spend > 50000 and clicks > 0:
            actions.append((cid, name, "winner"))
            winner_list.append(f"{name} (CPC {cpc:.0f}, spend {spend:.0f}, klik {clicks})")

    print(f"Evaluasi: {len(actions)} aksi (kill={len(kill_list)}, watch={len(watch_list)}, winner={len(winner_list)})", file=sys.stderr)

    for cid, name, act_type in actions:
        try:
            if act_type == "kill":
                api_post(cid, {"name": f"OFF_{name}"})
                time.sleep(1.5)
                api_post(cid, {"status": "PAUSED"})
                time.sleep(1.5)
            elif act_type == "pause":
                api_post(cid, {"status": "PAUSED"})
                time.sleep(1.5)
            elif act_type == "winner":
                api_post(cid, {"name": f"🌟_{name}"})
                time.sleep(1.5)
        except Exception as e:
            print(f"Gagal aksi {act_type} untuk {name} ({cid}): {e}", file=sys.stderr)

    # Re-fetch final counts
    final_campaigns = fetch_all(f"{ACT}/campaigns", {"fields":"id,name,status", "limit":"200"})
    active_count = sum(1 for c in final_campaigns if c["status"] == "ACTIVE")
    off_count = sum(1 for c in final_campaigns if c["name"].startswith("OFF_"))
    star_count = sum(1 for c in final_campaigns if c["name"].startswith("🌟_"))
    total_spend = sum(float(ins_by_id.get(c["id"], {}).get("spend", 0)) for c in final_campaigns)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"🛡️ SATPAM 1041 — {since} s/d {until} | {now_str}")
    print(f"ACTIVE: {active_count} | OFF_: {off_count} | 🌟: {star_count}")
    print(f"⚠️ KILL: {', '.join(kill_list) if kill_list else 'None'}")
    print(f"👀 WATCH: {', '.join(watch_list) if watch_list else 'None'}")
    print(f"🌟 WINNERS: {', '.join(winner_list) if winner_list else 'None'}")
    print(f"💰 Spend 7d: Rp{total_spend:,.0f}")

if __name__ == "__main__":
    main()
