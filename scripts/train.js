// Trains each sport's model from past seasons and writes it into data/<sport>.json as meta.trained.
//
// How it works:
//  1. Walk every past season in order. Before each game, rate both teams using only earlier
//     games (no peeking), and record the difference between them on every factor.
//  2. Fit how many points each factor is worth (ridge regression, factors kept non-negative),
//     plus the home edge, rest effect and, for baseball, the starting-pitcher effect.
//  3. Honest check: train without the most recent past season, then test on it.
//  4. Train the final model on all past seasons plus this season so far, and calibrate how
//     spread out the win chances should be from the test season's actual misses.
//
// Run: node scripts/train.js   (the GitHub job runs it after every data refresh)
const fs = require('fs');
const path = require('path');
require(path.join(__dirname, '..', 'engine.js'));
const E = globalThis.Engine;

const DATA = path.join(__dirname, '..', 'data');
const HIST = path.join(DATA, 'history');
const SPORTS = ['nfl', 'cfb', 'nhl', 'mlb', 'cbb', 'nba'];

const mean = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0);
const sd = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) ** 2))); };
const round = (v, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

function solve(A, b) { // Gaussian elimination with partial pivoting
  const n = b.length, M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    if (Math.abs(M[c][c]) < 1e-12) continue;
    for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
  }
  return M.map((r, i) => (Math.abs(r[i]) < 1e-12 ? 0 : r[n] / r[i]));
}

// Ridge regression with every coefficient kept >= 0 (drop negatives and refit).
function fitNonNeg(X, y, lambda) {
  const p = X[0].length; let active = [...Array(p).keys()], beta = new Array(p).fill(0);
  for (let pass = 0; pass < p + 1; pass++) {
    const A = active.map((i) => active.map((j) => X.reduce((s, r) => s + r[i] * r[j], 0) + (i === j ? lambda : 0)));
    const b = active.map((i) => X.reduce((s, r, k) => s + r[i] * y[k], 0));
    const sol = solve(A, b);
    beta = new Array(p).fill(0); active.forEach((i, k) => { beta[i] = sol[k]; });
    const neg = active.filter((i) => beta[i] < 0);
    if (!neg.length) break;
    active = active.filter((i) => beta[i] > 0);
    if (!active.length) { beta = new Array(p).fill(0); break; }
  }
  return beta;
}

function load(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }

// One row per finished game: each team's factor scores before kickoff, plus game context.
function rowsFor(ds, cur) {
  E.configure({ ...cur.meta, priors: ds.meta.priors || null });
  const keys = E.FACTORS.filter((f) => !f.game).map((f) => f.key);
  const unit = Object.fromEntries(keys.map((k) => [k, 1]));
  const pri = E.priors();
  const cap = ({ football: 28, hockey: 5, basketball: 30, baseball: 8 })[cur.meta.family];
  const rows = [];
  E.walk(ds, (w, games, P) => {
    const R = E.ratings(P, unit, { trust: 100, hfa: 0 });
    if (!R.z) return;
    games.forEach((g) => {
      const h = P[g.h], a = P[g.a];
      if (!h || !a || !R.z[g.h] || !R.z[g.a]) return;
      const rest = cur.meta.rest === 'bye' ? (g.hr >= 10 ? 1 : 0) - (g.ar >= 10 ? 1 : 0)
        : cur.meta.rest === 'b2b' ? (g.ar <= 1 ? 1 : 0) - (g.hr <= 1 ? 1 : 0) : 0;
      const L = R.league.ppg;
      rows.push({
        zh: keys.map((k) => R.z[g.h][k]), za: keys.map((k) => R.z[g.a][k]), nh: h.n, na: a.n,
        hasP: !!pri, ph: pri ? (pri[g.h] ?? -8) : 0, pa: pri ? (pri[g.a] ?? -8) : 0,
        home: g.neu ? 0 : 1, rest,
        st: cur.meta.family === 'baseball' ? E.starterRuns(ds, g.hp, g.w) - E.starterRuns(ds, g.ap, g.w) : 0,
        y: Math.max(-cap, Math.min(cap, g.hs - g.as_)),
        totX: ((h.pf + a.pa) / 2 - L) + ((a.pf + h.pa) / 2 - L), totY: g.hs + g.as_ - 2 * L,
      });
    });
  });
  return { rows, keys };
}

function design(rows, k, fam) {
  const X = [], y = [];
  rows.forEach((r) => {
    const kk = r.hasP ? k : 0; // seasons without a prior (the oldest one) just use their own games
    const wh = kk ? r.nh / (r.nh + kk) : 1, wa = kk ? r.na / (r.na + kk) : 1;
    const off = kk ? (kk * r.ph / (r.nh + kk) - kk * r.pa / (r.na + kk)) : 0;
    const x = r.zh.map((v, i) => wh * v - wa * r.za[i]);
    x.push(r.home, r.rest);
    if (fam === 'baseball') x.push(r.st);
    X.push(x); y.push(r.y - off);
  });
  return { X, y };
}

function fitModel(rows, keys, k, meta) {
  const fam = meta.family;
  const { X, y } = design(rows, k, fam);
  const beta = fitNonNeg(X, y, Math.max(5, rows.length * 0.01));
  const fb = beta.slice(0, keys.length), maxB = Math.max(...fb, 1e-6);
  const ppw = maxB / 10;
  const weights = Object.fromEntries(keys.map((key, i) => [key, round(fb[i] / ppw, 1)]));
  const hfa = round(beta[keys.length], 3), rest = round(beta[keys.length + 1], 3);
  if (fam === 'baseball') weights.starter = round(Math.min(10, 5 * beta[keys.length + 2]), 1);
  // totals: how far to trust each team's scoring vs the league average
  const tx = rows.map((r) => r.totX), ty = rows.map((r) => r.totY);
  const kt = Math.min(1.3, Math.max(0.1, tx.reduce((s, v, i) => s + v * ty[i], 0) / (tx.reduce((s, v) => s + v * v, 0) || 1)));
  const totRes = rows.map((r) => r.totY - kt * r.totX);
  // Keep the small-sample effects inside sensible ranges (a handful of byes can't justify 10 points).
  const settings = { hfa: Math.min(meta.hfaMax ?? 4, Math.max(0, hfa)), trust: 100, ppw: round(ppw, 4), totTrust: round(kt * 100, 1),
    bye: meta.rest === 'none' ? 0 : Math.min(meta.byeMax ?? 3, Math.max(0, rest)), prior: k };
  return { weights, settings, sigmaTot: round(sd(totRes), 2) };
}

function evaluate(ds, cur, model) {
  E.configure({ ...cur.meta, priors: ds.meta.priors || null });
  const b = E.backtest(ds, model.weights, { ...model.settings, qbOut: 0, weather: 0, goalie: 0 }, 0, true);
  const res = b.picks.map((p) => p.res - p.m);
  return { b, sigma: sd(res) };
}

function calibration(picks, sigma) {
  const bins = [[0.5, 0.6], [0.6, 0.7], [0.7, 0.8], [0.8, 1.01]].map(([lo, hi]) => ({ lo, hi, n: 0, p: 0, won: 0 }));
  picks.forEach((x) => {
    if (x.res === 0) return;
    const pFav = E.phi(Math.abs(x.m) / sigma), favWon = (x.m > 0) === (x.res > 0);
    const bin = bins.find((q) => pFav >= q.lo && pFav < q.hi); if (!bin) return;
    bin.n++; bin.p += pFav; bin.won += favWon ? 1 : 0;
  });
  return bins.filter((q) => q.n).map((q) => [Math.round(q.p / q.n * 100), Math.round(q.won / q.n * 100), q.n]);
}

// Moneylines: the blend of model and market win chance with the lowest log loss on past games.
function fitMl(rows) {
  if (rows.length < 150) return undefined;
  const ll = (w) => rows.reduce((s, [pm, pk, y]) => { const p = Math.min(0.99, Math.max(0.01, w * pm + (1 - w) * pk)); return s - (y ? Math.log(p) : Math.log(1 - p)); }, 0) / rows.length;
  let best = 0; for (let w = 0; w <= 1.0001; w += 0.05) if (ll(w) < ll(best)) best = w;
  return { w: round(best, 2), n: rows.length, logLoss: round(ll(best), 4), marketLogLoss: round(ll(0), 4), modelLogLoss: round(ll(1), 4) };
}

// How much of the model's gap from the Vegas line actually shows up in results, per market.
// Regress (result - line) on (model - line) over every past game with a line; slope kept in 0..1.
function marketTrust(past, cur, model) {
  const sp = [], tot = [], ml = [];
  past.forEach((ds) => {
    E.configure({ ...cur.meta, mkt: null, priors: ds.meta.priors || null });
    E.backtest(ds, model.weights, { ...model.settings, qbOut: 0, weather: 0, goalie: 0 }, 0, true).picks.forEach((p) => {
      if (p.sp != null) sp.push([p.m - p.sp, p.res - p.sp]);
      if (p.tot != null && p.pOver != null) tot.push([p.t - p.tot, p.at - p.tot]);
      if (p.hml != null && p.aml != null && p.res !== 0) {
        const a = E.impliedProb(p.hml), b = E.impliedProb(p.aml);
        ml.push([p.pH, a / (a + b), p.res > 0 ? 1 : 0]);
      }
    });
  });
  const fit = (rows, min) => {
    if (rows.length < 150) return undefined;
    const sxy = rows.reduce((s, [x, y]) => s + x * y, 0), sxx = rows.reduce((s, [x]) => s + x * x, 0) || 1;
    const big = rows.filter(([x, y]) => Math.abs(x) >= min && y !== 0), won = big.filter(([x, y]) => (x > 0) === (y > 0)).length;
    // Top picks: the quarter of games where the model disagreed with the line the most.
    const gaps = rows.map(([x]) => Math.abs(x)).sort((a, b) => a - b), topMin = gaps[Math.floor(gaps.length * 0.75)];
    const top = rows.filter(([x, y]) => Math.abs(x) >= topMin && y !== 0), topWon = top.filter(([x, y]) => (x > 0) === (y > 0)).length;
    return { s: round(Math.min(1, Math.max(0, sxy / sxx)), 3), sd: round(Math.sqrt(rows.reduce((s, [, y]) => s + y * y, 0) / rows.length), 2),
      n: rows.length, hitN: big.length, hit: big.length ? round(won / big.length * 100, 1) : null,
      topMin: round(topMin, 2), topN: top.length, topHit: top.length ? round(topWon / top.length * 100, 1) : null };
  };
  const min = cur.meta.edgeDefault ?? 1.5;
  const out = { sp: fit(sp, min), tot: fit(tot, min), ml: fitMl(ml), edgeMin: min };
  return out.sp || out.tot || out.ml ? out : null;
}

for (const sport of SPORTS) {
  const curFile = path.join(DATA, sport + '.json');
  if (!fs.existsSync(curFile)) continue;
  const cur = load(curFile);
  const histFiles = fs.existsSync(HIST) ? fs.readdirSync(HIST).filter((f) => f.startsWith(sport + '_') && f.endsWith('.json')).sort() : [];
  if (!histFiles.length) { console.log(sport + ': no past seasons yet, skipping'); continue; }
  const past = histFiles.map((f) => load(path.join(HIST, f)));
  const t0 = Date.now();
  const built = past.map((ds) => ({ ds, ...rowsFor(ds, cur) }));
  const keys = built[0].keys;
  const curRows = rowsFor(cur, cur).rows;

  // Honest test: train without the newest complete season, test on it. Between seasons
  // (college basketball and MLB in the fall) the site's own file is that complete season.
  const offseason = !cur.games.some((g) => g.hs == null && g.d >= new Date().toISOString().slice(0, 10));
  const curBuilt = { ds: cur, rows: curRows, keys };
  const test = offseason ? curBuilt : built[built.length - 1];
  const trainRows = (offseason ? built : built.slice(0, -1)).flatMap((x) => x.rows);
  const kGrid = cur.meta.priors ? (cur.meta.period === 'Day' ? [0, 3, 6, 10, 15, 25, 40] : [0, 1, 2, 3, 4, 6]) : [0];
  let best = null;
  for (const k of kGrid) {
    const m = trainRows.length > 200 ? fitModel(trainRows, keys, k, cur.meta) : fitModel(test.rows, keys, k, cur.meta);
    const ev = evaluate(test.ds, cur, m);
    if (!best || ev.b.mae < best.ev.b.mae) best = { k, m, ev };
  }
  const bal = evaluate(test.ds, cur, { weights: E.PRESETS.Balanced, settings: { hfa: cur.meta.hfa, trust: cur.meta.trust, bye: cur.meta.bye || 0, prior: cur.meta.priorGames || 0 } });
  const tb = best.ev.b, bb = bal.b;

  // Final model: every past season plus this season so far.
  const final = fitModel([...built.flatMap((x) => x.rows), ...curRows], keys, best.k, cur.meta);
  const sigma = round(best.ev.sigma, 2);
  const pct = (a, n) => (n ? round(a / n * 100, 1) : null);
  cur.meta.trained = {
    weights: final.weights, settings: final.settings, sigma, sigmaTot: final.sigmaTot,
    seasons: past.map((d) => d.season).concat(cur.season), testSeason: test.ds.season,
    games: built.reduce((s, x) => s + x.rows.length, 0) + curRows.length,
    test: { n: tb.n, su: pct(tb.su, tb.n), vegasSu: pct(tb.vegasSu, tb.vegasN), mae: round(tb.mae, 2), vegasMae: tb.vegasN ? round(tb.vegasMae, 2) : null,
      ats: tb.ats, atsPct: pct(tb.ats[0], tb.ats[0] + tb.ats[1]), balancedSu: pct(bb.su, bb.n), balancedMae: round(bb.mae, 2),
      calibration: calibration(tb.picks, sigma) },
  };
  cur.meta.trained.mkt = cur.meta.mkt = marketTrust(past, cur, final);
  cur.meta.sigma = sigma;            // win chances are spread to match the trained model's real misses
  cur.meta.sigmaTot = final.sigmaTot;
  fs.writeFileSync(curFile, JSON.stringify(cur));
  const t = cur.meta.trained.test;
  console.log(`${sport}: trained on ${cur.meta.trained.games} games (${cur.meta.trained.seasons.join(', ')}), prior=${best.k}; ` +
    `test ${test.ds.season}: winners ${t.su}% (Balanced ${t.balancedSu}%, Vegas ${t.vegasSu ?? '-'}%), miss ${t.mae} (Balanced ${t.balancedMae}, Vegas ${t.vegasMae ?? '-'}), ` +
    `ATS ${t.atsPct ?? '-'}% · ${Date.now() - t0}ms`);
  console.log('   vs Vegas', JSON.stringify(cur.meta.mkt));
  console.log('   weights', JSON.stringify(final.weights), 'settings', JSON.stringify(final.settings), 'sigma', sigma);
}

// ======================= UFC =======================
// Logistic regression on fighter-vs-fighter factor edges. Each fight is used twice (once from
// each corner, flipped) so the model can't learn anything from which corner a fighter is in.
(function trainUfc() {
  const file = path.join(DATA, 'ufc.json');
  if (!fs.existsSync(file)) return;
  const data = load(file);
  const keys = E.ufc.FACTORS.map((f) => f.key);
  const unit = Object.fromEntries(keys.map((k) => [k, 1]));
  const rows = [];
  E.ufc.walk(data, unit, unit, (f, x) => {
    const y = +f.d.slice(0, 4);
    if (y < data.meta.firstYear + 2 || f.win === 'draw') return; // first two years warm up the ratings
    rows.push({ year: y, x: keys.map((k) => x[k]), won: f.win === 'a' ? 1 : 0 });
  });
  if (rows.length < 300) { console.log('ufc: not enough fights yet'); return; }
  // Non-negative: a factor that comes out negative is dropped and the rest refit, so raising any
  // slider always means "this helps a fighter".
  function fitLogistic(rs, lambda = 2) {
    let active = keys.map((_, i) => i), coef;
    for (let pass = 0; pass <= keys.length; pass++) {
      coef = fitLogisticOn(rs, lambda, active);
      const neg = active.filter((i) => coef[keys[i]] < 0);
      if (!neg.length) break;
      active = active.filter((i) => coef[keys[i]] > 0);
    }
    return coef;
  }
  function fitLogisticOn(rs, lambda, active) {
    const on = new Set(active);
    let b = new Array(keys.length).fill(0);
    for (let it = 0; it < 25; it++) {
      const g = new Array(keys.length).fill(0), H = keys.map(() => new Array(keys.length).fill(0));
      for (const r of rs) for (const s of [1, -1]) { // both corners
        const x = r.x.map((v) => v * s), yv = s === 1 ? r.won : 1 - r.won;
        const p = 1 / (1 + Math.exp(-x.reduce((t, v, i) => t + v * b[i], 0)));
        for (let i = 0; i < keys.length; i++) { g[i] += (yv - p) * x[i]; for (let j = 0; j < keys.length; j++) H[i][j] += p * (1 - p) * x[i] * x[j]; }
      }
      for (let i = 0; i < keys.length; i++) { g[i] -= lambda * b[i]; H[i][i] += lambda; if (!on.has(i)) { g[i] = 0; H[i] = H[i].map((_, j) => (j === i ? 1 : 0)); H.forEach((row, r) => { if (r !== i) row[i] = 0; }); } }
      const step = solve(H, g); b = b.map((v, i) => (on.has(i) ? v + step[i] : 0));
      if (Math.max(...step.map(Math.abs)) < 1e-6) break;
    }
    return Object.fromEntries(keys.map((k, i) => [k, round(b[i], 4)]));
  }
  const score = (rs, coef) => {
    let right = 0, ll = 0;
    rs.forEach((r) => { const p = 1 / (1 + Math.exp(-r.x.reduce((t, v, i) => t + v * coef[keys[i]], 0))); right += (p >= 0.5) === (r.won === 1) ? 1 : 0; ll -= Math.log(Math.max(1e-6, r.won ? p : 1 - p)); });
    return { n: rs.length, su: round(right / rs.length * 100, 1), ll: round(ll / rs.length, 3) };
  };
  const years = [...new Set(rows.map((r) => r.year))].sort();
  const thisYear = new Date().getFullYear();
  const testYear = years.filter((y) => y < thisYear).pop();
  const train = rows.filter((r) => r.year < testYear), test = rows.filter((r) => r.year === testYear);
  const coefTest = fitLogistic(train);
  const t = score(test, coefTest), base = score(test, E.ufc.DEFAULT_COEF);
  // calibration + betting favorite on the test year, using the model's own walk
  // Slider scale: importance = coefficient x typical spread of that factor's edge.
  const spread = keys.map((k, i) => Math.sqrt(rows.reduce((t, r) => t + r.x[i] * r.x[i], 0) / rows.length) || 1);
  const toSliders = (c) => {
    const imp = keys.map((k, i) => Math.max(0, c[k]) * spread[i]), top = Math.max(...imp, 1e-9);
    const unitK = Object.fromEntries(keys.map((k, i) => [k, round(top / 10 / spread[i], 6)]));
    const w = Object.fromEntries(keys.map((k, i) => [k, round(10 * imp[i] / top, 1)]));
    return { unit: unitK, weights: w };
  };
  const sTest = toSliders(coefTest);
  const bt = E.ufc.backtest({ ...data, meta: { ...data.meta, trained: { ...sTest } } }, sTest.weights, testYear);
  const cal = bt.cal.filter((q) => q.n).map((q) => [Math.round(q.p / q.n * 100), Math.round(q.won / q.n * 100), q.n]);
  const coef = fitLogistic(rows);
  const sl = toSliders(coef);
  data.meta.trained = { coef, unit: sl.unit, weights: sl.weights, fights: rows.length, years: [years[0], years[years.length - 1]], testYear,
    test: { ...t, baseSu: base.su, baseLl: base.ll, favSu: bt.favN ? round(bt.favRight / bt.favN * 100, 1) : null, favN: bt.favN, calibration: cal } };
  fs.writeFileSync(file, JSON.stringify(data));
  console.log(`ufc: trained on ${rows.length} fights (${years[0]}-${years[years.length - 1]}); test ${testYear}: winners ${t.su}% (untrained ${base.su}%), log loss ${t.ll} (untrained ${base.ll})`);
  console.log('   coef', JSON.stringify(coef), '\n   sliders', JSON.stringify(sl.weights));
})();
