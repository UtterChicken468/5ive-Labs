"""One-off: sample NBA and UFC feeds. Never writes API keys."""
import json, os, re, urllib.error, urllib.request

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'probe')
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (5ive-lab personal sports model)'}
ODDS = os.environ.get('ODDS_API_KEY', '')


def trim(x, n=3):
    if isinstance(x, list):
        return [trim(v, n) for v in x[:n]] + ([f'... {len(x) - n} more'] if len(x) > n else [])
    if isinstance(x, dict):
        return {k: trim(v, n) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 160:
        return x[:160] + '...'
    return x


def raw(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def get(name, url, keep=None, n=3, text=False):
    rec = {'url': url.replace(ODDS, '<key>') if ODDS else url}
    body = None
    try:
        st, txt = raw(url)
        rec['status'] = st
        if text:
            body = txt
            rec['sample'] = keep(txt) if keep else txt[:3000]
        else:
            body = json.loads(txt)
            rec['sample'] = trim(keep(body) if keep else body, n)
    except urllib.error.HTTPError as e:
        rec['status'] = e.code; rec['error'] = e.read().decode('utf-8', 'replace')[:300]
    except Exception as e:
        rec['error'] = f'{type(e).__name__}: {e}'
    with open(os.path.join(OUT, name + '.json'), 'w') as f:
        json.dump(rec, f, indent=1)
    print(name, rec.get('status'), rec.get('error', '')[:120])
    return body


E = 'https://site.api.espn.com/apis/site/v2/sports'
# ---- NBA (ESPN)
sb = get('nba_sb', f'{E}/basketball/nba/scoreboard?dates=20260115', keep=lambda b: {'count': len(b.get('events', [])), 'event': (b.get('events') or [None])[0]})
try:
    eid = sb['events'][0]['id']
    get('nba_summary', f'{E}/basketball/nba/summary?event={eid}',
        keep=lambda b: {'teamstats': [[x.get('name') for x in t.get('statistics', [])] for t in b['boxscore']['teams']][:1],
                        'players_names': [s.get('names') for t in b['boxscore'].get('players', []) for s in t.get('statistics', [])][:1],
                        'athlete0': (b['boxscore'].get('players') or [{}])[0].get('statistics', [{}])[0].get('athletes', [None])[0],
                        'pickcenter': b.get('pickcenter')}, n=6)
except Exception as e:
    print('nba summary skipped', e)
get('nba_upcoming', f'{E}/basketball/nba/scoreboard?dates=20261021', keep=lambda b: {'count': len(b.get('events', [])), 'season': b.get('season'), 'odds': ((b.get('events') or [{}])[0].get('competitions') or [{}])[0].get('odds')}, n=2)
get('nba_season_start', f'{E}/basketball/nba/scoreboard?dates=20261020', keep=lambda b: {'count': len(b.get('events', [])), 'types': [e.get('season') for e in b.get('events', [])][:2]})
get('nba_old_lines', f'{E}/basketball/nba/scoreboard?dates=20240115', keep=lambda b: {'count': len(b.get('events', []))})
get('nba_injuries', f'{E}/basketball/nba/injuries', keep=lambda b: {'n': len(b.get('injuries', [])), 'first': (b.get('injuries') or [None])[0]}, n=2)

# ---- UFC via ESPN
u = get('ufc_sb', f'{E}/mma/ufc/scoreboard', keep=lambda b: {'leagues': [l.get('calendar', [])[:3] for l in b.get('leagues', [])], 'event': (b.get('events') or [None])[0]}, n=4)
get('ufc_sb_past', f'{E}/mma/ufc/scoreboard?dates=20250412', keep=lambda b: {'count': len(b.get('events', [])), 'event': (b.get('events') or [None])[0]}, n=4)
try:
    ev = u['events'][0]; comp = ev['competitions'][0]; ath = comp['competitors'][0]['athlete']['id'] if 'athlete' in comp['competitors'][0] else comp['competitors'][0].get('id')
    get('ufc_athlete', f'https://site.web.api.espn.com/apis/common/v3/sports/mma/athletes/{ath}', n=3)
    get('ufc_athlete_stats', f'https://site.web.api.espn.com/apis/common/v3/sports/mma/athletes/{ath}/stats', n=3)
except Exception as e:
    print('ufc athlete skipped', e)

# ---- ufcstats.com (full fight history with per-fight stats)
def strip_html(t):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', '', t, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t))
evs = get('ufcstats_events', 'http://ufcstats.com/statistics/events/completed?page=all', text=True,
          keep=lambda t: {'len': len(t), 'links': re.findall(r'http://ufcstats.com/event-details/[0-9a-f]+', t)[:3], 'n_links': len(set(re.findall(r'http://ufcstats.com/event-details/[0-9a-f]+', t))), 'text': strip_html(t)[:1500]})
try:
    link = re.findall(r'http://ufcstats.com/event-details/[0-9a-f]+', evs)[0]
    ev = get('ufcstats_event', link, text=True, keep=lambda t: {'fights': re.findall(r'http://ufcstats.com/fight-details/[0-9a-f]+', t)[:3], 'html_row': t[t.find('b-fight-details__table-row'):t.find('b-fight-details__table-row') + 4000]})
    fl = re.findall(r'http://ufcstats.com/fight-details/[0-9a-f]+', ev)[0]
    get('ufcstats_fight', fl, text=True, keep=lambda t: {'text': strip_html(t)[:4000]})
    fr = re.findall(r'http://ufcstats.com/fighter-details/[0-9a-f]+', ev)[0]
    get('ufcstats_fighter', fr, text=True, keep=lambda t: {'text': strip_html(t)[:2500]})
except Exception as e:
    print('ufcstats details skipped', e)

# ---- odds sport keys + one MMA/NBA odds sample (h2h only = 1 request each)
if ODDS:
    get('odds_mma', f'https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds?apiKey={ODDS}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings', n=2)
print('done')
