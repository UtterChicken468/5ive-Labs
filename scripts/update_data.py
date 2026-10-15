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


# ---------------------------------------------------------------- helpers
def default_season():
    today = datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1  # football seasons start in Aug/Sep


def http_get(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')


def load_csv(url, env_name):
    local = os.environ.get(env_name)
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
        for ev in events:
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
                        ho = oc.get(ev['home_team'])
                        if ho and ho.get('point') is not None:
                            line['sp'] = -float(ho['point'])
                    if m['key'] == 'totals' and 'tot' not in line:
                        ov = oc.get('Over')
                        if ov and ov.get('point') is not None:
                            line['tot'] = float(ov['point'])
            if line:
                cache[str(g['id'])] = line
    save_cache(cache_name, cache)
    for g in sched:
        l = cache.get(str(g['id']))
        if l:
            g['sp'], g['tot'], g['hml'], g['aml'] = l.get('sp'), l.get('tot'), l.get('hml'), l.get('aml')
    return note, cache.get('_remaining')


# ---------------------------------------------------------------- NHL (NHL public API + The Odds API)
def build_nhl(_season):
    today = datetime.datetime.now(ET).date()
    y = today.year if today.month >= 9 else today.year - 1
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

    meta = dict(sport='nhl', label='NHL', family='hockey', period='Day', start=first.isoformat(),
                seasonLabel=f'{y}-{str(y + 1)[2:]}', sigma=2.4, sigmaTot=2.3, btSkip=10, formN=5,
                hfa=0.15, trust=55, bye=0.2, rest='b2b', props=False, unit='goals', spreadName='Puck line',
                edgeSteps=[0.25, 0.5, 0.75, 1], edgeDefault=0.5, hfaMax=0.5, hfaStep=0.05, byeMax=0.5, byeStep=0.05,
                source='NHL stats API · lines from The Odds API', oddsNote=note, oddsRemaining=remaining)
    return dict(meta=meta, season=y, teamGames=team_games, games=sched, players=[])


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


def mlb_schedule(y):
    data = get_json(f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&season={y}&hydrate=probablePitcher,team')
    return [g for d in data.get('dates', []) for g in d.get('games', [])]


def build_mlb(_season):
    today = datetime.datetime.now(ET).date()
    y = today.year
    raw = mlb_schedule(y)
    final = lambda g: g.get('status', {}).get('abstractGameState') == 'Final' and g['teams']['home'].get('score') is not None \
        and 'Postponed' not in g['status'].get('detailedState', '') and 'Cancel' not in g['status'].get('detailedState', '')
    note = None
    if not any(final(g) for g in raw):
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
ESPN_CBB = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball'


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


def cbb_box(eid):
    s = get_json(f'{ESPN_CBB}/summary?event={eid}')
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
    return dict(teams=teams, line=espn_line(pc[0]) if pc else {})


def cbb_day(day):
    sb = get_json(f'{ESPN_CBB}/scoreboard?dates={day.strftime("%Y%m%d")}&groups=50&limit=400')
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


def build_cbb(_season):
    today = datetime.datetime.now(ET).date()
    y = today.year + 1 if today.month >= 8 else today.year  # season named for the spring it ends in

    def season_days(y):
        d, end = datetime.date(y - 1, 11, 1), datetime.date(y, 3, 16)
        while d <= end:
            yield d
            d += datetime.timedelta(days=1)

    note = None
    if today < datetime.date(y - 1, 11, 1):
        y -= 1
        note = f'{y - 1}-{str(y)[2:]} season · the new season starts in November'

    teams_raw = get_json(f'{ESPN_CBB}/teams?groups=50&limit=500')
    d1 = {}
    for lg in teams_raw.get('sports', [{}])[0].get('leagues', []):
        for t in lg.get('teams', []):
            tm = t['team']
            d1[str(tm['id'])] = tm.get('shortDisplayName') or tm.get('location') or tm.get('abbreviation')
    counts = {}
    for n in d1.values():
        counts[n] = counts.get(n, 0) + 1
    teams_raw_all = {str(t['team']['id']): t['team'] for lg in teams_raw.get('sports', [{}])[0].get('leagues', []) for t in lg.get('teams', [])}
    for k, n in list(d1.items()):
        if counts[n] > 1:
            d1[k] = f"{n} ({teams_raw_all[k].get('abbreviation', k)})"

    sb_name = f'cbb_scoreboard_{y}.json'
    sb = load_cache(sb_name)
    days = [d for d in season_days(y) if d <= today + datetime.timedelta(days=7)]
    need = [d for d in days if d.isoformat() not in sb or (today - d).days <= 3]
    fetched = parallel(cbb_day, need, workers=8)
    for d, v in fetched.items():
        if isinstance(v, list):
            sb[d.isoformat()] = v
    save_cache(sb_name, sb)

    events = [e for v in sb.values() for e in v if e['h'] in d1 and e['a'] in d1]
    box_name = f'cbb_box_{y}.json'
    box = load_cache(box_name)
    need = [e['id'] for e in events if e['hs'] is not None and (e['id'] not in box or '_error' in box[e['id']])]
    box.update(parallel(cbb_box, need, workers=12))
    save_cache(box_name, box)

    sched = []
    for e in events:
        d, tm = et_parts(e['date'])
        b = box.get(e['id']) or {}
        line = (b.get('line') if e['hs'] is not None else None) or e.get('line') or {}
        sched.append(dict(id=e['id'], d=d, tm=tm, h=d1[e['h']], a=d1[e['a']], hid=e['h'], aid=e['a'],
                          hs=e['hs'], as_=e['as_'], neu=e['neu'],
                          sp=line.get('sp'), tot=line.get('tot'), hml=line.get('hml'), aml=line.get('aml')))
    if not sched:
        raise RuntimeError(f'No {y} college basketball games found.')
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
    for g in sched:
        g['gk'] = g['id']
        g.pop('hid'); g.pop('aid')
    finalize_games_gk(team_games, sched)
    sched.sort(key=lambda g: (g['d'], g['tm']))
    meta = dict(sport='cbb', label='College basketball', family='basketball', period='Day', start=first.isoformat(),
                seasonLabel=f'{y - 1}-{str(y)[2:]}', sigma=11, sigmaTot=16, btSkip=21, formN=5, hfa=3.0, trust=70,
                bye=0, rest='none', props=False, unit='pts', spreadName='Spread', edgeSteps=[1, 2, 3, 4, 5],
                edgeDefault=2, hfaMax=5, hfaStep=0.5, byeMax=0, byeStep=0.5, note=note, source='ESPN')
    return dict(meta=meta, season=y, teamGames=team_games, games=sched, players=[])


# ---------------------------------------------------------------- NFL (nflverse)
NFL_GAMES = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
NFL_TEAM = 'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv'
NFL_PLAYER = 'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv'


def build_nfl(season):
    games = [g for g in load_csv(NFL_GAMES, 'GAMES_CSV') if g['season'] == str(season) and g['game_type'] == 'REG']
    if not games:
        raise RuntimeError(f'No {season} NFL schedule found yet.')
    try:
        team_raw = load_csv(NFL_TEAM.format(season=season), 'TEAM_CSV')
        player_raw = load_csv(NFL_PLAYER.format(season=season), 'PLAYER_CSV')
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
        neu=g['location'] == 'Neutral', ar=num(g['away_rest']), hr=num(g['home_rest'])) for g in games]
    finalize_games(team_games, sched)

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
                hfa=1.5, trust=60, bye=1.0, rest='bye', formN=3, props=True, source='nflverse', **FOOTBALL_UI)
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=players)


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

    last_date, sched = {}, []
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
            ac=pick(g, 'awayConference', 'away_conference', default='')))
    sched.sort(key=lambda x: (x['w'], x['d'], x['tm']))
    finalize_games(team_games, sched)

    meta = dict(sport='cfb', label='College football', family='football', period='Week', sigma=16, sigmaTot=15, btSkip=2,
                hfa=2.5, trust=70, bye=1.0, rest='bye', formN=3, props=False, source='CollegeFootballData.com', **FOOTBALL_UI)
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=[])


# ---------------------------------------------------------------- main
SPORTS = {'nfl': build_nfl, 'cfb': build_cfb, 'nhl': build_nhl, 'cbb': build_cbb, 'mlb': build_mlb}


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
            status[sport] = dict(ok=True, season=data['season'], finals=len(done),
                                 through=max((g['d'] for g in done), default=''),
                                 upcoming=len(data['games']) - len(done), rows=len(data['teamGames']))
            print(f"{sport}: {'updated' if changed else 'no change'} - {status[sport]}")
        except Exception as e:  # keep going: one feed failing shouldn't stop the others
            status[sport] = dict(ok=False, error=f'{type(e).__name__}: {e}',
                                 trace=traceback.format_exc().splitlines()[-6:])
            print(f'{sport}: FAILED - {e}', file=sys.stderr)

    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=1, sort_keys=True)


if __name__ == '__main__':
    sys.exit(main())
