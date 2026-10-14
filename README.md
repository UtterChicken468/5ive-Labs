# Gridiron Model Lab

A personal NFL prediction model. Weight the factors you believe in and get game picks,
predicted scores, win probabilities, edges against the Vegas lines, player prop projections,
a walk-forward backtest, and a pick log that grades itself.

## How it stays current

`.github/workflows/update-data.yml` runs twice a day. It runs `scripts/update_data.py`, which
pulls the latest schedule, scores, Vegas lines and box scores from
[nflverse](https://github.com/nflverse), and commits `data.json` only when something changed.
GitHub Pages then republishes the site automatically.

To refresh right away: **Actions** tab → **Update NFL data** → **Run workflow**.

## Files

| File | What it is |
|---|---|
| `index.html` | The whole app (layout, styles, interface) |
| `engine.js` | The model math: ratings, predictions, backtest, prop projections |
| `data.json` | Season data, rewritten by the update job |
| `scripts/update_data.py` | Builds `data.json` from nflverse |
| `manifest.webmanifest`, `sw.js`, `icons/` | Let it install to a phone home screen and work offline |

## Notes

- Saved models, entered lines and logged picks are stored in the browser on each device.
- The data doesn't include injuries. Check the injury report before trusting a projection.
- Data: nflverse (CC-BY 4.0 for stats; schedule and lines from nfldata).
