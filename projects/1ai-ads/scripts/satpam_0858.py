#!/usr/bin/env python3
import json
import time
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vilona_trakpro_engine as engine

ACT = '435670549443081'

def main():
    # verify account access
    act_check = engine.fb_get(ACT, fields='id,name')
    if isinstance(act_check, dict) and 'error' in act_check:
        print('BLOCKER: account self-check failed')
        print(json.dumps(act_check, indent=2))
        return

    # 1. campaigns
    camps = []
    page = engine.fb_get(f'{ACT}/campaigns', fields='id,name,status', params={'limit': 300})
    if isinstance(page, dict) and 'data' in page:
        camps = page['data']
        while page.get('paging', {}).get('next'):
            time.sleep(0.5)
            nxt = page['paging']['next']
            sep = '&' if '?' in nxt else '?'
            nxt = nxt + sep + 'access_token=' + engine.ACCESS_TOKEN
            req = __import__('urllib.request', fromlist=['Request']).Request(nxt)
            with __import__('urllib.request', fromlist=['urlopen']).urlopen(req, timeout=15) as r:
                page = json.loads(r.read())
            camps.extend(page.get('data', []))

    # 2. insights 7 hari
    ins = []
    page = engine.fb_get(
        f'{ACT}/insights',
        fields='campaign_id,campaign_name,spend,cpc,clicks,ctr',
        params={
            'time_range': json.dumps({'since': '2026-06-06', 'until': '2026-06-13'}),
            'level': 'campaign',
            'limit': 500,
            'filtering': json.dumps([{'field': 'campaign.effective_status', 'operator': 'IN', 'value': ['ACTIVE', 'PAUSED']}]),
        },
    )
    if isinstance(page, dict) and 'data' in page:
        ins = page['data']
        while page.get('paging', {}).get('next'):
            time.sleep(0.5)
            nxt = page['paging']['next']
            sep = '&' if '?' in nxt else '?'
            nxt = nxt + sep + 'access_token=' + engine.ACCESS_TOKEN
            req = __import__('urllib.request', fromlist=['Request']).Request(nxt)
            with __import__('urllib.request', fromlist=['urlopen']).urlopen(req, timeout=15) as r:
                page = json.loads(r.read())
            ins.extend(page.get('data', []))

    ins_idx = {r.get('campaign_id'): r for r in ins if r.get('campaign_id')}

    # 3. global CPC
    total_spend = 0.0
    total_clicks = 0
    for cid, row in ins_idx.items():
        try:
            total_spend += float(row.get('spend', 0) or 0)
            total_clicks += int(row.get('clicks', 0) or 0)
        except Exception:
            pass
    global_cpc = total_spend / total_clicks if total_clicks > 0 else 0.0

    active_count = sum(1 for c in camps if c.get('status') == 'ACTIVE')
    off_count = sum(1 for c in camps if c.get('name', '').startswith('OFF_'))
    star_count = sum(1 for c in camps if c.get('name', '').startswith('🌟_'))

    monsters = []
    watch_list = []
    winners = []
    lc_scale = []

    def num(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    for c in camps:
        name = c.get('name', '')
        if name.startswith('OFF_'):
            continue
        cid = c.get('id')
        row = ins_idx.get(cid, {})
        cpc = num(row.get('cpc'))
        spend = num(row.get('spend'))
        clicks = int(row.get('clicks', 0) or 0)
        status = c.get('status', 'PAUSED')

        if status != 'ACTIVE':
            continue

        # MONSTER
        if cpc >= 500 and spend > 1000:
            monsters.append((name, cpc, spend))
            continue
        # WATCH
        if cpc > 200 and clicks == 0 and spend > 500:
            watch_list.append((name, cpc, spend))
            continue
        # WINNER
        if cpc < 120 and clicks > 5 and spend > 10000:
            winners.append((name, cpc, spend, clicks))
        if 'LC' in name.upper() and cpc < 120:
            lc_scale.append((name, cpc, spend))

    # mutate if NORMAL
    for name, cpc, spend in monsters:
        if global_cpc >= 120:
            cid = next((c['id'] for c in camps if c.get('name') == name), None)
            if cid:
                engine.fb_post(cid, name=f'OFF_{name}')
                engine.fb_post(cid, status='PAUSED')

    for name, cpc, spend in watch_list:
        if global_cpc >= 120:
            cid = next((c['id'] for c in camps if c.get('name') == name), None)
            if cid:
                engine.fb_post(cid, status='PAUSED')

    for name, cpc, spend, clicks in winners:
        if global_cpc >= 120 and not name.startswith('🌟_'):
            cid = next((c['id'] for c in camps if c.get('name') == name), None)
            if cid:
                engine.fb_post(cid, name=f'🌟_{name}')

    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    mode = '🟢 AMAN' if global_cpc < 120 else '🔴 NORMAL'
    print(f"🛡️ SATPAM 0858 {ts}")
    print(f"ACTIVE:{active_count} | OFF_:{off_count} | 🌟:{star_count} | Global CPC:Rp{int(global_cpc)} | Spend:Rp{int(total_spend)} | Clicks:{total_clicks} | {mode}")
    print()
    print(f"💀 MONSTER (CPC>=500+spend>1K): {len(monsters)}")
    for n, cpc, s in monsters:
        print(f"  - {n} (CPC Rp{int(cpc)}, Spend Rp{int(s)})")
    print()
    print(f"👀 WATCH (CPC>200+0clicks+spend>500): {len(watch_list)}")
    for n, cpc, s in watch_list:
        print(f"  - {n} (CPC Rp{int(cpc)}, Spend Rp{int(s)})")
    print()
    print(f"🌟 WINNER (CPC<120+clicks>5+spend>10K): {len(winners)}")
    for n, cpc, s, c in winners:
        print(f"  - {n} (CPC Rp{int(cpc)}, Clicks {c}, Spend Rp{int(s)})")
    print()
    print(f"💰 LC SCALE (LC + CPC<120): {len(lc_scale)}")
    for n, cpc, s in lc_scale:
        print(f"  - {n} (CPC Rp{int(cpc)}, Spend Rp{int(s)})")

if __name__ == '__main__':
    main()
