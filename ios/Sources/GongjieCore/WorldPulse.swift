import Foundation

public enum PulseRules {
    public static func action(_ value: J, world: StoryWorld, characterID: String, input: J) -> J {
        let c = world.character(characterID), previous = world.actions[characterID] ?? [:]
        let places = ContextCompiler.pulseLocations(world: world, characterID: characterID)
        let currentName = world.knownLocations(characterID: characterID).first { $0["id"] == c["current_location_id"] }?["name"].string ?? ""
        let place = places.first { $0["name"] == value["location"] }
        let reason = value["active_reason"].string, source = value["active_source"].string
        let availability = value["availability"].string
        var grounded = false
        switch reason {
        case "new_fact": grounded = input["本次新获得或亲历的事实"].array.contains { $0["id"].string == source }
        case "commitment": grounded = input["玩家可被观察的状态"]["commitments"].array.contains { $0["id"].string == source }
        case "matured_intention": grounded = source == "previous_intention" && !previous["intention"].string.trimmed.isEmpty
        case "visible_change":
            if source == "location" { grounded = place != nil && place!["id"] != c["current_location_id"] }
            if source == "availability" {
                grounded = ["present", "reachable", "incoming", "unavailable"].contains(availability) && availability != (previous["availability"].string.isEmpty ? "reachable" : previous["availability"].string) && (availability != "present" || (place != nil && place!["id"] == world.player["location_id"]))
            }
        default: break
        }
        let proposedName = value["location"].string.trimmed
        let active = grounded && value["participation"].string == "active" && (proposedName.isEmpty || proposedName == currentName || place != nil)
        let location = active ? (place?["id"] ?? c["current_location_id"]) : c["current_location_id"]
        var status = active ? availability : previous["availability"].string
        if !["present", "reachable", "incoming", "unavailable"].contains(status) { status = "reachable" }
        if status == "present" && (location.string.isEmpty || location != world.player["location_id"]) { status = "reachable" }
        return ["character_name": c["name"], "participation": .string(active ? "active" : "quiet"), "active_reason": .string(active ? reason : ""), "active_source": .string(active ? source : ""), "intention": active ? value["intention"] : previous["intention"], "surface_state": active ? (value["surface_state"].string.isEmpty ? (c["state_summary"].string.isEmpty ? c["tagline"] : c["state_summary"]) : value["surface_state"]) : (previous["surface_state"].string.isEmpty ? (c["state_summary"].string.isEmpty ? c["tagline"] : c["state_summary"]) : previous["surface_state"]), "location_id": location, "location": .string(places.first { $0["id"] == location }?["name"].string ?? currentName), "availability": .string(status), "encounter_hook": active ? value["encounter_hook"] : "", "interacted_since_pulse": false]
    }
    public static func appointment(_ value: J, world: StoryWorld) throws -> J {
        let time = value["world_time"].string.trimmed
        guard PatchValidator.validTime(time), time != world.info["current_time"].string, time.count <= 120 else { throw GameError.invalid("未能确定下一段世界时间，请再试一次") }
        let ready = value["ready_commitment_ids"] == .null ? J.array([]) : value["ready_commitment_ids"]
        let times = value["appointment_times"] == .null ? J.object([:]) : value["appointment_times"]
        guard case .array = ready, case .object = times else { throw GameError.invalid("赴约时间规划无效") }
        let valid = Set(world.player["commitments"].array.map { $0["id"].string })
        guard ready.array.allSatisfy({ if case .string(let id) = $0 { return valid.contains(id) }; return false }),
              times.object.allSatisfy({ key, value in if case .string(let time) = value { return valid.contains(key) && time.count <= 120 }; return false }) else { throw GameError.invalid("约定时间预告无效") }
        var seen: Set<String> = []
        return ["world_time": .string(time), "ready_commitment_ids": .strings(ready.strings.filter { seen.insert($0).inserted }), "appointment_times": times]
    }
    public static func transition(_ value: J, world: StoryWorld) throws -> J {
        let observation = ContextCompiler.observation(world), time = value["world_time"].string.trimmed
        guard PatchValidator.validTime(time), time != world.info["current_time"].string else { throw GameError.invalid("这一刻还没有向前，请使用新的故事时刻") }
        guard ["keep", "clear"].contains(value["player_condition_action"].string), case .array(let choices) = value["retained_trace_indices"] else { throw GameError.invalid("玩家状态的保留选择无效") }
        let traces = observation["visible_traces"].array
        var indices: Set<Int> = []
        for v in choices {
            guard case .number(let n) = v, n >= 0, n < Double(traces.count), n.rounded() == n, !indices.contains(Int(n)) else { throw GameError.invalid("玩家痕迹只能选择已有条目，且不能重复") }
            indices.insert(Int(n))
        }
        return ["world_time": .string(time), "narration": value["narration"], "condition": value["player_condition_action"].string == "keep" ? observation["condition"] : "", "visible_traces": .array(traces.enumerated().filter { indices.contains($0.offset) }.map(\.element))]
    }
}

extension StoryEngine {
    public func preview(worldID: String, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws -> J {
        let initial = try await store.world(worldID)
        if let existing = initial.timePreview, existing["state_revision"] == .number(Double(initial.stateRevision)) { return existing }
        try await begin(worldID); defer { busy.remove(worldID) }
        await notify(.phase("pulse"))
        try credentials(profile, key)
        let world = try await store.world(worldID)
        await notify(.stage("正在确定下一时刻"))
        var raw: J?
        for attempt in 0..<2 {
            let reply = try await model.generate(ContextCompiler.appointment(world: world, profile: profile), profile: profile, key: key) { _ in }
            try reply.validateComplete()
            do { raw = try J.parse(reply.text); break } catch { if attempt == 1 { throw error } }
        }
        let clock = try PulseRules.appointment(raw ?? [:], world: world)
        let saved = clock.merging(["id": .string("preview_" + newID()), "state_revision": .number(Double(world.stateRevision))])
        try Task.checkCancellation()
        try await store.transact(worldID, invalidatePreview: false) { current in
            guard current.stateRevision == world.stateRevision, current.pending.isEmpty else { throw GameError.invalid("世界已经变化，请重新确定下一时刻") }
            current.timePreview = saved
        }
        await notify(.changed)
        return saved
    }
    public func pulse(worldID: String, previewID: String, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        try await begin(worldID); defer { busy.remove(worldID) }
        await notify(.phase("pulse"))
        try credentials(profile, key)
        let world = try await store.world(worldID)
        guard let preview = world.timePreview, preview["id"].string == previewID, preview["state_revision"] == .number(Double(world.stateRevision)) else { throw GameError.invalid("时间预告已过期，请重新确定下一时刻") }
        let clock = try PulseRules.appointment(preview, world: world), target = clock["world_time"].string
        guard !world.characters.isEmpty else { throw GameError.invalid("这个世界还没有可以行动的角色") }
        var actions: [String: J] = [:], succeeded = 0
        for character in world.characters {
            try Task.checkCancellation()
            let cid = character["id"].string, input = ContextCompiler.pulseInput(world: world, characterID: cid, target: target)
            await notify(.stage("\(character["name"].string)的生活正在继续"))
            do {
                var action: J?
                for attempt in 0..<2 {
                    let reply = try await model.generate(ContextCompiler.structured(ContextCompiler.prompt("pulse"), payload: input, profile: profile), profile: profile, key: key) { _ in }
                    try reply.validateComplete()
                    do { action = try J.parse(reply.text); break } catch { if attempt == 1 { throw error } }
                }
                actions[cid] = PulseRules.action(action ?? [:], world: world, characterID: cid, input: input); succeeded += 1
            } catch {
                if Task.isCancelled || error is CancellationError { throw CancellationError() }
                actions[cid] = PulseRules.action([:], world: world, characterID: cid, input: input)
                await notify(.warning("\(character["name"].string)暂时保持原状"))
            }
        }
        guard succeeded > 0 else { throw GameError.invalid("这一次世界没有成功向前，请检查模型连接后重试") }
        await notify(.stage("新的时刻正在展开"))
        let input = ContextCompiler.transitionInput(world: world, actions: actions, target: target)
        var raw: J?
        for attempt in 0..<2 {
            let reply = try await model.generate(ContextCompiler.structured(ContextCompiler.prompt("transition"), payload: input, profile: profile), profile: profile, key: key) { _ in }
            try reply.validateComplete()
            do { raw = try J.parse(reply.text); break } catch { if attempt == 1 { throw error } }
        }
        var transition = raw ?? [:]
        transition["world_time"] = clock["world_time"]
        transition = try PulseRules.transition(transition, world: world)
        let reviewInput = input.merging(["本次时刻": transition["world_time"], "待校对的局面": transition["narration"]])
        let review = try await model.generate(ContextCompiler.structured(ContextCompiler.prompt("transitionReview"), payload: reviewInput, profile: profile, review: true), profile: profile, key: key) { _ in }
        try review.validateComplete()
        let narration = try J.parse(review.text)["narration"].string.trimmed
        guard !narration.isEmpty else { throw GameError.invalid("新的局面没有通过校对") }
        transition["narration"] = .string(narration)
        try Task.checkCancellation()
        let committed = transition, committedActions = actions
        try await store.transact(worldID) { w in
            guard w.stateRevision == world.stateRevision, w.timePreview?["id"].string == previewID else { throw GameError.invalid("世界已经变化，请重新推进") }
            w.info["current_time"] = committed["world_time"]; w.narration = narration; w.pulse += 1
            w.player["condition"] = committed["condition"]; w.player["visible_traces"] = committed["visible_traces"]
            let ready = Set(clock["ready_commitment_ids"].strings)
            w.player["commitments"] = .array(w.player["commitments"].array.map { promise in
                promise.merging(["ready_to_visit": .bool(ready.contains(promise["id"].string)), "appointment_time": clock["appointment_times"][promise["id"].string]])
            })
            w.actions = committedActions
            for i in w.characters.indices {
                let cid = w.characters[i]["id"].string
                if var action = committedActions[cid], !action["location_id"].string.isEmpty {
                    w.characters[i]["current_location_id"] = action["location_id"]; w.characters[i]["state_summary"] = action["surface_state"]
                    action["pulse_number"] = .number(Double(w.pulse)); w.actions[cid] = action
                }
            }
            w.clearDepartedPresence()
            for i in w.events.indices where !w.events[i]["synced"].bool {
                let event = w.events[i]
                if event["visibility"].string == "public" {
                    for c in w.characters where !w.knowledge.contains(where: { $0["event_id"] == event["id"] && $0["character_id"] == c["id"] }) {
                        w.knowledge.append(["event_id": event["id"], "character_id": c["id"], "state": "heard", "belief": "", "source": "世界脉搏中的公开传播"])
                    }
                }
                w.events[i]["synced"] = true
            }
        }
        await notify(.changed)
    }
}
