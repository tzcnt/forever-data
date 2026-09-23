# Forever State 0.2.1

A standalone, windowless metadata recorder for WoW Forever 1.60.1 (interface 16001). It creates a timestamped history of character level, equipped items, stats and stance, for later alignment with disk combat logs.

## Install

Clone or extract this repository directly into your client's `Interface/AddOns/ForeverState` directory. `ForeverState.toc` must be directly inside that folder, not inside a second nested repository folder.

```text
<WoW client folder>/
  Logs/
  WTF/
  Interface/AddOns/ForeverState/
    ForeverState.toc
    ForeverState.lua
    Analyze.ps1
    analysis/              # External Python analyzer and synthetic tests
    tests/                 # Lua addon replay tests
    analysis-data/         # Generated locally; excluded from Git
```

The in-game addon needs no Python. Offline analysis requires **Python 3.10+**, with no additional packages. After saving a session, run `Analyze.ps1` in PowerShell or `python analysis/analyze.py` from the addon folder. The analyzer finds the client folder three directories above the addon, then reads its `Logs` and `WTF` directories. Its working directory does not affect that discovery. A checkout outside `Interface/AddOns` requires `--game-root` (PowerShell: `-GameRoot`). See [analysis instructions](analysis/README.md) for options and output details.

## Capture data in WoW

1. Restart WoW so the new addon is discovered. Enable **Forever State** in the character-selection AddOns list. It starts automatically on login.
2. Run `/fstate status` (or `/foreverstate status`). Healthy output says **RECORDING** and lists level, weapons, effective speeds, stats, stance and the number of prior sessions loaded. Unavailable or secret fields are listed individually. **RECORDING (partial metadata)** means core identity/level/equipment/timestamps are readable but some supplemental data is missing. **BLOCKED/PARTIAL** means a required field or event listener is missing; readable portions are still retained.
3. Before relying on a long session, do `/reload`, then `/fstate status` again. **Loaded this login** should now show at least one prior session and its snapshots. This specifically tests the beta's SavedVariables persistence, separately from API access. If it remains zero, stop relying on cross-login history and report the status; normal addon code cannot repair the client failing to load its save file.
4. ONCE: Enable disk logging separately: `/console advancedCombatLogging 1`.
5. EVERY LOGIN: Run `/combatlog` to start logging.
6. Play normally. Log out normally or use `/reload` after the session to save metadata. Keep both the combat log under `Logs/` and the character's `ForeverState.lua` SavedVariables file.

There are no windows or routine chat messages. `/fstate` is a shorthand for status. An optional `/fstate mark LABEL` adds a timestamped note, for example `no buffs, new sword`. Status checks retry the APIs and append a snapshot only if state/accessibility changed; the time shown is the latest retained snapshot, not necessarily the instant the status command was run.

## Analyze your captured sessions

The analyzer is setup to extract rage-per-swing and rage-per-damage-taken information for Warrior in the Forever beta only - not any other version of the game. It currently supports advanced combat-log format **22**, client **1.60.1**, project **18**, and Forever State SavedVariables schema **1**. It needs both the combat logs and matching addon metadata to assign character level, equipment and sessions. Python **3.10 or newer** is the only analysis dependency; no Python packages, API keys or network connection are needed.

After `/reload` or a normal logout, open PowerShell **in the installed `ForeverState` addon folder** and run:

```powershell
.\Analyze.ps1
```

Or run the Python script directly from that same folder (also works on macOS/Linux, using `python3` if appropriate):

```text
python analysis/analyze.py
```

If your terminal is instead in the WoW client folder containing `Interface`, `Logs` and `WTF`, the PowerShell command is:

```powershell
& '.\Interface\AddOns\ForeverState\Analyze.ps1'
```

These commands automatically read `Logs/WoWCombatLog*.txt` and `WTF/**/SavedVariables/ForeverState.lua` (plus `.bak`) from that installation. The client folder is resolved from the script's location, not your current working directory. All characters with saved metadata are considered; no account name or character GUID needs to be configured.

When it finishes, open these Markdown files in a text editor or Markdown viewer:

| File inside the addon folder | Contents |
| --- | --- |
| `analysis-data/latest-report.md` | Outgoing rage per weapon-second, two-hand comparisons, and crit bonuses, split by character/level/weapon |
| `analysis-data/latest-incoming-report.md` | Incoming-damage rage candidates, enemy timing, and comparisons of damage-, health-, and timing-based models |
| `analysis-data/latest.json` | Paths to the latest run's reports and detailed JSON data |
| `analysis-data/runs/<run>/` | Preserved reports and event-level JSON, including rejected samples and reasons |

**Run the same command after each saved session.** It imports new or changed files, deduplicates previously imported events, combines history in `analysis-data/history.sqlite3`, and recomputes the reports. Nothing is uploaded and the original logs/SavedVariables are not edited. Keep and back up `analysis-data` to retain cumulative history; it is excluded from Git. The script runs once, not continuously.

### A checkout outside the game folder

From the repository root, explicitly point to the client directory containing `Logs` and `WTF`:

```powershell
.\Analyze.ps1 -GameRoot 'C:\Program Files (x86)\World of Warcraft\_classic_beta_'
```

The Python equivalent is:

```text
python analysis/analyze.py --game-root "C:/Program Files (x86)/World of Warcraft/_classic_beta_"
```

Replace that example installation path with yours. Output still goes into the checkout's `analysis-data` folder. Use `-DataDir` / `--data-dir` to choose another output directory.

### Analyze shared or archived logs

`raw-logs/` is an explicit sharing folder, **not an automatically scanned input directory**. To analyze it, also obtain the corresponding Forever State metadata and put copies in a local `metadata/` directory, preserving a recognized filename: `ForeverState.lua`, `ForeverState.lua.bak`, or `ForeverState.saved.lua`. Keep different characters/sessions in separate subdirectories if filenames would collide. Use the SavedVariables file from `WTF`, not the addon's source file with the same name.

From the repository root, run:

```text
python analysis/analyze.py --game-root . --input ./raw-logs --input ./metadata --data-dir ./analysis-data/shared
```

Here `--game-root .` confines automatic `Logs`/`WTF` lookup to the checkout; the two `--input` options supply the archived inputs explicitly. No WoW installation is required for archive-only analysis. Directories are searched recursively. Keep combat-log filenames as `WoWCombatLog*.txt` (or `combat-log.txt` / `source.txt`); decompress ZIP/gzip downloads first. The separate output directory keeps this research database apart from your own default history.

**Raw combat logs alone cannot reproduce the automated reports.** Without matching metadata, logs are archived but there are no saved player sessions to analyze; the reports can show zero swings even though the files contain combat. Supplying `--player` does not replace metadata. Add the matching saves and rerun the same command to analyze those already imported events.

### If a command or report does not work

- **Python not found:** install Python 3.10+ and reopen the terminal. On Windows, `py -3 analysis/analyze.py` is another direct command if the Python launcher is installed. To select an existing interpreter explicitly, use `.\Analyze.ps1 -Python 'C:\path\to\python.exe'`.
- **PowerShell refuses to run scripts:** use the direct Python command above; changing execution policy is not required for analysis.
- **Addon outside `Interface/AddOns`:** pass `-GameRoot` / `--game-root` as shown above.
- **Empty or missing latest session:** verify `/combatlog` was enabled, then log out normally and rerun. Metadata is only saved on `/reload`, logout or normal exit; combat-log output may also be buffered.
- **Swings present but few accepted samples:** check the report's data-health notices and event exclusions in JSON. Nearby abilities, multiple enemies, resource caps, state transitions, unavailable weapon speed and other ambiguities are filtered out. A low accepted count is not automatically a script error.
- **Unsupported log format:** check import warnings. Other client versions/formats require parser changes; the analyzer does not guess new field layouts.

For all Python options, run `python analysis/analyze.py --help`. See [analysis/README.md](analysis/README.md) for filtering rules, persistence details and advanced options.

## Recorded data

Every snapshot contains the complete observed state, including explicit `empty` versus `unknown` inventory slots:

- Player GUID and character level. The `PLAYER_LEVEL_UP` payload takes precedence over a temporarily stale `UnitLevel` getter. An unreadable level-up payload is marked unknown until rechecked.
- Item IDs and full readable item links for inventory slots 1–19, including armor, main hand, off hand, and the Classic ranged slot. Links preserve the item/enchantment/variant information exposed by the API. Temporary enchants not represented in the item link are not independently sampled.
- Weapon equip location, item class/subclass, and localized type/subtype for slots 16–18. Classification distinguishes main-hand, off-hand, two-hand, ranged, non-weapons such as shields, and unknown items. A two-handed item in the off-hand keeps both `weaponType = "2H"` and `hand = "OH"`.
- Effective attack speeds returned by `UnitAttackSpeed`, when public. **These are haste-adjusted seconds per swing, not unmodified tooltip speed.** Base speed is not fabricated from intervals or extracted from protected tooltips; item IDs allow later lookup when necessary.
- `state.stats`: melee attack power (base, positive/negative modifiers and total), melee crit percent, melee hit modifier percent, melee hit-rating bonus percent, armor (base/effective/real/bonus) and max HP. Hit modifier and rating bonus are separate direct API results; the addon does not assume they are additive or calculate a target-specific chance to land a hit. Missing values are omitted with diagnostics, including nil results; they are never assumed to be zero.
- Armor mitigation (addon 0.2.1): `state.stats.armor.mitigationFraction` and `mitigationPercent`, calculated by the client's `C_PaperDollInfo.GetArmorEffectiveness(effectiveArmor, UnitEffectiveLevel("player"))`. `mitigationAttackerLevel`, `mitigationLevelSource` and `mitigationSource` preserve the calculation's reference and provenance. This is the character-sheet-style armor reduction against an attacker at your effective level, not a measurement against whichever mob hit you. It covers armor only; no Defensive Stance or other reduction is added. The saved values retain the API's full numeric precision; `/fstate status` displays three decimal places and the reference level. Missing/secret armor, level or API results remain unknown with diagnostics, without substituting a Classic formula. Old records are not backfilled.
- `state.stance`: whether the active form is known, its current bar index, spell ID and localized name when readable. Index zero explicitly means no active form; missing/secret results mean unknown. Spell IDs identify stances without assuming that bar slot 2 always means Defensive Stance.
- Wall-clock epoch seconds, local and UTC calendar strings, server epoch seconds, and precise session-time samples around the wall-clock read. Each record has a session ID and increasing sequence number.
- The reason for the record, sanitized event details, and per-field missing/restricted diagnostics. Session headers contain client/addon versions and event-registration failures.

Triggers include login, level-up, every equipment-change event, player inventory/link changes, player attack-speed changes, pending item-data completion, entering/leaving the world, entering/leaving combat, logout, manual markers, and a one-minute checkpoint. Stats and stance are read in every snapshot. Stance/form events are always retained, with a 250 ms deferred recheck. Player stat, armor, max-health, rating, talent and aura events also trigger checks to catch changes without a gear swap; unchanged states are deduplicated. Aura contents are not collected. The checkpoint retries unavailable fields and records clock changes without per-frame API polling. A short deferred check after login, level-up, equipment changes, stat events and combat exit catches getter/cache updates. Level/equipment events themselves are always retained.

`PLAYER_LEVEL_CHANGED` also triggers a stat recheck for effective-level changes. Armor mitigation travels with the other stats through SavedVariables, the cumulative database and detailed analysis JSON; no new analysis command is needed. After updating an existing installation, `/reload` and run `/fstate status` to confirm the beta exposes the calculation.

Each gear event records the state observed during its callback; that getter may still show the previous item. The event's changed slot/has-item payload and the later `DEFERRED_RECHECK` remain available to identify this transition. Do not label hits right at the transition as if the exact equip instant were known. Multiple fast swaps retain their immediate snapshots, even when a deferred recheck is coalesced.

### Comparing incoming rage between stances

After updating, use `/reload`, then `/fstate status` to check the new APIs. Try a stance change both outside and during combat. Save the session and run `Analyze.ps1` as usual. The beta may expose stats or stance out of combat but restrict them during combat; such snapshots retain diagnostics and do not reuse a previous readable value.

The incoming report separates measurements by recorded stance spell ID and effective armor, alongside level, max HP and enemy descriptors. It excludes samples near observed stat/stance/gear transitions and rage baselines that cross those transitions. Its `K health-normalized` column fits **rage = K × damage taken / max HP**. Compare K across stances while holding level, gear/max HP, buffs and enemy conditions constant. Separate single-enemy fights in each stance make the comparison easier than frequent switching during a fight.

If a stance reduces damage taken by exactly 10%, full compensation under this model would require a coefficient ratio of `1 / 0.9`, approximately **1.111**. That is a testable prediction, not an assumed stance bonus. A coefficient ratio near 1 would indicate unchanged rage per damage/max-HP unit. Small damage and rage values need many samples because both are quantized. No stance reduction is hardcoded into the analysis.

Stats/stance fields are additive within SavedVariables schema 1. Earlier saved snapshots remain intact and readable, with their missing fields unchanged. Analyzer 1.4 can recover stance independently from disk-log aura applications/refreshes for that character, carrying it forward until removal, death, or a logging/session/world boundary. A cast alone does not prove that the aura is active. Periods before the first observation remain **unknown**, never assumed to be Battle Stance. Each derived label retains its evidence and source line; conflicting addon/log readings are excluded. Full snapshots are preserved in the cumulative database and analysis JSON. Outgoing summary tables retain their existing level/weapon grouping; their detailed swing contexts also include the new fields.

## Save location and retention

WoW owns disk persistence. The addon cannot append to `WoWCombatLog` or write arbitrary files:

```text
_classic_beta_/WTF/Account/<account>/<realm>/<character>/SavedVariables/ForeverState.lua
```

The saved global is `ForeverStateDB`, schema 1. It contains `sessions`, each with `records`. A login/reload creates a new session. Old sessions and old records are never edited, merged, purged, or backfilled; item-cache completion creates a new record. The addon has no clear-history command and no rolling cap. Storage grows with equipment, stat, stance and speed changes, combat boundaries, and one checkpoint per minute, rather than with every attack. Large multi-month histories should be archived outside the addon folder/WTF before intentionally resetting storage.

SavedVariables is written on `/reload`, logout, or normal exit, not on every event. A client crash can lose the unsaved session. **The status counter proves what was loaded, not that the current in-memory history has reached disk.** There have been beta persistence concerns in the preceding Forever Rage experiment, so the short reload check above matters. Back up the file after a normal logout before the next login if that check fails. This is a client limitation, not something an additional event handler fixes.

## Aligning with a combat log

Use player GUID and wall-clock time to match sessions, then read each session's records in sequence. A record describes a complete observed state; an unknown field invalidates that field for attribution instead of carrying an earlier readable value through a restriction. Do not bridge logout, loading-screen boundaries, unexplained gaps, or changes in clock alignment without checking them. A heartbeat establishes what was visible at that time; it cannot recover the timing of an event missed earlier.

Wall time is deliberately declared as **one-second resolution**. The monotonic timer orders rapid events within a session but is not an independently measured millisecond wall clock. `uptimeBefore` and `uptimeAfter` bracket the wall read; at that instant, the wall epoch lies in `[wallEpoch, wallEpoch + 1)`. For a stable local clock, these pairs bound the offset from monotonic to wall time. Local/UTC strings and server time help diagnose timezone/clock differences. Do not assume the server clock and the combat log's local timestamp share an offset, or match timestamps across sessions using uptime alone.

The conservative initial analysis should exclude combat events within roughly one second of an equipment/level transition, widening that exclusion if the getter was stale or the snapshot was delayed. This timestamp scheme avoids invented millisecond precision. Reconstructing gear/level does not itself make ordinary combat's rage deltas attributable: incoming damage, abilities, passives, caps, and combined updates still need filtering.

The in-game addon collects metadata only. The reusable external analyzer in `analysis/analyze.py` imports these timelines and combat logs into a cumulative database; historical archives can be supplied explicitly with `--input`. Run `Analyze.ps1` after saving the session; read `analysis-data/latest-report.md` and `analysis-data/latest-incoming-report.md`. It estimates rage per weapon-second, two-hand multipliers, crit bonuses and incoming-damage relationships, with explicit exclusions and provenance. See [analysis/README.md](analysis/README.md) for usage, preservation behavior and limits.

## Sharing and development

The repository uses a **source allowlist** in `.gitignore`, with an explicit exception for everything inside `raw-logs/`, including compressed archives. Place only logs you intend to publish there. SavedVariables copies, `analysis-data`, `experiments`, generated reports/databases, caches and local Python configuration remain excluded. Add an explicit `.gitignore` exception when introducing another publishable file type or directory.

Before committing, inspect `git status --short` and `git diff --cached`. Share a Git checkout/archive of tracked files rather than zipping your entire installed addon directory, which also contains local research data. The tests generate synthetic characters, equipment and combat events; they do not depend on the shared raw logs or private account files.

From the repository root:

```text
python -m unittest discover -s analysis -p "test_*.py"
python -m pip install lupa
python tests/run.py
```

Only the optional Lua replay tests require `lupa` with its Lua 5.1 runtime. The addon and analyzer do not depend on it.

## API references and verification

Checked against Blizzard's generated Forever UI source mirrored here:

- [Level, level-up, attack speed, and speed events](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/UnitDocumentation.lua)
- [Item metadata and data-load events](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/ItemDocumentation.lua)
- [Crit and hit stat functions](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/PlayerScriptDocumentation.lua)
- [Stance spell-ID return layout](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_ActionBar/Shared/StanceBar.lua)
- [Melee hit-rating index and character stat listeners](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_UIPanels_Game/Camelot/PaperDollFrame.lua)
- [Armor-effectiveness API](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/PaperDollInfoDocumentation.lua)
- [Character-sheet armor percentage and effective-level reference](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_UIPanels_Game/Mainline/PaperDollFrame.lua)

All API returns and relevant event payloads are checked for secrets before comparison or serialization. Missing functions, secret values, and rejected event registrations produce diagnostics. The recorder never accesses secure combat-log processing or attempts to reveal secret values.

Run `python tests/run.py` from the addon folder with Python and `lupa` installed. Lua 5.1 replay tests exercise the real addon: stale level getters, rapid swaps, shield/two-hand/offhand types, link changes, delayed cache data, speed changes, secret values/error objects, missing APIs/listeners, heartbeat, clock changes, logout/reload serialization, and immutable prior history. They do not replace testing the actual beta client's API access and SavedVariables behavior.
