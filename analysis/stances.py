"""Reconstruct Warrior stance from public disk-log aura evidence, never casts alone."""
import bisect

STANCE_IDS = {'2457', '71', '2458'}


class StanceTimeline:
    def __init__(self, timeline, events, guid):
        self.timeline, self.sessions = timeline, timeline.sessions
        tokens = []
        for session in self.sessions:
            if session['guid'] != guid:
                continue
            for record in session['records']:
                if record['reason'] in ('STARTUP', 'PLAYER_ENTERING_WORLD', 'PLAYER_LEAVING_WORLD', 'PLAYER_LOGOUT'):
                    # Reset at both ends of uncertain wall-time bounds.
                    for t in record['bounds']:
                        tokens.append((t, 0, None))
        for event in events:
            f = event['fields']
            if f[0] == 'COMBAT_LOG_VERSION' or (len(f) > 5 and f[5] == guid and f[0] in ('UNIT_DIED', 'UNIT_DESTROYED')):
                tokens.append((event['epoch'], 0, event))
            elif len(f) > 12 and f[9] in STANCE_IDS:
                if (f[5] == guid and f[0] in ('SPELL_AURA_APPLIED', 'SPELL_AURA_REFRESH', 'SPELL_AURA_REMOVED')
                        or f[1] == guid and f[0] == 'SPELL_CAST_SUCCESS'):
                    tokens.append((event['epoch'], 1, event))
        tokens.sort(key=lambda x: (x[0], x[1]))
        self.points, self.times, self.changes = [], [], []
        key, evidence, segment = 'unknown', None, 0
        for t, action, event in tokens:
            before = key
            if action == 0:
                key, evidence = 'unknown', None
            else:
                f = event['fields']
                observed = 'spell:' + f[9]
                if f[0] in ('SPELL_AURA_APPLIED', 'SPELL_AURA_REFRESH'):
                    key = observed
                    evidence = {k:event[k] for k in ('id','epoch','timestamp','source','line')}
                    evidence.update(spell_id=int(f[9]), name=f[10], kind=f[0])
                elif f[0] == 'SPELL_AURA_REMOVED':
                    # Removal of the old aura may follow application of the new one.
                    if key == observed:
                        key, evidence = 'unknown', None
                elif key != observed:  # A successful cast is not proof of an active aura.
                    key, evidence = 'unknown', None
            if before != key or action == 0:
                segment += 1
                self.changes.append(t)
            self.times.append(t)
            self.points.append((key, evidence, segment))

    def at(self, guid, epoch):
        context = dict(self.timeline.at(guid, epoch))
        if context.get('issue'):
            return context
        index = bisect.bisect_right(self.times, epoch) - 1
        key, evidence, segment = self.points[index] if index >= 0 else ('unknown', None, 0)
        recorded = context.get('stance_key', 'unknown')
        context.update(recorded_stance_key=recorded, log_stance_key=key,
                       log_stance_evidence=evidence, log_stance_segment=segment)
        if recorded != 'unknown' and key != 'unknown' and recorded != key:
            context.update(stance_key='unknown', stance_basis='conflicting addon/log evidence',
                           research_issue='addon/log stance disagreement')
        elif key != 'unknown':
            context.update(stance_key=key, stance_basis='addon and combat log' if recorded == key else 'combat log aura')
        else:
            context['stance_basis'] = 'addon snapshot' if recorded != 'unknown' else 'unknown'
        lo = bisect.bisect_left(self.changes, epoch - .25)
        if lo < len(self.changes) and self.changes[lo] <= epoch + .25:
            context['research_issue'] = context.get('research_issue') or 'near logged stance/coverage transition (250 ms)'
        return context
