// 5ive Lab engine: pure functions over one sport's data file.
(function (root) {
  const mean = (a) => a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0;
  const sd = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) ** 2))) || 0; };
  const div = (a, b) => (b ? a / b : 0);

  // ---------- factor sets per sport family ----------
  // Each factor reads a team profile's season sums (S.x = team's own stat, S.o_x = what its
  // opponents did against it) and returns a value where higher = better.
  const FAMILIES = {
    football: {
      factors: [
        { key: 'margin', label: 'Scoring margin', hint: 'Points scored minus allowed, per game', fn: (t) => t.margin },
        { key: 'adjMargin', label: 'Opponent-adjusted margin', hint: 'Margin after accounting for who they played (like KenPom or SP+)', fn: (t) => t.adj },
        { key: 'offEpa', label: 'Offensive efficiency', hint: 'EPA per play on offense', fn: (t) => div(t.S.epa, t.S.pl) },
        { key: 'defEpa', label: 'Defensive efficiency', hint: 'EPA per play allowed (lower is better)', fn: (t) => -div(t.S.o_epa, t.S.o_pl) },
        { key: 'ypp', label: 'Yards per play edge', hint: 'Offense YPP minus defense YPP', fn: (t) => div(t.S.py + t.S.ry, t.S.pl) - div(t.S.o_py + t.S.o_ry, t.S.o_pl) },
        { key: 'turnovers', label: 'Turnover margin', hint: 'Takeaways minus giveaways, per game', fn: (t) => (t.S.o_to - t.S.to) / t.n },
        { key: 'passRush', label: 'Pass rush vs protection', hint: 'Sacks made minus sacks allowed, per game', fn: (t) => (t.S.sk - t.S.ss) / t.n },
        { key: 'form', label: 'Recent form', hint: 'Scoring margin over the last few games', fn: (t) => t.form },
        { key: 'discipline', label: 'Discipline', hint: 'Fewer penalty yards per game', fn: (t) => -t.S.pen / t.n },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
      ],
      presets: {
        Balanced: { margin: 3, adjMargin: 6, offEpa: 6, defEpa: 6, ypp: 4, turnovers: 3, passRush: 2, form: 3, discipline: 1, sos: 3 },
        'Efficiency first': { margin: 1, adjMargin: 3, offEpa: 9, defEpa: 8, ypp: 6, turnovers: 1, passRush: 2, form: 1, discipline: 0, sos: 3 },
        Scoreboard: { margin: 6, adjMargin: 8, offEpa: 2, defEpa: 2, ypp: 1, turnovers: 2, passRush: 0, form: 6, discipline: 0, sos: 2 },
        'Defense wins': { margin: 2, adjMargin: 3, offEpa: 3, defEpa: 10, ypp: 3, turnovers: 7, passRush: 7, form: 2, discipline: 2, sos: 2 },
      },
    },
    hockey: {
      factors: [
        { key: 'margin', label: 'Goal differential', hint: 'Goals for minus against, per game', fn: (t) => t.margin },
        { key: 'adjMargin', label: 'Opponent-adjusted margin', hint: 'Margin after accounting for who they played (like KenPom or SP+)', fn: (t) => t.adj },
        { key: 'shotShare', label: 'Shot share', hint: 'Share of all shots on goal taken by this team', fn: (t) => div(t.S.sf, t.S.sf + t.S.o_sf) },
        { key: 'finishing', label: 'Finishing', hint: 'Goals per shot on goal', fn: (t) => div(t.S.gf, t.S.sf) },
        { key: 'goaltending', label: 'Goaltending', hint: 'Save percentage', fn: (t) => 1 - div(t.S.o_gf, t.S.o_sf) },
        { key: 'specialTeams', label: 'Special teams', hint: 'Power play % plus penalty kill %', fn: (t) => (t.S.pp + t.S.pk) / t.n },
        { key: 'faceoffs', label: 'Faceoffs', hint: 'Faceoff win percentage', fn: (t) => t.S.fo / t.n },
        { key: 'form', label: 'Recent form', hint: 'Goal differential over the last few games', fn: (t) => t.form },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
      ],
      presets: {
        Balanced: { margin: 3, adjMargin: 5, shotShare: 6, finishing: 3, goaltending: 5, specialTeams: 3, faceoffs: 1, form: 3, sos: 2 },
        'Shot share': { margin: 1, adjMargin: 2, shotShare: 10, finishing: 1, goaltending: 3, specialTeams: 2, faceoffs: 2, form: 1, sos: 2 },
        'Hot goalie': { margin: 3, adjMargin: 3, shotShare: 3, finishing: 2, goaltending: 10, specialTeams: 2, faceoffs: 0, form: 5, sos: 1 },
        Scoreboard: { margin: 6, adjMargin: 8, shotShare: 2, finishing: 2, goaltending: 2, specialTeams: 2, faceoffs: 0, form: 6, sos: 2 },
      },
    },
    basketball: {
      factors: [
        { key: 'margin', label: 'Scoring margin', hint: 'Points scored minus allowed, per game', fn: (t) => t.margin },
        { key: 'adjMargin', label: 'Opponent-adjusted margin', hint: 'Margin after accounting for who they played (like KenPom or SP+)', fn: (t) => t.adj },
        { key: 'offEff', label: 'Offensive efficiency', hint: 'Points per 100 possessions', fn: (t) => 100 * div(t.S.pts, poss(t.S, '')) },
        { key: 'defEff', label: 'Defensive efficiency', hint: 'Points allowed per 100 possessions', fn: (t) => -100 * div(t.S.o_pts, poss(t.S, 'o_')) },
        { key: 'shooting', label: 'Shooting edge', hint: 'Effective FG% minus opponents\' effective FG%', fn: (t) => div(t.S.fgm + 0.5 * t.S.tpm, t.S.fga) - div(t.S.o_fgm + 0.5 * t.S.o_tpm, t.S.o_fga) },
        { key: 'ballSecurity', label: 'Turnover edge', hint: 'Forced turnover rate minus own turnover rate', fn: (t) => div(t.S.o_tov, poss(t.S, 'o_')) - div(t.S.tov, poss(t.S, '')) },
        { key: 'rebounding', label: 'Rebounding', hint: 'Offensive rebound % minus opponents\'', fn: (t) => div(t.S.orb, t.S.orb + t.S.o_drb) - div(t.S.o_orb, t.S.o_orb + t.S.drb) },
        { key: 'freeThrows', label: 'Getting to the line', hint: 'Free throw rate minus opponents\'', fn: (t) => div(t.S.fta, t.S.fga) - div(t.S.o_fta, t.S.o_fga) },
        { key: 'form', label: 'Recent form', hint: 'Scoring margin over the last few games', fn: (t) => t.form },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
      ],
      presets: {
        Balanced: { margin: 2, adjMargin: 8, offEff: 6, defEff: 6, shooting: 4, ballSecurity: 3, rebounding: 3, freeThrows: 1, form: 2, sos: 5 },
        'Four factors': { margin: 1, adjMargin: 3, offEff: 3, defEff: 3, shooting: 10, ballSecurity: 6, rebounding: 5, freeThrows: 3, form: 1, sos: 5 },
        Scoreboard: { margin: 6, adjMargin: 10, offEff: 3, defEff: 3, shooting: 1, ballSecurity: 1, rebounding: 1, freeThrows: 0, form: 5, sos: 4 },
        'Defense wins': { margin: 2, adjMargin: 4, offEff: 3, defEff: 10, shooting: 3, ballSecurity: 6, rebounding: 4, freeThrows: 1, form: 2, sos: 4 },
      },
    },
    baseball: {
      factors: [
        { key: 'margin', label: 'Run differential', hint: 'Runs scored minus allowed, per game', fn: (t) => t.margin },
        { key: 'adjMargin', label: 'Opponent-adjusted margin', hint: 'Margin after accounting for who they played (like KenPom or SP+)', fn: (t) => t.adj },
        { key: 'hitting', label: 'Hitting', hint: 'On-base plus slugging (OPS)', fn: (t) => div(t.S.h + t.S.bb + t.S.hbp, t.S.pa) + div(t.S.tb, t.S.pa - t.S.bb - t.S.hbp - t.S.sf) },
        { key: 'discipline', label: 'Plate discipline', hint: 'Walk rate minus strikeout rate', fn: (t) => div(t.S.bb - t.S.so, t.S.pa) },
        { key: 'rotation', label: 'Starting rotation', hint: 'Starters\' earned runs per 9 innings (lower is better)', fn: (t) => -9 * div(t.S.sp_er, t.S.sp_ip) },
        { key: 'bullpen', label: 'Bullpen', hint: 'Relievers\' earned runs per 9 innings (lower is better)', fn: (t) => -9 * div(t.S.bp_er, t.S.bp_ip) },
        { key: 'form', label: 'Recent form', hint: 'Run differential over the last 10 games', fn: (t) => t.form },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
        { key: 'starter', label: 'Today\'s starting pitcher', hint: 'Adjusts each game for who is pitching (5 = full effect)', game: true },
      ],
      presets: {
        Balanced: { margin: 3, adjMargin: 5, hitting: 4, discipline: 2, rotation: 3, bullpen: 3, form: 2, sos: 1, starter: 5 },
        'Pitching first': { margin: 2, adjMargin: 2, hitting: 2, discipline: 1, rotation: 6, bullpen: 6, form: 1, sos: 1, starter: 8 },
        'Bats first': { margin: 2, adjMargin: 3, hitting: 9, discipline: 5, rotation: 2, bullpen: 2, form: 2, sos: 1, starter: 4 },
        Scoreboard: { margin: 6, adjMargin: 8, hitting: 1, discipline: 0, rotation: 1, bullpen: 1, form: 6, sos: 1, starter: 3 },
      },
    },
  };
  function poss(S, p) { return S[p + 'fga'] - S[p + 'orb'] + S[p + 'tov'] + 0.475 * S[p + 'fta']; }

  // Per-sport settings; set with configure() once the sport's data loads.
  let MKT = null, SIGMA = 13.5, SIGMA_TOT = 13, BT_SKIP = 1, FORM_N = 3, REST = 'bye', FAMILY = 'football', PRIORS = null, LG_SV = 0.9, HFA0 = 1.5, CAP = 28, LG_PTS = null;
  const api = {};
  function configure(cfg) {
    if (cfg.sigma) SIGMA = cfg.sigma;
    if (cfg.sigmaTot) SIGMA_TOT = cfg.sigmaTot;
    MKT = cfg.mkt || null;
    if (cfg.btSkip != null) BT_SKIP = cfg.btSkip;
    if (cfg.formN) FORM_N = cfg.formN;
    if (cfg.rest) REST = cfg.rest;
    FAMILY = cfg.family || 'football';
    if (!FAMILIES[FAMILY]) { api.FACTORS = []; api.PRESETS = {}; PROPS = []; api.PROPS = []; return; } // UFC uses api.ufc
    api.FACTORS = FAMILIES[FAMILY].factors;
    api.PRESETS = FAMILIES[FAMILY].presets;
    PROPS = PROP_SETS[FAMILY] || []; api.PROPS = PROPS;
    PRIORS = cfg.priors || null;
    LG_SV = cfg.lgSv || 0.9;
    HFA0 = cfg.hfa ?? 1.5;
    LG_PTS = cfg.lgPts ?? null;
    CAP = cfg.marginCap || ({ football: 28, hockey: 5, basketball: 30, baseball: 8 })[FAMILY];
  }

  const phi = (x) => { // standard normal CDF
    const t = 1 / (1 + 0.2316419 * Math.abs(x));
    const d = 0.3989423 * Math.exp(-x * x / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return x > 0 ? 1 - p : p;
  };

  const rowKey = (r) => (r.g != null ? r.g : r.w) + '|' + r.t;
  const gameKey = (g, t) => (g.gk != null ? g.gk : g.w) + '|' + t;

  // Season-to-date team profiles. Built from an accumulator so the backtest can add one
  // day's games at a time instead of re-summing the whole season for every day.
  function indexRows(data) {
    const byKey = {};
    data.teamGames.forEach((r) => { byKey[rowKey(r)] = r; });
    return byKey;
  }
  function addGame(acc, g, byKey) {
    const sides = [[g.h, g.a, g.hs, g.as_], [g.a, g.h, g.as_, g.hs]];
    const rows = sides.map(([t, o]) => [byKey[gameKey(g, t)], byKey[gameKey(g, o)]]);
    if (rows.some(([me, op]) => !me || !op)) return;
    sides.forEach(([t, o, pf, pa], i) => {
      const [me, op] = rows[i];
      const a = acc[t] || (acc[t] = { n: 0, S: {}, pf: 0, pa: 0, hist: [], opps: [], adjSum: 0, lastWeek: -1 });
      a.n++; a.pf += pf; a.pa += pa; a.hist.push(pf - pa); a.opps.push(o); a.lastWeek = Math.max(a.lastWeek, g.w);
      const m = Math.max(-CAP, Math.min(CAP, pf - pa));
      a.adjSum += m - (g.neu ? 0 : (i === 0 ? HFA0 : -HFA0));
      for (const k in me) if (typeof me[k] === 'number' && k !== 'w' && k !== 'g') a.S[k] = (a.S[k] || 0) + me[k];
      for (const k in op) if (typeof op[k] === 'number' && k !== 'w' && k !== 'g') a.S['o_' + k] = (a.S['o_' + k] || 0) + op[k];
    });
  }
  function finalizeProfiles(acc) {
    const out = {};
    for (const team in acc) {
      const a = acc[team], n = a.n;
      out[team] = { team, n, S: a.S, pf: a.pf / n, pa: a.pa / n, margin: (a.pf - a.pa) / n,
        form: mean(a.hist.slice(-FORM_N)), opps: a.opps, lastWeek: a.lastWeek };
    }
    Object.values(out).forEach((t) => { t.sos = mean(t.opps.map((o) => (out[o] ? out[o].margin : 0))); });
    // Opponent-adjusted margin (simple rating system): rating = average neutral-site margin
    // + average opponent rating, solved by iteration. Blowouts are capped.
    // Warm-started from the previous call so a day-by-day walk needs only a few passes.
    const teams = Object.keys(acc);
    const r = acc.__srs || (Object.defineProperty(acc, '__srs', { value: {}, enumerable: false }), acc.__srs);
    const fresh = teams.some((t) => r[t] === undefined);
    teams.forEach((t) => { if (r[t] === undefined) r[t] = acc[t].adjSum / acc[t].n; });
    const ids = {}; teams.forEach((t, i) => { ids[t] = i; });
    const base = teams.map((t) => acc[t].adjSum / acc[t].n);
    const opp = teams.map((t) => acc[t].opps.map((o) => ids[o]).filter((i) => i !== undefined));
    let cur = teams.map((t) => r[t]);
    for (let it = 0, n = fresh ? 40 : 12; it < n; it++) {
      const nx = new Array(teams.length); let mu = 0;
      for (let i = 0; i < teams.length; i++) { let so = 0; const os = opp[i]; for (let j = 0; j < os.length; j++) so += cur[os[j]]; nx[i] = base[i] + (os.length ? so / os.length : 0); mu += nx[i]; }
      mu /= teams.length;
      for (let i = 0; i < teams.length; i++) cur[i] = 0.5 * cur[i] + 0.5 * (nx[i] - mu);
    }
    teams.forEach((t, i) => { r[t] = cur[i]; out[t].adj = cur[i]; });
    return out;
  }
  function teamProfiles(data, before) {
    const byKey = indexRows(data), acc = {};
    data.games.filter((g) => g.w < before && g.hs != null).sort((a, b) => a.w - b.w).forEach((g) => addGame(acc, g, byKey));
    return finalizeProfiles(acc);
  }

  // Ratings in points (goals, runs) vs an average team.
  function ratings(profiles, weights, settings) {
    const teams = Object.values(profiles).filter((t) => t.n >= 1);
    if (teams.length < 4) { // before the season (almost) anyone has played: last season's ratings carry it
      if (!PRIORS) return { rating: {}, contrib: {}, league: null };
      const rating = {}, contrib = {};
      Object.entries(PRIORS).forEach(([t, v]) => { rating[t] = v; contrib[t] = { prior: v }; });
      const ppg = teams.length ? mean(teams.map((t) => t.pf)) : (LG_PTS ?? 0);
      return { rating, contrib, z: {}, league: { ppg }, weights };
    }
    const F = api.FACTORS.filter((f) => !f.game);
    const z = {};
    F.forEach((f) => {
      const vals = teams.map((t) => f.fn(t));
      const m = mean(vals), s = sd(vals) || 1;
      teams.forEach((t, i) => { (z[t.team] || (z[t.team] = {}))[f.key] = (vals[i] - m) / s; });
    });
    const wsum = F.reduce((s, f) => s + (weights[f.key] || 0), 0) || 1;
    const comp = {};
    teams.forEach((t) => { comp[t.team] = F.reduce((s, f) => s + (weights[f.key] || 0) * z[t.team][f.key], 0) / wsum; });
    const cs = sd(Object.values(comp)) || 1;
    // Two modes: the default turns the weighted blend into points by matching the spread of
    // scoring margins; a trained model (settings.ppw) says exactly how many points each
    // weight unit is worth, learned from past seasons.
    const per = settings.ppw ? settings.ppw * (settings.trust / 100) * wsum : sd(teams.map((t) => t.margin)) * (settings.trust / 100) / cs;
    const rating = {}, contrib = {};
    const k = PRIORS ? (settings.prior ?? 4) : 0;
    teams.forEach((t) => {
      const r = comp[t.team] * per;
      const pr = PRIORS ? (PRIORS[t.team] ?? -8) : 0; // teams with no prior rating start below average
      const keep = k ? t.n / (t.n + k) : 1;
      rating[t.team] = r * keep + (k ? k * pr / (t.n + k) : 0);
      contrib[t.team] = k ? { prior: k * pr / (t.n + k) } : {};
      F.forEach((f) => { contrib[t.team][f.key] = (weights[f.key] || 0) * z[t.team][f.key] / wsum * per * keep; });
    });
    if (PRIORS) Object.entries(PRIORS).forEach(([t, v]) => { if (rating[t] === undefined) { rating[t] = v; contrib[t] = { prior: v }; } });
    const league = { ppg: mean(teams.map((t) => t.pf)) };
    return { rating, contrib, z, league, weights };
  }

  // NFL: each team's starting QB = most pass attempts in its latest game.
  function startingQB(data, team) {
    if (!data._qb) {
      data._qb = {};
      (data.players || []).forEach((r) => {
        if (r.p !== 'QB') return;
        const cur = data._qb[r.t];
        if (!cur || r.w > cur.w || (r.w === cur.w && r.pa > cur.pa)) data._qb[r.t] = { n: r.n, w: r.w, pa: r.pa };
      });
    }
    return data._qb[team];
  }
  // NHL: a goalie's save % from his starts before this game, shrunk toward league average (~600 shots).
  function goalieSv(data, gid, before) {
    let sa = 0, sv = 0;
    (data.goalies[gid] || []).forEach((s) => { if (s[0] < before) { sa += s[1]; sv += s[2]; } });
    return (sv + LG_SV * 600) / (sa + 600);
  }

  function teamSv(data, team, before) {
    let sa = 0, sv = 0;
    ((data.teamGoalies || {})[team] || []).forEach((gid) => (data.goalies[gid] || []).forEach((s) => { if (s[0] < before) { sa += s[1]; sv += s[2]; } }));
    return (sv + LG_SV * 600) / (sa + 600);
  }

  // Baseball: run value of each probable starter vs a league-average starter, from his starts
  // before this game. Shrunk toward average until he has ~40 innings.
  function starterRuns(data, pid, before) {
    if (!pid || !data.pitchers || !data.pitchers[pid]) return 0;
    let ip = 0, er = 0;
    data.pitchers[pid].forEach((s) => { if (s[0] < before) { ip += s[1]; er += s[2]; } });
    const lg = data.meta && data.meta.lgEra || 4.2;
    const era = ip ? er * 9 / ip : lg;
    const shrunk = (era * ip + lg * 40) / (ip + 40);
    return (lg - shrunk) * 5.5 / 9; // runs saved over a typical start
  }

  const impliedProb = (ml) => ml == null ? null : ml < 0 ? -ml / (-ml + 100) : 100 / (ml + 100);
  const profitPerUnit = (ml) => ml < 0 ? 100 / -ml : ml / 100;
  const evPerUnit = (p, ml) => p * profitPerUnit(ml) - (1 - p);

  // Predict one game. lines: {sp (home favored by), tot, hml, aml}
  function predict(game, profiles, R, settings, lines, data) {
    if (!R.league || R.rating[game.h] == null || R.rating[game.a] == null) return null;
    const blank = { pf: R.league.ppg, pa: R.league.ppg, n: 0, S: {} }; // no games yet: league-average scoring
    const h = profiles[game.h] || blank, a = profiles[game.a] || blank;
    let margin = R.rating[game.h] - R.rating[game.a] + (game.neu ? 0 : settings.hfa);
    let restEdge = 0;
    if (REST === 'bye') restEdge = (game.hr >= 10 ? 1 : 0) - (game.ar >= 10 ? 1 : 0); // coming off a bye
    if (REST === 'b2b') restEdge = (game.ar <= 1 ? 1 : 0) - (game.hr <= 1 ? 1 : 0);  // opponent on a back-to-back
    margin += restEdge * (settings.bye || 0);
    let qb = null, goalie = null, weather = null;
    if (data && data.injuries && settings.qbOut) { // NFL: starting QB ruled out or doubtful
      const out = (t) => { const q = startingQB(data, t); return q && (data.injuries[t] || []).some((i) => i.n === q.n && (i.st === 'Out' || i.st === 'Doubtful')) ? q : null; };
      const hq = out(game.h), aq = out(game.a);
      if (hq || aq) { qb = { h: hq && hq.n, a: aq && aq.n }; margin += ((aq ? 1 : 0) - (hq ? 1 : 0)) * settings.qbOut; }
    }
    if (FAMILY === 'hockey' && data && data.goalies && settings.goalie) {
      const hg = lines.hg !== undefined ? lines.hg : game.hg, ag = lines.ag !== undefined ? lines.ag : game.ag;
      // goals saved vs the team's usual goaltending, both measured from goalie box scores
      const adj = (gid, team) => gid ? (goalieSv(data, gid, game.w) - teamSv(data, team, game.w)) * 28 : 0;
      goalie = { h: adj(hg, game.h), a: adj(ag, game.a), hg, ag };
      margin += (goalie.h - goalie.a) * settings.goalie / 5;
    }
    let starter = null;
    if (FAMILY === 'baseball' && data && R.weights && R.weights.starter) {
      const k = R.weights.starter / 5;
      const hp = starterRuns(data, game.hp, game.w), ap = starterRuns(data, game.ap, game.w);
      starter = (hp - ap) * k; margin += starter;
    }
    const k = (settings.totTrust ?? settings.trust) / 100, L = R.league.ppg;
    const homePts = L + k * (((h.pf + a.pa) / 2) - L);
    const awayPts = L + k * (((a.pf + h.pa) / 2) - L);
    let total = homePts + awayPts;
    if (game.wx && settings.weather && FAMILY === 'football') { // wind, rain/snow and cold pull scoring down
      const w = game.wx;
      const pts = Math.max((w.w || 0) - 10, 0) * 0.3 + ((w.pp || 0) >= 60 && (w.pr || 0) >= 0.04 ? 1.5 : 0) + ((w.sn || 0) >= 0.05 ? 1.5 : 0) + ((w.t ?? 60) < 25 ? 1 : 0);
      weather = -pts * settings.weather / 5;
      total += weather;
    }
    const hs = total / 2 + margin / 2, as = total / 2 - margin / 2;
    const pHome = phi(margin / SIGMA);
    const out = { margin, total, hs, as, pHome, pAway: 1 - pHome, starter, qb, goalie, weather };
    if (lines.sp != null) {
      out.spEdge = margin - lines.sp; // + means model likes home vs the line
      out.pHomeCover = vsMarket('sp', margin - lines.sp, SIGMA);
    }
    // Totals need each team's own scoring; before both have played, skip the total edge.
    if (lines.tot != null && h.n && a.n) { out.totEdge = total - lines.tot; out.pOver = vsMarket('tot', total - lines.tot, SIGMA_TOT); }
    if (lines.hml != null) out.evHome = evPerUnit(pHome, lines.hml);
    if (lines.aml != null) out.evAway = evPerUnit(1 - pHome, lines.aml);
    return out;
  }

  // Walk-forward backtest: each week predicted only with earlier weeks.
  // Chance a spread or total bet wins. Vegas lines are sharp, so the model's gap from the line is
  // only worth what past seasons showed it was worth: MKT[k].s is the fitted share of the gap that
  // actually showed up in results (0 = none, 1 = all of it) and MKT[k].sd how far results land
  // from the line. Sports without past lines use a cautious default (every sport we could test
  // came out between 0 and 0.22).
  function vsMarket(k, gap, sigma) {
    const m = MKT && MKT[k];
    return m ? phi(m.s * gap / m.sd) : phi(0.1 * gap / sigma);
  }

  // Honest win chance for a moneyline: the model's chance blended with the market's (bookmaker cut
  // removed), with the model's share MKT.ml.w fitted on past seasons. Default 0.2 until a sport has past lines.
  function blendWin(pModel, pMarket) {
    if (pMarket == null) return pModel;
    const w = MKT && MKT.ml ? MKT.ml.w : 0.2;
    return w * pModel + (1 - w) * pMarket;
  }

  function backtest(data, weights, settings, edgeMin, collect) {
    const picks = collect ? [] : null;
    const done = data.games.filter((g) => g.hs != null).sort((a, b) => a.w - b.w);
    const byW = {}; done.forEach((g) => { (byW[g.w] || (byW[g.w] = [])).push(g); });
    const weeks = Object.keys(byW).map(Number).sort((a, b) => a - b);
    const byKey = indexRows(data), acc = {};
    let added = 0;
    const per = []; let su = 0, n = 0, vegasSu = 0, vegasN = 0, ats = [0, 0, 0], mae = 0, vegasMae = 0, ou = [0, 0, 0];
    weeks.forEach((w, wi) => {
      while (added < done.length && done[added].w < w) addGame(acc, done[added++], byKey);
      if (wi < BT_SKIP) return;
      const P = finalizeProfiles(acc); const R = ratings(P, weights, settings);
      const wk = { w, su: 0, n: 0, atsW: 0, atsL: 0 };
      byW[w].forEach((g) => {
        const pr = predict(g, P, R, settings, { sp: g.sp, tot: g.tot }, data); if (!pr) return;
        const res = g.hs - g.as_;
        if (picks) picks.push({ id: g.id, w, m: pr.margin, res, sp: g.sp, t: pr.total, tot: g.tot, at: g.hs + g.as_, pOver: pr.pOver, pH: pr.pHome, hml: g.hml, aml: g.aml });
        if (res !== 0) { n++; wk.n++; if ((pr.margin > 0) === (res > 0)) { su++; wk.su++; } }
        mae += Math.abs(pr.margin - res);
        if (g.sp != null) {
          vegasMae += Math.abs(g.sp - res);
          if (res !== 0 && g.sp !== 0) { vegasN++; if ((g.sp > 0) === (res > 0)) vegasSu++; }
          if (Math.abs(pr.spEdge) >= edgeMin && pr.spEdge !== 0) {
            const cover = res - g.sp; // + home covered
            if (cover === 0) ats[2]++;
            else if ((pr.spEdge > 0) === (cover > 0)) { ats[0]++; wk.atsW++; } else { ats[1]++; wk.atsL++; }
          }
        }
        if (g.tot != null && Math.abs(pr.totEdge) >= edgeMin) {
          const d = g.hs + g.as_ - g.tot;
          if (d === 0) ou[2]++; else if ((pr.totEdge > 0) === (d > 0)) ou[0]++; else ou[1]++;
        }
      });
      per.push(wk);
    });
    const games = per.reduce((s, x) => s + x.n, 0);
    return { su, n, vegasSu, vegasN, ats, ou, mae: games ? mae / games : 0, vegasMae: games ? vegasMae / games : 0, per, picks };
  }

  // ---------- Player props ----------
  const avg = (games, f) => games.reduce((t, g) => t + f(g), 0) / (games.length || 1);
  const PROP_SETS = {
    football: [
      { key: 'py', label: 'Passing yards', pos: ['QB'], dist: 'normal', minSd: 8, grp: () => 'QB', elig: (gs) => avg(gs, (g) => g.pa) >= 15 },
      { key: 'ptd', label: 'Passing TDs', pos: ['QB'], dist: 'poisson', grp: () => 'QB', elig: (gs) => avg(gs, (g) => g.pa) >= 15 },
      { key: 'ry', label: 'Rushing yards', pos: ['QB', 'RB', 'WR'], dist: 'normal', minSd: 8,
        elig: (gs) => ({ RB: avg(gs, (g) => g.ru) >= 5, QB: avg(gs, (g) => g.ru) >= 4 && avg(gs, (g) => g.pa) >= 15, WR: avg(gs, (g) => g.ru) >= 2 })[gs[0].p] },
      { key: 'rec', label: 'Receptions', pos: ['RB', 'WR', 'TE'], dist: 'poisson', elig: (gs) => avg(gs, (g) => g.tg) >= 3 },
      { key: 'rcy', label: 'Receiving yards', pos: ['RB', 'WR', 'TE'], dist: 'normal', minSd: 8, elig: (gs) => avg(gs, (g) => g.tg) >= 3 },
      { key: 'rry', label: 'Rush + rec yards', pos: ['RB', 'WR', 'TE'], dist: 'normal', minSd: 8, stat: (r) => r.ry + r.rcy,
        elig: (gs) => gs[0].p === 'RB' ? avg(gs, (g) => g.ru + g.tg) >= 8 : avg(gs, (g) => g.tg) >= 4 },
    ],
    hockey: [
      { key: 'sog', label: 'Shots on goal', pos: ['F', 'D'], dist: 'poisson', grp: () => 'S', elig: (gs) => avg(gs, (g) => g.toi) >= 12 },
      { key: 'pts', label: 'Points', pos: ['F', 'D'], dist: 'poisson', grp: () => 'S', elig: (gs) => avg(gs, (g) => g.toi) >= 13 },
      { key: 'gl', label: 'Goals', pos: ['F', 'D'], dist: 'poisson', grp: () => 'S', elig: (gs) => avg(gs, (g) => g.toi) >= 14 },
      { key: 'sv', label: 'Goalie saves', pos: ['G'], dist: 'normal', minSd: 4, grp: () => 'G', elig: (gs) => gs.length >= 2 },
    ],
    basketball: [
      { key: 'pts', label: 'Points', pos: ['G', 'F', 'C'], dist: 'normal', minSd: 3, grp: () => 'P', elig: (gs) => avg(gs, (g) => g.min) >= 18 },
      { key: 'reb', label: 'Rebounds', pos: ['G', 'F', 'C'], dist: 'poisson', grp: () => 'P', elig: (gs) => avg(gs, (g) => g.min) >= 18 },
      { key: 'ast', label: 'Assists', pos: ['G', 'F', 'C'], dist: 'poisson', grp: () => 'P', elig: (gs) => avg(gs, (g) => g.min) >= 18 },
      { key: 'tpm', label: 'Made threes', pos: ['G', 'F', 'C'], dist: 'poisson', grp: () => 'P', elig: (gs) => avg(gs, (g) => g.min) >= 18 && avg(gs, (g) => g.tpm) >= 0.8 },
      { key: 'pra', label: 'Pts + reb + ast', pos: ['G', 'F', 'C'], dist: 'normal', minSd: 4, grp: () => 'P', stat: (r) => r.pts + r.reb + r.ast, elig: (gs) => avg(gs, (g) => g.min) >= 18 },
    ],
    baseball: [],
  };
  let PROPS = PROP_SETS.football;
  const propOf = (key) => PROPS.find((p) => p.key === key);
  const statOf = (r, key) => { const p = propOf(key); return p && p.stat ? p.stat(r) : (r[key] || 0); };
  const grpOf = (p, pos) => (p.grp ? p.grp(pos) : pos);

  // What each defense allows per game, by stat and position group, vs league average.
  function defenseAllowed(data) {
    const games = {}; // team -> set of weeks
    data.teamGames.forEach((r) => { (games[r.t] || (games[r.t] = new Set())).add(r.w); });
    const tot = {};
    (data.players || []).forEach((r) => {
      PROPS.forEach((p) => {
        if (!p.pos.includes(r.p)) return;
        const k = r.o + '|' + p.key + '|' + grpOf(p, r.p);
        tot[k] = (tot[k] || 0) + statOf(r, p.key);
      });
    });
    const per = {}, lg = {};
    Object.entries(tot).forEach(([k, v]) => {
      const [team, stat, pos] = k.split('|');
      const g = games[team] ? games[team].size : 1;
      per[k] = v / g;
      const lk = stat + '|' + pos; (lg[lk] || (lg[lk] = [])).push(v / g);
    });
    const lgAvg = {}; Object.entries(lg).forEach(([k, a]) => { lgAvg[k] = mean(a); });
    return (opp, stat, pos) => {
      const p = grpOf(propOf(stat), pos);
      const v = per[opp + '|' + stat + '|' + p], l = lgAvg[stat + '|' + p];
      if (v == null || !l) return 1;
      return v / l;
    };
  }

  function poissonCdf(k, lam) { // P(X <= k)
    let s = 0, term = Math.exp(-lam);
    for (let i = 0; i <= k; i++) { if (i > 0) term *= lam / i; s += term; }
    return s;
  }

  // Projection for one player/prop. opts: {recent (0-100), matchup (0-100)}
  function projectProp(games, propKey, opp, defFn, opts) {
    const prop = propOf(propKey);
    const vals = games.map((g) => statOf(g, propKey));
    if (!vals.length) return null;
    const season = mean(vals);
    const last3 = mean([...games].sort((a, b) => b.w - a.w).slice(0, 3).map((g) => statOf(g, propKey)));
    const rw = opts.recent / 100;
    const base = season * (1 - rw) + last3 * rw;
    const mf = defFn(opp, propKey, games[0].p);
    const adj = 1 + (mf - 1) * (opts.matchup / 100) * 0.6; // damp: small samples
    const proj = Math.max(0, base * adj);
    const spread = Math.max(sd(vals), proj * 0.3, prop.minSd || 0.5);
    return { proj, season, last3, matchup: mf, spread, dist: prop.dist, n: vals.length, vals };
  }

  function overProb(pj, line) {
    if (pj.dist === 'poisson') return 1 - poissonCdf(Math.floor(line), Math.max(pj.proj, 0.05));
    return 1 - phi((line - pj.proj) / pj.spread);
  }

  const DEFAULT_SETTINGS = { hfa: 1.5, trust: 60, bye: 1.0 };
  // Walk a season in order, handing over each period's finished games with ratings built
  // only from earlier games (the same walk the backtest uses). Used by the trainer.
  function walk(data, cb, skip) {
    const done = data.games.filter((g) => g.hs != null).sort((a, b) => a.w - b.w);
    const byW = {}; done.forEach((g) => { (byW[g.w] || (byW[g.w] = [])).push(g); });
    const weeks = Object.keys(byW).map(Number).sort((a, b) => a - b);
    const byKey = indexRows(data), acc = {};
    let added = 0;
    weeks.forEach((w, wi) => {
      while (added < done.length && done[added].w < w) addGame(acc, done[added++], byKey);
      if (wi < (skip ?? BT_SKIP)) return;
      cb(w, byW[w], finalizeProfiles(acc));
    });
  }
  // ======================= UFC: fighters instead of teams =======================
  // Each fighter carries a chess-style rating plus per-minute fight stats, built only from
  // fights before the one being predicted. Win chance = logistic(sum of factor edges).
  const UFC_FACTORS = [
    { key: 'elo', label: 'Fighter rating', hint: 'Chess-style rating from every UFC result since 2019' },
    { key: 'striking', label: 'Striking differential', hint: 'Significant strikes landed minus absorbed, per minute' },
    { key: 'grappling', label: 'Takedown game', hint: 'Takedowns landed minus allowed, per 15 minutes' },
    { key: 'power', label: 'Knockdown power', hint: 'Knockdowns per 15 minutes' },
    { key: 'subs', label: 'Submission threat', hint: 'Submission attempts per 15 minutes' },
    { key: 'control', label: 'Control time', hint: 'Share of fight time spent in control' },
    { key: 'reach', label: 'Reach', hint: 'Reach advantage, in inches' },
    { key: 'age', label: 'Age', hint: 'Edge to the younger fighter' },
    { key: 'layoff', label: 'Ring rust', hint: 'Penalty for a long layoff since the last fight' },
    { key: 'experience', label: 'UFC experience', hint: 'Number of UFC fights' },
    { key: 'form', label: 'Recent form', hint: 'Results of the last three fights' },
  ];
  // Untrained starting point: the rating does almost all the work.
  const UFC_DEFAULT_COEF = { elo: 0.55, striking: 0.15, grappling: 0.05, power: 0.1, subs: 0.03, control: 0.3, reach: 0.04, age: 0.15, layoff: 0.05, experience: 0.05, form: 0.2 };
  // Sliders are real importance on a 0-10 scale: one point = the same push on the win chance
  // for a typical difference between two fighters, whatever the factor's units.
  const UFC_DEFAULT_WEIGHTS = { elo: 10, striking: 3, grappling: 1, power: 1, subs: 1, control: 3, reach: 1, age: 3, layoff: 1, experience: 1, form: 2 };
  const UFC_PRESETS = {
    'Rating only': Object.fromEntries(UFC_FACTORS.map((f) => [f.key, f.key === 'elo' ? 10 : 0])),
    Striker: { elo: 7, striking: 9, grappling: 1, power: 8, subs: 0, control: 1, reach: 5, age: 4, layoff: 1, experience: 1, form: 2 },
    Grappler: { elo: 7, striking: 2, grappling: 9, power: 1, subs: 7, control: 9, reach: 1, age: 4, layoff: 1, experience: 1, form: 2 },
  };
  // Units: logit change per slider point per unit of each factor's edge.
  const UFC_DEFAULT_UNIT = Object.fromEntries(UFC_FACTORS.map((f) => [f.key, UFC_DEFAULT_COEF[f.key] / UFC_DEFAULT_WEIGHTS[f.key]]));
  const yearsBetween = (a, b) => (new Date(b) - new Date(a)) / 3.15576e10;
  function ufcFeatures(st, fighter, date) {
    const mins = st.secs / 60;
    const age = fighter && fighter.dob ? yearsBetween(fighter.dob, date) : 30;
    const days = st.last ? (new Date(date) - new Date(st.last)) / 864e5 : 0;
    return {
      elo: (st.elo - 1500) / 100,
      striking: (st.ssl - st.sslAbs) / (mins + 10),
      grappling: 15 * (st.tdl - st.tdlAbs) / (mins + 15),
      power: 15 * st.kd / (mins + 15),
      subs: 15 * st.sub / (mins + 15),
      control: st.ctrl / (st.secs + 600),
      reach: fighter && fighter.r ? fighter.r : null,
      age: -age / 5,
      layoff: -Math.log1p(Math.max(0, days - 120) / 365),
      experience: Math.log1p(st.n),
      form: st.res.length ? mean(st.res.slice(-3)) - 0.5 : 0,
    };
  }
  function ufcDiff(fa, fb) {
    const x = {};
    UFC_FACTORS.forEach(({ key }) => {
      if (key === 'reach') x.reach = fa.reach != null && fb.reach != null ? (fa.reach - fb.reach) / 3 : 0;
      else x[key] = fa[key] - fb[key];
    });
    return x;
  }
  const ufcLogit = (x, unit, weights) => UFC_FACTORS.reduce((z, { key }) => z + (unit[key] || 0) * (weights[key] ?? 0) * x[key], 0);
  const sigmoid = (z) => 1 / (1 + Math.exp(-z));
  // Walk every fight in order. onFight(f, x, p) sees each finished fight before it updates
  // the fighters; returns final fighter states and predictions for fights not yet fought.
  function ufcWalk(data, weights, unit, onFight) {
    const tr = data.meta.trained && data.meta.trained.unit ? data.meta.trained : null;
    unit = unit || (tr ? tr.unit : UFC_DEFAULT_UNIT);
    weights = weights || (tr ? tr.weights : UFC_DEFAULT_WEIGHTS);
    const S = {}, F = data.fighters || {};
    const get = (id) => S[id] || (S[id] = { elo: 1450, n: 0, secs: 0, ssl: 0, sslAbs: 0, tdl: 0, tdlAbs: 0, kd: 0, sub: 0, ctrl: 0, res: [], last: null, wc: '' });
    const upcoming = [];
    for (const f of data.fights) {
      const A = get(f.a), B = get(f.b);
      const x = ufcDiff(ufcFeatures(A, F[f.a], f.d), ufcFeatures(B, F[f.b], f.d));
      const p = sigmoid(ufcLogit(x, unit, weights));
      if (f.win == null) { upcoming.push({ f, x, p }); continue; }
      if (onFight) onFight(f, x, p);
      // rating update (bigger swing for finishes, newcomers move faster)
      const sa = f.win === 'a' ? 1 : f.win === 'b' ? 0 : 0.5;
      const ea = 1 / (1 + 10 ** ((B.elo - A.elo) / 400));
      const mult = f.win !== 'draw' && !f.dec ? 1.2 : 1;
      const kA = (A.n < 5 ? 45 : 32) * mult, kB = (B.n < 5 ? 45 : 32) * mult;
      A.elo += kA * (sa - ea); B.elo += kB * (ea - sa);
      if (f.sa && f.sb && f.sec) {
        const [kda, ssla, , tdla, , suba, ctrla] = f.sa, [kdb, sslb, , tdlb, , subb, ctrlb] = f.sb;
        A.secs += f.sec; B.secs += f.sec;
        A.ssl += ssla; A.sslAbs += sslb; B.ssl += sslb; B.sslAbs += ssla;
        A.tdl += tdla; A.tdlAbs += tdlb; B.tdl += tdlb; B.tdlAbs += tdla;
        A.kd += kda; B.kd += kdb; A.sub += suba; B.sub += subb; A.ctrl += ctrla; B.ctrl += ctrlb;
      }
      A.n++; B.n++; A.res.push(sa); B.res.push(1 - sa); A.last = f.d; B.last = f.d; A.wc = f.wc; B.wc = f.wc;
    }
    return { state: S, upcoming, unit };
  }
  // Accuracy from a given year on: winners picked, calibration, and vs the betting favorite.
  function ufcBacktest(data, weights, fromYear) {
    const r = { n: 0, right: 0, ll: 0, favN: 0, favRight: 0, byYear: {}, cal: [[0.5, 0.6], [0.6, 0.7], [0.7, 0.8], [0.8, 1.01]].map(([lo, hi]) => ({ lo, hi, n: 0, p: 0, won: 0 })), picks: [] };
    ufcWalk(data, weights, null, (f, x, p) => {
      if (+f.d.slice(0, 4) < fromYear || f.win === 'draw') return;
      const aWon = f.win === 'a', pick = p >= 0.5;
      const y = f.d.slice(0, 4), by = r.byYear[y] || (r.byYear[y] = { n: 0, right: 0 });
      r.n++; by.n++; if (pick === aWon) { r.right++; by.right++; }
      r.ll -= Math.log(Math.max(1e-6, aWon ? p : 1 - p));
      const pf = Math.max(p, 1 - p), bin = r.cal.find((b) => pf >= b.lo && pf < b.hi);
      if (bin) { bin.n++; bin.p += pf; bin.won += (pick === aWon) ? 1 : 0; }
      if (f.aml != null && f.bml != null && f.aml !== f.bml) { r.favN++; if ((f.aml < f.bml) === aWon) r.favRight++; }
      r.picks.push({ id: f.id, p, aWon });
    });
    r.ll /= r.n || 1;
    return r;
  }
  const ufcTrainedWeights = (data) => (data.meta.trained && data.meta.trained.weights) || UFC_DEFAULT_WEIGHTS;
  api.ufc = { FACTORS: UFC_FACTORS, PRESETS: UFC_PRESETS, DEFAULT_COEF: UFC_DEFAULT_COEF, DEFAULT_WEIGHTS: UFC_DEFAULT_WEIGHTS, trainedWeights: ufcTrainedWeights, walk: ufcWalk, backtest: ufcBacktest, features: ufcFeatures, logit: ufcLogit, sigmoid };

  Object.assign(api, { configure, DEFAULT_SETTINGS, teamProfiles, ratings, predict, backtest, propOf, walk, starterRuns, priors: () => PRIORS,
    defenseAllowed, projectProp, overProb, impliedProb, blendWin, evPerUnit, profitPerUnit, phi, statOf });
  configure({ family: 'football' });
  root.Engine = api;
})(typeof window !== 'undefined' ? window : globalThis);
