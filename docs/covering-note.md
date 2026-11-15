# FINN — covering note

Subject: IIT Delhi – AI Engineer - 2027

**Links:** deployed app: `<paste Render URL>` · repo: `<paste GitHub URL>` · build history: `<paste Cursor / chat export links>`

## What I built
FINN is a mobile-first PWA with two modes over an 18-stock NSE portfolio (RELIANCE, HDFCBANK, TCS, BHARTIARTL, SUNPHARMA, LT, MARUTI, ITC, DIXON, POLYCAB, CDSL, DEEPAKNTR, CHALET, KPRMILL, CERA, BECTORFOOD, CMSINFO, SJS).

- **Push.** *Today* is a windowed digest (6h / 24h / 3d / 7d / custom) of every NSE filing, ranked by a transparent materiality score (tap the score to see why) with routine filings collapsed. Results filings get a Revenue / EBITDA / PBT / PAT card with QoQ and YoY. Management changes get the incoming executive's bio (filing text first, then public profile snippets). *Alerts* fire on thresholds the investor sets: price move, volume vs the 20-day average, filing materiality, and which filing types matter.
- **Pull.** Research on any NSE ticker, grounded only in the annual report, concall transcripts and exchange-filed results. Eight modules, every claim carrying a page-level citation that expands to the verbatim quote. The centrepiece is a four-quarter *Narrative vs. numbers* scorecard: what management promised on each call vs what the filings delivered. Four ad-hoc demo stocks outside the portfolio: DMART, MAZDOCK, KPITTECH, RAINBOW; ASIANPAINT was used to prove the engine is not hard-wired.

## Key decisions and trade-offs
- **Triage over aggregation.** The home screen is a ranked feed, not a chat. Chat exists only as a grounded "Ask this company" box inside research.
- **Model routing.** Code does everything deterministic (thresholds, QoQ/YoY, period matching, promised-vs-delivered status, citation checks). `gpt-4o-mini` does bulk classification and extraction, batched and cached per filing. `gpt-4o` runs once per deep analysis on a bounded evidence packet. Everything is cached by model, prompt version and input hash; seeded results ship with the repo.
- **Grounding as a hard constraint.** The validator re-finds every quote in the source pages and drops claims it cannot verify; numbers in claims come from facts computed in Python. This caught real model errors (invented targets, wrong-period comparisons), which I fixed by feeding the model pre-computed trend wording and timing rules instead of trusting it to do arithmetic.
- **Never-break demos.** NSE retries with cookie refresh, then cache, then a committed snapshot, always badged "Snapshot". With the network and the model disabled, the seeded digest, prices and research still work (verified).
- **Single deployable.** The API serves the built frontend, so one Render service gives one public link.

## Deliberately left out
Broker/news data, price targets or advice, trading, portfolio accounting, auth, a hosted vector database, native push notifications (in-app inbox instead), and test suites (one smoke script instead).

## Known limits
Screener lists only annual-call transcripts for DMART and nothing recent for MAZDOCK, so their scorecards show fewer than four consecutive quarters and say so. Results extraction depends on each filing's layout; units and consolidated/standalone basis are shown on every card. EBITDA appears only when reported or derivable.

## Where I'd take it next
Real push delivery (web push / mobile) fed by a scheduled worker; BSE as a second filings source; reconciling restated figures across quarters; a per-company memory of management promises that persists across calls; evaluation sets for classification and extraction accuracy.
