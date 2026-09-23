"""Focused synthetic checks for incoming rage attribution and cadence."""
import copy
import unittest

from incoming import analyze_incoming, model_comparisons, summarize_incoming, incoming_report

PLAYER = 'Player-1-TEST'
ENEMY = 'Creature-0-1-0-1-123-ABC'


class Timeline:
    sessions = [{'guid':PLAYER,'key':'session','records':[
        {'reason':'PLAYER_REGEN_DISABLED','bounds':[0,0],'sequence':1},
        {'reason':'PLAYER_REGEN_ENABLED','bounds':[20,20],'sequence':2}]}]

    def at(self, guid, epoch):
        return {'guid':guid,'build':'test','level':10,'item_id':1,'session':'session'}


def swing(t, kind, hp, rage, damage, enemy=ENEMY, own=False):
    f = ['nil']*38
    f[0:9] = [kind,PLAYER if own else enemy,'source','0','0',enemy if own else PLAYER,'target','0','0']
    info = PLAYER if own or kind == 'SWING_DAMAGE_LANDED' else enemy
    f[9:28] = [info,'0',str(hp),'250','30','0','100','0','0','0','1',str(rage),'1050','0','0','0','0','0','0']
    f[28:38] = [str(damage),str(damage),'-1','1','0','0','0','nil','nil','nil']
    return {'id':str(t)+kind+enemy,'epoch':t,'timestamp':str(t),'fields':f,'source':'test','line':1}


def fixture():
    events=[swing(2,'SWING_DAMAGE',250,100,10,own=True)]
    for t,hp,rage,damage in [(4,240,105,10),(6,228,111,12),(8,220,115,8),(10,210,120,10)]:
        events += [swing(t,'SWING_DAMAGE',100,0,damage),swing(t+.01,'SWING_DAMAGE_LANDED',hp,rage,damage)]
    return events


class IncomingTests(unittest.TestCase):
    def test_pairs_are_one_hit_and_delta_is_post_damage(self):
        rows=analyze_incoming(fixture(),PLAYER,Timeline())
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['accepted'] for r in rows))
        self.assertEqual([r['rage'] for r in rows],[.5,.6,.4,.5])
        self.assertEqual([r['cadence']['stable_cadence'] for r in rows],[False,True,True,False])

    def test_ability_spending_does_not_become_incoming_rage(self):
        events=fixture()
        cast=copy.deepcopy(events[0]);cast.update(id='cast',epoch=5)
        cast['fields']=['SPELL_CAST_SUCCESS',PLAYER,'p','0','0',ENEMY,'e','0','0']
        events.append(cast);events.sort(key=lambda e:e['epoch'])
        row=analyze_incoming(events,PLAYER,Timeline())[1]
        self.assertFalse(row['accepted']);self.assertIn('SPELL_CAST_SUCCESS',row['exclusions'])

    def test_second_enemy_excludes_entire_combat(self):
        events=fixture()+[swing(12,'SWING_DAMAGE',100,0,5,enemy='Creature-0-1-0-1-123-DEF')]
        rows=analyze_incoming(events,PLAYER,Timeline())
        self.assertTrue(all(not r['accepted'] for r in rows))

    def test_health_accounting_detects_hidden_damage(self):
        events=fixture();events[4]['fields'][11]='225'
        rows=analyze_incoming(events,PLAYER,Timeline())
        self.assertIn('health change does not isolate this damage',rows[1]['exclusions'])

    def test_irregular_interval_and_thunder_clap_transition(self):
        events=fixture()
        aura={'id':'tc','epoch':5,'timestamp':'5','source':'test','line':1,
              'fields':['SPELL_AURA_APPLIED',PLAYER,'p','0','0',ENEMY,'e','0','0','6343','Thunder Clap','1','DEBUFF']}
        events.append(aura);events.sort(key=lambda e:e['epoch'])
        rows=analyze_incoming(events,PLAYER,Timeline())
        self.assertFalse(rows[1]['cadence']['stable_cadence'])
        self.assertTrue(rows[2]['cadence']['thunder_clap'])
        events=fixture();events[-2]['epoch']=13;events[-1]['epoch']=13.01
        self.assertFalse(analyze_incoming(events,PLAYER,Timeline())[2]['cadence']['stable_cadence'])

    def test_zero_delta_retained_and_health_normalized_fit(self):
        events=fixture();events[2]['fields'][20]='100'
        row=analyze_incoming(events,PLAYER,Timeline())[0]
        self.assertTrue(row['accepted']);self.assertEqual(row['rage'],0)
        rows=analyze_incoming(fixture(),PLAYER,Timeline())
        model=next(m for m in model_comparisons(rows) if m['model']=='damage taken / player max health' and not m['by_level'])
        self.assertAlmostEqual(model['coefficients']['pooled'],12.5)
        self.assertAlmostEqual(model['rmse'],0)
        self.assertIsNone(model['held_out_rmse'])  # Only one fight: no fake validation.

    def test_stance_and_armor_groups_do_not_pool(self):
        rows = analyze_incoming(fixture(),PLAYER,Timeline())
        battle, defensive = copy.deepcopy(rows), copy.deepcopy(rows)
        for rr, stance, factor in [(battle,'spell:2457',1),(defensive,'spell:71',1.1)]:
            for r in rr:
                r['context']['stance_key'] = stance
                r['context']['stats'] = {'armor':{'effective':150}}
                r['rage'] *= factor
        models = [m for m in model_comparisons(battle+defensive+rows)
                  if m['model']=='damage taken / player max health' and not m['by_level']]
        self.assertEqual({m['stance_key'] for m in models},{'spell:2457','spell:71','unknown'})
        coefficients = {m['stance_key']:m['coefficients']['pooled'] for m in models}
        self.assertAlmostEqual(coefficients['spell:71']/coefficients['spell:2457'],1.1)
        groups = summarize_incoming(battle+defensive)
        self.assertEqual(len(groups),4)  # Each stance has regular and irregular cadence.
        self.assertEqual({g['armor'] for g in groups},{'150'})
        self.assertIn('spell:71',incoming_report(battle+defensive))
        defensive[0]['context']['stats']['armor']['effective'] = 200
        self.assertEqual(len(summarize_incoming(battle+defensive)),5)

    def test_incoming_crits_keep_their_damage_scaling_separate(self):
        events = fixture()
        for event in events[3:5]:
            event['fields'][35] = '1'
        rows = analyze_incoming(events,PLAYER,Timeline())
        self.assertTrue(rows[1]['accepted'])
        self.assertTrue(rows[1]['critical'])
        self.assertEqual(rows[1]['snapshot']['armor'],100)
        self.assertEqual(rows[1]['snapshot']['attack_power'],30)
        groups = summarize_incoming(rows)
        self.assertEqual(sum(g['n'] for g in groups if g['critical']),1)
        self.assertEqual(sum(g['n'] for g in groups if not g['critical']),3)
        self.assertAlmostEqual(next(g for g in groups if g['critical'])['rage_per_damage'],.05)

    def test_rage_baseline_cannot_cross_stance_or_stat_transition(self):
        class ChangingTimeline(Timeline):
            def at(self, guid, epoch):
                context = super().at(guid, epoch)
                context['research_segment'] = 1 if epoch < 5 else 2
                context['stance_key'] = 'spell:2457' if epoch < 5 else 'spell:71'
                return context
        rows = analyze_incoming(fixture(),PLAYER,ChangingTimeline())
        self.assertTrue(rows[0]['accepted'])
        self.assertIn('baseline crosses state transition',rows[1]['exclusions'])
        self.assertTrue(rows[2]['accepted'])


if __name__ == '__main__':
    unittest.main()
