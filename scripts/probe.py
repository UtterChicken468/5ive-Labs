"""One-off: save trimmed samples of each data feed so their exact formats can be checked.
Writes data/probe/*.json. Never writes API keys."""
import json, os, urllib.error, urllib.request, datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'probe')
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': '5ive-lab (personal sports model)'}
ODDS = os.environ.get('ODDS_API_KEY', '')


def trim(x, depth=0):
    if isinstance(x, list):
        return [trim(v, depth + 1) for v in x[:3]] + ([f'... {len(x) - 3} more'] if len(x) > 3 else [])
    if isinstance(x, dict):
        return {k: trim(v, depth + 1) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 200:
        return x[:200] + '...'
    return x


def get(name, url, keep=None):
    rec = {'url': url.replace(ODDS, '<key>') if ODDS else url}
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode('utf-8'))
            rec['status'] = r.status
            rec['headers'] = {k: v for k, v in r.headers.items() if k.lower().startswith('x-requests')}
        rec['sample'] = trim(keep(body) if keep else body)
        rec['_raw'] = body
    except urllib.error.HTTPError as e:
        rec['status'] = e.code
        rec['error'] = e.read().decode('utf-8', 'replace')[:300]
    except Exception as e:
        rec['error'] = f'{type(e).__name__}: {e}'
    raw = rec.pop('_raw', None)
    with open(os.path.join(OUT, name + '.json'), 'w') as f:
        json.dump(rec, f, indent=1)
    print(name, rec.get('status'), rec.get('error', '')[:120])
    return raw


# ---- NHL
sched = get('nhl_schedule', 'https://api-web.nhle.com/v1/schedule/2026-10-12')
gid = None
try:
    for day in sched['gameWeek']:
        for g in day['games']:
            if g.get('gameState') in ('OFF', 'FINAL') and g.get('gameType') == 2:
                gid = gid or g['id']
except Exception:
    pass
if gid:
    get('nhl_boxscore', f'https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore')
    get('nhl_rightrail', f'https://api-web.nhle.com/v1/gamecenter/{gid}/right-rail')
    get('nhl_landing', f'https://api-web.nhle.com/v1/gamecenter/{gid}/landing',
        keep=lambda b: {k: v for k, v in b.items() if k not in ('summary',)})
get('nhl_team_summary_games',
    'https://api.nhle.com/stats/rest/en/team/summary?isGame=true&limit=4&sort=gameId&cayenneExp=seasonId=20262027%20and%20gameTypeId=2')
get('nhl_season_now', 'https://api-web.nhle.com/v1/season', keep=lambda b: b[-3:] if isinstance(b, list) else b)

# ---- MLB
ms = get('mlb_schedule', 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&startDate=2026-09-01&endDate=2026-09-01&hydrate=probablePitcher,linescore,team')
pk = None
try:
    pk = ms['dates'][0]['games'][0]['gamePk']
except Exception:
    pass
if pk:
    get('mlb_boxscore', f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore',
        keep=lambda b: {side: {k: v for k, v in b['teams'][side].items() if k in ('team', 'teamStats', 'pitchers', 'battingOrder')} for side in ('home', 'away')})
get('mlb_season', 'https://statsapi.mlb.com/api/v1/seasons/2027?sportId=1')
get('mlb_season_2026', 'https://statsapi.mlb.com/api/v1/seasons/2026?sportId=1')

# ---- College basketball (ESPN)
sb = get('cbb_scoreboard', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=20260214&groups=50&limit=400',
         keep=lambda b: {'count': len(b.get('events', [])), 'events': b.get('events', [])[:1], 'leagues': 'omitted'})
eid = None
try:
    eid = sb['events'][0]['id']
except Exception:
    pass
if eid:
    get('cbb_summary', f'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={eid}',
        keep=lambda b: {k: b.get(k) for k in ('boxscore', 'header', 'pickcenter', 'odds')})
get('cbb_calendar', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=20261110&groups=50&limit=3',
    keep=lambda b: {'count': len(b.get('events', [])), 'season': b.get('season'), 'first': (b.get('events') or [None])[0]})

# ---- The Odds API
if ODDS:
    get('odds_sports', f'https://api.the-odds-api.com/v4/sports?apiKey={ODDS}',
        keep=lambda b: [s for s in b if s.get('group') in ('Ice Hockey', 'Baseball', 'Basketball', 'American Football')])
    get('odds_nhl', f'https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds?apiKey={ODDS}&regions=us&markets=h2h,spreads,totals&oddsFormat=american&bookmakers=draftkings')
else:
    print('ODDS_API_KEY not set')
print('done', datetime.datetime.utcnow().isoformat())
