import Foundation

public enum PatchValidator {
    public static func validate(_ proposal: J, context: J, reply: String) throws -> J {
        func require(_ yes: Bool, _ reason: String) throws { if !yes { throw PatchError(reason) } }
        try require({ if case .object = proposal { return true }; return false }() && Set(proposal.object.keys).isSubset(of: ["event", "scene", "relationship", "player", "evidence"]), "提案顶层字段无效")
        var patch = proposal
        let latest = GeographyRules.turnUserText(context)
        let sources = ["user": latest, "assistant": reply]
        let evidence: J = patch["evidence"] == .null ? [:] : patch["evidence"]
        guard case .object = evidence else { throw PatchError("evidence_invalid") }
        var required: [String: J] = [:]
        func citation(_ e: J, kinds: Set<String>, playerOnly: Bool = false) throws {
            let role = e["role"].string, quote = e["quote"].string
            try require(kinds.contains(e["kind"].string), "证据类型无效：必须区分发生、表达与意图")
            try require(!playerOnly || role == "user", "新增承诺只能引用玩家原文")
            try require(!quote.trimmed.isEmpty && sources[role]?.contains(quote) == true, "证据必须逐字引用本轮原文，不能拼接或引用旧话")
        }
        func proof(_ path: String, kinds: Set<String> = ["occurred", "expressed"], playerOnly: Bool = false) throws {
            try citation(evidence[path], kinds: kinds, playerOnly: playerOnly); required[path] = evidence[path]
        }
        let allowed: [String: Set<String>] = [
            "scene": ["location", "location_id", "world_time", "tension", "active_threads", "physical_state", "emotional_state"],
            "relationship": ["trust_delta", "desire_delta", "tension_delta", "label", "note"],
            "player": ["location", "location_id", "condition", "visible_traces", "commitments"]]
        let lists = ["active_threads": 4, "visible_traces": 8, "commitments": 6]
        let numeric: Set<String> = ["tension", "trust_delta", "desire_delta", "tension_delta"]
        for section in ["scene", "relationship", "player"] {
            if patch[section] == .null { patch[section] = [:] }
            guard case .object(let values) = patch[section] else { throw PatchError("\(section) 必须是对象") }
            try require(Set(values.keys).isSubset(of: allowed[section]!), "\(section) 含有未定义字段")
            for (key, value) in values {
                if let limit = lists[key] {
                    guard case .array(let entries) = value else { throw PatchError("\(key) 必须是数组") }
                    try require(entries.count <= limit && entries.allSatisfy { if case .string = $0 { return true }; return false }, "\(key) 应为最多 \(limit) 条文字")
                } else if !numeric.contains(key) { guard case .string = value else { throw PatchError("\(key) 必须是文字") } }
            }
            if section == "scene" || section == "player" {
                var value = patch[section]
                let id = value["location_id"].string, name = value["location"].string
                let found = context["locations"].array.first { !id.isEmpty ? $0["id"].string == id : $0["name"].string == name }
                if !id.isEmpty || !name.isEmpty {
                    try require(found != nil, "地点不在当前世界中")
                    try require(name.isEmpty || found?["name"].string == name, "地点名称与标识不一致")
                    value["location_id"] = found!["id"]; value["location"] = found!["name"]
                    if found!["id"] != context[section]["location_id"] { try proof("\(section).location", kinds: ["occurred"]) }
                    patch[section] = value
                }
            }
        }
        if !patch["scene"]["world_time"].string.isEmpty && patch["scene"]["world_time"] != context["world"]["current_time"] {
            try require(validTime(patch["scene"]["world_time"].string), "世界时间必须是故事锚点，不能是现实钟点")
            try proof("scene.world_time", kinds: ["occurred"])
        }
        for (section, key, range) in [("scene", "tension", 0...100), ("relationship", "trust_delta", -5...5), ("relationship", "desire_delta", -5...5), ("relationship", "tension_delta", -8...8)] {
            if patch[section][key] != .null {
                guard case .number(let n) = patch[section][key], n.rounded() == n, n >= Double(range.lowerBound), n <= Double(range.upperBound) else { throw PatchError("\(key) 超出允许的整数范围") }
            }
        }
        let relation = context["relationships"].array.first { $0["to_user"].bool } ?? [:], r = patch["relationship"]
        if ["trust_delta", "desire_delta", "tension_delta"].contains(where: { r[$0].int != 0 }) || (!r["label"].string.isEmpty && r["label"] != relation["label"]) || (!r["note"].string.isEmpty && r["note"] != relation["notes"]) { try proof("relationship") }
        if patch["player"]["commitments"] != .null {
            let old = Set(context["player"]["commitments"].array.map { $0["text"].string }), new = Set(patch["player"]["commitments"].strings)
            try require(!new.contains(""), "承诺不能为空")
            if old != new { try proof("player.commitments", playerOnly: !new.subtracting(old).isEmpty) }
        }
        if patch["event"] != .null {
            var event = patch["event"]
            try require(!event["title"].string.trimmed.isEmpty && !event["details"].string.trimmed.isEmpty, "事件缺少标题或事实")
            try require(Set(event.object.keys).isSubset(of: ["title", "details", "visibility", "participants", "witnesses", "beliefs", "world_time", "knowledge_sources"]), "事件含有未知字段")
            try require(["public", "private", "secret"].contains(event["visibility"].string), "事件可见性无效")
            try proof("event", kinds: ["occurred"])
            let people = context["room_characters"] == .null ? [context["character"]] : context["room_characters"].array
            let eligible = Set(people.map { $0["name"].string })
            var names: Set<String> = [context["character"]["name"].string]
            for key in ["participants", "witnesses"] {
                if event[key] == .null { event[key] = [] }
                guard case .array(let entries) = event[key] else { throw PatchError("事件人物须为数组") }
                try require(entries.allSatisfy { if case .string = $0 { return true }; return false }, "事件人物名称无效")
                try require(Set(event[key].strings).isSubset(of: eligible), "不能登记不在场的人物为知情者")
                names.formUnion(event[key].strings)
            }
            try require(Set(event["knowledge_sources"].object.keys) == names, "每名参与者和回复角色必须有对应知情来源")
            for (name, e) in event["knowledge_sources"].object {
                try citation(e, kinds: ["witnessed", "told"])
                if context["room"] != .null {
                    let cid = people.first { $0["name"].string == name }?["id"].string ?? ""
                    let visible = context["observation_sources"][cid][e["role"].string].string
                    try require(!cid.isEmpty && visible.contains(e["quote"].string), "knowledge_source_not_observed")
                }
            }
            if event["beliefs"] == .null { event["beliefs"] = [:] }
            guard case .object(let beliefs) = event["beliefs"] else { throw PatchError("信念须为对象") }
            try require(Set(beliefs.keys).isSubset(of: names) && beliefs.values.allSatisfy { if case .string = $0 { return true }; return false }, "信念的知情人或内容无效")
            let time = patch["scene"]["world_time"].string.isEmpty ? context["world"]["current_time"].string : patch["scene"]["world_time"].string
            try require(event["world_time"].string.isEmpty || event["world_time"].string == time, "event.world_time 应留空或严格等于本轮现场时间：\(time)")
            event["witnesses"] = .strings(names.sorted()); patch["event"] = event
        }
        let baselineScene = context["scene"], player = context["player"]
        func remove(_ key: String, from section: String) { var fields = patch[section].object; fields.removeValue(forKey: key); patch[section] = .object(fields) }
        for section in ["scene", "player"] {
            let current = context[section]
            if !patch[section]["location_id"].string.isEmpty && patch[section]["location_id"] == current["location_id"] { remove("location_id", from: section); remove("location", from: section) }
        }
        if patch["scene"]["world_time"] == context["world"]["current_time"] { remove("world_time", from: "scene") }
        for key in ["tension", "active_threads", "physical_state", "emotional_state"] where patch["scene"][key] != .null && patch["scene"][key] == baselineScene[key] { remove(key, from: "scene") }
        for key in ["trust_delta", "desire_delta", "tension_delta"] where patch["relationship"][key] == 0 { remove(key, from: "relationship") }
        for (key, baseline) in [("label", "label"), ("note", "notes")] where patch["relationship"][key] != .null && patch["relationship"][key] == relation[baseline] { remove(key, from: "relationship") }
        for key in ["condition", "visible_traces"] where patch["player"][key] != .null && patch["player"][key] == player[key] { remove(key, from: "player") }
        if patch["player"]["commitments"] == .strings(player["commitments"].array.map { $0["text"].string }) { remove("commitments", from: "player") }
        let plan = context["scene_plan"]
        if plan != .null {
            let resolution = plan["resolution"]
            if resolution != .null { _ = try GeographyRules.resolution(resolution, plan: plan, reply: reply) }
            for section in ["scene", "player"] {
                let current = context[section]["location_id"], expected = resolution["arrived"].bool ? plan["target"]["id"] : current
                let actual = patch[section]["location_id"] == .null ? current : patch[section]["location_id"]
                try require(actual == expected, "location_outside_plan")
            }
        }
        patch["evidence"] = .object(required)
        return patch
    }
    public static func validTime(_ time: String) -> Bool {
        !time.trimmed.isEmpty && time.range(of: #"^(\d{1,2}:\d{2}(:\d{2})?|\d{4}-\d{2}-\d{2}[T ].*)$"#, options: .regularExpression) == nil
    }
    public static func hasChanges(_ patch: J) -> Bool {
        patch["event"] != .null || ["scene", "relationship", "player"].contains { !patch[$0].object.isEmpty }
    }
    public static func reject(job: SettlementJob, reason: String, issues: [String] = [], to world: inout StoryWorld) throws {
        guard let ti = world.threads.firstIndex(where: { $0.id == job.threadID }), let mi = world.threads[ti].messages.firstIndex(where: { $0.id == job.id }) else { throw GameError.missing }
        if world.threads[ti].messages[mi].settlement != nil { world.pending.removeAll { $0.id == job.id }; return }
        guard world.pending.first?.id == job.id else { throw GameError.pending }
        world.threads[ti].messages[mi].settlement = ["event": .null, "scene": [:], "relationship": [:], "player": [:], "evidence": [:], "decision": ["outcome": "rejected", "reason": .string(reason), "issues": .strings(Array(issues.prefix(12)))]]
        world.pending.removeAll { $0.id == job.id }
    }
    public static func apply(_ patch: J, job: SettlementJob, to world: inout StoryWorld) throws {
        var next = world
        try commit(patch, job: job, to: &next)
        world = next
    }
    private static func commit(_ proposal: J, job: SettlementJob, to world: inout StoryWorld) throws {
        guard let ti = world.threads.firstIndex(where: { $0.id == job.threadID }), let mi = world.threads[ti].messages.firstIndex(where: { $0.id == job.id }), let ci = world.characters.firstIndex(where: { $0["id"].string == job.characterID }) else { throw GameError.missing }
        if world.threads[ti].messages[mi].settlement != nil { world.pending.removeAll { $0.id == job.id }; return }
        guard world.pending.first?.id == job.id else { throw GameError.pending }
        guard world.threads[ti].messages[mi].status == "complete", world.threads[ti].messages[mi].content == job.reply else { throw PatchError("settlement_source_mismatch") }
        let patch = try validate(proposal, context: job.context, reply: job.reply)
        try world.commitGeography(context: job.context, sourceID: job.id, reply: job.reply)
        let isRoom = world.threads[ti].isRoom, changedTime = patch["scene"]["world_time"].string
        let eventTime = changedTime.isEmpty ? job.context["world"]["current_time"].string : changedTime
        if !changedTime.isEmpty { world.info["current_time"] = .string(changedTime) }
        var scene = (isRoom ? world.threads[ti].characterScenes[job.characterID] ?? [:] : world.threads[ti].scene).merging(patch["scene"])
        scene["world_time"] = world.info["current_time"]
        let oldLocation = world.characters[ci]["current_location_id"]
        let newLocation = patch["scene"]["location_id"]
        if !newLocation.string.isEmpty { world.characters[ci]["current_location_id"] = newLocation }
        scene["location_id"] = world.characters[ci]["current_location_id"]
        if isRoom {
            world.threads[ti].characterScenes[job.characterID] = scene
            world.threads[ti].scene["world_time"] = world.info["current_time"]
            if newLocation != .null && newLocation != oldLocation { world.characterRooms.removeValue(forKey: job.characterID) }
        } else { world.threads[ti].scene = scene; world.threads[ti].previousScene = scene }
        if !patch["relationship"].object.isEmpty {
            var relation = world.relationship(job.characterID)
            if relation.object.isEmpty { relation = ["id": .string(newID()), "from_character_id": .string(job.characterID), "to_user": true, "label": "尚未命名", "trust": 0, "desire": 0, "tension": 0] }
            for key in ["trust", "desire", "tension"] { relation[key] = .number(Double(min(100, max(0, relation[key].int + patch["relationship"][key + "_delta"].int)))) }
            if !patch["relationship"]["label"].string.isEmpty { relation["label"] = patch["relationship"]["label"] }
            if !patch["relationship"]["note"].string.isEmpty { relation["notes"] = patch["relationship"]["note"] }
            if let ri = world.relationships.firstIndex(where: { $0["from_character_id"].string == job.characterID && $0["to_user"].bool }) { world.relationships[ri] = relation } else { world.relationships.append(relation) }
        }
        let playerPatch = patch["player"]
        if !playerPatch["location_id"].string.isEmpty {
            world.player["location_id"] = playerPatch["location_id"]
            if isRoom && world.player["location_id"].string != world.threads[ti].locationID {
                world.playerThreadID = world.threads.first { !$0.isRoom && $0.characterIDs == [job.characterID] }?.id
            }
        }
        for key in ["condition", "visible_traces"] where playerPatch[key] != .null { world.player[key] = playerPatch[key] }
        if playerPatch["commitments"] != .null {
            let old = world.player["commitments"].array, owned = old.filter { $0["character_id"].string == job.characterID }
            world.player["commitments"] = .array(old.filter { $0["character_id"].string != job.characterID } + playerPatch["commitments"].strings.map { text in
                owned.first { $0["text"].string == text } ?? ["id": .string(newID()), "character_id": .string(job.characterID), "text": .string(text), "source_message_id": .string(job.id), "world_time": .string(eventTime)]
            })
        }
        if patch["event"] != .null {
            var event = patch["event"]; let eventID = newID()
            let eventLocation = newLocation.string.isEmpty ? job.context["scene"]["location_id"] : newLocation
            event["id"] = .string(eventID); event["source_message_id"] = .string(job.id); event["source_thread_id"] = .string(job.threadID); event["source_character_id"] = .string(job.characterID)
            event["world_time"] = .string(eventTime); event["location_id"] = eventLocation; event["location_name"] = .string(world.locationName(eventLocation.string)); event["synced"] = false
            world.events.append(event)
            for name in event["witnesses"].strings {
                guard let character = world.characters.first(where: { $0["name"].string == name }) else { throw GameError.missing }
                let belief = event["beliefs"][name].string, source = event["knowledge_sources"][name]
                world.knowledge.append(["event_id": .string(eventID), "character_id": character["id"], "state": .string(belief.isEmpty ? (source["kind"] == "told" ? "told" : "witnessed") : "misunderstood"), "belief": .string(belief), "source": source])
            }
        }
        var settlement = patch
        let changed = hasChanges(patch) || job.context["scene_plan"]["resolution"]["confirmed"].bool || !job.context["location_mentions"].array.isEmpty
        settlement["decision"] = ["outcome": changed ? "accepted" : "unchanged", "reason": "", "issues": []]
        world.threads[ti].messages[mi].settlement = settlement
        world.pending.removeAll { $0.id == job.id }
        world.actions[job.characterID] = (world.actions[job.characterID] ?? [:]).merging(["interacted_since_pulse": true])
        world.clearDepartedPresence()
    }
}
