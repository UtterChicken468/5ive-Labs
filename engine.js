// Gridiron Model Lab engine: pure functions over the embedded data.
(function (root) {
  const SIGMA = 13.5;   // sd of NFL final margin around a good prediction
  const SIGMA_TOT = 13; // sd of game total

  const FACTORS = [
    { key: 'margin', label: 'Scoring margin', hint: 'Points scored minus allowed, per game' },
    { key: 'offEpa', label: 'Offensive efficiency', hint: 'EPA per play on offense' },
    { key: 'defEpa', label: 'Defensive efficiency', hint: 'EPA per play allowed (lower is better)' },
    { key: 'ypp', label: 'Yards per play edge', hint: 'Offense YPP minus defense YPP' },
    { key: 'turnovers', label: 'Turnover margin', hint: 'Takeaways minus giveaways, per game' },
    { key: 'passRush', label: 'Pass rush vs protection', hint: 'Sacks made minus sacks allowed, per game' },
    { key: 'form', label: 'Recent form', hint: 'Scoring margin over the last 3 games' },
    { key: 'discipline', label: 'Discipline', hint: 'Fewer penalty yards per game' },
    { key: 'sos', label: 'Schedule strength', hint: 'Credit for tougher past opponents' },
  ];

  const PRESETS = {
    Balanced: { margin: 5, offEpa: 6, defEpa: 6, ypp: 4, turnovers: 3, passRush: 2, form: 3, discipline: 1, sos: 3 },
    'Efficiency first': { margin: 2, offEpa: 9, defEpa: 8, ypp: 6, turnovers: 1, passRush: 2, form: 1, discipline: 0, sos: 3 },
    Scoreboard: { margin: 10, offEpa: 2, defEpa: 2, ypp: 1, turnovers: 2, passRush: 0, form: 6, discipline: 0, sos: 2 },
    'Defense wins': { margin: 3, offEpa: 3, defEpa: 10, ypp: 3, turnovers: 7, passRush: 7, form: 2, discipline: 2, sos: 2 },
  };

  const DEFAULT_SETTINGS = { hfa: 1.5, trust: 60, bye: 1.0 };

  const phi = (x) => { // standard normal CDF
    const t = 1 / (1 + 0.2316419 * Math.abs(x));
    const d = 0.3989423 * Math.exp(-x * x / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return x > 0 ? 1 - p : p;
  };
  const mean = (a) => a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0;
  const sd = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) ** 2))) || 0; };

  // Season-to-date team profiles using only games before `beforeWeek`.
  function teamProfiles(data, beforeWeek) {
    const rows = data.teamGames.filter((r) => r.w < beforeWeek);
    const byKey = {}; rows.forEach((r) => { byKey[r.w + '|' + r.t] = r; });
    const games = data.games.filter((g) => g.w < beforeWeek && g.hs != null);
    const P = {};
    const get = (t) => P[t] || (P[t] = { team: t, g: [], });
    games.forEach((g) => {
      [[g.h, g.a, g.hs, g.as_], [g.a, g.h, g.as_, g.hs]].forEach(([t, o, pf, pa]) => {
        const me = byKey[g.w + '|' + t], op = byKey[g.w + '|' + o];
        if (!me || !op) return;
        get(t).g.push({ w: g.w, opp: o, pf, pa, me, op });
      });
    });
    const out = {};
    Object.values(P).forEach((p) => {
      const n = p.g.length; if (!n) return;
      const sum = (f) => p.g.reduce((s, x) => s + f(x), 0);
      const offPlays = sum((x) => x.me.pl), defPlays = sum((x) => x.op.pl);
      const last = [...p.g].sort((a, b) => b.w - a.w).slice(0, 3);
      out[p.team] = {
        team: p.team, n,
        pf: sum((x) => x.pf) / n, pa: sum((x) => x.pa) / n,
        margin: sum((x) => x.pf - x.pa) / n,
        offEpa: sum((x) => x.me.epa) / offPlays,
        defEpa: sum((x) => x.op.epa) / defPlays,
        offYpp: sum((x) => x.me.py + x.me.ry) / offPlays,
        defYpp: sum((x) => x.op.py + x.op.ry) / defPlays,
        turnovers: sum((x) => x.op.to - x.me.to) / n,
        passRush: sum((x) => x.me.sk - x.me.ss) / n,
        form: mean(last.map((x) => x.pf - x.pa)),
        penYds: sum((x) => x.me.pen) / n,
        opps: p.g.map((x) => x.opp),
        lastWeek: Math.max(...p.g.map((x) => x.w)),
      };
    });
    // schedule strength = mean opponent margin
    Object.values(out).forEach((t) => { t.sos = mean(t.opps.map((o) => (out[o] ? out[o].margin : 0))); });
    return out;
  }

  // Raw factor values where higher = better.
  function factorValue(t, key) {
    switch (key) {
      case 'defEpa': return -t.defEpa;
      case 'ypp': return t.offYpp - t.defYpp;
      case 'discipline': return -t.penYds;
      default: return t[key];
    }
  }

  // Ratings in points vs an average team.
  function ratings(profiles, weights, settings) {
    const teams = Object.values(profiles);
    if (teams.length < 4) return { rating: {}, contrib: {}, league: null };
    const z = {};
    FACTORS.forEach((f) => {
      const vals = teams.map((t) => factorValue(t, f.key));
      const m = mean(vals), s = sd(vals) || 1;
      teams.forEach((t) => { (z[t.team] || (z[t.team] = {}))[f.key] = (factorValue(t, f.key) - m) / s; });
    });
    const wsum = FACTORS.reduce((s, f) => s + (weights[f.key] || 0), 0) || 1;
    const comp = {};
    teams.forEach((t) => { comp[t.team] = FACTORS.reduce((s, f) => s + (weights[f.key] || 0) * z[t.team][f.key], 0) / wsum; });
    const cs = sd(Object.values(comp)) || 1;
    const scale = sd(teams.map((t) => t.margin)) * (settings.trust / 100);
    const rating = {}, contrib = {};
    teams.forEach((t) => {
      rating[t.team] = comp[t.team] / cs * scale;
      contrib[t.team] = {};
      FACTORS.forEach((f) => { contrib[t.team][f.key] = (weights[f.key] || 0) * z[t.team][f.key] / wsum / cs * scale; });
    });
    const league = { ppg: mean(teams.map((t) => t.pf)) };
    return { rating, contrib, z, league };
  }

  const impliedProb = (ml) => ml == null ? null : ml < 0 ? -ml / (-ml + 100) : 100 / (ml + 100);
  const profitPerUnit = (ml) => ml < 0 ? 100 / -ml : ml / 100;
  const evPerUnit = (p, ml) => p * profitPerUnit(ml) - (1 - p);

  // Predict one game. lines: {sp (home favored by), tot, hml, aml}
  function predict(game, profiles, R, settings, lines) {
    const h = profiles[game.h], a = profiles[game.a];
    if (!h || !a || R.rating[game.h] == null || R.rating[game.a] == null) return null;
    let margin = R.rating[game.h] - R.rating[game.a] + (game.neu ? 0 : settings.hfa);
    const restEdge = (game.hr >= 10 ? 1 : 0) - (game.ar >= 10 ? 1 : 0); // coming off a bye
    margin += restEdge * settings.bye;
    const k = settings.trust / 100, L = R.league.ppg;
    const homePts = L + k * (((h.pf + a.pa) / 2) - L);
    const awayPts = L + k * (((a.pf + h.pa) / 2) - L);
    const total = homePts + awayPts;
    const hs = total / 2 + margin / 2, as = total / 2 - margin / 2;
    const pHome = phi(margin / SIGMA);
    const out = { margin, total, hs, as, pHome, pAway: 1 - pHome };
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
  function backtest(data, weights, settings, edgeMin) {
    const weeks = [...new Set(data.games.filter((g) => g.hs != null).map((g) => g.w))].sort((a, b) => a - b);
    const per = []; let su = 0, n = 0, vegasSu = 0, vegasN = 0, ats = [0, 0, 0], mae = 0, vegasMae = 0, ou = [0, 0, 0];
    weeks.filter((w) => w >= 2).forEach((w) => {
      const P = teamProfiles(data, w); const R = ratings(P, weights, settings);
      const wk = { w, su: 0, n: 0, atsW: 0, atsL: 0 };
      data.games.filter((g) => g.w === w && g.hs != null).forEach((g) => {
        const pr = predict(g, P, R, settings, { sp: g.sp, tot: g.tot }); if (!pr) return;
        const res = g.hs - g.as_;
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
    return { su, n, vegasSu, vegasN, ats, ou, mae: games ? mae / games : 0, vegasMae: games ? vegasMae / games : 0, per };
  }

  // ---------- Player props ----------
  const PROPS = [
    { key: 'py', label: 'Passing yards', pos: ['QB'], dist: 'normal', group: 'pass' },
    { key: 'ptd', label: 'Passing TDs', pos: ['QB'], dist: 'poisson', group: 'passTd' },
    { key: 'ry', label: 'Rushing yards', pos: ['QB', 'RB', 'WR'], dist: 'normal', group: 'rush' },
    { key: 'rec', label: 'Receptions', pos: ['RB', 'WR', 'TE'], dist: 'poisson', group: 'rec' },
    { key: 'rcy', label: 'Receiving yards', pos: ['RB', 'WR', 'TE'], dist: 'normal', group: 'recYds' },
    { key: 'rry', label: 'Rush + rec yards', pos: ['RB', 'WR', 'TE'], dist: 'normal', group: 'scrim' },
  ];
  const statOf = (r, key) => key === 'rry' ? r.ry + r.rcy : r[key];

  // What each defense allows per game, by stat and position group, vs league average.
  function defenseAllowed(data) {
    const games = {}; // team -> set of weeks
    data.teamGames.forEach((r) => { (games[r.t] || (games[r.t] = new Set())).add(r.w); });
    const tot = {};
    data.players.forEach((r) => {
      PROPS.forEach((p) => {
        if (!p.pos.includes(r.p)) return;
        const k = r.o + '|' + p.key + '|' + (p.key === 'py' || p.key === 'ptd' ? 'QB' : r.p);
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
      const p = (stat === 'py' || stat === 'ptd') ? 'QB' : pos;
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
    const prop = PROPS.find((p) => p.key === propKey);
    const vals = games.map((g) => statOf(g, propKey));
    if (!vals.length) return null;
    const season = mean(vals);
    const last3 = mean([...games].sort((a, b) => b.w - a.w).slice(0, 3).map((g) => statOf(g, propKey)));
    const rw = opts.recent / 100;
    const base = season * (1 - rw) + last3 * rw;
    const mf = defFn(opp, propKey, games[0].p);
    const adj = 1 + (mf - 1) * (opts.matchup / 100) * 0.6; // damp: small samples
    const proj = Math.max(0, base * adj);
    const spread = Math.max(sd(vals), proj * 0.3, prop.dist === 'normal' ? 8 : 0.5);
    return { proj, season, last3, matchup: mf, spread, dist: prop.dist, n: vals.length, vals };
  }

  function overProb(pj, line) {
    if (pj.dist === 'poisson') return 1 - poissonCdf(Math.floor(line), Math.max(pj.proj, 0.05));
    return 1 - phi((line - pj.proj) / pj.spread);
  }

  root.Engine = { FACTORS, PRESETS, DEFAULT_SETTINGS, PROPS, teamProfiles, ratings, predict, backtest,
    defenseAllowed, projectProp, overProb, impliedProb, evPerUnit, profitPerUnit, phi, statOf };
})(typeof window !== 'undefined' ? window : globalThis);
