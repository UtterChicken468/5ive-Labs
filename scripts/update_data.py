"""Refresh the data files for 5ive Lab.

Builds one file per sport in data/ (data/nfl.json, data/cfb.json, ...). A sport's file is
only rewritten when its stats or lines changed, so the scheduled job doesn't make empty
commits. data/status.json records whether each sport's last refresh worked, and the error
if it didn't, so one broken feed never blocks the others.

Usage:  python scripts/update_data.py [--only nfl,cfb] [--season 2026]
Keys come from environment variables (GitHub repository secrets in the workflow):
  CFBD_API_KEY   CollegeFootballData.com, for college football
For offline testing of the NFL feed: GAMES_CSV, TEAM_CSV and PLAYER_CSV can point at local files.
"""
import argparse, csv, datetime, io, json, os, sys, traceback, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'data')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
ET = ZoneInfo('America/New_York')
UA = {'User-Agent': '5ive-lab (personal sports model)'}
HIST = False   # True while building a past season (skips live-only extras)
YEAR = None    # season to build when HIST is on


# ---------------------------------------------------------------- helpers
def default_season():
    today = datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1  # football seasons start in Aug/Sep


def http_get(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')


def load_csv(url, env_name):
    local = os.environ.get(env_name) if env_name else None
    if local:
        with open(local, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    return list(csv.DictReader(io.StringIO(http_get(url))))


def num(v):
    try:
        return float(v) if v not in ('', None, 'NA') else 0.0
    except (TypeError, ValueError):
        return 0.0


def opt_float(v):
    try:
        return None if v in ('', None, 'NA') else float(v)
    except (TypeError, ValueError):
        return None


def opt_int(v):
    f = opt_float(v)
    return None if f is None else int(f)


def pick(d, *keys, default=None):
    """First present key: the CFBD API has used both camelCase and snake_case names."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def write_if_changed(path, data):
    old = None
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            try:
                old = json.load(f)
            except json.JSONDecodeError:
                old = None
        if isinstance(old, dict):
            old.pop('updated', None)
    if old == data:
        return False
    data = dict(data)
    data['updated'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    return True


def finalize_games(team_games, sched):
    """Only count a game as final once both teams' stats exist, so the model never
    sees a score without the matching box score."""
    have = {(r['w'], r['t']) for r in team_games}
    for g in sched:
        if g['hs'] is not None and not ((g['w'], g['h']) in have and (g['w'], g['a']) in have):
            g['hs'] = g['as_'] = None
    return sched


FOOTBALL_UI = dict(unit='pts', spreadName='Spread', edgeSteps=[1, 1.5, 2, 3, 4], edgeDefault=1.5, hfaMax=4, hfaStep=0.5, byeMax=3, byeStep=0.5)


def get_json(url, headers=None, tries=3):
    for i in range(tries):
        try:
            return json.loads(http_get(url, headers=headers, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (404, 400) or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tries - 1:
                raise
        import time; time.sleep(2 + 3 * i)


def load_cache(name):
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(name, obj):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'), sort_keys=True)


def parallel(fn, items, workers=8):
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for k, v in zip(items, ex.map(lambda x: _safe(fn, x), items)):
            out[k] = v
    return out


def _safe(fn, x):
    try:
        return fn(x)
    except Exception as e:  # one bad game shouldn't sink the run; it is retried next time
        return {'_error': str(e)[:120]}


def rest_days(sched):
    """Days since each team's previous game, filled into g['hr'] / g['ar']."""
    last = {}
    for g in sorted(sched, key=lambda x: (x['d'], x['tm'])):
        day = datetime.date.fromisoformat(g['d'])
        for side, t in (('hr', g['h']), ('ar', g['a'])):
            g[side] = float((day - last[t]).days) if t in last else 3.0
        last[g['h']] = last[g['a']] = day


def day_index(sched):
    first = min(datetime.date.fromisoformat(g['d']) for g in sched)
    for g in sched:
        g['w'] = (datetime.date.fromisoformat(g['d']) - first).days
    return first


# ---------------------------------------------------------------- shared extras: weather, injuries, opening lines
NFL_STADIUMS = {  # home team -> (lat, lon); roof comes from the schedule
    'ARI': (33.528, -112.263), 'ATL': (33.755, -84.401), 'BAL': (39.278, -76.623), 'BUF': (42.774, -78.787),
    'CAR': (35.226, -80.853), 'CHI': (41.862, -87.617), 'CIN': (39.095, -84.516), 'CLE': (41.506, -81.700),
    'DAL': (32.748, -97.093), 'DEN': (39.744, -105.020), 'DET': (42.340, -83.046), 'GB': (44.501, -88.062),
    'HOU': (29.685, -95.411), 'IND': (39.760, -86.164), 'JAX': (30.324, -81.637), 'KC': (39.049, -94.484),
    'LA': (33.953, -118.339), 'LAC': (33.953, -118.339), 'LV': (36.091, -115.184), 'MIA': (25.958, -80.239),
    'MIN': (44.974, -93.258), 'NE': (42.091, -71.264), 'NO': (29.951, -90.081), 'NYG': (40.813, -74.074),
    'NYJ': (40.813, -74.074), 'PHI': (39.901, -75.168), 'PIT': (40.447, -80.016), 'SEA': (47.595, -122.332),
    'SF': (37.403, -121.970), 'TB': (27.976, -82.503), 'TEN': (36.166, -86.771), 'WAS': (38.908, -76.864)}


def add_weather(sched, where, days=6):
    """Forecast at kickoff for upcoming outdoor games. where(g) -> (lat, lon) or None."""
    if HIST:
        return
    today = datetime.datetime.now(ET).date()
    todo = {}
    for g in sched:
        if g['hs'] is not None or not g.get('d') or not g.get('tm'):
            continue
        ahead = (datetime.date.fromisoformat(g['d']) - today).days
        loc = where(g)
        if 0 <= ahead <= days and loc:
            todo.setdefault((round(loc[0], 3), round(loc[1], 3)), []).append(g)

    def fetch(loc):
        return get_json('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=temperature_2m,'
                        'wind_speed_10m,wind_gusts_10m,precipitation_probability,precipitation,snowfall'
                        '&wind_speed_unit=mph&temperature_unit=fahrenheit&precipitation_unit=inch'
                        '&timezone=America%%2FNew_York&forecast_days=8' % loc)
    got = parallel(fetch, list(todo), workers=6)
    for loc, games in todo.items():
        f = got.get(loc) or {}
        hr = f.get('hourly') or {}
        times = hr.get('time') or []
        for g in games:
            key = f"{g['d']}T{g['tm'][:2]}:00"
            if key not in times:
                continue
            i = times.index(key)
            val = lambda k: (hr.get(k) or [None] * (i + 1))[i]
            g['wx'] = dict(t=val('temperature_2m'), w=val('wind_speed_10m'), gu=val('wind_gusts_10m'),
                           pp=val('precipitation_probability'), pr=val('precipitation'), sn=val('snowfall'))


INJ_KEEP = {'Out', 'Doubtful', 'Questionable', 'Day-To-Day', 'Suspension'}


def espn_injuries(path, team_key):
    """ESPN injury report -> {our team code: [{n, pos, st}]}. team_key(espn_team) -> our code."""
    if HIST:
        return {}
    try:
        data = get_json(f'https://site.api.espn.com/apis/site/v2/sports/{path}/injuries')
    except Exception:
        return {}
    out = {}
    for t in data.get('injuries', []):
        code = team_key(t)
        if not code:
            continue
        for i in t.get('injuries', []):
            st = i.get('status')
            if st not in INJ_KEEP:
                continue
            a = i.get('athlete') or {}
            out.setdefault(code, []).append(dict(n=a.get('displayName', ''), pos=(a.get('position') or {}).get('abbreviation', ''),
                                                 st=st, note=(i.get('shortComment') or '')[:140]))
    order = {'Out': 0, 'Suspension': 0, 'Doubtful': 1, 'Questionable': 2, 'Day-To-Day': 2}
    for v in out.values():
        v.sort(key=lambda x: order.get(x['st'], 3))
    return out


def track_open(sport, season, sched, true_open=None):
    """Remember the first line seen for every game; attach it as g['o'] for line movement.
    true_open(g) can supply the real opening number when the source has one (CFBD does)."""
    if HIST:
        return
    name = f'open_{sport}_{season}.json'
    cache = load_cache(name)
    for g in sched:
        k = str(g['id'])
        if k not in cache and any(g.get(x) is not None for x in ('sp', 'tot', 'hml')):
            cache[k] = {x: g.get(x) for x in ('sp', 'tot', 'hml', 'aml')}
        o = (true_open(g) if true_open else None) or cache.get(k)
        if o and any(o.get(x) is not None for x in ('sp', 'tot', 'hml')):
            g['o'] = o
    save_cache(name, cache)


# ---------------------------------------------------------------- The Odds API (NHL and MLB lines)
ODDS_PREF = ['draftkings', 'fanduel', 'betmgm', 'williamhill_us', 'betrivers']


def norm_name(s):
    import unicodedata
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return ''.join(ch for ch in s if ch.isalnum())


def odds_lines(sport_key, sched, names, cache_name):
    """Fetch current lines for upcoming games and merge them into a cache that keeps each
    game's last known line, so finished games keep their closing number for the backtest.
    names: normalized full team name -> team code used in sched."""
    if HIST:
        return 'past season', None
    cache = load_cache(cache_name)
    key = os.environ.get('ODDS_API_KEY')
    today = datetime.datetime.now(ET).date()
    soon = [g for g in sched if g['hs'] is None and g['d'] and 0 <= (datetime.date.fromisoformat(g['d']) - today).days <= 2]
    note = None
    if not key:
        note = 'ODDS_API_KEY not set'
    elif not soon:
        note = 'no games in the next 2 days'
    elif cache.get('_remaining') is not None and cache['_remaining'] < 40:
        note = f"saving the last {cache['_remaining']} odds requests this month"
    else:
        url = (f'https://api.the-odds-api.com/v4/sports/{sport_key}/odds?apiKey={key}&regions=us'
               f'&markets=h2h,spreads,totals&oddsFormat=american&bookmakers={",".join(ODDS_PREF)}')
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            events = json.loads(r.read().decode('utf-8'))
            rem = r.headers.get('x-requests-remaining')
        cache['_remaining'] = int(float(rem)) if rem else None
        by = {}
        for g in sched:
            if g['hs'] is None:
                by.setdefault((g['h'], g['a']), []).append(g)
        now = datetime.datetime.now(datetime.timezone.utc)
        for ev in events:
            start = datetime.datetime.fromisoformat((ev.get('commence_time') or '1970-01-01T00:00:00Z').replace('Z', '+00:00'))
            if start <= now:
                continue  # already started: these are live in-game odds, keep the pregame line
            h, a = names.get(norm_name(ev.get('home_team'))), names.get(norm_name(ev.get('away_team')))
            if not h or not a or (h, a) not in by:
                continue
            d, _ = et_parts(ev.get('commence_time'))
            cands = sorted(by[(h, a)], key=lambda g: abs((datetime.date.fromisoformat(g['d']) - datetime.date.fromisoformat(d)).days))
            g = cands[0]
            books = sorted(ev.get('bookmakers', []), key=lambda b: ODDS_PREF.index(b['key']) if b['key'] in ODDS_PREF else 99)
            line = {}
            for b in books:
                for m in b.get('markets', []):
                    oc = {o.get('name'): o for o in m.get('outcomes', [])}
                    if m['key'] == 'h2h' and 'hml' not in line:
                        ho, ao = oc.get(ev['home_team']), oc.get(ev['away_team'])
                        if ho and ao:
                            line['hml'], line['aml'] = int(ho['price']), int(ao['price'])
                    if m['key'] == 'spreads' and 'sp' not in line:
                        ho, ao = oc.get(ev['home_team']), oc.get(ev['away_team'])
                        if ho and ho.get('point') is not None:
                            line['sp'] = -float(ho['point'])
                            line['hspp'] = int(ho['price']) if ho.get('price') is not None else None
                            line['aspp'] = int(ao['price']) if ao and ao.get('price') is not None else None
                    if m['key'] == 'totals' and 'tot' not in line:
                        ov, un = oc.get('Over'), oc.get('Under')
                        if ov and ov.get('point') is not None:
                            line['tot'] = float(ov['point'])
                            line['ovp'] = int(ov['price']) if ov.get('price') is not None else None
                            line['unp'] = int(un['price']) if un and un.get('price') is not None else None
            if line:
                cache[str(g['id'])] = line
    save_cache(cache_name, cache)
    for g in sched:
        l = cache.get(str(g['id']))
        if l:
            g['sp'], g['tot'], g['hml'], g['aml'] = l.get('sp'), l.get('tot'), l.get('hml'), l.get('aml')
            for k in ('hspp', 'aspp', 'ovp', 'unp'):
                if l.get(k) is not None:
                    g[k] = l[k]
    return note, cache.get('_remaining')


# ---------------------------------------------------------------- NHL (NHL public API + The Odds API)
def toi_min(s):
    m, _, sec = str(s or '0:0').partition(':')
    return round(num(m) + num(sec) / 60, 1)


def nhl_box(gid):
    b = get_json(f'https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore')
    pbg = b.get('playerByGameStats') or {}
    out = {}
    for side, key in (('home', 'homeTeam'), ('away', 'awayTeam')):
        t = pbg.get(key) or {}
        sk = []
        for grp, pos in (('forwards', 'F'), ('defense', 'D')):
            for x in t.get(grp, []):
                sk.append([x['playerId'], (x.get('name') or {}).get('default', ''), pos, x.get('goals', 0), x.get('assists', 0),
                           x.get('sog', 0), toi_min(x.get('toi'))])
        gl = [[x['playerId'], (x.get('name') or {}).get('default', ''), bool(x.get('starter')), x.get('shotsAgainst', 0),
               x.get('saves', 0), toi_min(x.get('toi'))] for x in t.get('goalies', [])]
        out[side] = dict(sk=sk, gl=gl)
    return out

def build_nhl(_season):
    today = datetime.datetime.now(ET).date()
    y = YEAR if HIST else (today.year if today.month >= 9 else today.year - 1)
    sid = int(f'{y}{y + 1}')
    games, date, seen = {}, f'{y}-09-20', set()
    for _ in range(60):  # walk the season a week at a time
        wk = get_json(f'https://api-web.nhle.com/v1/schedule/{date}')
        for day in wk.get('gameWeek', []):
            for g in day.get('games', []):
                if g.get('gameType') == 2 and g.get('season') == sid:
                    games[g['id']] = g
        nxt = wk.get('nextStartDate')
        if not nxt or nxt in seen or nxt > f'{y + 1}-06-30':
            break
        seen.add(nxt); date = nxt
    if not games:
        raise RuntimeError(f'No {y}-{y + 1} NHL schedule found.')

    names, tid = {}, {}
    sched = []
    for g in games.values():
        h, a = g['homeTeam'], g['awayTeam']
        for t in (h, a):
            tid[t['id']] = t['abbrev']
            full = (t.get('placeName', {}).get('default', '') + ' ' + t.get('commonName', {}).get('default', '')).strip()
            names[norm_name(full)] = t['abbrev']
            names[norm_name(t.get('commonName', {}).get('default', ''))] = t['abbrev']
        d, tm = et_parts(g.get('startTimeUTC'))
        final = g.get('gameState') in ('OFF', 'FINAL') and h.get('score') is not None
        sched.append(dict(id=g['id'], d=d, tm=tm, h=h['abbrev'], a=a['abbrev'],
                          hs=h.get('score') if final else None, as_=a.get('score') if final else None,
                          sp=None, tot=None, hml=None, aml=None, neu=bool(g.get('neutralSite')),
                          ot=(g.get('gameOutcome') or {}).get('lastPeriodType') if final else None))
    first = day_index(sched)
    rest_days(sched)

    rows, start = [], 0
    while True:  # team stats per game, 100 rows a page
        url = ('https://api.nhle.com/stats/rest/en/team/summary?isGame=true&sort=gameId'
               f'&start={start}&limit=100&cayenneExp=seasonId={sid}%20and%20gameTypeId=2')
        page = get_json(url).get('data', [])
        rows += page
        if len(page) < 100:
            break
        start += 100
    gw = {g['id']: g for g in sched}
    by_game = {}
    for r in rows:
        by_game.setdefault(r['gameId'], []).append(r)
    team_games = []
    for gid, rs in by_game.items():
        g = gw.get(gid)
        if not g or len(rs) != 2:
            continue
        for me, op in ((rs[0], rs[1]), (rs[1], rs[0])):
            team_games.append(dict(
                w=g['w'], t=tid.get(me['teamId']), o=tid.get(op['teamId']),
                gf=num(me.get('goalsFor')), sf=num(me.get('shotsForPerGame')),
                pp=0.2 if me.get('powerPlayPct') is None else num(me['powerPlayPct']),
                pk=0.8 if me.get('penaltyKillPct') is None else num(me['penaltyKillPct']),
                fo=0.5 if me.get('faceoffWinPct') is None else num(me['faceoffWinPct'])))
    finalize_games(team_games, sched)
    note, remaining = odds_lines('icehockey_nhl', sched, names, f'odds_nhl_{sid}.json')
    sched.sort(key=lambda g: (g['d'], g['tm']))
    track_open('nhl', y, sched)

    # Box scores: starting goalies (for the goalie adjustment) and skater lines (for props).
    box_name = f'nhl_box_{sid}.json'
    box = load_cache(box_name)
    need = [] if HIST else [str(g['id']) for g in sched if g['hs'] is not None and (str(g['id']) not in box or '_error' in box[str(g['id'])])]
    if need:
        box.update(parallel(nhl_box, need, workers=8))
        save_cache(box_name, box)
    goalies, gnames, team_goalies, last_start, players = {}, {}, {}, {}, []
    for g in sched:
        b = box.get(str(g['id']))
        if g['hs'] is None or not b or '_error' in b:
            continue
        for side, t, o in (('home', g['h'], g['a']), ('away', g['a'], g['h'])):
            for pid, nm, starter, sa, sv, toi in b[side]['gl']:
                pid = str(pid); gnames[pid] = nm
                if starter:
                    goalies.setdefault(pid, []).append([g['w'], sa, sv])
                    g['hg' if side == 'home' else 'ag'] = pid
                    team_goalies.setdefault(t, [])
                    if pid not in team_goalies[t]:
                        team_goalies[t].append(pid)
                    last_start[t] = pid
                    players.append(dict(id='G' + pid, n=nm, p='G', t=t, o=o, w=g['w'], sv=sv, sa=sa))
            for pid, nm, pos, gl_, ast, sog, toi in b[side]['sk']:
                if toi >= 8:
                    players.append(dict(id=str(pid), n=nm, p=pos, t=t, o=o, w=g['w'], sog=sog, gl=gl_, ast=ast, pts=gl_ + ast, toi=toi))
    for g in sched:  # projected starter: whoever started the team's last game (you can change it on the card)
        if g['hs'] is None:
            g['hg'], g['ag'] = last_start.get(g['h']), last_start.get(g['a'])
    injuries = espn_injuries('hockey/nhl', lambda t: names.get(norm_name(t.get('displayName'))))

    meta = dict(sport='nhl', label='NHL', family='hockey', period='Day', start=first.isoformat(), props=True,
                seasonLabel=f'{y}-{str(y + 1)[2:]}', sigma=2.4, sigmaTot=2.3, btSkip=10, formN=5,
                hfa=0.15, trust=55, bye=0.2, rest='b2b', unit='goals', spreadName='Puck line',
                edgeSteps=[0.25, 0.5, 0.75, 1], edgeDefault=0.5, hfaMax=0.5, hfaStep=0.05, byeMax=0.5, byeStep=0.05,
                goalie=True, lgSv=round(sum(x[2] for v in goalies.values() for x in v) / max(sum(x[1] for v in goalies.values() for x in v), 1), 4),
                source='NHL stats API · lines from The Odds API · injuries from ESPN', oddsNote=note, oddsRemaining=remaining)
    return dict(meta=meta, season=y, teamGames=team_games, games=sched, players=players, goalies=goalies, gnames=gnames,
                teamGoalies=team_goalies, injuries=injuries)


# ---------------------------------------------------------------- MLB (MLB Stats API + The Odds API)
def ip_val(s):
    s = str(s or '0')
    whole, _, part = s.partition('.')
    return num(whole) + (num(part) / 3 if part else 0)


def mlb_box(pk):
    b = get_json(f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore')
    out = {}
    for side in ('home', 'away'):
        t = b['teams'][side]
        bat, pit = t['teamStats']['batting'], t['teamStats']['pitching']
        sp_id = t['pitchers'][0] if t.get('pitchers') else None
        sp = (t['players'].get(f'ID{sp_id}', {}).get('stats', {}).get('pitching', {}) if sp_id else {}) or {}
        tip, ter = ip_val(pit.get('inningsPitched')), num(pit.get('earnedRuns'))
        sip, ser = ip_val(sp.get('inningsPitched')), num(sp.get('earnedRuns'))
        name = t['players'].get(f'ID{sp_id}', {}).get('person', {}).get('fullName', '') if sp_id else ''
        out[side] = dict(r=num(bat.get('runs')), h=num(bat.get('hits')), bb=num(bat.get('baseOnBalls')),
                         hbp=num(bat.get('hitByPitch')), sf=num(bat.get('sacFlies')), so=num(bat.get('strikeOuts')),
                         pa=num(bat.get('plateAppearances')), tb=num(bat.get('totalBases')), hr=num(bat.get('homeRuns')),
                         sp=sp_id, spn=name, sp_ip=round(sip, 2), sp_er=ser,
                         bp_ip=round(max(tip - sip, 0), 2), bp_er=max(ter - ser, 0))
    return out


MLB_GAME_TYPES = ('R', 'F', 'D', 'L', 'W')  # regular season, wild card, division series, LCS, World Series
MLB_ROUND = {'F': 'Wild Card', 'D': 'Division Series', 'L': 'LCS', 'W': 'World Series'}


def mlb_schedule(y):
    out = []
    for t in MLB_GAME_TYPES:
        try:
            data = get_json(f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType={t}&season={y}&hydrate=probablePitcher,team')
        except Exception as e:  # a missing postseason round shouldn't sink the whole sport
            print(f'  mlb schedule {y} {t}: {e}')
            continue
        out += [g for d in data.get('dates', []) for g in d.get('games', [])]
    return out


def build_mlb(_season):
    today = datetime.datetime.now(ET).date()
    y = YEAR if HIST else today.year
    raw = mlb_schedule(y)
    final = lambda g: g.get('status', {}).get('abstractGameState') == 'Final' and g['teams']['home'].get('score') is not None \
        and 'Postponed' not in g['status'].get('detailedState', '') and 'Cancel' not in g['status'].get('detailedState', '')
    note = None
    if not HIST and not any(final(g) for g in raw):
        y -= 1; raw = mlb_schedule(y)
        note = f'{y} season · the {y + 1} season starts in the spring'
    names, sched, pnames = {}, [], {}
    seen = set()
    for g in raw:
        st = g.get('status', {})
        if 'Postponed' in st.get('detailedState', '') or 'Cancel' in st.get('detailedState', '') or g['gamePk'] in seen:
            continue
        seen.add(g['gamePk'])
        th, ta = g['teams']['home'], g['teams']['away']
        for t in (th['team'], ta['team']):
            names[norm_name(t.get('name'))] = t.get('abbreviation')
        d, tm = et_parts(g.get('gameDate'))
        d = g.get('officialDate') or d
        fin = final(g)
        hp, ap = (th.get('probablePitcher') or {}), (ta.get('probablePitcher') or {})
        for p_ in (hp, ap):
            if p_.get('id'):
                pnames[str(p_['id'])] = p_.get('fullName', '')
        sched.append(dict(id=g['gamePk'], gk=g['gamePk'], d=d, tm=tm, h=th['team'].get('abbreviation'), a=ta['team'].get('abbreviation'),
                          hs=th.get('score') if fin else None, as_=ta.get('score') if fin else None,
                          sp=None, tot=None, hml=None, aml=None, neu=False,
                          hp=str(hp['id']) if hp.get('id') else None, ap=str(ap['id']) if ap.get('id') else None))
        if g.get('gameType') in MLB_ROUND:
            sched[-1]['rd'] = MLB_ROUND[g['gameType']]
            if g.get('seriesGameNumber'):
                sched[-1]['rd'] += f" · Game {g['seriesGameNumber']}"
            if g.get('ifNecessary') == 'Y' and not fin:
                sched[-1]['rd'] += ' (if necessary)'
    if not HIST and not note and not any(g['hs'] is None and g['d'] >= today.isoformat() for g in sched):
        note = f'the {y} season is over · next season starts in the spring'
    first = day_index(sched)
    rest_days(sched)

    cache_name = f'mlb_box_{y}.json'
    cache = load_cache(cache_name)
    need = [str(g['id']) for g in sched if g['hs'] is not None and (str(g['id']) not in cache or '_error' in cache[str(g['id'])])]
    cache.update(parallel(mlb_box, need, workers=10))
    save_cache(cache_name, cache)

    team_games, pitchers = [], {}
    for g in sched:
        b = cache.get(str(g['id']))
        if g['hs'] is None or not b or '_error' in b:
            continue
        for side, t, o in (('home', g['h'], g['a']), ('away', g['a'], g['h'])):
            x = b[side]
            team_games.append(dict(w=g['w'], g=g['id'], t=t, o=o, **{k: x[k] for k in
                              ('h', 'bb', 'hbp', 'sf', 'so', 'pa', 'tb', 'hr', 'sp_ip', 'sp_er', 'bp_ip', 'bp_er')}))
            if x.get('sp'):
                pid = str(x['sp'])
                pitchers.setdefault(pid, []).append([g['w'], x['sp_ip'], x['sp_er']])
                if x.get('spn'):
                    pnames[pid] = x['spn']
                if side == 'home':
                    g['hp'] = pid
                else:
                    g['ap'] = pid
    finalize_games_gk(team_games, sched)
    tip = sum(r['sp_ip'] for r in team_games) or 1
    lg_era = round(sum(r['sp_er'] for r in team_games) * 9 / tip, 2)
    note_odds, remaining = odds_lines('baseball_mlb', sched, names, f'odds_mlb_{y}.json')
    sched.sort(key=lambda g: (g['d'], g['tm']))
    track_open('mlb', y, sched)
    meta = dict(sport='mlb', label='MLB', family='baseball', period='Day', start=first.isoformat(), seasonLabel=str(y),
                sigma=4.3, sigmaTot=4.5, btSkip=21, formN=10, hfa=0.15, trust=60, bye=0, rest='none', props=False,
                unit='runs', spreadName='Run line', edgeSteps=[0.25, 0.5, 0.75, 1], edgeDefault=0.5,
                hfaMax=0.5, hfaStep=0.05, byeMax=0, byeStep=0.05, lgEra=lg_era, note=note,
                source='MLB Stats API · lines from The Odds API', oddsNote=note_odds, oddsRemaining=remaining)
    return dict(meta=meta, season=y, teamGames=team_games, games=sched, players=[], pitchers=pitchers, pnames=pnames)


def finalize_games_gk(team_games, sched):
    have = {(r['g'], r['t']) for r in team_games}
    for g in sched:
        if g['hs'] is not None and not ((g['id'], g['h']) in have and (g['id'], g['a']) in have):
            g['hs'] = g['as_'] = None


# ---------------------------------------------------------------- College basketball (ESPN)
ESPN = 'https://site.api.espn.com/apis/site/v2/sports'
BBALL = {
    'cbb': dict(path='basketball/mens-college-basketball', groups='50', start=(11, 1), end=(3, 16), label='College basketball',
                short=False, hfa=3.0, sigma=11, sigmaTot=16, trust=70, rest='none', bye=0, byeMax=0, formN=5, btSkip=21,
                startsIn='November'),
    'nba': dict(path='basketball/nba', groups=None, start=(10, 1), end=(4, 20), label='NBA',
                short=True, hfa=2.0, sigma=12.5, sigmaTot=18, trust=80, rest='b2b', bye=1.5, byeMax=4, formN=8, btSkip=14,
                startsIn='October'),
}


def espn_line(o):
    """ESPN odds/pickcenter entry -> our line dict (sp: + means home favored)."""
    if not o:
        return {}
    line = {}
    sp = opt_float(o.get('spread'))
    hto, ato = o.get('homeTeamOdds') or {}, o.get('awayTeamOdds') or {}
    if sp is not None:
        sp = -sp  # ESPN spread is the home team's number (negative = home favored)
        if hto.get('favorite') and sp < 0 or ato.get('favorite') and sp > 0:
            sp = -sp
        line['sp'] = sp
    if opt_float(o.get('overUnder')) is not None:
        line['tot'] = float(o['overUnder'])
    if opt_int(hto.get('moneyLine')) is not None:
        line['hml'] = int(hto['moneyLine'])
    if opt_int(ato.get('moneyLine')) is not None:
        line['aml'] = int(ato['moneyLine'])
    return line


def bball_pos(abbr):
    a = (abbr or 'F').upper()
    return 'C' if a == 'C' else 'G' if a in ('G', 'PG', 'SG') else 'F'


def bball_box(path, eid):
    s = get_json(f'{ESPN}/{path}/summary?event={eid}')
    teams = {}
    for t in (s.get('boxscore') or {}).get('teams', []):
        st = {x.get('name'): x.get('displayValue') for x in t.get('statistics', [])}
        fg = st.get('fieldGoalsMade-fieldGoalsAttempted', '0-0')
        tp = st.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted', '0-0')
        ft = st.get('freeThrowsMade-freeThrowsAttempted', '0-0')
        tov = st.get('totalTurnovers', st.get('turnovers'))
        teams[str(t['team']['id'])] = dict(fgm=split_pair(fg, 0), fga=split_pair(fg, 1), tpm=split_pair(tp, 0),
                                           fta=split_pair(ft, 1), orb=num(st.get('offensiveRebounds')),
                                           drb=num(st.get('defensiveRebounds')), tov=num(tov))
    pc = s.get('pickcenter') or []
    pc = sorted(pc, key=lambda o: (o.get('provider') or {}).get('priority', 99))
    return dict(teams=teams, line=espn_line(pc[0]) if pc else {}, players=cbb_players(s))


def cbb_players(summary):
    """Player lines from an ESPN summary -> {team id: [[id, name, pos, min, pts, reb, ast, 3pm], ...]}."""
    out = {}
    for t in (summary.get('boxscore') or {}).get('players', []) or []:
        tid = str((t.get('team') or {}).get('id', ''))
        for block in t.get('statistics', []) or []:
            names = [str(n).upper() for n in (block.get('names') or block.get('labels') or [])]
            ix = {n: i for i, n in enumerate(names)}
            if 'PTS' not in ix:
                continue
            for a in block.get('athletes', []) or []:
                st = a.get('stats') or []
                if not st or a.get('didNotPlay'):
                    continue
                val = lambda k: st[ix[k]] if k in ix and ix[k] < len(st) else '0'
                mins = num(val('MIN'))
                if mins <= 0:
                    continue
                ath = a.get('athlete') or {}
                out.setdefault(tid, []).append([str(ath.get('id', '')), ath.get('shortName') or ath.get('displayName', ''),
                                                bball_pos((ath.get('position') or {}).get('abbreviation')),
                                                mins, num(val('PTS')), num(val('REB')), num(val('AST')), split_pair(val('3PT'), 0)])
    return out


def bball_day(path, groups, day):
    q = f'&groups={groups}' if groups else ''
    sb = get_json(f'{ESPN}/{path}/scoreboard?dates={day.strftime("%Y%m%d")}{q}&limit=400')
    out = []
    for e in sb.get('events', []):
        if (e.get('season') or {}).get('type') != 2:
            continue
        c = e['competitions'][0]
        comp = {x['homeAway']: x for x in c.get('competitors', [])}
        if 'home' not in comp or 'away' not in comp:
            continue
        done = (c.get('status') or e.get('status') or {}).get('type', {}).get('completed', False)
        odds = (c.get('odds') or [None])[0]
        out.append(dict(id=e['id'], date=e.get('date'), neu=bool(c.get('neutralSite')),
                        h=str(comp['home']['team']['id']), a=str(comp['away']['team']['id']),
                        hs=opt_int(comp['home'].get('score')) if done else None,
                        as_=opt_int(comp['away'].get('score')) if done else None,
                        line=espn_line(odds) if odds else {}))
    return out


def build_bball(key):
    cfg = BBALL[key]
    path = cfg['path']
    today = datetime.datetime.now(ET).date()
    season_days = lambda y: [datetime.date(y - 1, *cfg['start']) + datetime.timedelta(days=i)
                             for i in range((datetime.date(y, *cfg['end']) - datetime.date(y - 1, *cfg['start'])).days + 1)]
    y = YEAR if HIST else (today.year + 1 if today.month >= 8 else today.year)  # season named for the spring it ends in

    teams_raw = get_json(f'{ESPN}/{path}/teams?limit=500' + (f'&groups={cfg["groups"]}' if cfg['groups'] else ''))
    tlist = {str(t['team']['id']): t['team'] for lg in teams_raw.get('sports', [{}])[0].get('leagues', []) for t in lg.get('teams', [])}
    names = {k: (t.get('abbreviation') if cfg['short'] else (t.get('shortDisplayName') or t.get('location') or t.get('abbreviation'))) for k, t in tlist.items()}
    counts = {}
    for n in names.values():
        counts[n] = counts.get(n, 0) + 1
    for k, n in list(names.items()):
        if counts[n] > 1:
            names[k] = f"{n} ({tlist[k].get('abbreviation', k)})"

    def load_season(y, horizon):
        sb_name = f'{key}_scoreboard_{y}.json' if key != 'cbb' else f'cbb_scoreboard_{y}.json'
        sb = load_cache(sb_name)
        days = [d for d in season_days(y) if d <= today + datetime.timedelta(days=horizon)]
        need = [d for d in days if d.isoformat() not in sb or (d - today).days >= -3]
        if need:
            got = parallel(lambda d: bball_day(path, cfg['groups'], d), need, workers=8)
            for d, v in got.items():
                if isinstance(v, list):
                    sb[d.isoformat()] = v
            save_cache(sb_name, sb)
        return [e for v in sb.values() for e in v if e['h'] in names and e['a'] in names]

    note = None
    events = load_season(y, 21)
    if not HIST and not events:  # new season more than three weeks away: show last season
        y -= 1
        note = f"{y - 1}-{str(y)[2:]} season · the new season starts in {cfg['startsIn']}"
        events = load_season(y, 0)

    box_name = f'{key}_box_{y}.json'
    box = load_cache(box_name)
    need = [e['id'] for e in events if e['hs'] is not None and (e['id'] not in box or '_error' in box[e['id']])]
    if need:
        box.update(parallel(lambda eid: bball_box(path, eid), need, workers=12))
        save_cache(box_name, box)

    sched = []
    for e in events:
        d, tm = et_parts(e['date'])
        b = box.get(e['id']) or {}
        line = (b.get('line') if e['hs'] is not None else None) or e.get('line') or {}
        sched.append(dict(id=e['id'], d=d, tm=tm, h=names[e['h']], a=names[e['a']], hid=e['h'], aid=e['a'],
                          hs=e['hs'], as_=e['as_'], neu=e['neu'],
                          sp=line.get('sp'), tot=line.get('tot'), hml=line.get('hml'), aml=line.get('aml')))
    if not sched:
        raise RuntimeError(f"No {y} {cfg['label']} games found.")
    first = day_index(sched)
    rest_days(sched)
    team_games = []
    for g in sched:
        b = box.get(g['id'])
        if g['hs'] is None or not b or '_error' in b:
            continue
        tb = b.get('teams', {})
        if g['hid'] not in tb or g['aid'] not in tb:
            continue
        for me, op, t, o, pts in ((g['hid'], g['aid'], g['h'], g['a'], g['hs']), (g['aid'], g['hid'], g['a'], g['h'], g['as_'])):
            team_games.append(dict(w=g['w'], g=g['id'], t=t, o=o, pts=float(pts), **tb[me]))
    players = []
    if not note and not HIST:  # player props only for the season in progress
        for g in sched:
            b = box.get(g['id']) or {}
            pl = b.get('players') or {}
            for tid, team, opp in ((g['hid'], g['h'], g['a']), (g['aid'], g['a'], g['h'])):
                for pid, nm, pos, mins, pts, reb, ast, tpm in pl.get(tid, []):
                    if mins >= 10 and g['hs'] is not None:
                        players.append(dict(id=pid, n=nm, p=bball_pos(pos), t=team, o=opp, w=g['w'],
                                            min=mins, pts=pts, reb=reb, ast=ast, tpm=tpm))
    injuries = espn_injuries(path, lambda t: names.get(str(t.get('id'))))
    for g in sched:
        g['gk'] = g['id']
        g.pop('hid'); g.pop('aid')
    finalize_games_gk(team_games, sched)
    sched.sort(key=lambda g: (g['d'], g['tm']))
    track_open(key, y, sched)
    meta = dict(sport=key, label=cfg['label'], family='basketball', period='Day', start=first.isoformat(),
                seasonLabel=f'{y - 1}-{str(y)[2:]}', sigma=cfg['sigma'], sigmaTot=cfg['sigmaTot'], btSkip=cfg['btSkip'],
                formN=cfg['formN'], hfa=cfg['hfa'], trust=cfg['trust'], bye=cfg['bye'], rest=cfg['rest'],
                props=bool(players) or (not note and not HIST), unit='pts', spreadName='Spread', edgeSteps=[1, 2, 3, 4, 5],
                edgeDefault=2, hfaMax=5, hfaStep=0.5, byeMax=cfg['byeMax'], byeStep=0.5, note=note,
                source='ESPN' + (' · injuries from ESPN' if key == 'nba' else ''))
    return dict(meta=meta, season=y, teamGames=team_games, games=sched, players=players, injuries=injuries)


def build_cbb(_season):
    return build_bball('cbb')


def build_nba(_season):
    return build_bball('nba')


# ---------------------------------------------------------------- UFC (ESPN + The Odds API)
UFC_FIRST_YEAR = 2019
ESPN_UFC = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc'
CORE_MMA = 'https://sports.core.api.espn.com/v2/sports/mma'
UFC_STAT_KEYS = ['kd', 'ssl', 'ssa', 'tdl', 'tda', 'sub', 'ctrl']


def ufc_year(year):
    sb = get_json(f'{ESPN_UFC}/scoreboard?dates={year}&limit=200')
    out = []
    for e in sb.get('events', []):
        name = e.get('name') or ''
        if 'Contender' in name or 'Road to UFC' in name:
            continue
        for c in e.get('competitions', []):
            comps = sorted(c.get('competitors', []), key=lambda x: x.get('order', 9))
            if len(comps) != 2:
                continue
            st = c.get('status') or {}
            done = (st.get('type') or {}).get('completed', False)
            a, b = comps
            win = 'a' if a.get('winner') else 'b' if b.get('winner') else ('draw' if done else None)
            period, clock = st.get('period') or 0, st.get('clock') or 0
            dec = bool(a.get('linescores')) and done
            sched = ((c.get('format') or {}).get('regulation') or {}).get('periods') or (5 if period == 5 else 3)
            out.append(dict(id=c['id'], eid=e['id'], ev=name, date=c.get('date') or e.get('date'),
                            wc=(c.get('type') or {}).get('abbreviation', ''), sched=sched,
                            a=str(a['id']), b=str(b['id']), an=(a.get('athlete') or {}).get('displayName', ''),
                            bn=(b.get('athlete') or {}).get('displayName', ''),
                            ar=((a.get('records') or [{}])[0]).get('summary', ''), br=((b.get('records') or [{}])[0]).get('summary', ''),
                            done=done, win=win, rnd=period if done else None,
                            sec=int((period - 1) * 300 + clock) if done and period else None, dec=dec))
    return out


def ufc_fight_stats(fight):
    """Per-fighter stats for one finished fight from ESPN's core feed."""
    out = {}
    for side in ('a', 'b'):
        aid = fight[side]
        d = get_json(f"{CORE_MMA}/leagues/ufc/events/{fight['eid']}/competitions/{fight['id']}/competitors/{aid}/statistics")
        vals = {}
        for cat in ((d.get('splits') or {}).get('categories') or []):
            for x in cat.get('stats', []):
                vals[x.get('name', '')] = x.get('value') or 0
        find = lambda *words: next((v for k, v in vals.items() if all(w in k.lower() for w in words)), 0)
        out[side] = [vals.get('knockDowns', 0), vals.get('sigStrikesLanded', 0), vals.get('sigStrikesAttempted', 0),
                     find('takedown', 'landed') or find('takedowns', 'success'), find('takedown', 'attempt'),
                     find('submission', 'attempt') or find('submission'), find('control')]
    return out


def ufc_fighter(aid):
    a = get_json(f'{CORE_MMA}/athletes/{aid}')
    dob = (a.get('dateOfBirth') or '')[:10]
    return dict(n=a.get('displayName', ''), dob=dob, h=a.get('height'), r=a.get('reach'),
                st=(a.get('stance') or {}).get('text', ''), wc=(a.get('weightClass') or {}).get('text', ''))


def build_ufc(_season):
    today = datetime.datetime.now(ET).date()
    fights = []
    for year in range(UFC_FIRST_YEAR, today.year + 1):
        name = f'ufc_events_{year}.json'
        cached = load_cache(name)
        if year < today.year and cached.get('fights'):  # past years never change
            fights += cached['fights']; continue
        got = ufc_year(year)
        save_cache(name, {'fights': got})
        fights += got
    if today.month == 12:
        fights += ufc_year(today.year + 1)
    seen, uniq = set(), []
    for f in fights:
        if f['id'] not in seen:
            seen.add(f['id']); uniq.append(f)
    fights = uniq

    stats = load_cache('ufc_fight_stats.json')
    need = [f for f in fights if f['done'] and f['win'] in ('a', 'b', 'draw') and (f['id'] not in stats or '_error' in stats[f['id']])]
    if need:
        fmap = {f['id']: f for f in need}
        stats.update(parallel(lambda fid: ufc_fight_stats(fmap[fid]), list(fmap), workers=12))
        save_cache('ufc_fight_stats.json', stats)

    fighters = load_cache('ufc_fighters.json')
    ids = {f[s] for f in fights for s in ('a', 'b')}
    need = [i for i in ids if i not in fighters or '_error' in fighters[i]]
    if need:
        fighters.update(parallel(ufc_fighter, need, workers=12))
        save_cache('ufc_fighters.json', fighters)

    out = []
    for f in sorted(fights, key=lambda x: x['date'] or ''):
        d, tm = et_parts(f['date'])
        if not f['done'] and (not d or d < (today - datetime.timedelta(days=1)).isoformat() or 'TBA' in (f['an'] + f['bn'])):
            continue  # cancelled bouts and placeholder opponents
        st = stats.get(f['id']) or {}
        row = dict(id=f['id'], eid=f['eid'], ev=f['ev'], d=d, tm=tm, wc=f['wc'], sched=f['sched'], a=f['a'], b=f['b'],
                   an=f['an'], bn=f['bn'], ar=f['ar'], br=f['br'], win=f['win'] if f['done'] else None,
                   rnd=f['rnd'], sec=f['sec'], dec=f['dec'])
        if 'a' in st and 'b' in st and '_error' not in st:
            row['sa'], row['sb'] = st['a'], st['b']
        out.append(row)

    # Moneylines for upcoming fights (The Odds API), last known line kept per fight.
    names = {}
    for f in out:
        names[norm_name(f['an'])] = f['a']; names[norm_name(f['bn'])] = f['b']
    odds = load_cache('odds_ufc.json')
    key = os.environ.get('ODDS_API_KEY')
    soon = [f for f in out if f['win'] is None and f['d'] and 0 <= (datetime.date.fromisoformat(f['d']) - today).days <= 8]
    upcoming = [f for f in out if f['win'] is None and f['d'] and (datetime.date.fromisoformat(f['d']) - today).days >= 0]
    note = None
    if not key:
        note = 'ODDS_API_KEY not set'
    elif not soon:
        note = 'no fights in the next 8 days'
    elif odds.get('_remaining') is not None and odds['_remaining'] < 40:
        note = 'saving odds requests'
    else:
        # One request returns every posted MMA fight from all US books (same cost as a few books).
        url = (f'https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds?apiKey={key}&regions=us&markets=h2h'
               f'&oddsFormat=american')
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            events = json.loads(r.read().decode('utf-8')); rem = r.headers.get('x-requests-remaining')
        odds['_remaining'] = int(float(rem)) if rem else None
        now = datetime.datetime.now(datetime.timezone.utc)
        by_pair = {frozenset((f['a'], f['b'])): f for f in upcoming}
        last = lambda n: norm_name((n or '').split()[-1] if n else '')
        by_last = {}
        for f in upcoming:  # fallback when the two feeds spell a name differently
            by_last.setdefault(frozenset((last(f['an']), last(f['bn']))), []).append(f)
        for ev in events:
            start = datetime.datetime.fromisoformat((ev.get('commence_time') or '1970-01-01T00:00:00Z').replace('Z', '+00:00'))
            if start <= now:
                continue
            x, y = names.get(norm_name(ev.get('home_team'))), names.get(norm_name(ev.get('away_team')))
            f = by_pair.get(frozenset((x, y))) if x and y else None
            if not f:
                d_ev = start.astimezone(ET).date()
                cands = [c for c in by_last.get(frozenset((last(ev.get('home_team')), last(ev.get('away_team')))), [])
                         if abs((datetime.date.fromisoformat(c['d']) - d_ev).days) <= 2]
                if len(cands) == 1:
                    f = cands[0]
                    names[norm_name(ev.get('home_team'))] = f['a'] if last(f['an']) == last(ev.get('home_team')) else f['b']
                    names[norm_name(ev.get('away_team'))] = f['b'] if names[norm_name(ev.get('home_team'))] == f['a'] else f['a']
            if not f:
                continue
            for bk in sorted(ev.get('bookmakers', []), key=lambda b: ODDS_PREF.index(b['key']) if b['key'] in ODDS_PREF else 99):
                m = next((m for m in bk.get('markets', []) if m['key'] == 'h2h'), None)
                if not m:
                    continue
                pr = {names.get(norm_name(o.get('name'))): int(o['price']) for o in m.get('outcomes', [])}
                if f['a'] in pr and f['b'] in pr:
                    old = odds.get(f['id'])
                    odds[f['id']] = {'aml': pr[f['a']], 'bml': pr[f['b']], 'open': (old or {}).get('open') or [pr[f['a']], pr[f['b']]]}
                    break
    save_cache('odds_ufc.json', odds)
    for f in out:
        o = odds.get(f['id'])
        if o:
            f['aml'], f['bml'], f['oml'] = o['aml'], o['bml'], o.get('open')

    used = {f[s] for f in out for s in ('a', 'b')}
    meta = dict(sport='ufc', label='UFC', family='fight', period='Event', unit='', source='ESPN · lines from The Odds API',
                oddsNote=note, oddsRemaining=odds.get('_remaining'), firstYear=UFC_FIRST_YEAR, props=False)
    games = [dict(id=f['id'], d=f['d'], w=0, h=f['b'], a=f['a'], hs=None if f['win'] is None else 1, as_=0) for f in out]
    return dict(meta=meta, season=today.year, fights=out, fighters={k: v for k, v in fighters.items() if k in used and '_error' not in v},
                games=games, teamGames=[])


# ---------------------------------------------------------------- NFL (nflverse)
NFL_GAMES = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
NFL_TEAM = 'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv'
NFL_PLAYER = 'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv'


def build_nfl(season):
    global _NFL_GAMES
    if '_NFL_GAMES' not in globals() or _NFL_GAMES is None:
        _NFL_GAMES = load_csv(NFL_GAMES, 'GAMES_CSV')
    games = [g for g in _NFL_GAMES if g['season'] == str(season) and g['game_type'] == 'REG']
    if not games:
        raise RuntimeError(f'No {season} NFL schedule found yet.')
    try:
        team_raw = load_csv(NFL_TEAM.format(season=season), '' if HIST else 'TEAM_CSV')
        player_raw = [] if HIST else load_csv(NFL_PLAYER.format(season=season), 'PLAYER_CSV')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        team_raw, player_raw = [], []  # season hasn't started

    team_games = []
    for x in team_raw:
        if x.get('season_type') != 'REG':
            continue
        plays = num(x['attempts']) + num(x['sacks_suffered']) + num(x['carries'])
        team_games.append(dict(
            w=int(x['week']), t=x['team'], o=x['opponent_team'],
            py=num(x['passing_yards']), ry=num(x['rushing_yards']), pl=plays,
            epa=round(num(x['passing_epa']) + num(x['rushing_epa']), 2),
            to=num(x['passing_interceptions']) + num(x['fumbles_lost_total']),
            sk=num(x['def_sacks']), ss=num(x['sacks_suffered']),
            pen=num(x['penalty_yards'])))

    sched = [dict(
        id=g['game_id'], w=int(g['week']), d=g['gameday'], tm=g['gametime'],
        a=g['away_team'], h=g['home_team'],
        as_=opt_int(g['away_score']), hs=opt_int(g['home_score']),
        sp=opt_float(g['spread_line']), tot=opt_float(g['total_line']),
        aml=opt_int(g['away_moneyline']), hml=opt_int(g['home_moneyline']),
        neu=g['location'] == 'Neutral', ar=num(g['away_rest']), hr=num(g['home_rest']), roof=g.get('roof', '')) for g in games]
    finalize_games(team_games, sched)
    add_weather(sched, lambda g: NFL_STADIUMS.get(g['h']) if not g['neu'] and g.get('roof') in ('outdoors', 'open') else None)
    track_open('nfl', season, sched)
    espn_abbr = {'WSH': 'WAS', 'LAR': 'LA'}
    nfl_names = {'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL', 'Buffalo Bills': 'BUF',
                 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI', 'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE',
                 'Dallas Cowboys': 'DAL', 'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
                 'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX', 'Kansas City Chiefs': 'KC',
                 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC', 'Los Angeles Rams': 'LA', 'Miami Dolphins': 'MIA',
                 'Minnesota Vikings': 'MIN', 'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
                 'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT', 'San Francisco 49ers': 'SF',
                 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB', 'Tennessee Titans': 'TEN', 'Washington Commanders': 'WAS'}
    injuries = espn_injuries('football/nfl', lambda t: nfl_names.get(t.get('displayName')))

    players = []
    for x in player_raw:
        if x['position'] not in ('QB', 'RB', 'WR', 'TE') or x['season_type'] != 'REG':
            continue
        r = dict(id=x['player_id'], n=x['player_display_name'], p=x['position'], t=x['team'],
                 o=x['opponent_team'], w=int(x['week']),
                 pa=num(x['attempts']), py=num(x['passing_yards']), ptd=num(x['passing_tds']),
                 int_=num(x['passing_interceptions']),
                 ru=num(x['carries']), ry=num(x['rushing_yards']), rtd=num(x['rushing_tds']),
                 tg=num(x['targets']), rec=num(x['receptions']), rcy=num(x['receiving_yards']),
                 rctd=num(x['receiving_tds']))
        if r['pa'] + r['ru'] + r['tg'] > 0:
            players.append(r)

    meta = dict(sport='nfl', label='NFL', family='football', period='Week', sigma=13.5, sigmaTot=13, btSkip=1,
                hfa=1.5, trust=60, bye=1.0, rest='bye', formN=3, props=True, weather=True, qbOut=True,
                source='nflverse · injuries from ESPN · weather from Open-Meteo', **FOOTBALL_UI)
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=players, injuries=injuries)


# ---------------------------------------------------------------- College football (CollegeFootballData.com)
CFBD = 'https://api.collegefootballdata.com'


def cfbd(path, **params):
    key = os.environ.get('CFBD_API_KEY')
    if not key:
        raise RuntimeError('CFBD_API_KEY is not set (add it as a GitHub repository secret).')
    url = CFBD + path + '?' + urllib.parse.urlencode(params)
    try:
        return json.loads(http_get(url, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:200]
        raise RuntimeError(f'CFBD {path} returned HTTP {e.code}: {body}') from None


def stat_map(team):
    out = {}
    for s in pick(team, 'stats', default=[]) or []:
        out[pick(s, 'category', default='')] = pick(s, 'stat', default='')
    return out


def split_pair(v, i):
    """'20-31' or '20/31' -> part i."""
    if v in (None, ''):
        return 0.0
    for sep in ('-', '/'):
        if sep in str(v):
            try:
                return float(str(v).split(sep)[i])
            except (ValueError, IndexError):
                return 0.0
    return num(v) if i == 0 else 0.0


def et_parts(iso):
    if not iso:
        return '', ''
    dt = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(ET)
    return dt.date().isoformat(), dt.strftime('%H:%M')


def build_cfb(season):
    os.makedirs(CACHE_DIR, exist_ok=True)
    games_raw = cfbd('/games', year=season, seasonType='regular', classification='fbs')
    fbs = lambda g, side: (pick(g, side + 'Classification', side + '_classification', default='fbs') or 'fbs').lower() == 'fbs'
    games_raw = [g for g in games_raw if fbs(g, 'home') and fbs(g, 'away')]
    if not games_raw:
        raise RuntimeError(f'No {season} FBS schedule found.')

    lines_raw = cfbd('/lines', year=season, seasonType='regular')
    ppa_raw = cfbd('/ppa/games', year=season, seasonType='regular', excludeGarbageTime='true')

    # Box scores: completed weeks never change, so keep them in a committed cache and only
    # ask the API for weeks that aren't cached yet (keeps us well inside the free tier).
    cache_path = os.path.join(CACHE_DIR, f'cfb_box_{season}.json')
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    finished = lambda g: pick(g, 'homePoints', 'home_points') is not None
    weeks_done = sorted({pick(g, 'week') for g in games_raw if finished(g)})
    last_done = max(weeks_done) if weeks_done else None
    for w in weeks_done:
        if str(w) in cache and w != last_done:
            continue
        cache[str(w)] = cfbd('/games/teams', year=season, week=w, seasonType='regular', classification='fbs')
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, separators=(',', ':'))

    ppa = {}
    for r in ppa_raw:
        off = pick(r, 'offense', default={}) or {}
        ppa[(str(pick(r, 'gameId', 'game_id')), pick(r, 'team'))] = opt_float(pick(off, 'overall'))

    box = {}  # (gameId, team) -> stats
    for rows in cache.values():
        for g in rows:
            gid = str(pick(g, 'id'))
            for t in pick(g, 'teams', default=[]) or []:
                box[(gid, pick(t, 'team', 'school'))] = stat_map(t)

    team_games = []
    for g in games_raw:
        gid = str(pick(g, 'id'))
        h, a = pick(g, 'homeTeam', 'home_team'), pick(g, 'awayTeam', 'away_team')
        if not finished(g) or (gid, h) not in box or (gid, a) not in box:
            continue
        for me, op in ((h, a), (a, h)):
            s, o = box[(gid, me)], box[(gid, op)]
            plays = num(s.get('rushingAttempts')) + split_pair(s.get('completionAttempts'), 1)
            e = ppa.get((gid, me))
            to = num(s.get('turnovers')) if 'turnovers' in s else num(s.get('fumblesLost')) + num(s.get('interceptions'))
            team_games.append(dict(
                w=pick(g, 'week'), t=me, o=op, py=num(s.get('netPassingYards')), ry=num(s.get('rushingYards')),
                pl=plays or 1, epa=round((e or 0) * plays, 2), to=to,
                sk=num(s.get('sacks')), ss=num(o.get('sacks')),
                pen=split_pair(s.get('totalPenaltiesYards'), 1)))

    # Best available line per game: prefer a major book, then the consensus.
    pref = ['DraftKings', 'ESPN Bet', 'FanDuel', 'Bovada', 'consensus', 'teamrankings', 'numberfire']
    best = {}
    for row in lines_raw:
        ls = [l for l in (pick(row, 'lines', default=[]) or [])
              if pick(l, 'spread') is not None or pick(l, 'overUnder', 'over_under') is not None]
        if ls:
            ls.sort(key=lambda l: pref.index(pick(l, 'provider')) if pick(l, 'provider') in pref else 99)
            best[str(pick(row, 'id'))] = ls[0]

    last_date, sched, true_opens = {}, [], {}
    for g in sorted(games_raw, key=lambda g: pick(g, 'startDate', 'start_date') or ''):
        gid = str(pick(g, 'id'))
        h, a = pick(g, 'homeTeam', 'home_team'), pick(g, 'awayTeam', 'away_team')
        d, tm = et_parts(pick(g, 'startDate', 'start_date'))
        if pick(g, 'startTimeTBD', 'start_time_tbd'):
            tm = ''
        day = datetime.date.fromisoformat(d) if d else None
        rest = lambda t: float((day - last_date[t]).days) if day and t in last_date else 7.0
        hr, ar = rest(h), rest(a)
        if day:
            last_date[h] = last_date[a] = day
        l = best.get(gid, {})
        spread = opt_float(pick(l, 'spread'))
        sched.append(dict(
            id=gid, w=pick(g, 'week'), d=d, tm=tm, a=a, h=h,
            as_=opt_int(pick(g, 'awayPoints', 'away_points')), hs=opt_int(pick(g, 'homePoints', 'home_points')),
            sp=None if spread is None else -spread,  # CFBD: negative = home favored; ours: + = home favored
            tot=opt_float(pick(l, 'overUnder', 'over_under')),
            aml=opt_int(pick(l, 'awayMoneyline', 'away_moneyline')), hml=opt_int(pick(l, 'homeMoneyline', 'home_moneyline')),
            neu=bool(pick(g, 'neutralSite', 'neutral_site', default=False)), ar=ar, hr=hr,
            hc=pick(g, 'homeConference', 'home_conference', default=''),
            ac=pick(g, 'awayConference', 'away_conference', default=''),
            hid=str(pick(g, 'homeId', 'home_id', default='')), aid=str(pick(g, 'awayId', 'away_id', default='')),
            vid=str(pick(g, 'venueId', 'venue_id', default=''))))
        if l and (pick(l, 'spreadOpen', 'spread_open') is not None or pick(l, 'overUnderOpen', 'over_under_open') is not None):
            so = opt_float(pick(l, 'spreadOpen', 'spread_open'))
            true_opens[gid] = dict(sp=None if so is None else -so, tot=opt_float(pick(l, 'overUnderOpen', 'over_under_open')))
    sched.sort(key=lambda x: (x['w'], x['d'], x['tm']))
    finalize_games(team_games, sched)
    track_open('cfb', season, sched, true_open=lambda g: true_opens.get(str(g['id'])))

    # Stadiums (cached; they rarely change) for weather at kickoff.
    venues = load_cache('cfb_venues.json')
    if not venues and not HIST:
        for v in cfbd('/venues'):
            if v.get('latitude') is not None:
                venues[str(v['id'])] = [round(v['latitude'], 4), round(v['longitude'], 4), bool(v.get('dome'))]
        save_cache('cfb_venues.json', venues)
    add_weather(sched, lambda g: (venues[g['vid']][0], venues[g['vid']][1]) if g.get('vid') in venues and not venues[g['vid']][2] else None)

    # Preseason ratings: last season's final SP+, pulled 40% back toward average.
    priors = {}
    try:
        for r in cfbd('/ratings/sp', year=season - 1):
            if r.get('team') and r.get('rating') is not None and r['team'] != 'nationalAverages':
                priors[r['team']] = round(0.6 * float(r['rating']), 1)
    except Exception:
        priors = {}
    names_by_id = {}
    for g in sched:
        names_by_id[g['hid']] = g['h']; names_by_id[g['aid']] = g['a']
    injuries = espn_injuries('football/college-football', lambda t: names_by_id.get(str(t.get('id'))))
    for g in sched:
        g.pop('vid', None)

    meta = dict(sport='cfb', label='College football', family='football', period='Week', sigma=16, sigmaTot=15, btSkip=2,
                hfa=2.5, trust=70, bye=1.0, rest='bye', formN=3, props=False, weather=True,
                priors=priors, priorGames=3, priorLabel=f'{season - 1} SP+',
                source='CollegeFootballData.com · injuries from ESPN · weather from Open-Meteo', **FOOTBALL_UI)
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=[], injuries=injuries)


# ---------------------------------------------------------------- main
# ---------------------------------------------------------------- Golf (ESPN, PGA Tour)
GOLF_FIRST_YEAR = 2025
GOLF_SB = 'https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard'


def golf_num(v):
    if isinstance(v, dict):
        v = v.get('value', v.get('displayValue'))
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v).strip()
    if v.upper() == 'E':
        return 0.0
    try:
        return float(v.replace('+', ''))
    except ValueError:
        return None


def golf_calendar(year):
    """Every PGA Tour event in a calendar year: [{id, name, start, end}]."""
    out = {}
    for url in (f'{GOLF_SB}?dates={year}', f'{GOLF_SB}?dates={year}0101-{year}1231'):
        try:
            data = get_json(url)
        except Exception as e:
            print(f'  golf calendar {year}: {e}'); continue
        for lg in data.get('leagues', []):
            for c in lg.get('calendar', []) or []:
                if not isinstance(c, dict):
                    continue
                eid = str(c.get('id') or (c.get('event') or {}).get('id') or '')
                if not eid:
                    ref = (c.get('event') or {}).get('$ref', '')
                    eid = ref.rstrip('/').split('/')[-1].split('?')[0] if ref else ''
                st = (c.get('startDate') or '')[:10]
                if eid and st.startswith(str(year)):
                    out[eid] = dict(id=eid, name=c.get('label') or c.get('name') or '', start=st, end=(c.get('endDate') or '')[:10])
        for ev in data.get('events', []):
            st = (ev.get('date') or '')[:10]
            if st.startswith(str(year)):
                out.setdefault(str(ev['id']), dict(id=str(ev['id']), name=ev.get('name', ''), start=st, end=(ev.get('endDate') or '')[:10]))
        if out:
            break
    return sorted(out.values(), key=lambda e: e['start'])


def golf_event(ev):
    """One tournament's leaderboard: players with their completed round scores."""
    urls = [f'https://site.web.api.espn.com/apis/site/v2/sports/golf/leaderboard?league=pga&event={ev["id"]}',
            f'{GOLF_SB}?event={ev["id"]}']
    if ev.get('start'):
        urls.append(f'{GOLF_SB}?dates={ev["start"].replace("-", "")}')
    last = None
    for url in urls:
        try:
            data = get_json(url)
        except Exception as e:
            last = e; continue
        evs = data.get('events') or []
        e = next((x for x in evs if str(x.get('id')) == ev['id']), evs[0] if len(evs) == 1 else None)
        if not e:
            continue
        comp = (e.get('competitions') or [{}])[0]
        state = ((e.get('status') or comp.get('status') or {}).get('type') or {}).get('state', 'pre')
        players, names = [], {}
        for c in comp.get('competitors', []) or []:
            a = c.get('athlete') or {}
            pid = str(a.get('id') or c.get('id') or '')
            if not pid:
                continue
            names[pid] = a.get('displayName') or a.get('shortName') or pid
            rounds, partial = [], None
            for ls in sorted(c.get('linescores', []) or [], key=lambda x: x.get('period', 0)):
                per, v = ls.get('period', 0), golf_num(ls.get('value'))
                if not (1 <= per <= 4) or v is None:
                    continue
                holes = len(ls.get('linescores') or [])
                if v >= 55 and (holes in (0, 18)):
                    rounds.append(int(round(v)))
                else:
                    partial = per
            st = c.get('status') or {}
            pos = ((st.get('position') or {}).get('displayName')) or c.get('position') or ''
            thru = st.get('thru') if isinstance(st.get('thru'), (int, float)) else None
            players.append(dict(id=pid, r=rounds, tp=golf_num(c.get('score')), pos=str(pos or ''), thru=thru, ord=c.get('order') or c.get('sortOrder')))
        if not players:
            continue
        venue = (comp.get('venue') or {}).get('fullName') or ((e.get('courses') or [{}])[0].get('name') if e.get('courses') else '')
        return dict(state=state, players=players, names=names, course=venue or '', name=e.get('name') or ev.get('name'),
                    start=(e.get('date') or ev.get('start') or '')[:10], end=(e.get('endDate') or ev.get('end') or '')[:10])
    raise RuntimeError(f'no leaderboard for {ev["id"]}: {last}')


def build_golf(_season):
    today = datetime.datetime.now(ET).date()
    cache = load_cache('golf_events.json')
    cal = []
    for y in range(GOLF_FIRST_YEAR, today.year + 1):
        cal += golf_calendar(y)
    if not cal:
        raise RuntimeError('golf calendar empty')
    soon = (today + datetime.timedelta(days=10)).isoformat()
    want = [e for e in cal if e['start'] <= soon]
    need = [e for e in want if cache.get(e['id'], {}).get('state') != 'post' or e['start'] >= (today - datetime.timedelta(days=6)).isoformat()]
    byid = {e['id']: e for e in need}
    got = parallel(lambda eid: golf_event(byid[eid]), list(byid), workers=6) if need else {}
    errs, first_err = 0, None
    for eid, r in got.items():
        if isinstance(r, dict) and r.get('players'):
            cache[eid] = r
        else:
            errs += 1; first_err = first_err or (r.get('_error') if isinstance(r, dict) else str(r))
    save_cache('golf_events.json', cache)
    names, events = {}, []
    for e in want:
        x = cache.get(e['id'])
        if not x:
            continue
        names.update(x.get('names', {}))
        # par: completed players' strokes minus their score to par
        pars = [sum(p['r']) - p['tp'] for p in x['players'] if p.get('tp') is not None and len(p['r']) == 4]
        par = round(sorted(pars)[len(pars) // 2] / 4) if pars else 72
        events.append(dict(id=e['id'], name=x.get('name') or e['name'], d=x.get('start') or e['start'], end=x.get('end') or e['end'],
                           course=x.get('course', ''), state=x['state'], par=par,
                           p=[[p['id'], p['r'], p['pos'], p['tp'], p['thru']] for p in x['players']]))
    upcoming = [e for e in cal if e['start'] > soon][:3]
    events.sort(key=lambda e: e['d'])
    games = [dict(id=e['id'], d=e['d'], hs=0 if e['state'] == 'post' else None) for e in events] + \
            [dict(id=e['id'], d=e['start'], hs=None) for e in upcoming]
    meta = dict(sport='golf', label='Golf', family='golf', tour='PGA Tour', firstYear=GOLF_FIRST_YEAR, source='ESPN',
                debug=dict(calendar=len(cal), fetched=len(need), errors=errs, firstError=first_err, cached=len(cache)))
    return dict(meta=meta, season=today.year, events=events, names=names, schedule=upcoming, games=games, teamGames=[])


SPORTS = {'nfl': build_nfl, 'cfb': build_cfb, 'nhl': build_nhl, 'cbb': build_cbb, 'nba': build_nba, 'mlb': build_mlb, 'ufc': build_ufc, 'golf': build_golf}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default=','.join(SPORTS))
    ap.add_argument('--season', type=int, default=default_season())
    args = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)

    status_path = os.path.join(DATA_DIR, 'status.json')
    status = {}
    if os.path.exists(status_path):
        with open(status_path, encoding='utf-8') as f:
            status = json.load(f)

    for sport in [s for s in args.only.split(',') if s in SPORTS]:
        try:
            data = SPORTS[sport](args.season)
            changed = write_if_changed(os.path.join(DATA_DIR, sport + '.json'), data)
            done = [g for g in data['games'] if g['hs'] is not None]
            today = datetime.datetime.now(ET).date().isoformat()
            nxt = min((g['d'] for g in data['games'] if g['hs'] is None and g.get('d') and g['d'] >= today), default=None)
            status[sport] = dict(ok=True, season=data['season'], finals=len(done), next=nxt,
                                 through=max((g['d'] for g in done), default=''),
                                 upcoming=len(data['games']) - len(done), rows=len(data.get('teamGames', [])))
            print(f"{sport}: {'updated' if changed else 'no change'} - {status[sport]}")
        except Exception as e:  # keep going: one feed failing shouldn't stop the others
            status[sport] = dict(ok=False, error=f'{type(e).__name__}: {e}',
                                 trace=traceback.format_exc().splitlines()[-6:])
            print(f'{sport}: FAILED - {e}', file=sys.stderr)

    build_history(status, args)
    add_priors(status, args)
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=1, sort_keys=True)


HIST_DIR = os.path.join(DATA_DIR, 'history')

# How each sport carries last season into the new one: margin cap (blowouts), how far to pull
# ratings back toward average, and how many games' worth of weight the prior starts with.
PRIOR_CFG = {'nfl': (28, 0.5, 3), 'nhl': (5, 0.5, 10), 'mlb': (8, 0.5, 25), 'cbb': (30, 0.4, 6), 'nba': (30, 0.35, 10)}


def final_ratings(data, cap, regress):
    """Opponent-adjusted margin over a full season (simple rating system), pulled toward average."""
    hfa = data['meta'].get('hfa', 0)
    margins, opps = {}, {}
    for g in data['games']:
        if g['hs'] is None:
            continue
        m = max(-cap, min(cap, g['hs'] - g['as_'])) - (0 if g.get('neu') else hfa)
        for t, o, v in ((g['h'], g['a'], m), (g['a'], g['h'], -m)):
            margins.setdefault(t, []).append(v); opps.setdefault(t, []).append(o)
    r = {t: sum(v) / len(v) for t, v in margins.items()}
    for _ in range(60):
        nx = {t: sum(margins[t]) / len(margins[t]) + sum(r[o] for o in opps[t]) / len(opps[t]) for t in r}
        mu = sum(nx.values()) / len(nx)
        r = {t: 0.5 * r[t] + 0.5 * (nx[t] - mu) for t in r}
    pts = [g['hs'] + g['as_'] for g in data['games'] if g['hs'] is not None]
    lg = round(sum(pts) / len(pts) / 2, 2) if pts else None
    return {t: round(v * (1 - regress), 2) for t, v in r.items()}, lg


def season_label(sport, y):
    return f'{y - 1}-{str(y)[2:]}' if sport in ('cbb', 'nba') else f'{y}-{str(y + 1)[2:]}' if sport == 'nhl' else str(y)


def add_priors(status, args):
    """Give every season (current and past) last season's final ratings as a starting point,
    so the model can make picks before a season's first games. College football keeps SP+."""
    for sport in [s for s in args.only.split(',') if s in PRIOR_CFG]:
        if not (status.get(sport) or {}).get('ok'):
            continue
        cap, regress, games = PRIOR_CFG[sport]
        files = {}
        for f in os.listdir(HIST_DIR) if os.path.isdir(HIST_DIR) else []:
            if f.startswith(sport + '_') and f.endswith('.json'):
                files[int(f[len(sport) + 1:-5])] = os.path.join(HIST_DIR, f)
        cur_path = os.path.join(DATA_DIR, sport + '.json')
        targets = dict(files); targets[status[sport]['season']] = cur_path
        for y, path in sorted(targets.items()):
            prev = files.get(y - 1)
            if not prev:
                continue
            with open(prev, encoding='utf-8') as f:
                pr, lg = final_ratings(json.load(f), cap, regress)
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            meta = data['meta']
            new = dict(meta, priors=pr, priorGames=games, priorLabel=f'{season_label(sport, y - 1)} final ratings', lgPts=lg)
            if new == meta:
                continue
            data['meta'] = new
            if path == cur_path:
                write_if_changed(path, {k: v for k, v in data.items() if k != 'updated'})
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, separators=(',', ':'))


def history_seasons(sport, cur):
    """Past seasons the trainer learns from, relative to the season currently on the site."""
    back = {'nfl': 5, 'cfb': 3, 'nhl': 3, 'mlb': 2, 'cbb': 2, 'nba': 3}.get(sport, 0)
    return [cur - i for i in range(back, 0, -1)]


def build_history(status, args):
    """Build each past season once; they never change, so later runs skip them."""
    global HIST, YEAR
    os.makedirs(HIST_DIR, exist_ok=True)
    for sport in [s for s in args.only.split(',') if s in SPORTS]:
        if not (status.get(sport) or {}).get('ok'):
            continue
        done, failed = [], {}
        for season in history_seasons(sport, status[sport]['season']):
            path = os.path.join(HIST_DIR, f'{sport}_{season}.json')
            if os.path.exists(path):
                done.append(season); continue
            HIST, YEAR = True, season
            try:
                data = SPORTS[sport](season)
                for k in ('players', 'injuries', 'pnames'):
                    data.pop(k, None)
                if sum(1 for g in data['games'] if g['hs'] is not None) < 50:
                    raise RuntimeError('too few finished games')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, separators=(',', ':'))
                done.append(season)
                print(f'{sport} {season}: history built')
            except Exception as e:
                failed[season] = f'{type(e).__name__}: {e}'[:200]
                print(f'{sport} {season}: history FAILED - {e}', file=sys.stderr)
            finally:
                HIST, YEAR = False, None
        status[sport]['history'] = done
        if failed:
            status[sport]['historyErrors'] = failed
        else:
            status[sport].pop('historyErrors', None)


if __name__ == '__main__':
    sys.exit(main())
