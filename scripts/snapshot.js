// Saves the trained model's official prediction for every game before it starts, and grades it
// once the game is final. This is the site's real track record (data/picks/<sport>.json): unlike
// the Accuracy tab, nothing here is recomputed later with a newer model.
//
// Run: node scripts/snapshot.js   (the GitHub job runs it after every data refresh)
const fs = require('fs');
const path = require('path');
require(path.join(__dirname, '..', 'engine.js'));
const E = globalThis.Engine;
const DATA = path.join(__dirname, '..', 'data');
const OUT = path.join(DATA, 'picks');
fs.mkdirSync(OUT, { recursive: true });
const load = (f) => JSON.parse(fs.readFileSync(f, 'utf8'));
const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
const r3 = (v) => (v == null ? null : Math.round(v * 1000) / 1000);
const now = Date.now();
const nowIso = new Date().toISOString().slice(0, 16);

// kickoff as a timestamp (data times are US Eastern)
function kickoff(d, tm) {
  if (!d) return null;
  const [y, m, day] = d.split('-').map(Number);
  const dst = (() => { // US daylight time: 2nd Sunday of March to 1st Sunday of November
    const sun = (mo, n) => { const f = new Date(Date.UTC(y, mo, 1)).getUTCDay(); return 1 + ((7 - f) % 7) + 7 * (n - 1); };
    const a = Date.UTC(y, 2, sun(2, 2)), b = Date.UTC(y, 10, sun(10, 1)), t = Date.UTC(y, m - 1, day);
    return t >= a && t < b;
  })();
  return Date.parse(`${d}T${tm || '12:00'}:00${dst ? '-04:00' : '-05:00'}`);
}
const defaults = (m) => ({ hfa: m.hfa ?? 1.5, trust: m.trust ?? 60, bye: m.bye ?? 1, prior: m.priorGames ?? 0, weather: m.weather ? 5 : 0, qbOut: m.qbOut ? 5 : 0, goalie: m.goalie ? 5 : 0 });

for (const sport of ['nfl', 'cfb', 'nhl', 'mlb', 'cbb', 'nba']) {
  const f = path.join(DATA, sport + '.json'); if (!fs.existsSync(f)) continue;
  const data = load(f), m = data.meta, tr = m.trained;
  const outF = path.join(OUT, sport + '.json');
  const book = fs.existsSync(outF) ? load(outF) : { sport, games: {} };
  E.configure(m);
  const weights = tr ? tr.weights : E.PRESETS.Balanced, settings = { ...defaults(m), ...(tr ? tr.settings : {}) };
  const done = data.games.filter((g) => g.hs != null), lastDone = done.length ? Math.max(...done.map((g) => g.w)) : 0;
  let added = 0, graded = 0, back = 0;

  // Finished games we never saved a pre-game pick for (this season before tracking started, or a
  // missed refresh): fill them in from the game-by-game test, which only uses earlier games. Marked bf.
  if (done.some((g) => !book.games[g.id])) {
    const b = E.backtest(data, weights, settings, 0, true), byId = Object.fromEntries(data.games.map((g) => [g.id, g]));
    const sig = m.sigma || 13.5;
    b.picks.forEach((p) => {
      const g = byId[p.id]; if (!g || book.games[g.id]) return;
      book.games[g.id] = { season: data.season, w: g.w, d: g.d, tm: g.tm, a: g.a, h: g.h, m: r1(p.m), pH: r3(E.phi(p.m / sig)), sp: g.sp ?? null, tot: g.tot ?? null,
        hml: g.hml ?? null, aml: g.aml ?? null, bf: 1, at: nowIso };
      back++;
    });
  }
  // Upcoming games: keep the latest prediction until kickoff, then it's locked
  const byW = {};
  data.games.forEach((g) => {
    if (g.hs != null || !g.d || !g.h || !g.a) return;
    const ko = kickoff(g.d, g.tm); if (ko == null || ko <= now) return;
    if (ko - now > 8 * 864e5) return; // only the next week or so
    const before = Math.min(g.w, lastDone + 1);
    const PR = byW[before] || (byW[before] = (() => { const P = E.teamProfiles(data, before); return { P, R: E.ratings(P, weights, settings) }; })());
    const L = { sp: g.sp, tot: g.tot, hml: g.hml, aml: g.aml, hspp: g.hspp, aspp: g.aspp, ovp: g.ovp, unp: g.unp };
    const pr = E.predict(g, PR.P, PR.R, settings, L, data); if (!pr) return;
    book.games[g.id] = { season: data.season, w: g.w, d: g.d, tm: g.tm, a: g.a, h: g.h, m: r1(pr.margin), pH: r3(pr.pHome), ps: [r1(pr.as), r1(pr.hs)], t: r1(pr.total),
      sp: g.sp ?? null, tot: g.tot ?? null, hml: g.hml ?? null, aml: g.aml ?? null, at: nowIso };
    added++;
  });
  // Grade finished games
  const final = Object.fromEntries(done.map((g) => [String(g.id), g]));
  Object.entries(book.games).forEach(([id, x]) => {
    const g = final[id]; if (!g || x.r) return;
    x.r = [g.as_, g.hs]; graded++;
  });
  book.sport = sport; book.updated = nowIso;
  fs.writeFileSync(outF, JSON.stringify(book));
  console.log(`${sport}: ${added} upcoming saved, ${graded} graded, ${back} backfilled, ${Object.keys(book.games).length} total`);
}

// UFC: fights instead of games
(() => {
  const f = path.join(DATA, 'ufc.json'); if (!fs.existsSync(f)) return;
  const data = load(f), U = E.ufc;
  const outF = path.join(OUT, 'ufc.json');
  const book = fs.existsSync(outF) ? load(outF) : { sport: 'ufc', games: {} };
  const res = U.walk(data, U.trainedWeights(data));
  let added = 0, graded = 0;
  res.upcoming.forEach(({ f: x, p }) => {
    const ko = kickoff(x.d, x.tm); if (ko == null || ko <= now || ko - now > 15 * 864e5) return;
    book.games[x.id] = { d: x.d, tm: x.tm, ev: x.ev, a: x.an, h: x.bn, pH: r3(1 - p), wc: x.wc, hml: x.bml ?? null, aml: x.aml ?? null, at: nowIso };
    added++;
  });
  const byId = Object.fromEntries(data.fights.map((x) => [String(x.id), x]));
  Object.entries(book.games).forEach(([id, x]) => {
    const g = byId[id]; if (!g || x.r || g.win == null) return;
    x.r = g.win === 'a' ? [1, 0] : g.win === 'b' ? [0, 1] : [0, 0]; graded++;
  });
  book.updated = nowIso; fs.writeFileSync(outF, JSON.stringify(book));
  console.log(`ufc: ${added} upcoming saved, ${graded} graded`);
})();
