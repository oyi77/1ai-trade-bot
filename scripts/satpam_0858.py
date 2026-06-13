#!/usr/bin/env python3
import urllib.request, urllib.parse, json, time, sys
from datetime import datetime

ACT = '435670549443081'
API = 'https://graph.facebook.com/v22.0'
ENV = '/home/openclaw/projects/1ai-ads/.env'

def load_token():
    for line in open(ENV).read().splitlines():
        if not line or line.startswith('#'):
            continue
        if line.split('=', 1)[0] == 'META_ACCESS_TOKEN':
            return line.split('=', 1)[1].strip().strip("'").strip('"')
    raise RuntimeError('META_ACCESS_TOKEN missing')

TOKEN = load_token()

def api_get(path, params=None):
    url = f"{API}/{path}"
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOKEN}'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def api_post(path, data):
    url = f"{API}/{path}"
    data['access_token'] = TOKEN
    qs = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=qs, method='POST')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def fetch_all(endpoint, fields, limit=300):
    results = []
    next_url = f"act_{ACT}/{endpoint}?fields={urllib.parse.quote(fields)}&limit={limit}"
    while next_url:
        full = f"{API}/{next_url}" if next_url.startswith('http') else f"{API}/{next_url}"
        if '?' not in full:
            full += '?access_token=' + urllib.parse.quote(TOKEN)
        else:
            sep = '&' if not full.endswith('&') else ''
            full += sep + 'access_token=' + urllib.parse.quote(TOKEN)
        req = urllib.request.Request(full, headers={'User-Agent': 'HermesPatrol/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            blob = json.loads(r.read())
        data = blob.get('data', [])
        results.extend(data)
        nxt = blob.get('paging', {}).get('next')
        if not nxt:
            break
        # rebuild next URL with current token
        sep2 = '&' if '?' in nxt else '?'
        next_url = nxt + sep2 + 'access_token=' + urllib.parse.quote(TOKEN)
        time.sleep(0.3)
    return results

def main():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"🛡️ SATPAM 0858 {now}")
    start = time.time()

    # 1. Campaigns
    camps = fetch_all('campaigns', 'id,name,status', limit=300)
    active = [c for c in camps if c['status'] == 'ACTIVE']
    off = [c for c in camps if c['name'].startswith('OFF_')]
    print(f"ACTIVE:{len(active)} | OFF_:{len(off)}")

    # 2. Insights 7-day
    insights_raw = api_get(f"act_{ACT}/insights", {
        'fields': 'campaign_id,campaign_name,spend,cpc,clicks,ctr',
        'time_range': json.dumps({'since': '2026-06-06', 'until': '2026-06-13'}),
        'level': 'campaign',
        'limit': '500'
    })
    rows = {}
    for r in insights_raw.get('data', []):
        cid = r.get('campaign_id')
        if cid:
            rows[cid] = r

    spend_total = 0
    clicks_total = 0
    for r in rows.values():
        try:
            spend_total += float(r.get('spend', 0) or 0)
            clicks_total += float(r.get('clicks', 0) or 0)
        except Exception:
            pass
    global_cpc = round(spend_total / clicks_total, 2) if clicks_total else 0
    print(f"Spend 7d:Rp{int(spend_total)} | Clicks:{int(clicks_total)} | Global CPC:Rp{global_cpc}")

    mode = 'AMAN' if global_cpc < 120 else 'NORMAL'
    print(f"Mode:{mode}")

    monsters = []
    watch = []
    winners = []
    lc = []

    for c in active:
        cid = c['id']
        name = c['name']
        cname_lower = name.lower()
        r = rows.get(cid, {})
        cpc = float(r.get('cpc', 0) or 0)
        clicks = int(float(r.get('clicks', 0) or 0))
        spend = float(r.get('spend', 0) or 0)

        # MONSTER
        if cpc >= 500 and spend > 1000:
            monsters.append((name, cpc, spend, clicks))
            continue
        # WATCH
        if cpc > 200 and clicks == 0 and spend > 500:
            watch.append((name, cpc, spend))
            continue
        # WINNER
        if cpc < 120 and clicks > 5 and spend > 10000:
            winners.append((name, cpc, spend, clicks))
        # LC SCALE
        if 'lc' in cname_lower and cpc < 120:
            lc.append((name, cpc, spend, clicks))

    print(f"\n💀 MONSTER: {len(monsters)}")
    for n, cpc, spend, clicks in monsters:
        print(f"  - {n} | CPC Rp{cpc:.0f} | Spend Rp{spend:.0f} | Clicks {clicks}")

    print(f"\n👀 WATCH: {len(watch)}")
    for n, cpc, spend in watch:
        print(f"  - {n} | CPC Rp{cpc:.0f} | Spend Rp{spend:.0f}")

    print(f"\n🌟 WINNER: {len(winners)}")
    for n, cpc, spend, clicks in winners:
        print(f"  - {n} | CPC Rp{cpc:.0f} | Spend Rp{spend:.0f} | Clicks {clicks}")

    print(f"\n💰 LC SCALE (CPC<120): {len(lc)}")
    for n, cpc, spend, clicks in lc:
        print(f"  - {n} | CPC Rp{cpc:.0f} | Clicks {clicks}")

    # Execute mutations
    actions = []
    if mode == 'NORMAL' and monsters:
        print('\n⚡ EXECUTE: pausing MONSTERs')
        for name, cpc, spend, clicks in monsters:
            for c in camps:
                if c['name'] == name:
                    try:
                        api_post(c['id'], {'status': 'PAUSED'})
                        actions.append(f"PAUSED {name}")
                    except Exception as e:
                        actions.append(f"FAIL {name}: {e}")
                    break
    if watch and mode == 'NORMAL':
        print('\n⚡ EXECUTE: pausing WATCH')
        for name, cpc, spend in watch:
            for c in camps:
                if c['name'] == name:
                    try:
                        api_post(c['id'], {'status': 'PAUSED'})
                        actions.append(f"PAUSED {name}")
                    except Exception as e:
                        actions.append(f"FAIL {name}: {e}")
                    break

    if actions:
        print('\nActions:')
        for a in actions:
            print('  ' + a)

    print(f"\nElapsed: {time.time()-start:.1f}s")

if __name__ == '__main__':
    main()
