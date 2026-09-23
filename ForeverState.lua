local ADDON, F = ...
local VERSION, SCHEMA = "0.2.1", 1
local frame = CreateFrame("Frame") -- Event receiver only; no visible UI.
F.frame = frame
local db, session, latest, levelFloor, disabled
local loadedSessions, loadedRecords = 0, 0
local pending, requested, eventErrors = {}, {}, {}
local elapsed, settleIn, settleSequence = 0, nil, nil
local clockName = type(GetTimePreciseSec) == "function" and "GetTimePreciseSec" or "GetTime"
local clock = clockName == "GetTimePreciseSec" and GetTimePreciseSec or GetTime

local function secret(value) return issecretvalue and issecretvalue(value) end
local function pack(...) return {n = select("#", ...), ...} end
local function printLine(message)
    if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cffedc669Forever State:|r " .. message) end
end

-- Only public scalar results are retained. No secret is compared, formatted,
-- indexed, or saved; even errors are replaced with a fixed diagnostic string.
local function call(issues, key, fn, ...)
    if type(fn) ~= "function" then issues[key] = "API unavailable"; return nil end
    local values = pack(pcall(fn, ...))
    if not values[1] then issues[key] = "API call failed/restricted"; return nil end
    for i = 2, values.n do
        if secret(values[i]) then issues[key] = "secret value"; return nil end
        local kind = type(values[i])
        if kind ~= "nil" and kind ~= "number" and kind ~= "string" and kind ~= "boolean" then
            issues[key] = "unexpected API result"; return nil
        end
    end
    local result = {n = values.n - 1}
    for i = 2, values.n do result[i - 1] = values[i] end
    return result
end

local function read(issues, key, fn, ...)
    local values = call(issues, key, fn, ...)
    return values and values[1]
end

local function number(value)
    return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge and value or nil
end
local function text(value) return type(value) == "string" and value or nil end
local function equal(a, b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= "table" then return a == b end
    for k, v in pairs(a) do if not equal(v, b[k]) then return false end end
    for k in pairs(b) do if a[k] == nil then return false end end
    return true
end

local function timestamp(issues)
    local before = number(read(issues, "clock.uptime", clock))
    local wall = number(read(issues, "clock.wall", time))
    local after = number(read(issues, "clock.uptime", clock))
    local stamp = {wallEpoch = wall, wallResolutionSeconds = 1,
        uptimeBefore = before, uptimeAfter = after, uptimeClock = clockName,
        serverEpoch = number(read(issues, "clock.server", GetServerTime))}
    if wall then
        stamp.localTime = text(read(issues, "clock.localTime", date, "%Y-%m-%d %H:%M:%S", wall))
        stamp.utcTime = text(read(issues, "clock.utcTime", date, "!%Y-%m-%d %H:%M:%S", wall))
    else issues["clock.wall"] = issues["clock.wall"] or "no numeric wall time" end
    if before and after then stamp.uptime = (before + after) / 2
    else issues["clock.uptime"] = issues["clock.uptime"] or "no numeric session time" end
    return stamp
end

local function weaponType(slot, equipLoc, classID)
    if classID and classID ~= 2 then return "not-weapon" end
    if equipLoc == "INVTYPE_2HWEAPON" then return "2H" end
    if equipLoc == "INVTYPE_WEAPON" or equipLoc == "INVTYPE_WEAPONMAINHAND" or equipLoc == "INVTYPE_WEAPONOFFHAND" then
        return slot == 17 and "OH" or "MH"
    end
    if equipLoc == "INVTYPE_SHIELD" or equipLoc == "INVTYPE_HOLDABLE" then return "not-weapon" end
    if equipLoc == "INVTYPE_RANGED" or equipLoc == "INVTYPE_RANGEDRIGHT" or equipLoc == "INVTYPE_THROWN" then return "ranged" end
    return "unknown"
end

local function equipment(issues)
    local slots = {}
    pending = {}
    for slot = 1, 19 do
        local prefix = "slot." .. slot
        local row = {slot = slot}
        slots[slot] = row
        local values = call(issues, prefix .. ".itemID", GetInventoryItemID, "player", slot)
        if not values then row.state = "unknown"
        elseif values[1] == nil or values[1] == 0 then row.state = "empty"
        elseif number(values[1]) and values[1] > 0 then
            row.state, row.itemID = "equipped", values[1]
            row.itemLink = text(read(issues, prefix .. ".itemLink", GetInventoryItemLink, "player", slot))
            if not row.itemLink then issues[prefix .. ".itemLink"] = issues[prefix .. ".itemLink"] or "item link not ready" end
            if slot >= 16 and slot <= 18 then
                row.hand = slot == 16 and "MH" or slot == 17 and "OH" or "ranged"
                local getter = C_Item and C_Item.GetItemInfoInstant or GetItemInfoInstant
                local info = call(issues, prefix .. ".metadata", getter, row.itemID)
                if info then
                    row.itemType, row.itemSubType = text(info[2]), text(info[3])
                    row.equipLoc, row.classID, row.subClassID = text(info[4]), number(info[6]), number(info[7])
                end
                row.weaponType = weaponType(slot, row.equipLoc, row.classID)
                if not row.equipLoc then issues[prefix .. ".metadata"] = issues[prefix .. ".metadata"] or "item metadata not ready" end
            end
            if not row.itemLink or (slot >= 16 and slot <= 18 and not row.equipLoc) then
                pending[row.itemID] = true
                if not requested[row.itemID] and C_Item and type(C_Item.RequestLoadItemDataByID) == "function" then
                    requested[row.itemID] = true
                    call(issues, prefix .. ".request", C_Item.RequestLoadItemDataByID, row.itemID)
                end
            end
        else row.state = "unknown"; issues[prefix .. ".itemID"] = "invalid item ID" end
    end
    return slots
end

local function numericRead(issues, key, fn, ...)
    local value = number(read(issues, key, fn, ...))
    if not value then issues[key] = issues[key] or "numeric value unavailable" end
    return value
end

local function stats(issues)
    local result = {
        meleeCritPercent = numericRead(issues, "stats.meleeCritPercent", GetCritChance),
        meleeHitModifierPercent = numericRead(issues, "stats.meleeHitModifierPercent", GetHitModifier),
        -- Keep these API results separate: do not assume they are additive.
        meleeHitRatingBonusPercent = numericRead(issues, "stats.meleeHitRatingBonusPercent", GetCombatRatingBonus, 6),
        maxHP = numericRead(issues, "stats.maxHP", UnitHealthMax, "player"),
    }
    local ap = call(issues, "stats.attackPower", UnitAttackPower, "player")
    if ap and number(ap[1]) and number(ap[2]) and number(ap[3]) then
        result.attackPower = {base = ap[1], positive = ap[2], negative = ap[3], total = ap[1] + ap[2] + ap[3]}
    else issues["stats.attackPower"] = issues["stats.attackPower"] or "attack power components unavailable" end
    local armor = call(issues, "stats.armor", UnitArmor, "player")
    if armor then
        result.armor = {base = number(armor[1]), effective = number(armor[2]), real = number(armor[3]), bonus = number(armor[4])}
        if not result.armor.effective then issues["stats.armor"] = "effective armor unavailable" end
    end
    -- Character-sheet reference: an attacker at the player's effective level.
    -- Use the client's formula and retain full precision, not tooltip text.
    local mitigationKey = "stats.armor.mitigationPercent"
    local effectiveArmor = result.armor and result.armor.effective
    if effectiveArmor and effectiveArmor >= 0 then
        local referenceLevel = numericRead(issues, "stats.armor.mitigationAttackerLevel", UnitEffectiveLevel, "player")
        if referenceLevel and referenceLevel > 0 and referenceLevel == math.floor(referenceLevel) then
            result.armor.mitigationAttackerLevel = referenceLevel
            result.armor.mitigationLevelSource = "UnitEffectiveLevel"
            local fraction = numericRead(issues, mitigationKey,
                C_PaperDollInfo and C_PaperDollInfo.GetArmorEffectiveness, effectiveArmor, referenceLevel)
            if fraction and fraction >= 0 and fraction <= 1 then
                result.armor.mitigationFraction = fraction
                result.armor.mitigationPercent = fraction * 100
                result.armor.mitigationSource = "C_PaperDollInfo.GetArmorEffectiveness"
            elseif fraction then issues[mitigationKey] = "armor effectiveness outside 0..1" end
        else
            issues[mitigationKey] = "reference attacker level unavailable/invalid"
        end
    else
        issues[mitigationKey] = "effective armor unavailable/invalid"
    end
    return result
end

local function stance(issues)
    local result = {known = false}
    local index = numericRead(issues, "stance.index", GetShapeshiftForm)
    if not index or index < 0 or index ~= math.floor(index) then
        issues["stance.index"] = issues["stance.index"] or "invalid form index"
        return result
    end
    result.index = index
    if index == 0 then result.known = true; return result end -- Explicitly no active form.
    local info = call(issues, "stance.info", GetShapeshiftFormInfo, index)
    if info then
        result.spellID = number(info[4])
        result.active = type(info[2]) == "boolean" and info[2] or false
        result.known = result.active and result.spellID ~= nil and result.spellID > 0
        if result.known then
            result.name = text(read(issues, "stance.name", C_Spell and C_Spell.GetSpellName or GetSpellInfo, result.spellID))
            if not result.name then issues["stance.name"] = issues["stance.name"] or "spell name unavailable" end
        end
    end
    if not result.known then issues["stance.info"] = issues["stance.info"] or "active form identity unavailable" end
    return result
end

local function snapshot(reportedLevel)
    local issues = {}
    local stamp = timestamp(issues) -- Capture receipt time before reading equipment.
    local state = {issues = issues, guid = text(read(issues, "identity.guid", UnitGUID, "player"))}
    if not state.guid then issues["identity.guid"] = issues["identity.guid"] or "GUID unavailable" end
    local level = number(read(issues, "level", UnitLevel, "player"))
    if number(reportedLevel) and reportedLevel > 0 and reportedLevel == math.floor(reportedLevel) then
        levelFloor = math.max(levelFloor or 0, reportedLevel)
        level, state.levelSource, issues.level = levelFloor, "PLAYER_LEVEL_UP", nil
    elseif level and level > 0 and level == math.floor(level) then
        if levelFloor and level < levelFloor then level, state.levelSource = levelFloor, "PLAYER_LEVEL_UP (getter stale)"
        else levelFloor, state.levelSource = level, "UnitLevel" end
    else level = nil; issues.level = issues.level or "level unavailable" end
    state.level = level
    state.equipment = equipment(issues)
    state.stats = stats(issues)
    state.stance = stance(issues)
    local speeds = call(issues, "attackSpeed", UnitAttackSpeed, "player")
    if speeds then
        state.effectiveSpeed = {MH = number(speeds[1]), OH = number(speeds[2]), ranged = number(speeds[3])}
        if not state.effectiveSpeed.MH then issues.attackSpeed = "main-hand speed unavailable" end
    end
    -- Base/tooltip speed is deliberately absent: UnitAttackSpeed is haste-adjusted.
    return stamp, state
end

function F.Capture(reason, details, force, reportedLevel)
    if not session or disabled then return end
    local stamp, state = snapshot(reportedLevel)
    if reason == "PLAYER_LEVEL_UP" and not (number(reportedLevel) and reportedLevel > 0 and reportedLevel == math.floor(reportedLevel)) then
        state.level = nil
        state.issues["event.level"] = "level-up payload unavailable; getter may be stale"
    end
    if not force and latest and equal(latest.state, state) then return latest end
    local record = {sequence = #session.records + 1, reason = reason, time = stamp, state = state, details = details}
    session.records[#session.records + 1] = record
    latest = record
    return record
end

local function initialize()
    if db or disabled then return end
    if ForeverStateDB == nil then ForeverStateDB = {schema = SCHEMA, sessions = {}} end
    if type(ForeverStateDB) ~= "table" or ForeverStateDB.schema ~= SCHEMA or type(ForeverStateDB.sessions) ~= "table" then
        disabled = "unsupported saved-history schema; existing data preserved"
        printLine("BLOCKED: " .. disabled); return
    end
    db = ForeverStateDB
    loadedSessions = #db.sessions
    for _, old in ipairs(db.sessions) do loadedRecords = loadedRecords + #(old.records or {}) end
    F.db = db
end

local function start()
    initialize()
    if disabled or session then return end
    local issues = {}
    local build = call(issues, "build", GetBuildInfo) or {}
    session = {id = #db.sessions + 1, addonVersion = VERSION, records = {},
        clientVersion = text(build[1]), build = text(build[2]), interface = number(build[4]),
        loadedSessions = loadedSessions, loadedRecords = loadedRecords, startupIssues = issues,
        eventRegistrationErrors = eventErrors}
    db.sessions[#db.sessions + 1] = session
    F.session = session
    F.Capture("STARTUP", nil, true)
    settleIn = 0.25
end

local function status()
    if disabled then printLine("BLOCKED: " .. disabled); return end
    if not session then printLine("Waiting for player login."); return end
    local row = F.Capture("STATUS_CHECK", nil, false)
    local state, issues = row.state, row.state.issues
    local coreBlocked = not state.level or not state.guid or not row.time.wallEpoch or not row.time.uptime
    for slot = 1, 19 do if state.equipment[slot].state == "unknown" then coreBlocked = true end end
    for _, required in ipairs({"PLAYER_LOGIN", "PLAYER_LEVEL_UP", "PLAYER_EQUIPMENT_CHANGED", "PLAYER_LOGOUT"}) do
        if eventErrors[required] then coreBlocked = true end
    end
    local partial = next(issues) ~= nil or next(eventErrors) ~= nil
    printLine(VERSION .. " | " .. (coreBlocked and "BLOCKED/PARTIAL" or partial and "RECORDING (partial metadata)" or "RECORDING"))
    printLine("Level " .. tostring(state.level or "unknown") .. " | this login: " .. #session.records .. " snapshots")
    local st, form = state.stats, state.stance
    local function shown(value) return value ~= nil and tostring(value) or "unavailable" end
    printLine("AP " .. shown(st.attackPower and st.attackPower.total) .. " | melee crit " .. shown(st.meleeCritPercent)
        .. "% | armor " .. shown(st.armor and st.armor.effective) .. " | max HP " .. shown(st.maxHP))
    local armor = st.armor
    printLine("Armor mitigation: " .. (armor and armor.mitigationPercent and
        string.format("%.3f%% vs level %d (armor only)", armor.mitigationPercent, armor.mitigationAttackerLevel)
        or "unavailable"))
    printLine("Melee hit modifier " .. shown(st.meleeHitModifierPercent) .. "% | hit rating bonus "
        .. shown(st.meleeHitRatingBonusPercent) .. "% (separate API values)")
    printLine("Stance: " .. (form.known and (form.index == 0 and "none" or
        (form.name or "unnamed") .. " (spell " .. shown(form.spellID) .. ")") or "unavailable"))
    for _, slot in ipairs({16, 17, 18}) do
        local item = state.equipment[slot]
        local hand = slot == 16 and "MH" or slot == 17 and "OH" or "ranged"
        local speed = state.effectiveSpeed and state.effectiveSpeed[hand]
        printLine(hand .. ": " .. (item.itemID and ("item " .. item.itemID .. " / " .. (item.weaponType or "unknown")) or item.state)
            .. " | effective speed " .. (speed and string.format("%.3f s", speed) or "unavailable/not applicable"))
    end
    printLine("Loaded this login: " .. loadedSessions .. " prior sessions, " .. loadedRecords .. " snapshots.")
    printLine("Wall time: " .. (row.time.localTime or "unavailable") .. " (1-second resolution); precise session timer recorded.")
    local keys = {}
    for key in pairs(issues) do keys[#keys + 1] = key end
    table.sort(keys)
    for _, key in ipairs(keys) do printLine(key .. ": " .. issues[key]) end
    for event, problem in pairs(eventErrors) do printLine(event .. ": " .. problem) end
    printLine("Saved on /reload or normal logout. Combat logging must be enabled separately with /combatlog.")
end
F.Status = status

frame:SetScript("OnEvent", function(_, event, a, b)
    if event == "ADDON_LOADED" then
        if not secret(a) and a == ADDON then initialize() end
        return
    end
    if event == "PLAYER_LOGIN" then start(); return end
    if not session or disabled then return end
    -- Event payloads need the same guard as function return values.
    if secret(a) then a = nil end
    if secret(b) then b = nil end
    if event == "PLAYER_LEVEL_UP" then
        F.Capture(event, {reportedLevel = number(a)}, true, number(a))
        settleIn = 0.25
    elseif event == "PLAYER_EQUIPMENT_CHANGED" then
        local hasItem
        if type(b) == "boolean" then hasItem = b end
        local record = F.Capture(event, {slot = number(a), hasItem = hasItem}, true)
        settleIn, settleSequence = 0.25, record.sequence
    elseif event == "UNIT_INVENTORY_CHANGED" or event == "UNIT_ATTACK_SPEED" or event == "UNIT_LEVEL" then
        if a == "player" then F.Capture(event, nil, false) end
    elseif event == "UPDATE_SHAPESHIFT_FORM" or event == "UPDATE_SHAPESHIFT_FORMS" then
        local record = F.Capture(event, nil, true)
        settleIn, settleSequence = 0.25, record.sequence
    elseif event == "UNIT_ATTACK_POWER" or event == "UNIT_STATS" or event == "UNIT_RESISTANCES"
        or event == "UNIT_MAXHEALTH" or event == "UNIT_AURA" or event == "UNIT_DAMAGE" then
        if a == "player" then
            F.Capture(event, nil, false)
            settleIn = 0.25
        end
    elseif event == "COMBAT_RATING_UPDATE" or event == "PLAYER_DAMAGE_DONE_MODS"
        or event == "PLAYER_TALENT_UPDATE" or event == "SPELLS_CHANGED" or event == "PLAYER_LEVEL_CHANGED" then
        F.Capture(event, nil, false)
        settleIn = 0.25
    elseif event == "GET_ITEM_INFO_RECEIVED" or event == "ITEM_DATA_LOAD_RESULT" then
        if number(a) and pending[a] and b == true then
            F.Capture(event, {itemID = a}, false)
        end
    elseif event == "PLAYER_LOGOUT" then
        F.Capture(event, nil, true)
        -- Only this login's session is finalized; earlier sessions are never edited.
        session.closed = true
    elseif event == "PLAYER_ENTERING_WORLD" or event == "PLAYER_LEAVING_WORLD"
        or event == "PLAYER_REGEN_DISABLED" or event == "PLAYER_REGEN_ENABLED" then
        F.Capture(event, nil, true)
        if event == "PLAYER_ENTERING_WORLD" or event == "PLAYER_REGEN_ENABLED" then settleIn = 0.25 end
    end
end)

frame:SetScript("OnUpdate", function(_, delta)
    if not session or disabled then return end
    if settleIn then
        settleIn = settleIn - delta
        if settleIn <= 0 then
            local origin = settleSequence
            settleIn, settleSequence = nil, nil
            F.Capture("DEFERRED_RECHECK", {afterSequence = origin}, false)
        end
    end
    elapsed = elapsed + delta
    if elapsed >= 60 then
        elapsed = 0
        F.Capture("HEARTBEAT", nil, true) -- Sparse recovery/clock checkpoint; no per-frame API polling.
    end
end)

for _, event in ipairs({"ADDON_LOADED", "PLAYER_LOGIN", "PLAYER_LEVEL_UP", "PLAYER_EQUIPMENT_CHANGED",
    "UPDATE_SHAPESHIFT_FORM", "UPDATE_SHAPESHIFT_FORMS", "UNIT_ATTACK_POWER", "UNIT_STATS", "UNIT_RESISTANCES",
    "UNIT_MAXHEALTH", "UNIT_AURA", "UNIT_DAMAGE", "COMBAT_RATING_UPDATE", "PLAYER_DAMAGE_DONE_MODS", "PLAYER_TALENT_UPDATE", "SPELLS_CHANGED", "PLAYER_LEVEL_CHANGED",
    "UNIT_INVENTORY_CHANGED", "UNIT_ATTACK_SPEED", "UNIT_LEVEL", "GET_ITEM_INFO_RECEIVED", "ITEM_DATA_LOAD_RESULT",
    "PLAYER_ENTERING_WORLD", "PLAYER_LEAVING_WORLD", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED", "PLAYER_LOGOUT"}) do
    local ok = pcall(frame.RegisterEvent, frame, event)
    local checked, registered = pcall(frame.IsEventRegistered, frame, event)
    if not ok or not checked or secret(registered) or not registered then
        eventErrors[event] = "event registration unavailable/restricted"
    end
end
F.eventErrors = eventErrors
SLASH_FOREVERSTATE1, SLASH_FOREVERSTATE2 = "/fstate", "/foreverstate"
SlashCmdList.FOREVERSTATE = function(message)
    local command = (message or ""):lower():match("^%s*(%S*)")
    if command == "" or command == "status" then status()
    elseif command == "mark" then
        local label = message:match("^%s*%S+%s+(.+)") or "manual mark"
        F.Capture("MANUAL_MARK", {label = label:sub(1, 200)}, true)
        printLine("Marker recorded.")
    else printLine("/fstate status | /fstate mark LABEL") end
end
