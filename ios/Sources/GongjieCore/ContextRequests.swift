import Foundation

extension ContextCompiler {
    public static func turnUserText(_ context: J) -> String {
        guard !context["continuation"].bool else { return "" }
        let messages = context["messages"].array
        if context["current_user_message_id"] != .null {
            return messages.first { $0["id"] == context["current_user_message_id"] && $0["role"].string == "user" }?["content"].string ?? ""
        }
        return messages.last?["role"].string == "user" ? (messages.last?["content"].string ?? "") : ""
    }
    static func history(_ context: J, limit: Int) -> [J] {
        Array(context["messages"].array.filter { $0["id"] != context["current_user_message_id"] || context["continuation"].bool }.suffix(limit))
    }
    public static func sourceCatalog(_ context: J, reply: String) -> J {
        var result: [String: J] = [:], seen: Set<String> = []
        let regex = try! NSRegularExpression(pattern: "[^\\n。！？]+[。！？]?")
        for (role, content) in [("user", turnUserText(context)), ("assistant", reply)] {
            for (index, match) in regex.matches(in: content, range: NSRange(content.startIndex..., in: content)).enumerated() {
                guard let range = Range(match.range, in: content) else { continue }
                let quote = String(content[range]); guard !quote.trimmed.isEmpty else { continue }
                result["\(role)_\(index)"] = ["role": .string(role), "quote": .string(quote)]
                seen.insert(role + "\u{0}" + quote)
            }
        }
        for person in context["observation_sources"].object.keys.sorted() {
            let views = context["observation_sources"][person]
            for role in ["user", "assistant"] {
                let text = role == "user" ? turnUserText(context) : reply
                for quote in views[role].string.components(separatedBy: "\n") where !quote.trimmed.isEmpty && text.contains(quote) {
                    if seen.insert(role + "\u{0}" + quote).inserted {
                        result["observation_\(result.count)"] = ["role": .string(role), "quote": .string(quote)]
                    }
                }
            }
        }
        return .object(result)
    }
    public static func decodeProposal(_ raw: J, context: J, reply: String) throws -> J {
        func require(_ valid: Bool, _ message: String, _ reason: String = "proposal_fields") throws { if !valid { throw PatchError(reason, issues: [message]) } }
        guard case .object = raw else { throw PatchError("proposal_fields", issues: ["提案必须为 JSON 对象"]) }
        try require(Set(raw.object.keys).isSubset(of: ["event", "scene", "relationship", "player"]), "顶层只允许 event、scene、relationship、player；证据写在对应变化内部。")
        let sources = sourceCatalog(context, reply: reply)
        func resolve(_ ref: J) throws -> J {
            try require(Set(ref.object.keys) == ["source_id", "kind"], "证据只包含 source_id 和 kind，不能抄写 role 或 quote。", "source_reference_required")
            let source = sources[ref["source_id"].string]
            try require(!source.object.isEmpty, "请选择本轮来源表中存在的 source_id。", "source_reference_unknown")
            return source.merging(["kind": ref["kind"]])
        }
        var result = raw, evidence: J = [:]
        for section in ["event", "relationship"] where result[section]["evidence"] != .null {
            evidence[section] = try resolve(result[section]["evidence"])
            result[section] = .object(result[section].object.filter { $0.key != "evidence" })
        }
        if case .object = result["event"] {
            try require(result["event"]["world_time"] == .null && !result["event"].object.keys.contains("world_time"), "event 不填写 world_time，事件继承本轮有效时钟。")
            if result["event"]["knowledge_sources"] != .null {
                guard case .object = result["event"]["knowledge_sources"] else { throw PatchError("knowledge_sources_mismatch", issues: ["knowledge_sources 必须为对象"]) }
                var knowledge: J = [:]
                for (name, ref) in result["event"]["knowledge_sources"].object { knowledge[name] = try resolve(ref) }
                result["event"]["knowledge_sources"] = knowledge
            }
        }
        for (section, field) in [("scene", "world_time"), ("player", "commitments")] {
            guard case .object = result[section] else { continue }
            try require(Set(result[section].object.keys).isDisjoint(with: ["location", "location_id"]), "\(section) 不填写地点；转场只由已核对的场景方案生成。")
            if result[section].object.keys.contains(field) {
                let change = result[section][field]
                try require(Set(change.object.keys) == ["value", "source_id", "kind"], "\(section).\(field) 必须包含 value、source_id、kind。")
                evidence[section + "." + field] = try resolve(change.selecting(["source_id", "kind"]))
                result[section][field] = change["value"]
            }
        }
        result["evidence"] = evidence
        return result
    }
    public static func adjudication(_ context: J, reply: String, feedback: String, repairProposal: J? = nil, profile: ProviderProfile) -> ModelRequest {
        var player = context["player"]; player["commitments"] = .strings(player["commitments"].array.map { $0["text"].string })
        let people = context["room_characters"] == .null ? [context["character"]] : context["room_characters"].array
        let sources = sourceCatalog(context, reply: reply)
        var payload: J = ["角色": context["character"]["name"], "本轮可登记角色": .strings(people.map { $0["name"].string }), "当前世界时间": context["world"]["current_time"], "现有地点": .strings(context["locations"].array.map { $0["name"].string }), "当前场景": context["scene"], "玩家当前状态": player, "当前关系": context["relationships"], "本轮已核对的场景方案": context["scene_plan"], "背景旧对话": .array(history(context, limit: 4)), "本轮来源表": sources]
        if context["room"] != .null {
            var allowed: J = [:]
            for person in people {
                let visible = context["observation_sources"][person["id"].string]
                allowed[person["name"].string] = .strings(sources.object.keys.sorted().filter { id in
                    let source = sources[id]
                    return visible[source["role"].string].string.contains(source["quote"].string)
                })
            }
            payload["各人在这轮实际可见的来源编号"] = allowed
        }
        if context["location_mentions"] != .null { payload["本轮已识别地点名称及告知原文（待提交，不证明路线或权限）"] = context["location_mentions"] }
        var feedback = feedback
        // Review sees normalized domain evidence; repair must only see the model wire format.
        for (path, wire) in [("event", "event.evidence"), ("relationship", "relationship.evidence"), ("scene.world_time", "scene.world_time 的 source_id/kind"), ("player.commitments", "player.commitments 的 source_id/kind")] {
            for original in ["evidence[\"\(path)\"]", "evidence['\(path)']", "evidence.\(path)"] {
                feedback = feedback.replacingOccurrences(of: original, with: wire)
            }
        }
        var system = prompt("adjudication")
        if !feedback.isEmpty { system += "\n上次提案没有通过校验：\(feedback)。请对照契约重新提取，不能省略真实变化或新造证据。" }
        if let repairProposal {
            system += "\n本次修复上一份提案，不是从头提取。逐条处理具体问题，正确且有原文支持的字段原样保留，只修改受影响字段，不做无关重写、不额外创造事件，也不为通过校验删掉真实变化。其余字段仍须检查。返回完整变化对象，引用仍使用本轮 source_id。"
            payload["待修复的原始提案与具体问题"] = ["proposal": repairProposal, "issues": .string(feedback)]
        }
        return structured(system, payload: payload, profile: profile)
    }
    public static func patchReview(_ context: J, reply: String, patch: J, profile: ProviderProfile) -> ModelRequest {
        let plan = context["scene_plan"]
        let admitted: J = plan["resolution"] == .null ? .null : ["purpose": plan["purpose"], "target_id": plan["target"]["id"], "target_name": plan["target"]["name"], "resolution": plan["resolution"]]
        return structured(prompt("review"), payload: ["previous_dialogue": .array(history(context, limit: 8)), "current_scene": context["scene"], "locations": .array(context["locations"].array.map { $0.selecting(["id", "name"]) }), "player": context["player"], "relationships": context["relationships"], "present": .strings((context["room_characters"] == .null ? [context["character"]] : context["room_characters"].array).map { $0["name"].string }), "user": .string(turnUserText(context)), "assistant": .string(reply), "proposal": patch, "validated_geography": admitted], profile: profile, review: true)
    }
    static func scenePayload(_ context: J) -> J {
        ["world": context["world"].selecting(["name", "premise", "rules", "current_time"]), "character": context["character"].selecting(["name", "role", "persona"]), "scene": context["scene"], "player": context["player"], "known_locations": .array(context["locations"].array.map(locationView)), "world_region_id": context["world"]["id"], "known_facts": .array(Array(context["public_events"].array.suffix(12)) + Array(context["knowledge"].array.suffix(12))), "previous_dialogue": .array(history(context, limit: 8)), "user": .string(turnUserText(context))]
    }
    public static func scenePlanning(_ context: J, feedback: String = "", profile: ProviderProfile) -> ModelRequest {
        var request = structured(prompt("scenePlanning") + (feedback.isEmpty ? "" : "\n上一方案的问题：\(feedback)。只修正冲突；无法确定时保留原现场。"), payload: scenePayload(context), profile: profile)
        request.maxTokens = 1200
        return request
    }
    public static func scenePlanReview(_ context: J, plan: J, profile: ProviderProfile) -> ModelRequest {
        structured(prompt("scenePlanReview"), payload: scenePayload(context).merging(["scene_plan": plan]), profile: profile, review: true)
    }
    public static func sceneReplyReview(_ context: J, reply: String, profile: ProviderProfile) -> ModelRequest {
        var sources: J = [:]
        for (index, text) in reply.components(separatedBy: "\n").enumerated() where !text.trimmed.isEmpty { sources["p\(index)"] = .string(text) }
        return structured(prompt("sceneReplyReview"), payload: scenePayload(context).merging(["scene_plan": context["scene_plan"], "reply_sources": sources]), profile: profile, review: true)
    }
    public static func observationRequest(payload: J, feedback: String = "", profile: ProviderProfile) -> ModelRequest {
        var request = structured(prompt("observation") + (feedback.isEmpty ? "" : "\n上一份观察未通过检查，请重新选择完整观察对象：\(feedback)"), payload: payload, profile: profile)
        request.maxTokens = max(profile.maxTokens, 8192)
        return request
    }
    public static func observationReview(payload: J, profile: ProviderProfile) -> ModelRequest {
        structured(prompt("observationReview"), payload: payload, profile: profile, review: true)
    }
    public static func appointment(world: StoryWorld, profile: ProviderProfile) -> ModelRequest {
        let commitments = world.player["commitments"].array
        let ids = Set(commitments.map { $0["character_id"].string })
        let histories: [J] = world.threads.filter { !$0.isRoom && $0.characterIDs.contains(where: ids.contains) }.map { thread in
            ["character_id": .string(thread.characterIDs.first ?? ""), "history": .array(thread.messages.filter { $0.status == "complete" }.suffix(24).map { message in
                messageValue(message).merging(["decision": message.settlement?["decision"] ?? [:]])
            })]
        }
        return structured(prompt("appointment"), payload: ["world": world.info.selecting(["current_time", "rules"]), "commitments": .array(commitments), "history": .array(histories)], profile: profile, review: true)
    }
}
