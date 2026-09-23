"""Run: python analysis/test_analysis.py. Tests use isolated temporary databases."""
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

import analyze as app
from savedvars import loads
from synthetic_fixtures import combat, saved, header

ADDON = Path(__file__).resolve().parents[1]
HEADER = header()


class ParserTests(unittest.TestCase):
    def test_literal_only_and_escaped_strings(self):
        data = loads(r'''ForeverStateDB = {schema=1, ["s"]="quote: \" and \195\169", ["x"]={true,false,3.25},}''')
        self.assertEqual(data["s"], 'quote: " and é')
        self.assertEqual(data["x"], {1: True, 2: False, 3: 3.25})
        for bad in ('ForeverStateDB = os.execute("anything")', 'ForeverStateDB = {}; print("bad")',
                    'ForeverStateDB = {x=1,x=2}', 'ForeverStateDB = {'):
            with self.assertRaises(ValueError): loads(bad)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game, self.storage = self.root / "game", self.root / "data"
        self.logs, self.saves = self.game / "Logs", self.game / "WTF/character/SavedVariables"
        self.logs.mkdir(parents=True)
        self.saves.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, **kwargs):
        return app.run(self.game, self.storage, **kwargs)[0]

    def seed(self):
        (self.saves / 'ForeverState.lua').write_bytes(saved([2]))
        (self.saves / 'ForeverState.lua.bak').write_bytes(saved([0,1]))
        (self.logs / 'WoWCombatLog-synthetic.txt').write_bytes(combat([0,1,2]))

    def test_synthetic_sessions_and_repeat_are_idempotent(self):
        self.seed()
        first, second = self.run_import(), self.run_import()
        self.assertEqual(len(first["sessions"]), 3)
        self.assertEqual(sum(len(s["records"]) for s in first["sessions"]), 18)
        self.assertEqual(len(first["swings"]), 12)
        self.assertEqual(sum(r["accepted"] for r in first["swings"]), 9)
        self.assertEqual(first["swings"], second["swings"])
        self.assertEqual(second["new_source_versions"], 0)
        self.assertFalse(second["issues"])
        self.assertEqual(second["crit_comparisons"][0]["crit_to_normal_ratio"], 1)
        self.assertEqual(len(second["two_hand_comparisons"]), 2)
        self.assertAlmostEqual(second["provisional_two_hand_comparisons"][0]["multiplier"], 1.3, delta=.02)

    def test_growing_log_metadata_arrival_and_overlapping_copy(self):
        name = "WoWCombatLog-synthetic.txt"
        raw = combat([0,1,2])
        log = self.logs / name
        log.write_bytes(combat([0]))
        (self.saves / "ForeverState.lua").write_bytes(saved([0]))
        early = self.run_import()
        log.write_bytes(raw)
        without_metadata = self.run_import()
        self.assertGreater(len(without_metadata["swings"]), len(early["swings"]))
        self.assertEqual(len(without_metadata["sessions"]), 1)
        (self.saves / "ForeverState.lua.bak").write_bytes(saved([0,1]))
        complete = self.run_import()
        self.assertEqual(len(complete["swings"]), len(without_metadata["swings"]))
        self.assertGreater(sum(r["accepted"] for r in complete["swings"]), sum(r["accepted"] for r in without_metadata["swings"]))
        extra = self.root / "old-copy"
        extra.mkdir()
        (extra / "combat-log.txt").write_bytes(combat([0]))
        (extra / "ForeverState.saved.lua").write_bytes(saved([0]))
        copied = self.run_import(extras=[extra])
        self.assertEqual(copied["swings"], complete["swings"])
        self.assertEqual(len(copied["sessions"]), 2)
        self.assertFalse(any(s["conflict"] for s in copied["sessions"]))

    def test_removed_sources_and_later_reset_cannot_erase_history(self):
        self.seed()
        before = self.run_import()
        for path in [*self.logs.iterdir(), *self.saves.iterdir()]:
            path.rename(path.with_name(path.name + ".retired"))
        after = self.run_import()
        self.assertEqual(before["swings"], after["swings"])
        self.assertEqual(len(after["sessions"]), 3)
        self.assertTrue(any("History reset detected" in n for n in after["notices"]))
        self.assertTrue(all((self.storage / s["archive"]).exists() for s in after["sources"]))

    def test_conflicting_snapshot_preserved_and_excluded(self):
        (self.saves / "ForeverState.lua").write_bytes(saved([0]))
        (self.logs / "WoWCombatLog-test.txt").write_bytes(combat([0]))
        before = self.run_import()
        self.assertEqual(sum(r["accepted"] for r in before["swings"]), 3)
        save = self.saves / "ForeverState.lua"
        original = save.read_text()
        save.write_text(original.replace('["level"] = 9,', '["level"] = 10,', 1))
        after = self.run_import()
        self.assertTrue(after["sessions"][0]["conflict"])
        self.assertEqual(len(after["sessions"][0]["record_variants"][1]), 2)
        self.assertEqual(sum(r["accepted"] for r in after["swings"]), 0)

    def test_partial_line_quoted_name_and_exact_duplicate_multiplicity(self):
        db = app.database(self.storage / "test.sqlite3")
        try:
            line = (r'9/18/2026 13:16:00.001-7  SPELL_AURA_APPLIED,Player-A,"Foo",0x1,0x0,Creature-B,"Remy \"Two Times\"",0x1,0x0,123,"Buff",0x1,BUFF' + '\n').encode()
            with db:
                app.ingest_combat(db, HEADER + line + line[:-1], "partial")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
                app.ingest_combat(db, HEADER + line + line, "complete")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
                app.ingest_combat(db, HEADER + line + line, "copy")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
            fields = json.loads(db.execute("SELECT fields FROM events WHERE kind='SPELL_AURA_APPLIED'").fetchone()[0])
            self.assertEqual(fields[6], 'Remy "Two Times"')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)
        finally:
            db.close()

    def test_unsupported_header_archived_without_invented_results(self):
        (self.logs / "WoWCombatLog-test.txt").write_bytes(HEADER.replace(b",22,", b",99,"))
        result = self.run_import()
        self.assertTrue(result["issues"])
        self.assertEqual(result["swings"], [])
        self.assertEqual(len(result["sources"]), 1)

    def test_failed_literal_save_does_not_damage_existing_data(self):
        self.seed()
        before = self.run_import()
        (self.saves / "ForeverState.lua").write_text('ForeverStateDB = print("not allowed")')
        after = self.run_import()
        self.assertEqual(len(after["sessions"]), 3)
        self.assertEqual(before["swings"], after["swings"])
        self.assertTrue(after["issues"])


class ComparisonTests(unittest.TestCase):
    def row(self, item, kind, speed, outcome, rage, level=9):
        return {"accepted": True, "context": {"guid": "player", "build": "1", "level": level,
                "type": kind, "hand": "MH", "item_id": item, "speed": speed, "weapon": str(item),
                "speed_basis": "reference"}, "rage": rage, "damage": 10, "outcome": outcome, "event_id": str(item) + outcome}

    def test_matched_comparisons_and_level_separation(self):
        rows = [self.row(1, "MH", 2, "normal", 7), self.row(1, "MH", 2, "critical", 8.4),
                self.row(2, "2H", 3, "normal", 13.65), self.row(1, "MH", 2, "critical", 99, level=10)]
        groups, crits, pairs, provisional = app.summarize(rows)
        self.assertEqual(len(groups), 4)
        self.assertEqual(len(crits), 1)
        self.assertAlmostEqual(crits[0]["crit_to_normal_ratio"], 1.2)
        self.assertAlmostEqual(crits[0]["extra_rage_per_crit"], 1.4)
        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(pairs[0]["speed_normalized_multiplier"], 1.3)
        self.assertAlmostEqual(provisional[0]["multiplier"], 1.3)

    def test_relaxed_mode_is_explicit_and_keeps_other_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'synthetic-archive'
            archive.mkdir()
            (archive/'ForeverState.saved.lua').write_bytes(saved([0,1,2]))
            (archive/'combat-log.txt').write_bytes(combat([0,1,2]))
            result, _ = app.run(Path(tmp) / "game", Path(tmp) / "data", [archive], include_close=True)
            self.assertTrue(result["settings"]["include_close"])
            self.assertEqual(sum(r["accepted"] for r in result["swings"]), 12)
            self.assertFalse(any(r["accepted"] and r.get("delta_raw", 0) <= 0 for r in result["swings"]))


class PathTests(unittest.TestCase):
    def test_game_root_is_relative_to_addon_not_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'A game with spaces'
            self.assertEqual(app.default_game_root(root/'Interface/AddOns/ForeverState'),root)
            with self.assertRaises(ValueError): app.default_game_root(root/'standalone-checkout')

    def test_cli_runs_from_unrelated_directory_with_explicit_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'game/Logs').mkdir(parents=True)
            (root/'game/Logs/WoWCombatLog-test.txt').write_bytes(combat([0]))
            saves=root/'game/WTF/test/SavedVariables'
            saves.mkdir(parents=True)
            (saves/'ForeverState.lua').write_bytes(saved([0]))
            result=subprocess.run([sys.executable,str(ADDON/'analysis/analyze.py'),
                                   '--game-root',str(root/'game'),'--data-dir',str(root/'data')],
                                  cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            pointer=json.loads((root/'data/latest.json').read_text())
            data=json.loads(Path(pointer['data']).read_text())
            self.assertEqual(len(data['swings']),4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
