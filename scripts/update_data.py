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

    meta = dict(sport='nfl', label='NFL', period='Week', sigma=13.5, sigmaTot=13, btSkip=1,
                hfa=1.5, trust=60, bye=1.0, props=True, source='nflverse')
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=players)


# ---------------------------------------------------------------- College football (CollegeFootballData.com)
CFBD = 'https://api.collegefootballdata.com'
CACHE_DIR = os.path.join(DATA_DIR, 'cache')


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

    meta = dict(sport='cfb', label='College football', period='Week', sigma=16, sigmaTot=15, btSkip=2,
                hfa=2.5, trust=70, bye=1.0, props=False, source='CollegeFootballData.com')
    return dict(meta=meta, season=season, teamGames=team_games, games=sched, players=[])


# ---------------------------------------------------------------- main
SPORTS = {'nfl': build_nfl, 'cfb': build_cfb}


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
            status[sport] = dict(ok=True, season=args.season, finals=len(done),
                                 through=max((g['w'] for g in done), default=0),
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
