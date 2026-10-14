"""Refresh data.json for Gridiron Model Lab from nflverse.

Downloads the season schedule (with scores and Vegas lines), team weekly stats and
player weekly stats, and writes a compact data.json next to index.html.
data.json is only rewritten when the stats or lines actually changed, so the
scheduled job doesn't make empty commits.

Usage:  python scripts/update_data.py [--season 2026]
For offline testing you can point at local files with the GAMES_CSV, TEAM_CSV and
PLAYER_CSV environment variables.
"""
import argparse, csv, datetime, io, json, os, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data.json')

GAMES_URL = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
TEAM_URL = 'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv'
PLAYER_URL = 'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv'


def default_season():
    today = datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1  # NFL season starts in September


def load_csv(url, env_name):
    local = os.environ.get(env_name)
    if local:
        with open(local, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    req = urllib.request.Request(url, headers={'User-Agent': '5ive-lab'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode('utf-8'))))


def num(v):
    try:
        return float(v) if v not in ('', None, 'NA') else 0.0
    except ValueError:
        return 0.0


def opt_float(v):
    return None if v in ('', None, 'NA') else float(v)


def opt_int(v):
    return None if v in ('', None, 'NA') else int(float(v))


def build(season):
    games_raw = load_csv(GAMES_URL, 'GAMES_CSV')
    games = [g for g in games_raw if g['season'] == str(season) and g['game_type'] == 'REG']
    if not games:
        raise SystemExit(f'No {season} regular-season schedule found yet.')

    try:
        team_raw = load_csv(TEAM_URL.format(season=season), 'TEAM_CSV')
        player_raw = load_csv(PLAYER_URL.format(season=season), 'PLAYER_CSV')
    except urllib.error.HTTPError as e:
        if e.code == 404:  # season hasn't started: no stats files yet
            team_raw, player_raw = [], []
        else:
            raise

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
            pen=num(x['penalty_yards']),
            fd=num(x['passing_first_downs']) + num(x['rushing_first_downs'])))

    sched = []
    for g in games:
        sched.append(dict(
            id=g['game_id'], w=int(g['week']), d=g['gameday'], tm=g['gametime'],
            a=g['away_team'], h=g['home_team'],
            as_=opt_int(g['away_score']), hs=opt_int(g['home_score']),
            sp=opt_float(g['spread_line']), tot=opt_float(g['total_line']),
            aml=opt_int(g['away_moneyline']), hml=opt_int(g['home_moneyline']),
            neu=g['location'] == 'Neutral', ar=num(g['away_rest']), hr=num(g['home_rest'])))

    # Only count a game as final once its team stats exist too, so the model never
    # sees a score without the matching box score.
    have_stats = {(r['w'], r['t']) for r in team_games}
    for g in sched:
        if g['hs'] is not None and not ((g['w'], g['h']) in have_stats and (g['w'], g['a']) in have_stats):
            g['hs'] = g['as_'] = None

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
        if r['pa'] + r['ru'] + r['tg'] == 0:
            continue
        players.append(r)

    return dict(season=season, teamGames=team_games, games=sched, players=players)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=default_season())
    args = ap.parse_args()

    data = build(args.season)

    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            old = json.load(f)
        old.pop('updated', None)
    if old == data:
        print('No changes in the data.')
        return

    data['updated'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    done = [g for g in data['games'] if g['hs'] is not None]
    weeks = max((g['w'] for g in done), default=0)
    print(f"Wrote data.json: season {data['season']}, {len(done)} final games through week {weeks}, "
          f"{len(data['players'])} player-games.")


if __name__ == '__main__':
    sys.exit(main())
