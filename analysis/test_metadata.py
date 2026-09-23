"""Stats/stance persistence and conservative transition attribution."""
import tempfile
import unittest
from pathlib import Path

import analyze as app
from measure import Timeline
from synthetic_fixtures import BASE, GUID, combat, lua, session


def metadata():
    result = session(0)
    result.update(key='test-session', guid=GUID)
    for record in result['records']:
        record['state']['stats'] = {'maxHP':250, 'armor':{'effective':150,'mitigationFraction':.1234567890123,
                                    'mitigationPercent':12.34567890123,'mitigationAttackerLevel':9,
                                    'mitigationLevelSource':'UnitEffectiveLevel',
                                    'mitigationSource':'C_PaperDollInfo.GetArmorEffectiveness'},
                                    'attackPower':{'base':50,'positive':10,'negative':-5,'total':55},
                                    'meleeCritPercent':5.25,'meleeHitModifierPercent':0}
        record['state']['stance'] = {'known':True,'index':1,'spellID':2457,'name':'Battle Stance'}
    return result


class MetadataTests(unittest.TestCase):
    def test_new_fields_survive_import_and_old_sessions_remain_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            (game/'Logs').mkdir()
            (game/'WTF').mkdir()
            save = 'ForeverStateDB = '+lua({'schema':1,'sessions':[metadata(),session(1)]})
            (game/'WTF/ForeverState.lua').write_text(save)
            (game/'Logs/WoWCombatLog-test.txt').write_bytes(combat([0,1]))
            first = app.run(game,game/'data')[0]
            second = app.run(game,game/'data')[0]
            self.assertEqual(first['swings'],second['swings'])
            self.assertEqual(second['new_source_versions'],0)
            current = first['swings'][0]['context']
            self.assertEqual(current['stats']['attackPower']['total'],55)
            self.assertEqual(current['stats']['armor']['mitigationPercent'],12.34567890123)
            self.assertEqual(current['stats']['armor']['mitigationAttackerLevel'],9)
            self.assertEqual(current['stance_key'],'spell:2457')
            self.assertIsNone(first['swings'][-1]['context']['stats'])
            self.assertEqual(first['swings'][-1]['context']['stance_key'],'unknown')

    def test_stance_boundary_restriction_and_recovery(self):
        data = metadata()
        records = data['records']
        records[3]['reason'] = 'UPDATE_SHAPESHIFT_FORM'
        records[3]['state']['stance'] = {'known':False}
        for record in records[4:]:
            record['state']['stance'] = {'known':True,'index':2,'spellID':71,'name':'Defensive Stance'}
        timeline = Timeline([data])
        self.assertIsNotNone(timeline.at(GUID,BASE+30.5)['research_issue'])
        restricted = timeline.at(GUID,BASE+34)
        self.assertEqual(restricted['stance_key'],'unknown')
        self.assertNotIn('spellID',restricted['stance'])
        self.assertEqual(timeline.at(GUID,BASE+44)['stance_key'],'spell:71')
        self.assertNotEqual(timeline.at(GUID,BASE+20)['research_segment'],restricted['research_segment'])

    def test_stale_stance_event_and_nonweapon_gear_guard(self):
        data = metadata()
        data['records'][3]['reason'] = 'UPDATE_SHAPESHIFT_FORM'  # Getter unchanged.
        timeline = Timeline([data])
        self.assertIsNotNone(timeline.at(GUID,BASE+30.5)['research_issue'])
        self.assertNotEqual(timeline.at(GUID,BASE+20)['research_segment'],
                            timeline.at(GUID,BASE+34)['research_segment'])
        data = metadata()
        for record in data['records'][3:]:
            record['state']['equipment']['1'] = {'state':'equipped','itemID':999}
            record['state']['stats']['armor']['effective'] = 200
        timeline = Timeline([data])
        self.assertIsNotNone(timeline.at(GUID,BASE+30.5)['research_issue'])
        self.assertEqual(timeline.at(GUID,BASE+34)['stats']['armor']['effective'],200)

    def test_listener_failure_prevents_stance_assignment(self):
        data = metadata()
        data['header'] = {'eventRegistrationErrors':{'UPDATE_SHAPESHIFT_FORM':'unavailable'}}
        self.assertEqual(Timeline([data]).at(GUID,BASE+20)['stance_key'],'unknown')


if __name__ == '__main__':
    unittest.main()
