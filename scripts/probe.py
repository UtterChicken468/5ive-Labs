"""One-off: save trimmed samples of the feeds the next features need. Never writes API keys."""
import json, os, urllib.error, urllib.parse, urllib.request, datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'probe')
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': '5ive-lab (personal sports model)'}
CFBD = os.environ.get('CFBD_API_KEY', '')


def trim(x, n=3):
    if isinstance(x, list):
        return [trim(v, n) for v in x[:n]] + ([f'... {len(x) - n} more'] if len(x) > n else [])
    if isinstance(x, dict):
        return {k: trim(v, n) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 160:
        return x[:160] + '...'
    return x


def get(name, url, keep=None, headers=None, n=3):
    rec = {'url': url}
    body = None
    try:
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode('utf-8'))
            rec['status'] = r.status
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
get('inj_nfl', f'{E}/football/nfl/injuries', keep=lambda b: {k: v for k, v in b.items() if k != 'season'}, n=4)
get('inj_nhl', f'{E}/hockey/nhl/injuries', n=4)
get('inj_cfb', f'{E}/football/college-football/injuries', n=3)
get('inj_cbb', f'{E}/basketball/mens-college-basketball/injuries', n=3)
get('nfl_teams_espn', f'{E}/football/nfl/teams', keep=lambda b: [t['team']['abbreviation'] + '|' + t['team']['displayName'] for t in b['sports'][0]['leagues'][0]['teams']], n=40)

# NHL: a finished game's box score (goalies + skaters) and an upcoming game's landing page
today = datetime.date.today()
wk = get('nhl_week', f'https://api-web.nhle.com/v1/schedule/{today.isoformat()}', keep=lambda b: {'n': sum(len(d['games']) for d in b['gameWeek'])})
fin = fut = None
try:
    for d in wk['gameWeek']:
        for g in d['games']:
            if g['gameState'] in ('OFF', 'FINAL') and not fin: fin = g['id']
            if g['gameState'] == 'FUT' and not fut: fut = g['id']
except Exception:
    pass
prev = get('nhl_prevweek', f'https://api-web.nhle.com/v1/schedule/{(today - datetime.timedelta(days=4)).isoformat()}', keep=lambda b: 'ok')
try:
    for d in prev['gameWeek']:
        for g in d['games']:
            if g['gameState'] in ('OFF', 'FINAL') and not fin: fin = g['id']
except Exception:
    pass
if fin:
    get('nhl_box_players', f'https://api-web.nhle.com/v1/gamecenter/{fin}/boxscore',
        keep=lambda b: {'playerByGameStats': b.get('playerByGameStats'), 'keys': list(b.keys())}, n=2)
if fut:
    get('nhl_landing_future', f'https://api-web.nhle.com/v1/gamecenter/{fut}/landing',
        keep=lambda b: {k: b.get(k) for k in ('matchup', 'gameState', 'awayTeam', 'homeTeam')}, n=3)
    get('nhl_rightrail_future', f'https://api-web.nhle.com/v1/gamecenter/{fut}/right-rail', keep=lambda b: list(b.keys()))

# Weather (Open-Meteo, no key)
get('weather', 'https://api.open-meteo.com/v1/forecast?latitude=40.8122&longitude=-77.8561&hourly=temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,precipitation'
    '&wind_speed_unit=mph&temperature_unit=fahrenheit&precipitation_unit=inch&timezone=America%2FNew_York&forecast_days=7',
    keep=lambda b: {k: (v if k != 'hourly' else {kk: vv[:4] for kk, vv in v.items()}) for k, v in b.items()})

# College football extras (CFBD)
if CFBD:
    H = {'Authorization': 'Bearer ' + CFBD, 'Accept': 'application/json'}
    C = 'https://api.collegefootballdata.com'
    get('cfbd_sp_prev', f'{C}/ratings/sp?year={today.year - 1}', headers=H, n=4)
    get('cfbd_venues', f'{C}/venues', headers=H, n=3)
    get('cfbd_lines_open', f'{C}/lines?year={today.year}&week=7&seasonType=regular', headers=H, keep=lambda b: b[:1], n=4)
    get('cfbd_games_venue', f'{C}/games?year={today.year}&week=8&seasonType=regular&classification=fbs', headers=H, keep=lambda b: b[:1])

# College basketball player box (ESPN summary, last season)
sb = get('cbb_sb', f'{E}/basketball/mens-college-basketball/scoreboard?dates=20260214&groups=50&limit=5', keep=lambda b: 'ok')
try:
    eid = sb['events'][0]['id']
    get('cbb_players', f'{E}/basketball/mens-college-basketball/summary?event={eid}',
        keep=lambda b: {'players': b['boxscore'].get('players'), 'pickcenter_keys': [list(p.keys()) for p in (b.get('pickcenter') or [])][:1]}, n=3)
except Exception as e:
    print('cbb players skipped', e)

# ESPN NFL scoreboard odds (for opening lines)
get('nfl_espn_sb', f'{E}/football/nfl/scoreboard', keep=lambda b: {'odds': b['events'][0]['competitions'][0].get('odds')}, n=2)
print('done')
