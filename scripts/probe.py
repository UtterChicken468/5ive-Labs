"""One-off: sample ESPN UFC detail feeds. Never writes API keys."""
import json, os, urllib.error, urllib.request

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'probe')
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (5ive-lab personal sports model)'}


def trim(x, n=4):
    if isinstance(x, list):
        return [trim(v, n) for v in x[:n]] + ([f'... {len(x) - n} more'] if len(x) > n else [])
    if isinstance(x, dict):
        return {k: trim(v, n) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 200:
        return x[:200] + '...'
    return x


def get(name, url, keep=None, n=4):
    rec = {'url': url}; body = None
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode('utf-8')); rec['status'] = r.status
        rec['sample'] = trim(keep(body) if keep else body, n)
    except urllib.error.HTTPError as e:
        rec['status'] = e.code; rec['error'] = e.read().decode('utf-8', 'replace')[:300]
    except Exception as e:
        rec['error'] = f'{type(e).__name__}: {e}'
    with open(os.path.join(OUT, name + '.json'), 'w') as f:
        json.dump(rec, f, indent=1)
    print(name, rec.get('status'), rec.get('error', '')[:120])
    return body


E = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc'
C = 'https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc'
yr = get('ufc_year', f'{E}/scoreboard?dates=2025', keep=lambda b: {'events': len(b.get('events', [])), 'calendar': [(c.get('label'), c.get('startDate')) for c in (b.get('leagues') or [{}])[0].get('calendar', [])]}, n=60)
past = get('ufc_event_full', f'{E}/scoreboard?dates=20250412', keep=lambda b: b, n=20)
try:
    ev = past['events'][0]
    comp = ev['competitions'][-1]  # main event is usually last
    with open(os.path.join(OUT, 'ufc_main_comp.json'), 'w') as f:
        json.dump(comp, f, indent=1)
    aid = comp['competitors'][0]['id']
    get('ufc_athlete', f'https://site.web.api.espn.com/apis/common/v3/sports/mma/athletes/{aid}', n=6)
    get('ufc_athlete_stats', f'https://site.web.api.espn.com/apis/common/v3/sports/mma/athletes/{aid}/stats', n=6)
    get('ufc_athlete_core', f'https://sports.core.api.espn.com/v2/sports/mma/athletes/{aid}', n=6)
    get('ufc_comp_core', f"{C}/events/{ev['id']}/competitions/{comp['id']}", n=6)
    get('ufc_comp_status', f"{C}/events/{ev['id']}/competitions/{comp['id']}/status", n=6)
    get('ufc_competitor_stats', f"{C}/events/{ev['id']}/competitions/{comp['id']}/competitors/{aid}/statistics", n=8)
    get('ufc_summary', f"{E}/summary?event={ev['id']}", keep=lambda b: {k: (v if k != 'news' else None) for k, v in b.items()}, n=3)
except Exception as e:
    print('detail skipped', type(e).__name__, e)
print('done')
