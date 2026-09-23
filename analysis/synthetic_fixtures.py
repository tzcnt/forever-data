"""Generated format-22 fixtures. No captured account data is used here."""
import csv
import io
import json
from datetime import datetime, timezone

GUID = 'Player-0000-SYNTHETIC'
ENEMY = 'Creature-0-0-0-0-999999-SYNTHETIC'
BASE = 978307200  # 2001-01-01 UTC: arbitrary fixture clock.


def lua(value):
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua(k) + '] = ' + lua(v) for k,v in value.items()) + '}'
    if isinstance(value, list):
        return '{' + ','.join(lua(v) for v in value) + '}'
    if isinstance(value, str): return json.dumps(value)
    if isinstance(value, bool): return 'true' if value else 'false'
    if value is None: return 'nil'
    return str(value)


def session(index):
    base = BASE + index * 100
    two = index == 2
    item, kind, speed = (200, '2H', 3) if two else (100, 'MH', 2)
    records = []
    for seq,(offset,reason) in enumerate([(0,'STARTUP'),(2,'PLAYER_ENTERING_WORLD'),
                                         (4,'PLAYER_REGEN_DISABLED'),(30,'HEARTBEAT'),
                                         (40,'PLAYER_REGEN_ENABLED'),(60,'PLAYER_LOGOUT')],1):
        records.append({'sequence':seq,'reason':reason,
                        'time':{'wallEpoch':base+offset,'uptime':offset+.1,
                                'uptimeBefore':offset+.1,'uptimeAfter':offset+.101,
                                'localTime':datetime.fromtimestamp(base+offset,timezone.utc).isoformat()},
                        'state':{'guid':GUID,'level':9,'equipment':{
                            '16':{'state':'equipped','itemID':item,'itemLink':f'|h[Synthetic weapon {item}]|h','weaponType':kind},
                            '17':{'state':'empty'}},'effectiveSpeed':{'MH':speed}}})
    return {'id':1,'build':'69913','closed':True,'loadedSessions':0,'records':records}


def saved(indices):
    return ('ForeverStateDB = ' + lua({'schema':1,'sessions':[session(i) for i in indices]}) + '\n').encode()


def log_line(epoch, fields):
    stamp=datetime.fromtimestamp(epoch,timezone.utc).strftime('%m/%d/%Y %H:%M:%S.%f')[:-3]+'+0'
    out=io.StringIO()
    csv.writer(out,lineterminator='\n',escapechar='\\').writerow(fields)
    return (stamp+'  '+out.getvalue()).encode()


def header():
    return log_line(BASE,['COMBAT_LOG_VERSION','22','ADVANCED_LOG_ENABLED','1',
                          'BUILD_VERSION','1.60.1','PROJECT_ID','18'])


def swing(epoch, kind, rage, damage, own=True, critical=False, hp=250):
    source,dest=(GUID,ENEMY) if own else (ENEMY,GUID)
    info=source if kind=='SWING_DAMAGE' else dest
    f=[kind,source,'Synthetic attacker','0x1','0x0',dest,'Synthetic target','0x1','0x0',
       info,'0',str(hp),'250','20','0','100','0','0','0','1',str(rage),'1050','0','0','0','0','0','0',
       str(damage),str(damage),'-1','1','0','0','0','1' if critical else 'nil','nil','nil']
    return log_line(epoch,f)


def combat(indices):
    result=header()
    for index in indices:
        base=BASE+index*100
        gain=137 if index==2 else 70
        result+=swing(base+10,'SWING_DAMAGE',0,2,own=False)
        result+=swing(base+10,'SWING_DAMAGE_LANDED',0,2,own=False)
        for offset,rage,damage,crit,hp in [(12,gain,10,False,250),(14,2*gain,20,True,250),
                                           (18,3*gain+2,12,False,248),(18.1,4*gain+2,12,False,248)]:
            if offset==18:
                result+=swing(base+16,'SWING_DAMAGE',0,2,own=False)
                result+=swing(base+16,'SWING_DAMAGE_LANDED',2*gain+2,2,own=False,hp=248)
            result+=swing(base+offset,'SWING_DAMAGE',rage,damage,critical=crit,hp=hp)
            result+=swing(base+offset,'SWING_DAMAGE_LANDED',0,damage,critical=crit)
    return result
