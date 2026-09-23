# Reusable Forever analysis

Run **Analyze.ps1** in the Forever State addon folder after `/reload` or normal logout. It discovers new and changed files, preserves the original bytes, merges prior imports, and rebuilds the report. It runs outside WoW; the in-game addon cannot read combat-log files or launch Python.

From the game directory in PowerShell:

```powershell
& '.\Interface\AddOns\ForeverState\Analyze.ps1'
```

Or, with Python 3.10+ installed:

```text
python Interface/AddOns/ForeverState/analysis/analyze.py
```

The launcher finds an installed Python 3.10+ using `py -3`, `python3`, or `python`. To select an interpreter, use `-Python <executable>`, set `FOREVERSTATE_PYTHON`, or put its executable path on one line in the addon's Git-ignored `.python-path` file (in that priority order). No machine-specific runtime path is part of the source. The analyzer itself uses **only the Python standard library**: no lupa, Lua runtime, internet access, or Python packages are required. SavedVariables is parsed as literal data; Lua code is never executed.

Default game discovery is relative to the script's installed location: `analysis/analyze.py` → addon folder → `AddOns` → `Interface` → client folder. It does not depend on your shell's working directory or the installation's drive/name. Default output is `analysis-data` inside the addon. For a checkout elsewhere, supply `--game-root` or PowerShell `-GameRoot`; an uninstalled checkout without that option fails with a clear message rather than guessing which folders to scan.

## Results

The stable readable report is:

```text
Interface/AddOns/ForeverState/analysis-data/latest-report.md
```

Each run also creates an immutable directory under `analysis-data/runs/`, containing `report.md` and `analysis.json`. `latest.json` points to it. The JSON includes all recovered metadata sessions, individual swing attempts, rejected-sample reasons, raw rage differences, source hashes/line numbers, and comparison results. The stable latest-report pointer is replaced; previous run reports remain intact.

The report answers:

1. **Rage per second of weapon swing time:** mean of `rage gained / reference speed`, plus count, observed range and sample standard deviation in JSON. Groups separate character, beta build, level, item, hand/type, speed and normal/critical/glancing outcome. This coefficient is not elapsed-combat rage per second.
2. **Two-hand multiplier:** compare the speed-normalized coefficient with one-handed main-hand attacks at the same character/build/level/hit outcome. A separate provisional estimate pools normal and glancing hits under the explicit extra assumption that both use the same noncritical coefficient. Different weapon setups can still differ in buffs/talents/stance, so the estimate alone does not prove an innate modifier.
3. **Critical bonus:** compare normal versus critical mean rage with the same character/build/level/weapon/hand/type/reference speed. Report both crit/normal ratio and additional rage per crit, with sample counts.

There are no hardcoded expected answers such as 3.5 rage/second, a two-hand multiplier, or a zero crit bonus. Each run recomputes the estimates from eligible observations. Small samples are displayed, not treated as proof. Shared captures may be supplied in `raw-logs/`; these are opt-in inputs and the tests do not depend on them. See the [main README](../README.md#analyze-shared-or-archived-logs) for the archive-analysis command and the matching SavedVariables requirement.

## Import and preservation

By default the importer scans:

- `Logs/WoWCombatLog*.txt` in this game installation.
- `WTF/**/ForeverState.lua` and `ForeverState.lua.bak` for every character.
- Historical archives only when explicitly supplied with `--input` (PowerShell: `-InputPath`). No other addon's directories are scanned. Files already imported into the cumulative database remain available without rescanning their original locations.

Files are hashed and retained as compressed originals in `analysis-data/archive/`. SQLite in `analysis-data/history.sqlite3` stores cumulative events, snapshots and provenance. The importer does not modify WoW's logs, active SavedVariables, or their `.bak` files. If a future beta login loses its history, records imported previously remain available in the analysis database. **An import cannot recover a session that was never saved or whose last copy disappeared before import.** Back up the entire `analysis-data` directory outside the game installation periodically; deleting/reinstalling the addon can otherwise remove its analysis archive too.

Unchanged files are skipped by size/mtime. Changed/growing files are archived as a new content version and re-read; existing events and state snapshots deduplicate against the database. This intentionally favors reproducible prefix/backup recovery over byte-offset-only ingestion. Very large logs therefore cost more time and temporary memory on changed-file imports; unchanged history is not re-parsed. A `--rescan` can rehash files if timestamps were preserved by another copy tool.

An event is identified by the complete timestamped log line plus its occurrence number within the source file. Thus growing prefixes and copied archives do not double-count attacks, while two identical entries within one source remain two occurrences. Independently captured files with indistinguishable identical rows at the exact same timestamp are an inherent ambiguity. The separate outgoing `SWING_DAMAGE_LANDED` row is never treated as an additional attack.

Sessions use player GUID plus initial wall/monotonic timestamps, **not the addon's local session ID**, which can restart at 1 after a beta persistence failure. Identical snapshots from overlapping saves merge. A genuinely different snapshot for the same session/sequence is preserved as a conflict and makes that session ineligible for attribution until resolved. Unknown saved schemas or log headers are archived and reported rather than guessed.

## Running during play

You may run the script while playing. It snapshots the currently available file extent, ignores an incomplete final combat-log line, and retries changed sources on later runs. Logging and game buffers may not have flushed yet; an empty file is reported as pending. Most importantly, **Forever State metadata reaches disk only on `/reload` or normal logout**. Combat events without a matching saved session remain archived and excluded until a later run can match them. Keep remembering to enable `/combatlog` at login; this script does not change game settings.

The tool runs once and exits. It does not install a scheduled task or background watcher.

## Attribution and speed limits

- Supported combat format: advanced log version 22, build version 1.60.1, project 18. Detailed header/layout gates prevent shifted fields from becoming invented measurements. New formats require inspection and an explicit parser update.
- Raw resource units are divided by 10 by default. This is an assumption exposed by `--rage-scale`; both raw and converted values remain in JSON.
- Positive net player-rage differences are candidates only when the baseline is recent and in the same known session/level/weapon state. Spending, casts, resource events, caps, stale or missing snapshots, overkill, partial mitigation and unknown events exclude samples.
- **Default timing:** incoming hits and player spells within ±250 ms, plus short baseline gaps, are excluded. `--include-close` enables a more permissive timing selection; every report records the setting.
- Normal, critical and glancing outcomes are separate. Glances are eligible for their own group, not silently relabeled as normal. Misses/dodges/parries are retained without inferring zero rage.
- Metadata timestamps use raw one-second bounds or an independently derived monotonic/wall-clock interval when all saved pairs support a stable offset. A one-second margin on either side of gear/level/world transitions is excluded. A backwards clock ordering is rejected.
- Dual wield cannot be attributed from these observed disk rows, which lack a verified hand discriminator. It is explicitly excluded rather than guessing from damage or splitting rage. Two-handed weapons used in the main hand and one-handed weapons with an empty off hand or shield are supported.
- Speeds come from readable `UnitAttackSpeed` snapshots within a continuous gear/level segment. If there is exactly one observed speed, it is used as a **reference**, even if some combat reads were secret; the missing reading stays marked missing. Multiple observed speeds prevent a coefficient estimate for that segment. This is not a base-tooltip-speed extractor and does not prove that secret intervals had no haste changes.
- Passive/unlogged gains and delayed updates can still contaminate an apparently eligible net delta. Addon 0.2 records public stats and stance; talents and individual buffs are not fully captured. Controlled replication is needed before attributing a coefficient difference solely to an innate weapon mechanic.

## Options

```text
python analysis/analyze.py --help
python analysis/analyze.py --include-close
python analysis/analyze.py --player Player-0000-EXAMPLE
python analysis/analyze.py --input "D:/My old WoW archives"
python analysis/analyze.py --game-root "D:/World of Warcraft/_classic_beta_" --data-dir "D:/ForeverResearch"
python analysis/analyze.py --rage-scale 10 --rescan
```

These examples assume the working directory is the Forever State folder. The defaults work from any working directory. Extra archives use the same standard save/log filenames as the discovered inputs. `Analyze.ps1` exposes `-IncludeClose`, `-Rescan`, `-Player`, `-GameRoot`, `-DataDir`, `-InputPath` (one or more archive paths), and `-Python`. Use the Python entry point to override `--rage-scale`. Explicit relative option paths are relative to your shell's working directory; only the omitted defaults are resolved from the addon.

## Incoming damage research (analyzer 1.5)

Every normal import also writes `incoming.json` and `incoming-report.md` beside that run's outgoing report, plus `analysis-data/latest-incoming-report.md`. `latest.json` links both. Older captures remain usable; recording the new stats and stance fields requires addon 0.2 or later.

Incoming analysis pairs each enemy `SWING_DAMAGE` with its matching `SWING_DAMAGE_LANDED` player snapshot, counting it once. It requires a recent post-event player rage baseline, a matching health loss, unchanged max health and known level/session, no spending or intervening unknown effects, no rage cap, and no other ability/attack within 250 ms. Zero rage deltas are retained. All landed hits and exclusion reasons remain in JSON.

The primary selection uses complete addon combat windows containing only one observed enemy GUID, including outgoing targets and avoided attacks. The smaller regular-cadence subset requires both neighboring enemy attempts in the same window, with intervals of 1–4 seconds differing by no more than 0.20 seconds. These filters reduce movement/multiple-enemy contamination but cannot prove a stationary fight or complete combat-log coverage.

Thunder Clap aura state is tracked separately; an application, refresh, or removal between neighboring swings prevents regular-cadence eligibility. Measured intervals are not treated as verified enemy base attack times, and no assumed slow percentage is applied. Enemy NPC ID, max health and attack power are retained; the log has no verified enemy-level field.

Model comparisons test constant rage per hit, damage taken, damage/max player health, logged raw damage, and observed enemy interval. Fits stay separate by character/build/stance, optionally allowing a coefficient for each player level. Each fight is also predicted using coefficients from other fights. This comparison helps rank hypotheses but is not an independent validation of a formula discovered in the same data. Detailed groups additionally split by player max health, recorded effective armor, enemy descriptors and Thunder Clap state. Ordinary-play evidence cannot establish an exact universal formula or separate all buffs/passives from level and health effects.

Detailed swing contexts preserve the latest snapshot's `stats` and `stance`, without carrying readable fields across restricted snapshots. `stance_key` is `spell:<observed ID>`, `none` (explicit no form) or `unknown`. Analyzer 1.4 supplements saved stance with a separate forward reconstruction from disk-log player aura applications/refreshes for Warrior stance spell IDs 2457, 71 and 2458. Successful casts alone never establish an active stance, and failed casts do not change it. Repeated observations of the same aura do not count as a switch. Removal of the current aura clears that evidence; removal of an old aura after a new one is applied does not erase the new stance.

Addon 0.2.1 additionally preserves the client's armor mitigation fraction/percentage and its reference attacker level under `context.stats.armor`. This uses the player's effective level as the attacker-level reference, not a verified enemy level, and includes armor only. Keep it separate from stance modifiers and the actual damage/health evidence. Full precision and API provenance survive import; old or restricted snapshots are not backfilled with a guessed formula.

The disk reconstruction resets at combat-log headers, saved startup/world/logout boundaries and player death. It never infers the initial stance backward from a later removal or carries evidence across those boundaries. Unknown logging gaps are still a limitation. `recorded_stance_key`, `log_stance_key`, `stance_basis`, and `log_stance_evidence` distinguish the sources; the evidence includes the event ID, timestamp, source hash and line. A disagreement between readable addon and log stance excludes the incoming sample. This enables stance attribution for earlier addon versions without modifying any saved snapshot.

Incoming samples within the timestamp bounds plus a one-second margin of a saved stat/stance/equipment transition are excluded, as are baselines crossing those transitions. Logged stance transitions have a 250 ms margin and also prevent crossing baselines. Explicit saved stance/gear events establish a boundary even if getters have not updated yet. Missing new fields do not invalidate older rage measurements; periods without independent stance evidence remain in unknown-stance groups, and missing armor stays unknown.

The stratified report includes `K health-normalized`, fitting `rage = K × damage taken / player max HP`. Max HP here remains the combat-log value checked for health accounting; recorded stat max HP is retained separately. Compare stance coefficients under matched level, max HP, armor and enemy conditions. No assumed Defensive Stance reduction or compensating rage multiplier is applied. The main outgoing summary retains its existing grouping, while its per-hit JSON contexts expose the new stats and stance.

Incoming measurement groups also split critical versus noncritical hits. A crit giving more rage solely because it deals more damage is not an additional crit multiplier; compare rage per damage within comparable conditions. The disk log marks critical hits but does not identify which were caused by sitting. Advanced player-snapshot armor and attack power are retained in `snapshot.armor` and `snapshot.attack_power`, separately from addon stats (which may be restricted in combat). The historical `raw_damage` column does not necessarily include the critical multiplier; the actual-damage field is the primary measure for this comparison.

## Verification commands

```text
python -m unittest discover -s analysis -p "test_*.py"
```

The suite uses isolated temporary databases and generated synthetic fixtures from `synthetic_fixtures.py`. It works in a fresh source-only checkout with no private logs or archives. It covers safe literal parsing, quoted log names, partial writes, exact duplicate multiplicity, incremental prefixes, repeat-run idempotence, later metadata arrival, overlapping copies, lost source files, local-session-ID resets, conflicting snapshots, malformed inputs, level-separated comparisons, both timing modes, path discovery, and known arithmetic for crit bonuses and two-hand multipliers. Incoming tests additionally cover resource/health isolation, multiple enemies, Thunder Clap transitions, timing and health-scaled fits.
