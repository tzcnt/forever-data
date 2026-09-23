"""Disk aura evidence is scoped to character and continuous observed history."""
import unittest

from incoming import analyze_incoming
from stances import StanceTimeline
from test_incoming import PLAYER, Timeline, fixture


def event(t, kind='SPELL_AURA_APPLIED', spell='2457', player=PLAYER):
    return {'id':f'{t}:{kind}:{spell}', 'epoch':t, 'timestamp':str(t), 'source':'test', 'line':1,
            'fields':[kind,player,'p','0','0',player,'p','0','0',spell,'test stance','1','BUFF']}


class StanceTests(unittest.TestCase):
    def test_only_own_aura_establishes_stance_and_failed_cast_is_ignored(self):
        events = [event(1,player='Player-OTHER'), event(2,'SPELL_CAST_SUCCESS'),
                  event(3),event(4,'SPELL_CAST_FAILED','71')]
        timeline = StanceTimeline(Timeline(),events,PLAYER)
        self.assertEqual(timeline.at(PLAYER,2.5)['log_stance_key'],'unknown')
        self.assertEqual(timeline.at(PLAYER,5)['stance_key'],'spell:2457')
        self.assertEqual(timeline.at(PLAYER,5)['log_stance_evidence']['epoch'],3)

    def test_removal_switch_and_duplicate_observation(self):
        events = [event(1),event(3,'SPELL_AURA_APPLIED','71'),
                  event(3,'SPELL_AURA_REMOVED','2457'),event(4,'SPELL_AURA_REFRESH','71'),
                  event(6,'SPELL_AURA_REMOVED','71')]
        timeline = StanceTimeline(Timeline(),events,PLAYER)
        self.assertEqual(timeline.at(PLAYER,3.5)['stance_key'],'spell:71')
        self.assertEqual(timeline.at(PLAYER,3.5)['log_stance_segment'],timeline.at(PLAYER,4.5)['log_stance_segment'])
        self.assertEqual(timeline.at(PLAYER,7)['log_stance_key'],'unknown')
        self.assertTrue(timeline.at(PLAYER,3.1)['research_issue'])

    def test_logging_death_and_world_boundaries_reset_evidence(self):
        header = event(3); header['fields'] = ['COMBAT_LOG_VERSION']
        death = event(6,'UNIT_DIED')
        base = Timeline()
        base.sessions = [{'guid':PLAYER,'records':[{'reason':'PLAYER_ENTERING_WORLD','bounds':[9,10]}]}]
        timeline = StanceTimeline(base,[event(1),header,event(4),death,event(7)],PLAYER)
        for t in (3.5,6.5,10.5):
            self.assertEqual(timeline.at(PLAYER,t)['log_stance_key'],'unknown')
        self.assertEqual(timeline.at(PLAYER,8)['stance_key'],'spell:2457')

    def test_addon_disagreement_is_not_silently_overridden(self):
        class Recorded(Timeline):
            def at(self,guid,epoch):
                return dict(super().at(guid,epoch),stance_key='spell:71')
        timeline = StanceTimeline(Recorded(),[event(1)],PLAYER)
        self.assertEqual(timeline.at(PLAYER,2)['stance_key'],'unknown')
        self.assertEqual(timeline.at(PLAYER,2)['research_issue'],'addon/log stance disagreement')
        self.assertEqual(timeline.at(PLAYER,.5)['stance_key'],'spell:71')

    def test_prior_incoming_hits_are_recovered_without_saved_stance(self):
        events = fixture()+[event(1),event(5,'SPELL_AURA_REMOVED'),event(5,'SPELL_AURA_APPLIED','71')]
        events.sort(key=lambda e:e['epoch'])
        rows = analyze_incoming(events,PLAYER,StanceTimeline(Timeline(),events,PLAYER))
        self.assertTrue(rows[0]['accepted'])
        self.assertEqual(rows[0]['context']['stance_key'],'spell:2457')
        self.assertFalse(rows[1]['accepted'])
        self.assertTrue(rows[2]['accepted'])
        self.assertEqual(rows[2]['context']['stance_key'],'spell:71')


if __name__ == '__main__':
    unittest.main()
