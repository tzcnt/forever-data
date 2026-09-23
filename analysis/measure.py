"""Conservative net-snapshot attribution for observed advanced format 22.

Independent of addon Lua and third-party Python libraries. Unknown events act
as barriers. Never infer dual-wield hands from damage or split combined rage.
"""
import bisect
import re

LAYOUT = {"SWING_DAMAGE": (38, 9), "SWING_DAMAGE_LANDED": (38, 9),
          "SPELL_CAST_SUCCESS": (31, 12), "SPELL_DAMAGE": (42, 12)}


def equipment(record, slot):
    return record["state"]["equipment"].get(str(slot), {})


def signature(record):
    return (record["state"].get("level"), equipment(record, 16).get("itemID"),
            equipment(record, 16).get("itemLink"), equipment(record, 17).get("itemID"))


def research_signature(record):
    state = record['state']
    return (state.get('level'), state.get('equipment'), state.get('stats'), state.get('stance'))


def stance_key(stance):
    if not stance or not stance.get('known'):
        return 'unknown'
    if stance.get('index') == 0:
        return 'none'
    return 'spell:' + str(stance['spellID']) if stance.get('spellID') else 'unknown'


class Timeline:
    def __init__(self, sessions):
        self.sessions = sessions
        for session in sessions:
            records = session["records"]
            lower = max(r["time"]["wallEpoch"] - r["time"]["uptimeAfter"] for r in records)
            upper = min(r["time"]["wallEpoch"] + 1 - r["time"]["uptimeBefore"] for r in records)
            session["clock_bounds"] = [lower, upper] if lower <= upper else None
            for record in records:
                t = record["time"]
                record["bounds"] = ([t["uptimeBefore"] + lower, t["uptimeAfter"] + upper]
                                    if lower <= upper else [t["wallEpoch"], t["wallEpoch"] + 1])
            session["end_times"] = [r["bounds"][1] for r in records]
            session["time_order_invalid"] = session["end_times"] != sorted(session["end_times"])

    def at(self, guid, epoch):
        matches = [s for s in self.sessions if s["guid"] == guid
                   and s["records"][0]["bounds"][1] <= epoch <= s["records"][-1]["bounds"][0]]
        if len(matches) != 1:
            return {"issue": "no unique saved session (possibly not saved yet)"}
        s = matches[0]
        if s.get("conflict") or s.get("time_order_invalid"):
            return {"issue": "conflicting saved session versions or clock order"}
        records = s["records"]
        index = bisect.bisect_right(s["end_times"], epoch) - 1
        if index < 0:
            return {"issue": "no established state"}
        r = records[index]
        if r["reason"] in ("PLAYER_LEAVING_WORLD", "PLAYER_LOGOUT"):
            return {"issue": "outside world/session"}
        for a, b in zip(records, records[1:]):
            boundary = b["reason"] in ("PLAYER_LEAVING_WORLD", "PLAYER_ENTERING_WORLD", "PLAYER_LOGOUT")
            if (boundary or signature(a) != signature(b)) and b["bounds"][0] - 1 <= epoch <= b["bounds"][1] + 1:
                return {"issue": "near gear/level/world transition"}
        st, mh, oh = r["state"], equipment(r, 16), equipment(r, 17)
        research_issue, research_segment = None, records[0]['sequence']
        for a, b in zip(records, records[1:]):
            changed = (research_signature(a) != research_signature(b)
                       or b['reason'] in ('UPDATE_SHAPESHIFT_FORM', 'UPDATE_SHAPESHIFT_FORMS', 'PLAYER_EQUIPMENT_CHANGED'))
            if changed:
                if b['bounds'][1] <= epoch:
                    research_segment = b['sequence']
                if b['bounds'][0] - 1 <= epoch <= b['bounds'][1] + 1:
                    research_issue = 'near stats/stance/equipment transition'
        form = st.get('stance')
        form_key = stance_key(form)
        # Do not label a whole fight from a snapshot if the stance listener failed.
        if s.get('header', {}).get('eventRegistrationErrors', {}).get('UPDATE_SHAPESHIFT_FORM'):
            form_key = 'unknown'
        if mh.get("state") != "equipped" or not st.get("level"):
            return {"issue": "level or main hand unavailable"}
        if oh.get("state") == "unknown" or (oh.get("state") == "equipped" and oh.get("weaponType") != "not-weapon"):
            return {"issue": "off-hand unknown or dual-wield hand ambiguous"}
        if mh.get("weaponType") not in ("MH", "2H"):
            return {"issue": "unsupported weapon classification"}
        # Reference speeds only within a continuous gear/level segment. A loading
        # boundary, swap, or level transition prevents pooling unrelated reads.
        left, right = index, index + 1
        while left > 0 and signature(records[left - 1]) == signature(r) and records[left]["reason"] != "PLAYER_ENTERING_WORLD":
            left -= 1
        while right < len(records) and signature(records[right]) == signature(r) and records[right]["reason"] not in ("PLAYER_LEAVING_WORLD", "PLAYER_LOGOUT"):
            right += 1
        speeds = sorted({round(x["state"].get("effectiveSpeed", {}).get("MH", 0), 3)
                         for x in records[left:right] if x["state"].get("effectiveSpeed", {}).get("MH", 0) > 0})
        name = re.search(r"\|h\[(.*?)\]\|h", mh.get("itemLink", ""))
        return {"session": s["key"], "sequence": r["sequence"], "level": st["level"], "guid": guid,
                "build": s["build"], "item_id": mh["itemID"], "weapon": name[1] if name else str(mh["itemID"]),
                "type": mh["weaponType"], "hand": "MH", "speed": speeds[0] if len(speeds) == 1 else None,
                "speed_basis": "reference within gear/level segment", "reference_speeds": speeds,
                "speed_at_last_snapshot": st.get("effectiveSpeed", {}).get("MH"),
                "snapshot_speed_issue": st.get("issues", {}).get("attackSpeed"),
                "stats": st.get("stats"), "stance": form, "stance_key": form_key,
                "research_issue": research_issue, "research_segment": research_segment}


def analyze(events, guid, timeline, scale=10, include_close=False):
    previous, barriers, rows = None, [], []
    # A later incoming hit or cast in the same update can also contaminate a
    # delta; inspect both sides of each swing, not just its prior snapshot gap.
    contaminants = sorted(e["epoch"] for e in events if
                          (e["fields"][0] in ("SWING_DAMAGE", "SWING_MISSED") and e["fields"][5] == guid)
                          or (e["fields"][1] == guid and e["fields"][0].startswith(("SPELL_", "RANGE_"))))
    for e in events:
        f, kind, epoch = e["fields"], e["fields"][0], e["epoch"]
        if kind == "COMBAT_LOG_VERSION":
            previous, barriers = None, []
            continue
        own = f[1] == guid
        snapshot = None
        if kind in LAYOUT:
            length, start = LAYOUT[kind]
            if len(f) != length:
                barriers.append("unsupported event layout")
                previous = None
                continue
            if f[start] == guid and f[start + 10] == "1":
                try:
                    snapshot = {"epoch": epoch, "raw": int(f[start + 11]), "cap": int(f[start + 12]),
                                "cost": int(f[start + 13]), "event_id": e["id"]}
                except ValueError:
                    barriers.append("unsupported resource representation")
        if kind == "SWING_DAMAGE_LANDED" and own:
            continue
        if kind == "SWING_MISSED" and own:
            rows.append({"event_id": e["id"], "epoch": epoch, "timestamp": e["timestamp"],
                         "context": timeline.at(guid, epoch), "outcome": f[9] if len(f) > 9 else "unknown miss",
                         "damage": None, "accepted": False, "exclusions": ["avoided swing; no resource snapshot"],
                         "source": e["source"], "line": e["line"]})
        if kind == "SWING_DAMAGE" and own:
            reasons = list(barriers)
            context = timeline.at(guid, epoch)
            if context.get("issue"):
                reasons.append(context["issue"])
            delta, gap = None, None
            if not previous or not snapshot:
                reasons.append("missing player rage baseline/snapshot")
            else:
                delta, gap = snapshot["raw"] - previous["raw"], epoch - previous["epoch"]
                if gap <= 0 or gap > 5:
                    reasons.append("simultaneous or stale baseline")
                if delta <= 0:
                    reasons.append("nonpositive net resource change")
                if snapshot["cap"] <= 0 or snapshot["cap"] != previous["cap"] or snapshot["raw"] >= snapshot["cap"] or previous["raw"] >= previous["cap"]:
                    reasons.append("rage cap reached/changed/unknown")
                before_context = timeline.at(guid, previous["epoch"])
                if before_context.get("issue") or any(before_context.get(k) != context.get(k) for k in ("session", "level", "item_id")):
                    reasons.append("baseline crosses unknown session/gear/level state")
            outcome = "critical" if f[35] == "1" else "normal"
            if f[36] == "1": outcome = "glancing"
            if f[37] == "1": outcome = "crushing"
            if any(f[i] not in ("nil", "1") for i in (35, 36, 37)):
                reasons.append("unknown hit flags")
            if outcome == "crushing": reasons.append("crushing hit")
            damage = int(f[28])
            if damage <= 0 or int(f[30]) >= 0 or any(int(f[i]) != 0 for i in (32, 33, 34)):
                reasons.append("nonpositive damage, overkill, or partial mitigation")
            close = gap is not None and gap <= .25
            nearby = bisect.bisect_right(contaminants, epoch + .25) > bisect.bisect_left(contaminants, epoch - .25)
            if not include_close and (close or nearby): reasons.append("nearby resource/ability/incoming event (250 ms)")
            rows.append({"event_id": e["id"], "epoch": epoch, "timestamp": e["timestamp"], "context": context,
                         "outcome": outcome, "damage": damage, "delta_raw": delta, "baseline": previous,
                         "snapshot": snapshot, "gap_seconds": gap, "close_timing": close or nearby,
                         "accepted": not reasons, "exclusions": sorted(set(reasons)),
                         "rage": delta / scale if delta is not None else None,
                         "source": e["source"], "line": e["line"]})
            previous, barriers = snapshot, []
        elif kind == "SWING_DAMAGE_LANDED" and not own:
            previous, barriers = snapshot, []
        else:
            barriers.append(kind)
            if snapshot:
                previous = snapshot
    return rows
