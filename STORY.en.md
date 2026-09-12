# I'm an Accountant With No Coding Background. I Used Munetaka Murakami's Name to Stress-Test the MLB Record-Digging Tool I Built

Quick disclaimer up front: I'm not a programmer or an engineer. My day job is accounting — I hold a mid-level bookkeeping certification and that's about the extent of my "technical" background. And yet this year, using Claude Code, I built a CLI tool called mlb-record-finder that digs up obscure MLB records, and open-sourced it on GitHub.

The whole thing started because I fell for the kind of articles Sarah Langs writes for MLB.com — the "who on earth went looking for this stat" pieces. I wanted to do that kind of digging myself.

## What it's built from

The tool combines free, publicly available data — three sources at first, four by the time I was done:

- Lahman Baseball Database (SABR's official release): season and career stats going back to 1871
- Retrosheet Game Logs: team-level results for every game since 1871
- Retrosheet Play-by-Play: pitch-by-pitch event data for recent seasons
- Statcast (via pybaseball): exit velocity, distance, spin rate and other tracking data from 2015 onward

All of it lands in a single DuckDB file, and you can query it either through 14 built-in templates (exit velo x distance homers, franchise HR rankings, barrel-rate leaderboards, expected-vs-actual performance gaps, and so on) or by just typing a question in plain Japanese or English.

## Filling in numbers the raw data doesn't give you

Some metrics, like barrel rate or xwOBA, are basically free — Statcast's raw data already ships with those columns computed, so you just aggregate them. The hard part was WAR. Look through Lahman, Retrosheet, or Statcast and you won't find a single "WAR" number anywhere. Nobody hands it to you.

So I built it myself. Batting value comes from Lahman stats run through a wOBA-based runs-above-average calculation with year-specific coefficients. Defense uses Statcast's OAA (Outs Above Average) from 2016 onward, and falls back to a simplified Lahman fielding metric before that. Pitching value is FIP-based. The most annoying piece was park factor — none of the three sources ship one, so I derived it myself from the home/away scoring splits in Retrosheet Game Logs. Replacement level and positional adjustments are the only pieces borrowed wholesale from published standard values.

Which means this WAR isn't a reproduction of FanGraphs' or Baseball-Reference's official numbers — it's my own simplified version, built entirely from whatever free data I had on hand. I said so plainly in the README: "will not match official sites." Fudging that would have wrecked the whole point of building a record-digging tool in the first place.

## What the free-question mode actually returns

With Munetaka Murakami — who joined the White Sox this season (2026) — somewhere in the back of my mind, I typed this into the free-question mode, in Japanese, nothing fancier than: "top 10 hard-hit rate leaders in 2026." Here's what came back:

```sql
SELECT (pl.name_first || ' ' || pl.name_last) AS batter_name,
       COUNT(sp.launch_speed) AS batted_balls,
       COUNT(CASE WHEN sp.launch_speed >= 95.0 THEN 1 END) AS hard_hit_balls,
       ROUND(100.0 * COUNT(CASE WHEN sp.launch_speed >= 95.0 THEN 1 END)
             / COUNT(sp.launch_speed), 1) AS hard_hit_pct
FROM statcast_pitches sp
JOIN player_id_lookup pl ON sp.batter = pl.key_mlbam
WHERE sp.game_year = 2026 AND sp.launch_speed IS NOT NULL
GROUP BY pl.key_mlbam, pl.name_first, pl.name_last
HAVING COUNT(sp.launch_speed) >= 100
ORDER BY hard_hit_pct DESC
LIMIT 10;
```

| Batter | Batted Balls | Hard-Hit Balls | Hard-Hit % |
|---|---|---|---|
| Angel Genao | 142 | 53 | 37.3 |
| Fernando Tatís | 729 | 266 | 36.5 |
| Nelson Velázquez | 128 | 46 | 35.9 |
| James Wood | 605 | 213 | 35.2 |
| Junior Caminero | 781 | 274 | 35.1 |
| Munetaka Murakami | 469 | 163 | 34.8 |
| Jac Caglianone | 661 | 229 | 34.6 |
| Elly De La Cruz | 629 | 213 | 33.9 |
| Pete Alonso | 845 | 284 | 33.6 |
| Oneil Cruz | 391 | 131 | 33.5 |

The model that answered was gemini-3.8-flash. I never told it how to define "hard-hit rate" — it decided on its own to use a 95 mph exit-velocity threshold with a 100-batted-ball minimum, and then volunteered that definition back to me in plain language instead of silently guessing. The generated SQL also gets printed to the screen every time, labeled as being "for verification," so I can always eyeball whether the filter logic actually matches what I meant.

Typing a Japanese name doesn't trip it up either — under the hood it joins through the player_id_lookup table using the MLBAM ID, so name-spelling variants aren't an issue. Watching Murakami — a guy who's been in MLB for less than a season — show up at #6 with a 469-batted-ball sample, right next to Fernando Tatís and James Wood, was the moment I thought, "okay, this actually works."

Under the hood, the free-question mode hands the Gemini API the full DuckDB schema and asks it to generate SQL. It tries the cheap model (Flash) first, and only escalates to the Pro-tier model when a question gets flagged as complex or the first generation fails validation. Generated SQL gets filtered through a regex that rejects anything other than SELECT/WITH, and then gets run through EXPLAIN to confirm every table and column actually exists before it's allowed to execute for real. Identical questions get cached locally under a normalized key, so asking the same thing twice is free the second time.

## The time I accidentally found a possible "first in MLB history"

This is the part I'm most excited to write about. The White Sox went 60-102 in 2025 — dead last in the AL Central, and dead last in the entire American League. Fast-forward to 2026, and as of early September they're sitting at 75-69, two games ahead of the Guardians, leading their division. And one of the guys carrying that offense is a rookie who just cleared 30 home runs: Munetaka Murakami.

So I asked the free-question mode:

"Has any team ever gone from last place in their league one year to contending for first place the next year, with a rookie who hit 30+ home runs?"

The generated SQL (a CTE starting with `WITH team_ranks AS (...)`) built the filter out of three conditions: finishing last in your division/league the prior year, contending the following year (finishing top-2 or winning the division/league), and rookie eligibility (130 or fewer career at-bats coming into the season — which lines up closely with MLB's actual official rookie threshold) combined with 30+ home runs. It escalated to gemini-3.1-pro-preview for this one — Flash apparently flagged it as too complex to handle solo. Two results came back:

| Season | Team | Player | HR |
|---|---|---|---|
| 2007 | Arizona Diamondbacks | Chris Young | 32 |
| 1986 | Texas Rangers | Pete Incaviglia | 30 |

So "worst-to-contender plus a 30-homer rookie" has happened twice before. Then I tightened the question — swapping "contending" for "actually won the pennant":

"Among teams that went from last place to winning their league championship the next year, has any of them had a rookie hit 30+ home runs?"

Zero results. It has never happened.

Which means if the White Sox keep this up and actually win the American League pennant this year, a last-place-to-pennant-winner turnaround season that includes a 30-homer rookie would be something that has literally never happened in MLB history. Nothing's decided yet — they haven't won anything — but just by phrasing the right question, I stumbled onto a genuinely live storyline worth watching this season. For a second there, I felt like I'd recreated, on a tiny scale, exactly the kind of thing Sarah Langs does for a living.

## Behind-the-scenes development notes

- Retrosheet's official parser, cwevent, needs a C compiler to build, and my environment didn't have one. So I ended up writing my own Python-native play-by-play parser from scratch, and validated it against Retrosheet Game Logs' final scores: 2417 out of 2430 games matched (99.4%). Along the way I found and fixed real bugs — pickoff plays corrupting base-runner state, foul flies getting misclassified, doubles and triples getting swapped. Unglamorous work, but watching the match rate climb every time I fixed something was genuinely satisfying.
- The free-question feature was originally going to run on the Anthropic API, but there's no free tier there, and I already had a paid Gemini contract and didn't want to add another one, so I switched to Gemini.
- gemini-2.5-pro is supposedly GA according to the docs, but the live API returned a 404 for it, so I had to swap the fallback model to gemini-3.1-pro-preview instead.
- The chadwickbureau GitHub repo I'd originally planned to pull the Lahman database from had already gone dark by the time I got to that part. Switched to SABR's official distribution instead and moved on.

## Where it stands now, and why I didn't turn it into a hosted service

Phases 1 through 4 are all implemented, tested, and pushed to GitHub. I deliberately didn't turn this into a hosted web service anyone can just use. Statcast and Retrosheet data are free to access, but MLB.com's terms of service restrict redistributing automatically-collected data, and hosting it publicly felt like it'd run straight into that. So the code is open source, and running it is on you. Both README.md and README.en.md carry the same two disclaimers: limited completeness for 19th-century data, and the fact that WAR here is a homegrown simplified calculation.

This is really just the story of an accounting guy who fell for some baseball journalism and built something with an AI's help. If it's useful as one example of how far you can get by just combining free datasets, I'll take that as a win.

日本語版はこちら: [STORY.ja.md](./STORY.ja.md)

GitHub: https://github.com/hito1120-crypto/mlb-record-finder
