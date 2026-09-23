"""Import all available Forever logs/saves and rebuild cumulative rage research.

Python 3.10+ standard library only. Run from any working directory. The archive
and SQLite database are independent of WoW SavedVariables; no live game file is
modified. A later run can label previously imported combat after metadata saves.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from savedvars import loads, array
from measure import Timeline, analyze
from stances import StanceTimeline
from incoming import analyze_incoming, incoming_report, model_comparisons, summarize_incoming

ADDON = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"(\d+/\d+/\d+ \d+:\d+:\d+\.\d+)([+-]\d+)")
VERSION = "1.5.0"


def default_game_root(addon=ADDON):
    """Installed layout: <game>/Interface/AddOns/<addon>/analysis/analyze.py."""
    if addon.parent.name.lower() != 'addons' or addon.parent.parent.name.lower() != 'interface':
        raise ValueError('Addon is outside Interface/AddOns. Install it there or supply --game-root.')
    return addon.parent.parent.parent


def dump(value):
    # JSON normalizes numeric Lua keys to strings before further processing.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript('''
      CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS sources(hash TEXT PRIMARY KEY,kind TEXT,archive TEXT);
      CREATE TABLE IF NOT EXISTS locations(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,hash TEXT);
      CREATE TABLE IF NOT EXISTS issues(source TEXT,message TEXT,UNIQUE(source,message));
      CREATE TABLE IF NOT EXISTS sessions(key TEXT PRIMARY KEY,guid TEXT,header TEXT);
      CREATE TABLE IF NOT EXISTS records(session TEXT,sequence INTEGER,hash TEXT,data TEXT,
        PRIMARY KEY(session,sequence,hash));
      CREATE TABLE IF NOT EXISTS record_sources(session TEXT,sequence INTEGER,hash TEXT,source TEXT,
        PRIMARY KEY(session,sequence,hash,source));
      CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,event_id TEXT UNIQUE,epoch REAL,
        timestamp TEXT,kind TEXT,source_guid TEXT,dest_guid TEXT,fields TEXT,source TEXT,line INTEGER);
      CREATE INDEX IF NOT EXISTS event_source_guid ON events(source_guid,epoch);
      CREATE INDEX IF NOT EXISTS event_dest_guid ON events(dest_guid,epoch);
      CREATE TABLE IF NOT EXISTS event_sources(event_id TEXT,source TEXT,line INTEGER,
        PRIMARY KEY(event_id,source,line));
    ''')
    schema = db.execute("SELECT value FROM config WHERE key='schema'").fetchone()
    if schema and schema[0] != "1":
        raise ValueError("Unsupported analysis database schema; data left intact")
    db.execute("INSERT OR IGNORE INTO config VALUES('schema','1')")
    db.commit()
    return db


def discover(game, extras):
    found = set()
    for path in (game / "Logs").glob("WoWCombatLog*.txt"):
        found.add((path.resolve(), "combat"))
    for path in (game / "WTF").rglob("ForeverState.lua*"):
        if path.name in ("ForeverState.lua", "ForeverState.lua.bak"):
            found.add((path.resolve(), "state"))
    roots = extras  # Historical archives are opt-in; never scan another addon.
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*") if root.exists() else []
        for path in paths:
            if not path.is_file(): continue
            if path.name in ("ForeverState.lua", "ForeverState.lua.bak", "ForeverState.saved.lua"):
                found.add((path.resolve(), "state"))
            elif (path.name.startswith("WoWCombatLog") and path.suffix == ".txt") or path.name in ("combat-log.txt", "source.txt"):
                found.add((path.resolve(), "combat"))
    # Current complete logs first, then historical prefixes. Either ordering is
    # safe for counts; source order within equal timestamps remains stable.
    return sorted(found, key=lambda x: ("Logs" not in x[0].parts, str(x[0])))


def issue(db, source, message):
    db.execute("INSERT OR IGNORE INTO issues VALUES(?,?)", (source, message))


def ingest_state(db, raw, source):
    saved = loads(raw.decode("utf-8-sig"))
    if saved.get("schema") != 1:
        raise ValueError("Unsupported ForeverState schema")
    for session in array(saved["sessions"]):
        records = array(session["records"])
        if not records: continue
        # Normalize key types once so all subsequent comparisons are stable.
        records = [json.loads(json.dumps(r, ensure_ascii=False)) for r in records]
        first = records[0]
        guid = first["state"].get("guid")
        if not guid:
            issue(db, source, "Session has no startup GUID; not assigned to a character")
            continue
        identity = [guid, first["time"]["wallEpoch"], first["time"]["uptime"]]
        key = digest(dump(identity).encode())
        header = json.loads(json.dumps({k: v for k, v in session.items() if k != "records"}))
        old = db.execute("SELECT header FROM sessions WHERE key=?", (key,)).fetchone()
        if old:
            prior = json.loads(old[0])
            # A saved closed flag can advance but a shorter stale copy cannot
            # reopen an already completed session.
            header["closed"] = bool(header.get("closed") or prior.get("closed"))
        db.execute("INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET header=excluded.header",
                   (key, guid, dump(header)))
        for r in records:
            if r["state"].get("guid") not in (None, guid):
                raise ValueError("Mixed GUIDs inside one state session")
            if not isinstance(r["sequence"], int) or r["sequence"] < 1:
                raise ValueError("Invalid state sequence")
            for field in ("wallEpoch", "uptimeBefore", "uptimeAfter"):
                if not isinstance(r["time"].get(field), (int, float)) or not math.isfinite(r["time"][field]):
                    raise ValueError("State snapshot missing finite clock fields")
            encoded = dump(r)
            record_hash = digest(encoded.encode())
            db.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?)", (key, r["sequence"], record_hash, encoded))
            db.execute("INSERT OR IGNORE INTO record_sources VALUES(?,?,?,?)", (key, r["sequence"], record_hash, source))


def ingest_combat(db, raw, source):
    # A writer may still be appending its final line. Preserve the bytes in the
    # archive but import only newline-terminated records; next run completes it.
    if raw and not raw.endswith(b"\n"):
        raw = raw[:raw.rfind(b"\n") + 1]
    occurrences, supported = Counter(), False
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip(): continue
        try:
            stamp, payload = line.split("  ", 1)
            match = STAMP.fullmatch(stamp)
            if not match: raise ValueError("unsupported timestamp")
            epoch = datetime.strptime(match[1], "%m/%d/%Y %H:%M:%S.%f").replace(
                tzinfo=timezone(timedelta(hours=int(match[2])))).timestamp()
            # WoW escapes quotes inside names as backslash-quote (e.g. Remy
            # "Two Times"), rather than only using RFC CSV doubled quotes.
            fields = next(csv.reader([payload], strict=True, escapechar="\\"))
            kind = fields[0]
            if kind == "COMBAT_LOG_VERSION":
                supported = fields == ["COMBAT_LOG_VERSION", "22", "ADVANCED_LOG_ENABLED", "1",
                                       "BUILD_VERSION", "1.60.1", "PROJECT_ID", "18"]
                if not supported: issue(db, source, f"line {number}: unsupported combat-log header")
            if not supported: continue
            # Identical rows can legitimately occur more than once in a file.
            # The ordinal preserves that multiplicity while deduplicating file
            # copies and growing-prefix archives. Timestamps remain in the key.
            fingerprint = digest(line.encode())
            occurrences[fingerprint] += 1
            event_id = fingerprint + ":" + str(occurrences[fingerprint])
            unit_event = len(fields) >= 9 and kind not in ("COMBAT_LOG_VERSION", "ZONE_CHANGE", "MAP_CHANGE")
            sguid, dguid = (fields[1], fields[5]) if unit_event else (None, None)
            db.execute("INSERT OR IGNORE INTO events(event_id,epoch,timestamp,kind,source_guid,dest_guid,fields,source,line) VALUES(?,?,?,?,?,?,?,?,?)",
                       (event_id, epoch, stamp, kind, sguid, dguid, dump(fields), source, number))
            db.execute("INSERT OR IGNORE INTO event_sources VALUES(?,?,?)", (event_id, source, number))
        except (ValueError, IndexError, csv.Error) as error:
            supported = False  # A malformed gap cannot be silently bridged.
            issue(db, source, f"line {number}: {error}; remainder skipped until a supported header")


def ingest(db, paths, storage, rescan=False):
    changed, notices = 0, []
    for path, kind in paths:
        try:
            stat = path.stat()
            prior = db.execute("SELECT size,mtime FROM locations WHERE path=?", (str(path),)).fetchone()
            if not rescan and prior and tuple(prior) == (stat.st_size, stat.st_mtime_ns): continue
            if not stat.st_size:
                notices.append(f"Empty file, awaiting game flush: {path.name}")
                continue
            with path.open("rb") as handle:
                raw = handle.read(stat.st_size)  # Snapshot the current extent, not an unbounded growing stream.
            source = digest(raw)
            if not db.execute("SELECT 1 FROM sources WHERE hash=?", (source,)).fetchone():
                archive = storage / "archive" / (source + ".gz")
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    temporary = archive.with_suffix("." + uuid.uuid4().hex + ".tmp")
                    temporary.write_bytes(gzip.compress(raw, mtime=0))
                    os.replace(temporary, archive)
                db.execute("SAVEPOINT import_file")
                try:
                    (ingest_state if kind == "state" else ingest_combat)(db, raw, source)
                except (ValueError, KeyError, TypeError, UnicodeError, OverflowError) as error:
                    db.execute("ROLLBACK TO import_file")
                    issue(db, source, f"{kind} import failed: {error}")
                finally:
                    db.execute("RELEASE import_file")
                db.execute("INSERT INTO sources VALUES(?,?,?)", (source, kind, str(archive.relative_to(storage))))
                changed += 1
            db.execute("INSERT INTO locations VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,hash=excluded.hash",
                       (str(path), len(raw), stat.st_mtime_ns, source))
        except OSError as error:
            notices.append(f"Could not read {path}: {error}")
    return changed, notices


def load_sessions(db):
    sessions = []
    for row in db.execute("SELECT * FROM sessions ORDER BY key"):
        header = json.loads(row["header"])
        variants = defaultdict(list)
        for record in db.execute("SELECT sequence,data FROM records WHERE session=? ORDER BY sequence,hash", (row["key"],)):
            variants[record["sequence"]].append(json.loads(record["data"]))
        records = [v[0] for _, v in sorted(variants.items())]
        conflict = any(len(v) > 1 for v in variants.values()) or list(sorted(variants)) != list(range(1, len(variants) + 1))
        sessions.append({"key": row["key"], "guid": row["guid"], "header": header,
                         "build": str(header.get("build", "unknown")), "records": records,
                         "conflict": conflict, "record_variants": dict(variants) if conflict else {}})
    return sorted(sessions, key=lambda s: s["records"][0]["time"]["wallEpoch"])


def statistics_for(rows):
    rates = [r["rage"] / r["context"]["speed"] for r in rows]
    return {"n": len(rows), "mean_rage": statistics.mean(r["rage"] for r in rows),
            "rage_range": [min(r["rage"] for r in rows), max(r["rage"] for r in rows)],
            "damage_range": [min(r["damage"] for r in rows), max(r["damage"] for r in rows)],
            "mean_rage_per_weapon_second": statistics.mean(rates),
            "rate_range": [min(rates), max(rates)],
            "rate_sample_sd": statistics.stdev(rates) if len(rows) > 1 else None}


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        c = row["context"]
        if row["accepted"] and c.get("speed"):
            key = tuple(c[k] for k in ("guid", "build", "level", "type", "hand", "item_id", "speed")) + (row["outcome"],)
            grouped[key].append(row)
    groups = []
    for key, items in sorted(grouped.items()):
        c = items[0]["context"]
        groups.append({**{k: c[k] for k in ("guid", "build", "level", "type", "hand", "item_id", "speed", "weapon", "speed_basis")},
                       "outcome": key[-1], **statistics_for(items), "event_ids": [r["event_id"] for r in items]})
    crits, multipliers = [], []
    for normal in [g for g in groups if g["outcome"] == "normal"]:
        for crit in [g for g in groups if g["outcome"] == "critical"]:
            keys = ("guid", "build", "level", "type", "hand", "item_id", "speed")
            if all(normal[k] == crit[k] for k in keys):
                crits.append({"weapon": normal["weapon"], "guid": normal["guid"], "level": normal["level"], "build": normal["build"],
                              "speed": normal["speed"], "normal_n": normal["n"], "crit_n": crit["n"],
                              "crit_to_normal_ratio": crit["mean_rage"] / normal["mean_rage"],
                              "extra_rage_per_crit": crit["mean_rage"] - normal["mean_rage"]})
    for one in [g for g in groups if g["type"] == "MH"]:
        for two in [g for g in groups if g["type"] == "2H"]:
            if all(one[k] == two[k] for k in ("guid", "build", "level", "hand", "outcome")):
                multipliers.append({"guid": one["guid"], "level": one["level"], "build": one["build"], "outcome": one["outcome"],
                                    "one_hand": one["weapon"], "two_hand": two["weapon"], "one_n": one["n"], "two_n": two["n"],
                                    "speed_normalized_multiplier": two["mean_rage_per_weapon_second"] / one["mean_rage_per_weapon_second"]})
    # Under the requested speed-proportional working model, also show a clearly
    # conditional comparison pooling noncritical normals/glances. Keep it apart
    # from outcome-matched evidence so future data can test that extra assumption.
    pools = defaultdict(lambda: defaultdict(list))
    for row in rows:
        c = row["context"]
        if row["accepted"] and c.get("speed") and row["outcome"] in ("normal", "glancing"):
            pools[(c["guid"], c["build"], c["level"], c["hand"])][c["type"]].append(row)
    provisional = []
    for key, types in sorted(pools.items()):
        if "MH" in types and "2H" in types:
            one, two = statistics_for(types["MH"]), statistics_for(types["2H"])
            provisional.append({"guid": key[0], "build": key[1], "level": key[2], "hand": key[3],
                                "one_n": one["n"], "two_n": two["n"],
                                "one_rate": one["mean_rage_per_weapon_second"], "two_rate": two["mean_rage_per_weapon_second"],
                                "multiplier": two["mean_rage_per_weapon_second"] / one["mean_rage_per_weapon_second"],
                                "one_outcomes": dict(Counter(r["outcome"] for r in types["MH"])),
                                "two_outcomes": dict(Counter(r["outcome"] for r in types["2H"]))})
    return groups, crits, multipliers, provisional


def report(result):
    lines = ["# Forever rage research", "", f"Generated {result['generated_utc']} by analyzer {VERSION}.", "",
             "Working model: rage per hit = weapon swing time × a rage-per-second coefficient, with possible weapon-type and crit modifiers. Reports describe observations, not a claim that a difference is innate.", "",
             f"{len(result['sessions'])} recovered sessions; {sum(len(s['records']) for s in result['sessions'])} distinct snapshots; "
             f"{len(result['swings'])} player swing attempts; {sum(r['accepted'] for r in result['swings'])} accepted net-rage candidates.", "",
             "## Data health", ""]
    lines += [f"- {n}" for n in result["notices"]] or ["- No new input-read warnings."]
    for s in result["sessions"]:
        first, last = s["records"][0], s["records"][-1]
        lines.append(f"- {s['guid']} / {s['key'][:12]}: {first['time'].get('localTime')} to {last['time'].get('localTime')}; "
                     f"{len(s['records'])} snapshots, {s['combat_swing_count']} logged swing attempts; "
                     f"loaded {s['header'].get('loadedSessions', '?')} prior sessions; conflicts={s['conflict']}.")
    if result["issues"]:
        lines += [f"- Import warning ({x['source'][:12]}): {x['message']}" for x in result["issues"]]
    lines += ["", "## 1. Rage per second of weapon swing time", "",
              "Speeds are reference effective speeds within a continuous gear/level segment. A secret per-hit speed is never presented as directly measured. Multiple readable speeds in one segment leave its coefficient unknown.", "",
              "| Character | Build | Level | Weapon | Type | Speed | Outcome | n | Damage range | Rage range | Mean rage / weapon second |",
              "| --- | --- | ---: | --- | --- | ---: | --- | ---: | --- | --- | ---: |"]
    for g in result["groups"]:
        lines.append(f"| {g['guid']} | {g['build']} | {g['level']} | {g['weapon']} | {g['type']} | {g['speed']:.3f} | {g['outcome']} | {g['n']} | {g['damage_range']} | {g['rage_range']} | {g['mean_rage_per_weapon_second']:.4f} |")
    lines += ["", "## 2. Two-handed multiplier", "",
              "Comparisons match character, build, level, hand and hit outcome, and divide rage by speed before comparing. Weapon/buff/talent/stance differences can still confound an innate-effect claim.", ""]
    if not result["two_hand_comparisons"]:
        lines.append("Insufficient matched-outcome data. A one-hand normal hit is not compared directly with a two-hand glance to declare an innate multiplier.")
    for x in result["two_hand_comparisons"]:
        lines.append(f"- Level {x['level']} {x['outcome']}: {x['two_hand']} / {x['one_hand']} = {x['speed_normalized_multiplier']:.4f}×; n={x['two_n']} / {x['one_n']}.")
    if result["provisional_two_hand_comparisons"]:
        lines += ["", "Conditional estimate under an additional assumption that normal and glancing hits share the same noncritical coefficient (different outcome mixes are allowed here):", ""]
    for x in result["provisional_two_hand_comparisons"]:
        lines.append(f"- Level {x['level']}: one-hand {x['one_rate']:.4f}, two-hand {x['two_rate']:.4f} rage/weapon-second; multiplier {x['multiplier']:.4f}× ({(x['multiplier'] - 1) * 100:.2f}% higher). "
                     f"One-hand outcomes {x['one_outcomes']}; two-hand outcomes {x['two_outcomes']}. Provisional, not proof of an innate modifier.")
    lines += ["", "## 3. Critical-hit bonus", "",
              "Normal and critical hits are paired only within the same character, build, level, weapon, hand, type and reference speed. Small samples remain preliminary.", ""]
    if not result["crit_comparisons"]: lines.append("Insufficient matching normal/critical samples.")
    for x in result["crit_comparisons"]:
        lines.append(f"- Level {x['level']} {x['weapon']} ({x['speed']} s): crit/normal rage = {x['crit_to_normal_ratio']:.4f}×; extra rage = {x['extra_rage_per_crit']:.4f}; normal n={x['normal_n']}, crit n={x['crit_n']}.")
    lines += ["", "## Measurement limits and provenance", "",
              f"- Raw resource divisor: {result['settings']['rage_scale']}; this is a configurable assumption, and raw deltas remain in analysis.json.",
              f"- Close-timing candidates included: {result['settings']['include_close']}. Default excludes ±250 ms around incoming hits/abilities and short baseline gaps, alongside caps, spending, overkill, ambiguity, and state transitions.",
              "- Every autoattack attempt is retained in analysis.json, including rejected samples and explicit reasons. SWING_DAMAGE_LANDED is not counted as an extra outgoing hit.",
              "- Passive/unlogged resource changes or delayed updates can still resemble clean snapshot differences. Ordinary-play data cannot prove an innate mechanic without controlled replication.",
              "- Metadata cannot be read from the active game's memory: use /reload or normal logout, then rerun. Previously unassigned log events are re-evaluated when metadata arrives.",
              "- Sessions/records with divergent saved copies are flagged and excluded from attribution. Earlier archives and reports are never overwritten.",
              "- Event deduplication uses full timestamped log text plus its occurrence number within each file, preserving repeated identical rows within a file. Copies/prefixes therefore do not inflate counts. Independent files containing indistinguishable identical events at the exact same timestamp cannot be distinguished.",
              "- Raw compressed sources, SQLite provenance, cumulative snapshots, individual swing results, and previous reports are kept in analysis-data. Back up that whole directory independently of the addon installation.", ""]
    return "\n".join(lines)


def run(game, storage, extras=(), player=None, scale=10, include_close=False, rescan=False):
    storage.mkdir(parents=True, exist_ok=True)
    db = database(storage / "history.sqlite3")
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            new_sources, notices = ingest(db, discover(game, extras), storage, rescan)
        sessions = load_sessions(db)
        if player: sessions = [s for s in sessions if s["guid"] == player]
        timeline = Timeline(sessions)
        swings, incoming = [], []
        for guid in sorted({s["guid"] for s in sessions}):
            events = [{"id": r["event_id"], "epoch": r["epoch"], "timestamp": r["timestamp"],
                       "fields": json.loads(r["fields"]), "source": r["source"], "line": r["line"]}
                      for r in db.execute("SELECT * FROM events WHERE source_guid=? OR dest_guid=? OR kind='COMBAT_LOG_VERSION' ORDER BY epoch,seq", (guid, guid))]
            stance_timeline = StanceTimeline(timeline, events, guid)
            swings.extend(analyze(events, guid, stance_timeline, scale, include_close))
            incoming.extend(analyze_incoming(events, guid, stance_timeline, scale))
        groups, crits, multipliers, provisional = summarize(swings)
        for s in sessions:
            s["combat_swing_count"] = sum(r["context"].get("session") == s["key"] for r in swings)
            prior = [x for x in sessions if x["guid"] == s["guid"] and x["records"][0]["time"]["wallEpoch"] < s["records"][0]["time"]["wallEpoch"]]
            if prior and s["header"].get("loadedSessions") == 0:
                notices.append(f"History reset detected at {s['records'][0]['time'].get('localTime')}; imported earlier sessions remain protected in this archive.")
            if not s["combat_swing_count"]:
                notices.append(f"No assigned combat swings for session starting {s['records'][0]['time'].get('localTime')}; logging may be absent, buffered, or unmatched.")
        result = {"version": VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(),
                  "new_source_versions": new_sources, "settings": {"rage_scale": scale, "include_close": include_close},
                  "sessions": sessions, "swings": swings, "groups": groups, "crit_comparisons": crits,
                  "two_hand_comparisons": multipliers, "provisional_two_hand_comparisons": provisional, "notices": notices,
                  "issues": [dict(r) for r in db.execute("SELECT * FROM issues ORDER BY source,message")],
                  "sources": [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY hash")],
                  "locations": [dict(r) for r in db.execute("SELECT * FROM locations ORDER BY path")]}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        output = storage / "runs" / run_id
        atomic(output / "analysis.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        atomic(output / "report.md", report(result))
        incoming_data = {'version':VERSION,'generated_utc':result['generated_utc'],
                         'settings':{'rage_scale':scale,'timing_guard_seconds':.25},
                         'rows':incoming,'groups':summarize_incoming(incoming),
                         'model_comparisons':model_comparisons(incoming)}
        atomic(output / "incoming.json", json.dumps(incoming_data, ensure_ascii=False, indent=2) + "\n")
        atomic(output / "incoming-report.md", incoming_report(incoming))
        atomic(storage / "latest-incoming-report.md", incoming_report(incoming))
        atomic(storage / "latest-report.md", report(result))
        atomic(storage / "latest.json", json.dumps({"run": run_id, "report": str(output / "report.md"), "data": str(output / "analysis.json"),
                                                   "incoming_report":str(output / "incoming-report.md"),"incoming_data":str(output / "incoming.json")}, indent=2))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result, output
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, help="Defaults to the game folder above this installed addon")
    parser.add_argument("--data-dir", type=Path, default=ADDON / "analysis-data")
    parser.add_argument("--input", type=Path, action="append", default=[], help="Additional old-save/log file or archive directory")
    parser.add_argument("--player", help="Optional exact GUID; defaults to all characters found in metadata")
    parser.add_argument("--rage-scale", type=float, default=10)
    parser.add_argument("--include-close", action="store_true", help="Exploratory: include otherwise valid close-timing samples")
    parser.add_argument("--rescan", action="store_true", help="Rehash unchanged input paths (content is still deduplicated)")
    args = parser.parse_args()
    if args.game_root is None:
        try:
            args.game_root = default_game_root()
        except ValueError as error:
            parser.error(str(error))
    if not math.isfinite(args.rage_scale) or args.rage_scale <= 0: parser.error("rage scale must be positive and finite")
    result, output = run(args.game_root.resolve(), args.data_dir.resolve(), args.input, args.player,
                         args.rage_scale, args.include_close, args.rescan)
    print(f"Imported {result['new_source_versions']} new source versions; {len(result['sessions'])} sessions; "
          f"{len(result['swings'])} swing attempts; {sum(r['accepted'] for r in result['swings'])} accepted candidates.")
    for notice in result["notices"]: print(notice)
    print(f"Report: {output / 'report.md'}")
    print(f"Incoming damage report: {output / 'incoming-report.md'}")
    if result["issues"]: print(f"Import warnings: {len(result['issues'])}; see report (original bytes preserved).")


if __name__ == "__main__":
    main()
