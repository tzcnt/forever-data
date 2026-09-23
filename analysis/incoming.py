"""Incoming melee rage candidates; empirical cadence is not NPC base speed."""
import bisect
from collections import Counter, defaultdict
from statistics import mean

from measure import LAYOUT


def combat_windows(timeline, guid, events):
    windows = []
    for s in timeline.sessions:
        if s['guid'] != guid or s.get('conflict') or s.get('time_order_invalid'):
            continue
        start = None
        for r in s['records']:
            if r['reason'] == 'PLAYER_REGEN_DISABLED':
                start = r
            elif r['reason'] == 'PLAYER_REGEN_ENABLED' and start:
                windows.append({'id': s['key'] + ':' + str(start['sequence']),
                                'start': start['bounds'][1] + 1,
                                'end': r['bounds'][0] - 1,
                                'outer_start': start['bounds'][0] - 1,
                                'outer_end': r['bounds'][1] + 1})
                start = None
            elif r['reason'] in ('PLAYER_LEAVING_WORLD', 'PLAYER_LOGOUT'):
                start = None
    for w in windows:
        # Count individual enemy GUIDs, including outgoing targets and misses.
        enemies = set()
        for e in events:
            if not w['outer_start'] <= e['epoch'] <= w['outer_end']:
                continue
            f = e['fields']
            if len(f) < 9:
                continue
            if f[1] == guid and f[5].startswith(('Creature-', 'Vehicle-', 'Pet-')):
                enemies.add(f[5])
            elif f[5] == guid and f[1].startswith(('Creature-', 'Vehicle-', 'Pet-')):
                enemies.add(f[1])
        w['enemies'] = sorted(enemies)
    return windows


def analyze_incoming(events, guid, timeline, scale=10):
    windows = combat_windows(timeline, guid, events)
    attempts, pending, aura = defaultdict(list), defaultdict(list), {}
    matched, timing = {}, {}
    harmful = []
    for e in events:
        f, k, t = e['fields'], e['fields'][0], e['epoch']
        if k == 'COMBAT_LOG_VERSION':
            pending.clear()
            aura.clear()
            continue
        if len(f) < 9:
            continue
        if k.startswith('SPELL_AURA_') and len(f) > 10 and f[10] == 'Thunder Clap':
            if k in ('SPELL_AURA_APPLIED', 'SPELL_AURA_REFRESH'):
                aura[f[5]] = e['id']
            elif k == 'SPELL_AURA_REMOVED':
                aura.pop(f[5], None)
        if f[5] == guid and k in ('SWING_DAMAGE', 'SWING_MISSED'):
            a = {'event': e, 'thunder_clap': f[1] in aura, 'aura_revision': aura.get(f[1])}
            attempts[f[1]].append(a)
            if k == 'SWING_DAMAGE' and len(f) == 38:
                pending[(f[1], tuple(f[28:]))].append(e)
        if f[5] == guid and k == 'SWING_DAMAGE_LANDED' and len(f) == 38:
            candidates = pending.get((f[1], tuple(f[28:])), [])
            candidates = [x for x in candidates if 0 <= t - x['epoch'] <= .25]
            if len(candidates) == 1:
                matched[e['id']] = candidates[0]
                pending[(f[1], tuple(f[28:]))].remove(candidates[0])
        # Unknown abilities are contaminants, even if their resource effect is unknown.
        if (f[1] == guid and (k == 'SWING_DAMAGE' or k.startswith(('SPELL_', 'RANGE_')))
                or (f[5] == guid and (k in ('SWING_DAMAGE', 'SWING_MISSED', 'ENVIRONMENTAL_DAMAGE') or k.startswith(('SPELL_', 'RANGE_'))))):
            harmful.append(e)
    for enemy, aa in attempts.items():
        for i, a in enumerate(aa):
            info = {'thunder_clap': a['thunder_clap'], 'stable_cadence': False}
            if 0 < i < len(aa) - 1:
                before, after = aa[i-1], aa[i+1]
                dt1 = a['event']['epoch'] - before['event']['epoch']
                dt2 = after['event']['epoch'] - a['event']['epoch']
                same_aura = before['aura_revision'] == a['aura_revision'] == after['aura_revision']
                same_window = any(w['start'] <= before['event']['epoch'] and after['event']['epoch'] <= w['end']
                                  and w['enemies'] == [enemy] for w in windows)
                info.update(previous_interval=dt1, next_interval=dt2,
                            observed_interval=(dt1 + dt2) / 2,
                            stable_cadence=same_window and same_aura and 1 <= min(dt1, dt2)
                            and max(dt1, dt2) <= 4 and abs(dt1-dt2) <= .20)
            timing[a['event']['id']] = info
    harm_times = [e['epoch'] for e in harmful]
    previous, barriers, rows = None, [], []
    for e in events:
        f, k, t = e['fields'], e['fields'][0], e['epoch']
        if k == 'COMBAT_LOG_VERSION':
            previous, barriers = None, []
            continue
        own = f[1] == guid
        snapshot = None
        if k in LAYOUT and len(f) == LAYOUT[k][0]:
            a = LAYOUT[k][1]
            if f[a] == guid and f[a+10] == '1':
                snapshot = {'event_id': e['id'], 'epoch': t, 'raw': int(f[a+11]),
                            'cap': int(f[a+12]), 'hp': int(f[a+2]), 'max_hp': int(f[a+3]),
                            'armor': int(f[a+6]), 'attack_power': int(f[a+4]),
                            'kind': k}
        if k == 'SWING_DAMAGE_LANDED' and own:
            continue
        if k == 'SWING_DAMAGE_LANDED' and f[5] == guid and len(f) == 38:
            pair = matched.get(e['id'])
            context = timeline.at(guid, t)
            reasons = [x['fields'][0] for x in barriers if not pair or x['id'] != pair['id']]
            if context.get('issue'): reasons.append(context['issue'])
            if context.get('research_issue'): reasons.append(context['research_issue'])
            if not pair: reasons.append('no unique incoming damage pair within 250 ms')
            window = [w for w in windows if w['start'] <= t <= w['end']]
            if len(window) != 1 or window[0]['enemies'] != [f[1]]:
                reasons.append('not a complete single-enemy combat window')
            delta, gap = None, None
            damage = int(f[28])
            if previous and snapshot:
                delta, gap = snapshot['raw']-previous['raw'], t-previous['epoch']
                if not .25 < gap <= 5: reasons.append('close or stale baseline')
                if delta < 0: reasons.append('negative net rage')
                if previous['kind'] == 'SPELL_CAST_SUCCESS': reasons.append('pre-spend baseline')
                if previous['cap'] != snapshot['cap'] or min(snapshot['cap'], previous['cap']) <= 0 or any(s['raw'] >= s['cap'] for s in (previous,snapshot)):
                    reasons.append('rage cap reached/changed/unknown')
                if min(previous['max_hp'],snapshot['max_hp']) <= 0 or previous['max_hp'] != snapshot['max_hp'] or previous['hp']-snapshot['hp'] != damage:
                    reasons.append('health change does not isolate this damage')
                bc = timeline.at(guid, previous['epoch'])
                if bc.get('issue') or bc.get('research_issue') or any(bc.get(key) != context.get(key) for key in ('session','level','item_id','stance_key','research_segment','log_stance_segment')):
                    reasons.append('baseline crosses state transition')
                if len(window) == 1 and previous['epoch'] < window[0]['start']:
                    reasons.append('baseline outside established combat')
            else: reasons.append('missing rage baseline/snapshot')
            if damage <= 0 or int(f[30]) >= 0 or any(int(f[i]) != 0 for i in (32,33,34)):
                reasons.append('overkill, nonpositive damage or partial mitigation')
            if pair:
                near = harmful[bisect.bisect_left(harm_times,pair['epoch']-.25):bisect.bisect_right(harm_times,t+.25)]
                if any(x['id'] != pair['id'] for x in near): reasons.append('nearby other combat/ability event (250 ms)')
            cadence = timing.get(pair['id'], {}) if pair else {}
            # Enemy advanced info has health/AP, not a verified level or base attack time.
            enemy_f = pair['fields'] if pair else None
            rows.append({'event_id':e['id'],'timestamp':e['timestamp'],'epoch':t,
                         'source':e['source'],'line':e['line'],'context':context,
                         'enemy_guid':f[1],'enemy':f[2],'enemy_npc_id':f[1].split('-')[-2],
                         'enemy_max_hp':int(enemy_f[12]) if enemy_f else None,
                         'enemy_attack_power':int(enemy_f[13]) if enemy_f else None,
                         'damage':damage,'raw_damage':int(f[29]),'critical':f[35]=='1',
                         'baseline':previous,'snapshot':snapshot,'delta_raw':delta,
                         'rage':delta/scale if delta is not None else None,
                         'gap_seconds':gap,'pair_id':pair['id'] if pair else None,
                         'combat_window':window[0]['id'] if len(window)==1 else None,
                         'cadence':cadence,'accepted':not reasons,
                         'exclusions':sorted(set(reasons))})
            previous, barriers = snapshot, []
        elif k == 'SWING_DAMAGE' and own and snapshot:
            previous, barriers = snapshot, []
        else:
            barriers.append(e)
            if snapshot: previous = snapshot
    return rows


def summarize_incoming(rows):
    groups = defaultdict(list)
    for r in rows:
        if r['accepted']:
            groups[(r['context']['guid'],r['context']['build'],r['context']['level'],r['snapshot']['max_hp'],r['enemy'],
                    r['enemy_npc_id'],r['enemy_max_hp'],r['enemy_attack_power'],
                    r['context'].get('stance_key','unknown'),
                    str((r['context'].get('stats') or {}).get('armor',{}).get('effective','unknown')),
                    r['critical'],
                    r['cadence'].get('thunder_clap',False),r['cadence'].get('stable_cadence',False))].append(r)
    result = []
    for key, rr in sorted(groups.items()):
        ds, rs = [r['damage'] for r in rr], [r['rage'] for r in rr]
        coefficient = sum(d*r for d,r in zip(ds,rs))/sum(d*d for d in ds)
        constant = mean(rs)
        result.append(dict(zip(('guid','build','level','player_max_hp','enemy','npc_id','enemy_max_hp','enemy_attack_power','stance_key','armor','critical','thunder_clap','stable_cadence'),key),
                           n=len(rr),damage_range=[min(ds),max(ds)],rage_range=[min(rs),max(rs)],
                           rage_per_damage=coefficient,mean_rage=constant,
                           health_normalized_coefficient=coefficient*key[3],
                           damage_rmse=(mean((r-coefficient*d)**2 for d,r in zip(ds,rs)))**.5,
                           constant_rmse=(mean((r-constant)**2 for r in rs))**.5,
                           mean_interval=mean(r['cadence']['observed_interval'] for r in rr) if key[-1] else None,
                           event_ids=[r['event_id'] for r in rr]))
    return result


def model_comparisons(rows):
    results = []
    for guid, build, stance in sorted({(r['context'].get('guid'),r['context'].get('build'),r['context'].get('stance_key','unknown'))
                               for r in rows if r['accepted']}):
        subset = [r for r in rows if (r['context'].get('guid'),r['context'].get('build'),r['context'].get('stance_key','unknown')) == (guid,build,stance)]
        results.extend(dict(m,guid=guid,build=build,stance_key=stance) for m in _model_comparisons_one(subset))
    return results


def _model_comparisons_one(rows):
    """One-parameter fits and leave-one-combat-window-out prediction error."""
    rr = [r for r in rows if r['accepted'] and r['cadence'].get('stable_cadence')]
    models = {'constant per hit': lambda r: 1,
              'damage taken': lambda r: r['damage'],
              'damage taken / player max health': lambda r: r['damage']/r['snapshot']['max_hp'],
              'observed enemy interval': lambda r: r['cadence']['observed_interval'],
              'logged raw damage': lambda r: r['raw_damage']}
    result = []
    for name, fn in models.items():
        for by_level in (False, True):
            coefficients, errors, held_out = {}, [], []
            for level in sorted({r['context']['level'] if by_level else 0 for r in rr}):
                q = [r for r in rr if (r['context']['level'] if by_level else 0) == level]
                k = sum(fn(r)*r['rage'] for r in q)/sum(fn(r)**2 for r in q)
                coefficients[str(level) if by_level else 'pooled'] = k
                errors.extend((r['rage']-k*fn(r))**2 for r in q)
                for r in q:
                    train = [v for v in q if v['combat_window'] != r['combat_window']]
                    if train:
                        kc = sum(fn(v)*v['rage'] for v in train)/sum(fn(v)**2 for v in train)
                        held_out.append((r['rage']-kc*fn(r))**2)
            if errors:
                result.append({'model':name,'by_level':by_level,'n':len(errors),
                               'coefficients':coefficients,'rmse':mean(errors)**.5,
                               'held_out_n':len(held_out),
                               'held_out_rmse':mean(held_out)**.5 if held_out else None})
    return result


def incoming_report(rows):
    groups = summarize_incoming(rows)
    lines=['# Incoming melee rage research','',
           f"{len(rows)} incoming landed hits; {sum(r['accepted'] for r in rows)} isolated single-enemy candidates; "
           f"{sum(r['accepted'] and r['cadence'].get('stable_cadence',False) for r in rows)} also have regular adjacent swing intervals.",'',
           '## Stance coverage', '',
           '| Stance | All incoming hits | Isolated candidates | Regular cadence candidates |',
           '| --- | ---: | ---: | ---: |']
    for stance in sorted({r['context'].get('stance_key','unknown') for r in rows}):
        rr = [r for r in rows if r['context'].get('stance_key','unknown') == stance]
        lines.append(f"| {stance} | {len(rr)} | {sum(r['accepted'] for r in rr)} | {sum(r['accepted'] and r['cadence'].get('stable_cadence',False) for r in rr)} |")
    lines += ['', 'Battle Stance is spell:2457; Defensive Stance is spell:71. Aura-derived labels retain event ID, source and line in each hit context. Unknown periods are not assumed to be Battle Stance.', '',
           '## Model comparisons (regular cadence subset)', '',
           'Zero-intercept one-coefficient models, either pooled or fit separately per player level. Held-out RMSE predicts each fight using coefficients fitted on the other fights. This is a descriptive comparison, not independent confirmation of a formula selected while exploring this dataset.','',
           '| Character/build | Stance | Model | Separate level coefficients | n | Fitted coefficients | RMSE | Held-out fight RMSE |',
           '| --- | --- | --- | --- | ---: | --- | ---: | ---: |']
    for m in model_comparisons(rows):
        cv=f"{m['held_out_rmse']:.4f}" if m['held_out_rmse'] is not None else '-'
        lines.append(f"| {m['guid']} / {m['build']} | {m['stance_key']} | {m['model']} | {m['by_level']} | {m['n']} | { {k:round(v,5) for k,v in m['coefficients'].items()} } | {m['rmse']:.4f} | {cv} |")
    lines += ['', '## Stratified measurements', '',
           'Fits: rage = k × damage (zero intercept), versus constant rage per hit. K = k × max HP fits rage = K × damage / max HP. RMSE is in rage units. Groups split by character/build/level/max health, stance, recorded armor, NPC type, NPC max health/AP, Thunder Clap state, and cadence eligibility. Enemy health/AP are observed descriptors, not inferred enemy levels. Stances use recorded spell IDs; unknown includes old addon sessions and restricted reads.','',
           '| Character | Level | Player max HP | Stance | Armor | Enemy (HP/AP) | Critical | Thunder Clap | Regular cadence | n | Damage | Rage | k rage/damage | K health-normalized | Damage RMSE | Constant RMSE | Mean interval |',
           '| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for g in groups:
        dt=f"{g['mean_interval']:.3f}" if g['mean_interval'] else '-'
        lines.append(f"| {g['guid']} | {g['level']} | {g['player_max_hp']} | {g['stance_key']} | {g['armor']} | {g['enemy']} ({g['enemy_max_hp']}/{g['enemy_attack_power']}) | {g['critical']} | {g['thunder_clap']} | {g['stable_cadence']} | {g['n']} | {g['damage_range']} | {g['rage_range']} | {g['rage_per_damage']:.5f} | {g['health_normalized_coefficient']:.5f} | {g['damage_rmse']:.4f} | {g['constant_rmse']:.4f} | {dt} |")
    lines += ['', '## Limits and selection', '',
              '- This measures damage actually taken after mitigation. Both logged damage fields, health, raw rage and event provenance are preserved in incoming.json.',
              '- Only complete addon combat windows with one observed enemy GUID qualify; all nearby abilities/other attacks, unexplained health changes, spending, caps, missing metadata and transition boundaries are excluded. Zero deltas remain eligible; avoided swings are used for timing, not assumed to grant zero rage.',
              '- A regular sample needs both adjacent enemy attack attempts inside that same single-enemy combat window; intervals 1–4 seconds, differing by at most 0.20 seconds. Thunder Clap application/refresh/removal across those attempts excludes regular status. Cadence may still be affected by movement or unrecorded mechanics.',
              '- Observed intervals are NOT verified NPC base attack times. Thunder Clap is split by logged aura state; no assumed percentage correction is silently applied.',
              '- Raw rage uses the same configurable divisor as outgoing analysis. Quantization can move a snapshot delta by roughly 0.1 rage. Small integer gains need many samples.',
              '- Critical and noncritical incoming hits have separate measurement groups. Compare rage per actual damage, controlling for stance/level/gear/enemy, to test for a crit bonus beyond the extra damage. Sitting itself is not identified in these disk rows. Player armor and attack power from advanced combat snapshots are retained separately from addon stat reads.',
              '- Addon 0.2 records stats and stance. Earlier stance history can also be reconstructed forward from player aura applications/refreshes in the disk log. Casts alone do not establish an active aura. Log headers, saved session/world boundaries, death and removal reset the relevant evidence. Unknown initial periods are not backfilled. Undetectable gaps in logging remain a limitation.',
              '- Nearby stat/stance/gear transitions and baselines crossing them are excluded. Addon/log disagreement is excluded. Snapshot fields remain unchanged; disk-log stance evidence and provenance are separate. No stance damage reduction or rage multiplier is assumed. Compare stance coefficients at matched level, max HP, armor and enemy conditions.',
              '- No verified NPC level field is available. NPC type/health/AP stratification does not establish a level formula. Max player health, stance, buffs and hidden passives can confound a universal coefficient.',
              '- Within-group fit errors are descriptive, not out-of-sample validation. Single observations and constant-damage groups cannot distinguish the models.', '', 'Exclusion counts (a row may have several):', '']
    lines += [f'- {k}: {n}' for k,n in Counter(x for r in rows for x in r['exclusions']).most_common()]
    return '\n'.join(lines)+'\n'
