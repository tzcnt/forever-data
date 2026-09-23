-- Runs the real addon with Lua 5.1 API fixtures. No WoW process required.
local passed = 0
local function check(ok, label) assert(ok, label); passed = passed + 1 end
local uptime, epoch, level, speed, offSpeed = 10.125, 1789750000, 9, 1.9, nil
local equipped = {[16] = 100, [17] = 200}
local locations = {[100] = "INVTYPE_WEAPON", [200] = "INVTYPE_SHIELD", [300] = "INVTYPE_2HWEAPON", [400] = "INVTYPE_WEAPONOFFHAND"}
local cached, links, rejected = {}, {}, {}
local secretValue = setmetatable({}, {__tostring = function() error("SECRET STRINGIFIED") end})
issecretvalue = function(value) return rawequal(value, secretValue) end
time = function() return epoch end
date = function(format, value) return format .. ":" .. value end
GetTimePreciseSec = function() return uptime end
GetTime = GetTimePreciseSec
GetServerTime = function() return epoch + 2 end
GetBuildInfo = function() return "1.60.1", "69913", "date", 16001 end
UnitLevel = function() return level end
UnitGUID = function() return "Player-0000-SYNTHETIC" end
UnitAttackSpeed = function() return speed, offSpeed end
local apBase, apPositive, apNegative, crit, hit, hitRating, armor, maxHP = 50, 10, -5, 5.25, 1, 2, 150, 250
local formIndex, formActive, formSpell = 1, true, 2457
UnitAttackPower = function() return apBase, apPositive, apNegative end
GetCritChance = function() return crit end
GetHitModifier = function() return hit end
GetCombatRatingBonus = function(index) assert(index == 6); return hitRating end
UnitArmor = function() return 100, armor, 120, 30 end
local mitigationValue, mitigationCalls, effectiveLevelOverride = .1234567890123, 0, nil
local mitigationArmor, mitigationLevel
UnitEffectiveLevel = function() if effectiveLevelOverride ~= nil then return effectiveLevelOverride end; return level end
C_PaperDollInfo = {GetArmorEffectiveness = function(rawArmor, attackerLevel)
    assert(not issecretvalue(rawArmor) and not issecretvalue(attackerLevel), "secret passed to armor API")
    mitigationCalls = mitigationCalls + 1
    mitigationArmor, mitigationLevel = rawArmor, attackerLevel
    return mitigationValue
end}
UnitHealthMax = function() return maxHP end
GetShapeshiftForm = function() return formIndex end
GetShapeshiftFormInfo = function(index) assert(index == formIndex); return 123, formActive, true, formSpell end
C_Spell = {GetSpellName = function(id) return id == 2457 and "Battle Stance" or "Defensive Stance" end}
GetInventoryItemID = function(_, slot) return equipped[slot] end
GetInventoryItemLink = function(_, slot)
    local id = equipped[slot]
    if issecretvalue(id) then return secretValue end
    if id then return links[id] or ("item:" .. id .. ":0") end
end
C_Item = {
    GetItemInfoInstant = function(id)
        if cached[id] == false then return nil end
        local loc = locations[id]
        return id, loc == "INVTYPE_SHIELD" and "Armor" or "Weapon", "Sword", loc, 123,
            loc == "INVTYPE_SHIELD" and 4 or 2, 7
    end,
    RequestLoadItemDataByID = function(id) cached.requested = id end,
}
local messages = {}
DEFAULT_CHAT_FRAME = {AddMessage = function(_, message) messages[#messages + 1] = message end}
SlashCmdList = {}
local frames = {}
CreateFrame = function(kind)
    check(kind == "Frame", "only invisible event frame created")
    local f = {events = {}, scripts = {}}
    function f:RegisterEvent(event)
        if rejected[event] then error("blocked event") end
        self.events[event] = true
    end
    function f:IsEventRegistered(event) return self.events[event] or false end
    function f:SetScript(name, callback) self.scripts[name] = callback end
    frames[#frames + 1] = f
    return f
end
local function boot()
    local addon = {}
    assert(loadfile("ForeverState.lua"))("ForeverState", addon)
    addon.frame.scripts.OnEvent(addon.frame, "ADDON_LOADED", "ForeverState")
    addon.frame.scripts.OnEvent(addon.frame, "PLAYER_LOGIN")
    return addon
end
local F = boot()
local function event(name, ...) F.frame.scripts.OnEvent(F.frame, name, ...) end
local function update(delta)
    uptime = uptime + delta
    F.frame.scripts.OnUpdate(F.frame, delta)
end
local function last() return F.session.records[#F.session.records] end
local function count() return #F.session.records end
local function statusText()
    messages = {}
    F.Status()
    return table.concat(messages, "\n")
end
local function serialize(value)
    assert(not issecretvalue(value), "secret leaked to SavedVariables")
    if type(value) == "table" then
        local keys, pieces = {}, {"{"}
        for key in pairs(value) do keys[#keys + 1] = key end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        for _, key in ipairs(keys) do pieces[#pieces + 1] = "[" .. serialize(key) .. "]=" .. serialize(value[key]) .. "," end
        pieces[#pieces + 1] = "}"
        return table.concat(pieces)
    end
    if type(value) == "string" then return string.format("%q", value) end
    return tostring(value)
end

check(count() == 1 and last().reason == "STARTUP", "startup captures exactly once")
check(last().state.level == 9 and last().state.guid == "Player-0000-SYNTHETIC", "level and identity")
check(last().time.wallEpoch == epoch and last().time.uptime == uptime, "wall and monotonic timestamps")
check(last().time.wallResolutionSeconds == 1 and last().time.serverEpoch == epoch + 2, "timestamp precision and independent server clock")
check(last().state.equipment[16].weaponType == "MH", "one-handed main hand")
check(last().state.equipment[17].weaponType == "not-weapon", "shield is not off-hand weapon")
check(last().state.equipment[18].state == "empty" and #last().state.equipment == 19, "empty slots explicit, all armor captured")
check(last().state.effectiveSpeed.MH == 1.9 and last().state.baseSpeed == nil, "effective speed is not misrepresented as base speed")
check(last().state.stats.attackPower.total == 55 and last().state.stats.attackPower.negative == -5, "AP components and signed total")
check(last().state.stats.meleeCritPercent == 5.25 and last().state.stats.meleeHitModifierPercent == 1
    and last().state.stats.meleeHitRatingBonusPercent == 2, "crit and distinct hit API values")
check(last().state.stats.armor.effective == 150 and last().state.stats.maxHP == 250, "armor and max HP at startup")
check(last().state.stats.armor.mitigationFraction == mitigationValue
    and last().state.stats.armor.mitigationPercent == mitigationValue * 100, "full precision armor fraction and percent")
check(mitigationArmor == 150 and mitigationLevel == 9 and last().state.stats.armor.mitigationAttackerLevel == 9,
    "effective armor and reference level passed to client calculation")
check(last().state.stance.known and last().state.stance.spellID == 2457 and last().state.stance.name == "Battle Stance", "stance spell identity at startup")
check(#messages == 0, "no startup chat spam or UI")
check(statusText():find("RECORDING", 1, true), "healthy status")
check(count() == 1, "unchanged status is not a duplicate record")
check(statusText():find("12.346% vs level 9 (armor only)", 1, true), "mitigation status has percent units and level basis")
local original = serialize(last())
event("PLAYER_LEVEL_UP", 10) -- UnitLevel deliberately remains stale at 9.
check(last().state.level == 10 and last().state.levelSource == "PLAYER_LEVEL_UP", "authoritative level-up payload")
update(.3)
check(last().state.level == 10, "deferred getter cannot roll back level")
level = 10
event("UNIT_LEVEL", "player")
check(last().state.level == 10 and last().state.levelSource == "UnitLevel", "getter catches up")
check(serialize(F.session.records[1]) == original, "old-level record never modified")

equipped[16], equipped[17] = 300, nil
event("PLAYER_EQUIPMENT_CHANGED", 16, true)
local firstSwap = last()
check(firstSwap.state.equipment[16].weaponType == "2H", "two-handed type recorded")
event("PLAYER_EQUIPMENT_CHANGED", 17, false)
check(last().details.hasItem == false and last().state.equipment[17].state == "empty", "unequip false payload preserved")
equipped[16], equipped[17], offSpeed = 100, 400, 1.5
event("PLAYER_EQUIPMENT_CHANGED", 16, true)
check(last().state.equipment[17].weaponType == "OH" and last().state.effectiveSpeed.OH == 1.5, "offhand weapon and speed")
check(firstSwap.state.equipment[16].itemID == 300, "rapid swap retains intermediate snapshot")
speed = 1.7
event("UNIT_ATTACK_SPEED", "player")
check(last().state.effectiveSpeed.MH == 1.7, "haste/speed changes captured")
local before = count()
event("UNIT_ATTACK_SPEED", "target")
event("UNIT_INVENTORY_CHANGED", "target")
check(count() == before, "other units ignored")
links[100] = "item:100:123"
event("UNIT_INVENTORY_CHANGED", "player")
check(last().state.equipment[16].itemLink == "item:100:123", "same-item enchant link change retained")

cached[100] = false
event("PLAYER_EQUIPMENT_CHANGED", 16, true)
local uncached = last()
check(uncached.state.equipment[16].weaponType == "unknown" and cached.requested == 100, "uncached item requested, unknown type explicit")
cached[100] = true
event("ITEM_DATA_LOAD_RESULT", 100, true)
check(last().state.equipment[16].weaponType == "MH" and uncached.state.equipment[16].weaponType == "unknown", "cache completion appends, does not rewrite history")
before = count()
event("GET_ITEM_INFO_RECEIVED", 9999, true)
check(count() == before, "unrelated item-cache events ignored")

speed = secretValue
event("PLAYER_REGEN_DISABLED")
check(last().state.effectiveSpeed == nil and last().state.issues.attackSpeed == "secret value", "secret speed omitted")
check(statusText():find("RECORDING (partial metadata)", 1, true), "secret speed alone does not imply level/items are blocked")
equipped[16] = secretValue
event("PLAYER_EQUIPMENT_CHANGED", secretValue, secretValue)
check(last().state.equipment[16].state == "unknown" and last().details.slot == nil, "secret equipment/payload never mistaken for unequipped")
check(statusText():find("BLOCKED/PARTIAL", 1, true), "required metadata blocked status")
check(#serialize(F.db) > 0, "no secret anywhere in serialized history")
equipped[16], speed = 100, 1.9
event("PLAYER_REGEN_ENABLED")
check(statusText():find("0.2.1 | RECORDING\n", 1, true), "current status recovers after restrictions lift")
event("PLAYER_LEVEL_UP", secretValue)
check(last().state.level == nil and last().state.issues["event.level"], "unreadable level-up does not silently report stale level")
level = 11
update(.3)
check(last().state.level == 11, "level getter recovery recorded")

-- A gear callback may precede updated stats; the later record must not rewrite it.
event("PLAYER_EQUIPMENT_CHANGED", 1, true)
local beforeStats = last()
armor, maxHP = 180, 300
mitigationValue = .234567890123
update(.3)
check(last().reason == "DEFERRED_RECHECK" and last().state.stats.armor.effective == 180, "deferred gear stats")
check(last().state.stats.armor.mitigationPercent == mitigationValue * 100
    and beforeStats.state.stats.armor.mitigationFraction == .1234567890123, "mitigation follows gear without rewriting history")
check(last().state.stats.maxHP == 300 and beforeStats.state.stats.maxHP == 250, "previous stats immutable")
apPositive = 25
event("UNIT_ATTACK_POWER", "player")
check(last().state.stats.attackPower.total == 70, "AP buff event")
before = count()
event("UNIT_AURA", "player", {})
event("UNIT_MAXHEALTH", "target")
check(count() == before, "unchanged aura and other-unit stats deduplicated")
crit = 7.5
event("COMBAT_RATING_UPDATE")
check(last().state.stats.meleeCritPercent == 7.5, "rating event updates stats")
before = count()
event("UPDATE_SHAPESHIFT_FORM") -- Deliberately stale getter at callback.
check(count() == before + 1 and last().state.stance.spellID == 2457, "stance event retained with stale getter")
local beforeStance = last()
formIndex, formSpell = 2, 71
update(.3)
check(last().reason == "DEFERRED_RECHECK" and last().state.stance.spellID == 71, "deferred stance change")
check(beforeStance.state.stance.spellID == 2457, "previous stance immutable")
check(statusText():find("Defensive Stance (spell 71)", 1, true), "stance shown in status")
formIndex = secretValue
event("UPDATE_SHAPESHIFT_FORM")
check(not last().state.stance.known and last().state.stance.index == nil, "secret stance not mistaken for none or previous stance")
formIndex, formSpell = 1, secretValue
event("UPDATE_SHAPESHIFT_FORM")
check(not last().state.stance.known and last().state.issues['stance.info'] == "secret value", "secret stance detail rejected")
formSpell, formActive = 2457, false
event("UPDATE_SHAPESHIFT_FORM")
check(not last().state.stance.known, "inactive stale form info is unknown")
formActive, formIndex = true, 0
event("UPDATE_SHAPESHIFT_FORM")
check(last().state.stance.known and last().state.stance.index == 0, "explicit no form retained")
formIndex = 1
apNegative, crit, hit, hitRating, armor, maxHP = secretValue, secretValue, secretValue, secretValue, secretValue, secretValue
event("UNIT_AURA", "player")
local private = last().state
check(private.stats.attackPower == nil and private.stats.armor == nil and private.stats.maxHP == nil, "secret components omit derived stats")
check(private.stats.meleeCritPercent == nil and private.stats.meleeHitModifierPercent == nil and private.stats.meleeHitRatingBonusPercent == nil, "secret chances omitted")
check(statusText():find("stats.maxHP: secret value", 1, true), "stat API status diagnostic")
check(#serialize(F.db) > 0, "new stats and stance never serialize secrets")
apNegative, crit, hit, hitRating, armor, maxHP = -5, 5.25, 0, 0, 150, 250
event("PLAYER_REGEN_ENABLED")
check(last().state.stats.meleeHitModifierPercent == 0, "zero hit bonus is a known value")
local oldCrit = GetCritChance
GetCritChance = nil
event("COMBAT_RATING_UPDATE")
check(last().state.issues['stats.meleeCritPercent'] == "API unavailable", "missing stat API diagnosed")
GetCritChance = function() error(secretValue) end
event("COMBAT_RATING_UPDATE")
check(last().state.issues['stats.meleeCritPercent'] == "API call failed/restricted", "stat error is not exposed")
GetCritChance = oldCrit
event("COMBAT_RATING_UPDATE")
update(.3)

-- No legacy armor formula, stale armor, secret operand, or guessed zero.
local beforeMitigation = mitigationCalls
armor = secretValue
event("UNIT_RESISTANCES", "player")
check(mitigationCalls == beforeMitigation and last().state.stats.armor == nil, "secret armor never fed to effectiveness API")
armor = 150
effectiveLevelOverride = secretValue
event("PLAYER_LEVEL_CHANGED")
check(mitigationCalls == beforeMitigation and last().state.stats.armor.mitigationPercent == nil,
    "secret reference level prevents calculation")
effectiveLevelOverride = 7
event("PLAYER_LEVEL_CHANGED")
check(last().state.level == 11 and last().state.stats.armor.mitigationAttackerLevel == 7 and mitigationLevel == 7,
    "effective level is separate from actual character level")
effectiveLevelOverride = nil
mitigationValue = secretValue
event("UNIT_RESISTANCES", "player")
check(last().state.stats.armor.mitigationPercent == nil
    and last().state.issues['stats.armor.mitigationPercent'] == "secret value", "secret effectiveness omitted before arithmetic")
check(#serialize(F.db) > 0, "secret effectiveness never serialized")
for _, invalid in ipairs({-0.1, 1.1, math.huge, 0/0}) do
    mitigationValue = invalid
    event("UNIT_RESISTANCES", "player")
    check(last().state.stats.armor.mitigationPercent == nil and last().state.issues['stats.armor.mitigationPercent'],
        "invalid effectiveness is diagnosed")
end
mitigationValue = nil
event("UNIT_RESISTANCES", "player")
check(last().state.stats.armor.mitigationPercent == nil and last().state.issues['stats.armor.mitigationPercent'], "nil effectiveness is unknown")
local armorAPI = C_PaperDollInfo
C_PaperDollInfo = nil
event("UNIT_RESISTANCES", "player")
check(last().state.issues['stats.armor.mitigationPercent'] == "API unavailable", "missing namespace has no formula fallback")
C_PaperDollInfo = {GetArmorEffectiveness = function() error(secretValue) end}
event("UNIT_RESISTANCES", "player")
check(last().state.issues['stats.armor.mitigationPercent'] == "API call failed/restricted", "armor API error sanitized")
C_PaperDollInfo = armorAPI
armor, mitigationValue = 0, 0
event("UNIT_RESISTANCES", "player")
check(last().state.stats.armor.mitigationPercent == 0 and mitigationArmor == 0, "zero armor mitigation is valid")
armor, mitigationValue = 150, .1234567890123
event("UNIT_RESISTANCES", "player")
update(.3)

before = count()
update(61)
check(count() == before + 1 and last().reason == "HEARTBEAT", "one-minute checkpoint")
SlashCmdList.FOREVERSTATE("mark test label")
check(last().details.label == "test label", "optional manual marker")
epoch = epoch - 3600
event("PLAYER_ENTERING_WORLD")
check(last().time.wallEpoch == epoch and last().time.uptime == uptime, "clock adjustment preserved rather than silently extrapolated")
event("PLAYER_LOGOUT")
check(F.session.closed and last().reason == "PLAYER_LOGOUT", "logout boundary recorded")
local oldSession = serialize(F.session)
local disk = serialize(F.db)
ForeverStateDB = assert(loadstring("return " .. disk))()
uptime = .25
F = boot()
check(#F.db.sessions == 2 and F.session.loadedSessions == 1 and F.session.loadedRecords > 0, "saved-variable reload appends new session")
check(serialize(F.db.sessions[1]) == oldSession, "prior session byte-equivalent after new login")
check(last().time.uptime == .25, "new session may reset monotonic clock")

-- Required event failures are both reported and persisted for offline inspection.
rejected.PLAYER_EQUIPMENT_CHANGED = true
F = boot()
check(F.session.eventRegistrationErrors.PLAYER_EQUIPMENT_CHANGED ~= nil, "failed listener saved")
check(statusText():find("BLOCKED/PARTIAL", 1, true), "failed listener reported")
rejected.PLAYER_EQUIPMENT_CHANGED = nil

-- Missing APIs fail closed without crashing or masquerading as empty gear.
GetInventoryItemID = nil
F = boot()
check(last().state.equipment[16].state == "unknown", "missing inventory API")
UnitLevel = function() error(secretValue) end
event("UNIT_LEVEL", "player")
check(last().state.level == nil and last().state.issues.level == "API call failed/restricted", "secret error object is never formatted")
check(#serialize(F.db) > 0, "failed APIs leave serializable records")

ForeverStateDB = {schema = 999, important = "preserve me"}
local beforeUnknown = serialize(ForeverStateDB)
F = boot()
check(F.session == nil and serialize(ForeverStateDB) == beforeUnknown, "unsupported schema preserved, recording refused")
print("PASS: " .. passed .. " Forever State assertions")
