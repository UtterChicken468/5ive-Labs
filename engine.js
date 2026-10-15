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
        Balanced: { margin: 5, offEpa: 6, defEpa: 6, ypp: 4, turnovers: 3, passRush: 2, form: 3, discipline: 1, sos: 3 },
        'Efficiency first': { margin: 2, offEpa: 9, defEpa: 8, ypp: 6, turnovers: 1, passRush: 2, form: 1, discipline: 0, sos: 3 },
        Scoreboard: { margin: 10, offEpa: 2, defEpa: 2, ypp: 1, turnovers: 2, passRush: 0, form: 6, discipline: 0, sos: 2 },
        'Defense wins': { margin: 3, offEpa: 3, defEpa: 10, ypp: 3, turnovers: 7, passRush: 7, form: 2, discipline: 2, sos: 2 },
      },
    },
    hockey: {
      factors: [
        { key: 'margin', label: 'Goal differential', hint: 'Goals for minus against, per game', fn: (t) => t.margin },
        { key: 'shotShare', label: 'Shot share', hint: 'Share of all shots on goal taken by this team', fn: (t) => div(t.S.sf, t.S.sf + t.S.o_sf) },
        { key: 'finishing', label: 'Finishing', hint: 'Goals per shot on goal', fn: (t) => div(t.S.gf, t.S.sf) },
        { key: 'goaltending', label: 'Goaltending', hint: 'Save percentage', fn: (t) => 1 - div(t.S.o_gf, t.S.o_sf) },
        { key: 'specialTeams', label: 'Special teams', hint: 'Power play % plus penalty kill %', fn: (t) => (t.S.pp + t.S.pk) / t.n },
        { key: 'faceoffs', label: 'Faceoffs', hint: 'Faceoff win percentage', fn: (t) => t.S.fo / t.n },
        { key: 'form', label: 'Recent form', hint: 'Goal differential over the last few games', fn: (t) => t.form },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
      ],
      presets: {
        Balanced: { margin: 5, shotShare: 6, finishing: 3, goaltending: 5, specialTeams: 3, faceoffs: 1, form: 3, sos: 2 },
        'Shot share': { margin: 2, shotShare: 10, finishing: 1, goaltending: 3, specialTeams: 2, faceoffs: 2, form: 1, sos: 2 },
        'Hot goalie': { margin: 4, shotShare: 3, finishing: 2, goaltending: 10, specialTeams: 2, faceoffs: 0, form: 5, sos: 1 },
        Scoreboard: { margin: 10, shotShare: 2, finishing: 2, goaltending: 2, specialTeams: 2, faceoffs: 0, form: 6, sos: 2 },
      },
    },
    basketball: {
      factors: [
        { key: 'margin', label: 'Scoring margin', hint: 'Points scored minus allowed, per game', fn: (t) => t.margin },
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
        Balanced: { margin: 5, offEff: 6, defEff: 6, shooting: 4, ballSecurity: 3, rebounding: 3, freeThrows: 1, form: 2, sos: 5 },
        'Four factors': { margin: 1, offEff: 3, defEff: 3, shooting: 10, ballSecurity: 6, rebounding: 5, freeThrows: 3, form: 1, sos: 5 },
        Scoreboard: { margin: 10, offEff: 3, defEff: 3, shooting: 1, ballSecurity: 1, rebounding: 1, freeThrows: 0, form: 5, sos: 4 },
        'Defense wins': { margin: 3, offEff: 3, defEff: 10, shooting: 3, ballSecurity: 6, rebounding: 4, freeThrows: 1, form: 2, sos: 4 },
      },
    },
    baseball: {
      factors: [
        { key: 'margin', label: 'Run differential', hint: 'Runs scored minus allowed, per game', fn: (t) => t.margin },
        { key: 'hitting', label: 'Hitting', hint: 'On-base plus slugging (OPS)', fn: (t) => div(t.S.h + t.S.bb + t.S.hbp, t.S.pa) + div(t.S.tb, t.S.pa - t.S.bb - t.S.hbp - t.S.sf) },
        { key: 'discipline', label: 'Plate discipline', hint: 'Walk rate minus strikeout rate', fn: (t) => div(t.S.bb - t.S.so, t.S.pa) },
        { key: 'rotation', label: 'Starting rotation', hint: 'Starters\' earned runs per 9 innings (lower is better)', fn: (t) => -9 * div(t.S.sp_er, t.S.sp_ip) },
        { key: 'bullpen', label: 'Bullpen', hint: 'Relievers\' earned runs per 9 innings (lower is better)', fn: (t) => -9 * div(t.S.bp_er, t.S.bp_ip) },
        { key: 'form', label: 'Recent form', hint: 'Run differential over the last 10 games', fn: (t) => t.form },
        { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents', fn: (t) => t.sos },
        { key: 'starter', label: 'Today\'s starting pitcher', hint: 'Adjusts each game for who is pitching (5 = full effect)', game: true },
      ],
      presets: {
        Balanced: { margin: 6, hitting: 4, discipline: 2, rotation: 3, bullpen: 3, form: 2, sos: 1, starter: 5 },
        'Pitching first': { margin: 3, hitting: 2, discipline: 1, rotation: 6, bullpen: 6, form: 1, sos: 1, starter: 8 },
        'Bats first': { margin: 4, hitting: 9, discipline: 5, rotation: 2, bullpen: 2, form: 2, sos: 1, starter: 4 },
        Scoreboard: { margin: 10, hitting: 1, discipline: 0, rotation: 1, bullpen: 1, form: 6, sos: 1, starter: 3 },
      },
    },
  };
  function poss(S, p) { return S[p + 'fga'] - S[p + 'orb'] + S[p + 'tov'] + 0.475 * S[p + 'fta']; }

  // Per-sport settings; set with configure() once the sport's data loads.
  let SIGMA = 13.5, SIGMA_TOT = 13, BT_SKIP = 1, FORM_N = 3, REST = 'bye', FAMILY = 'football', PRIORS = null, LG_SV = 0.9;
  const api = {};
  function configure(cfg) {
    if (cfg.sigma) SIGMA = cfg.sigma;
    if (cfg.sigmaTot) SIGMA_TOT = cfg.sigmaTot;
    if (cfg.btSkip != null) BT_SKIP = cfg.btSkip;
    if (cfg.formN) FORM_N = cfg.formN;
    if (cfg.rest) REST = cfg.rest;
    FAMILY = cfg.family || 'football';
    api.FACTORS = FAMILIES[FAMILY].factors;
    api.PRESETS = FAMILIES[FAMILY].presets;
    PROPS = PROP_SETS[FAMILY] || []; api.PROPS = PROPS;
    PRIORS = cfg.priors || null;
    LG_SV = cfg.lgSv || 0.9;
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
      const a = acc[t] || (acc[t] = { n: 0, S: {}, pf: 0, pa: 0, hist: [], opps: [], lastWeek: -1 });
      a.n++; a.pf += pf; a.pa += pa; a.hist.push(pf - pa); a.opps.push(o); a.lastWeek = Math.max(a.lastWeek, g.w);
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
    if (teams.length < 4) return { rating: {}, contrib: {}, league: null };
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
    const scale = sd(teams.map((t) => t.margin)) * (settings.trust / 100);
    const rating = {}, contrib = {};
    const k = PRIORS ? (settings.prior ?? 4) : 0;
    teams.forEach((t) => {
      const r = comp[t.team] / cs * scale;
      const pr = PRIORS ? (PRIORS[t.team] ?? -8) : 0; // teams with no prior rating start below average
      rating[t.team] = k ? (t.n * r + k * pr) / (t.n + k) : r;
      contrib[t.team] = k ? { prior: rating[t.team] - r } : {};
      F.forEach((f) => { contrib[t.team][f.key] = (weights[f.key] || 0) * z[t.team][f.key] / wsum / cs * scale; });
    });
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
    const h = profiles[game.h], a = profiles[game.a];
    if (!h || !a || R.rating[game.h] == null || R.rating[game.a] == null) return null;
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
    const k = settings.trust / 100, L = R.league.ppg;
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
      out.pHomeCover = phi((margin - lines.sp) / SIGMA);
    }
    if (lines.tot != null) { out.totEdge = total - lines.tot; out.pOver = phi((total - lines.tot) / SIGMA_TOT); }
    if (lines.hml != null) out.evHome = evPerUnit(pHome, lines.hml);
    if (lines.aml != null) out.evAway = evPerUnit(1 - pHome, lines.aml);
    return out;
  }

  // Walk-forward backtest: each week predicted only with earlier weeks.
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
        if (picks) picks.push({ id: g.id, w, m: pr.margin, res, sp: g.sp });
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
  Object.assign(api, { configure, DEFAULT_SETTINGS, teamProfiles, ratings, predict, backtest, propOf,
    defenseAllowed, projectProp, overProb, impliedProb, evPerUnit, profitPerUnit, phi, statOf });
  configure({ family: 'football' });
  root.Engine = api;
})(typeof window !== 'undefined' ? window : globalThis);
