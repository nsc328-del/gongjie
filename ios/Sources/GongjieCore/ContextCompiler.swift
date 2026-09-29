import Foundation

public enum ContextCompiler {
    private static let prompts: [String: String] = {
        let url = Bundle.module.url(forResource: "Prompts", withExtension: "json")!
        return (try? JSONDecoder().decode([String: String].self, from: Data(contentsOf: url))) ?? [:]
    }()
    public static func prompt(_ name: String) -> String { prompts[name] ?? "" }

    public static func projectLocation(_ value: J, locations: [J], idKey: String = "location_id") -> J {
        var result = value
        let place = locations.first { $0["id"] == value[idKey] } ?? [:]
        for (field, source) in [("location_name", "name"), ("location", "name"), ("location_description", "description")] where value[field] != .null {
            result[field] = place[source] == .null ? "" : place[source]
        }
        return result
    }
    public static func locationView(_ place: J) -> J {
        let geo = place["geography"]
        return place.selecting(["id", "name", "description"]).merging([
            "parent_id": geo["parent_id"], "aliases": geo["aliases"] == .null ? [] : geo["aliases"],
            "route_known": geo["route_known"] == .null ? .bool(!geo["generated"].bool) : geo["route_known"]
        ])
    }
    public static func observation(_ world: StoryWorld) -> J {
        var player = world.player
        let names = world.characters.map { $0["name"].string }.filter { !$0.isEmpty }
        if names.contains(where: { player["condition"].string.contains($0) }) { player["condition"] = "" }
        player["visible_traces"] = .strings(player["visible_traces"].strings.filter { text in !names.contains(where: { text.contains($0) }) })
        player["location_name"] = .string(world.locationName(player["location_id"].string))
        return player
    }
    /// One acquired quotation never grants the objective event surrounding it.
    public static func knowledgeProjection(_ item: J) -> J {
        let state = item["knowledge_state"].string.isEmpty ? (item["state"].string.isEmpty ? "heard" : item["state"].string) : item["knowledge_state"].string
        var projected = item.selecting(["id", "title", "details", "world_time", "location_id", "location_name", "visibility", "source", "confidence", "belief"])
        projected["knowledge_state"] = .string(state)
        if ["believed", "misunderstood"].contains(state) {
            let belief = item["belief"].string.isEmpty ? "尚不确定" : item["belief"].string
            projected = projected.selecting(["id", "visibility", "confidence", "knowledge_state"])
            return projected.merging(["title": "所知版本", "details": .string(belief), "belief": .string(belief), "source": "原有认知"])
        }
        let source = item["source"]
        let record: J
        if case .object = source { record = source }
        else if source.string.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("{") { record = (try? J.parse(source.string)) ?? [:] }
        else { return projected } // Authored/public knowledge is already the granted version.
        projected = projected.selecting(["id", "visibility", "confidence", "knowledge_state"])
        if ["user", "assistant"].contains(record["role"].string), ["witnessed", "told"].contains(record["kind"].string), !record["quote"].string.trimmed.isEmpty {
            let speaker = record["role"].string == "user" ? "玩家" : "角色", told = record["kind"].string == "told"
            return projected.merging(["title": .string(told ? "\(speaker)告知的内容（未证实）" : "亲历片段"), "details": record["quote"], "source": .string(told ? "\(speaker)告知" : "亲历")])
        }
        return projected.merging(["title": "已有认知", "details": "", "source": "来源未明"])
    }
    public static func knowledge(_ world: StoryWorld, characterID: String, includePendingPublic: Bool = false) -> [J] {
        let result = world.events.compactMap { event -> J? in
            let acquired = world.knowledge.first { $0["character_id"].string == characterID && $0["event_id"] == event["id"] }
            guard acquired != nil || (includePendingPublic && event["visibility"].string == "public" && !event["synced"].bool) else { return nil }
            var item = event
            if let acquired {
                item = item.merging(acquired.selecting(["state", "source", "belief", "confidence"]))
                // Old room records also need the message's actual observation;
                // possessing a historical knowledge row never grants raw prose.
                var source = item["source"]
                if source.string.trimmed.hasPrefix("{") { source = (try? J.parse(source.string)) ?? [:] }
                if ["user", "assistant"].contains(source["role"].string),
                   let thread = world.threads.first(where: { $0.isRoom && $0.messages.contains(where: { $0.id == event["source_message_id"].string }) }),
                   let index = thread.messages.firstIndex(where: { $0.id == event["source_message_id"].string }) {
                    let message = source["role"].string == "user" ? thread.messages.prefix(index).last(where: { $0.role == "user" }) : thread.messages[index]
                    guard let view = message?.observations?[characterID], !view.isEmpty else { return nil }
                    let quote = source["quote"].string
                    if !view.contains(quote) || quote.isEmpty {
                        guard !["believed", "misunderstood"].contains(item["state"].string) else { return nil }
                        let pieces = view.components(separatedBy: "\n").filter { !$0.trimmed.isEmpty && quote.contains($0) }
                        guard !pieces.isEmpty else { return nil }
                        source["quote"] = .string(pieces.joined(separator: "\n"))
                    }
                    item["source"] = source
                }
            } else { item = item.merging(["state": "heard", "source": "世界脉搏中的公开传播"]) }
            return knowledgeProjection(item)
        }
        return Array(result.suffix(120))
    }
    public static func messageValue(_ message: Message, content: String? = nil) -> J {
        var result: J = ["id": .string(message.id), "role": .string(message.role), "content": .string(content ?? message.content), "status": .string(message.status)]
        if let characterID = message.characterID { result["character_id"] = .string(characterID) }
        if let time = message.recordedTime { result["recorded_time"] = .string(time) }
        if let settlement = message.settlement { result["settlement_outcome"] = settlement["decision"]["outcome"] }
        return result
    }
    public static func context(world: StoryWorld, thread: StoryThread, characterID: String, continuation: Bool = false, currentUserMessageID: String? = nil) -> J {
        let locations = world.knownLocations(characterID: characterID)
        var character = J.object(world.character(characterID).object.filter { $0.key != "art_key" })
        let place = locations.first { $0["id"] == character["current_location_id"] } ?? [:]
        character["location_name"] = place["name"] == .null ? "" : place["name"]
        character["location_description"] = place["description"] == .null ? "" : place["description"]
        var scene = thread.isRoom ? (thread.characterScenes[characterID] ?? [:]) : thread.scene
        scene["world_time"] = world.info["current_time"]
        scene["location_id"] = thread.isRoom ? .string(thread.locationID ?? "") : character["current_location_id"]
        scene["location_name"] = ""; scene = projectLocation(scene, locations: locations)
        var player = projectLocation(observation(world), locations: locations)
        player["commitments"] = .array(player["commitments"].array.filter { $0["character_id"].string == characterID })
        let known = knowledge(world, characterID: characterID)
        var info = world.info; info["id"] = .string(world.id)
        if world.pulse > 0 { info["opening_narration"] = ""; character["opening_hook"] = "" }
        let complete = thread.messages.filter { $0.status == "complete" && !$0.content.isEmpty }
        let userID = currentUserMessageID ?? (thread.isRoom ? complete.last(where: { $0.role == "user" })?.id : (complete.last?.role == "user" ? complete.last?.id : nil))
        let messages = complete.suffix(thread.isRoom ? 40 : 100).compactMap { message -> J? in
            let content = thread.isRoom ? (message.observations?[characterID] ?? "") : message.content
            guard !content.trimmed.isEmpty else { return nil }
            return messageValue(message, content: content).merging(["character_name": .string(world.character(message.characterID ?? "")["name"].string)])
        }
        var context: J = [
            "world": info, "character": character, "scene": scene,
            "previous_scene": projectLocation(thread.isRoom ? (thread.characterScenes[characterID] ?? [:]) : thread.previousScene, locations: locations),
            "messages": .array(messages), "continuation": .bool(continuation), "current_user_message_id": userID.map(J.string) ?? .null,
            "relationships": .array(world.relationships.filter { $0["from_character_id"].string == characterID }),
            "public_events": .array(known.filter { $0["visibility"].string == "public" }),
            "knowledge": .array(known.filter { $0["visibility"].string != "public" }),
            "player": player, "player_profile": world.playerProfile, "locations": .array(locations),
            "characters": .array(world.characters.map { $0.selecting(["id", "name", "age", "role"]) }),
            "character_world_state": projectLocation(world.actions[characterID] ?? [:], locations: locations), "pulse": ["number": .number(Double(world.pulse))]
        ]
        if thread.isRoom {
            let members = world.activeMembers(threadID: thread.id)
            context["room"] = ["id": .string(thread.id), "name": .string(thread.name)]
            context["room_characters"] = .array(world.characters.filter { members.contains($0["id"].string) }.map { $0.selecting(["id", "name", "age", "role"]) })
            let currentUser = complete.first { $0.id == userID }
            context["observation_sources"] = .object(Dictionary(uniqueKeysWithValues: members.map { ($0, ["user": .string(currentUser?.observations?[$0] ?? "")]) }))
        }
        return context
    }
    static func memory(_ context: J) -> J {
        context.selecting(["world", "character", "scene", "relationships", "public_events", "knowledge", "player", "player_profile", "locations", "characters", "character_world_state", "pulse", "room", "room_characters"])
    }
    static func settlementMemory(_ messages: [J]) -> String {
        let rejected = messages.enumerated().filter { $0.element["settlement_outcome"].string == "rejected" }.map { String($0.offset + 1) }
        guard !rejected.isEmpty else { return "" }
        return "\n内部记忆约束（不得输出、复述或作为角色台词）：历史消息序号 [\(rejected.joined(separator: ","))] 的状态变化未确认。历史仍可用于理解交谈，但行动结果、承诺、转场和外界情报以已确认状态为准。这是系统元数据，不是故事正文。\n"
    }
    static func planInstructions(_ context: J) -> String {
        context["scene_plan"] == .null ? "" : "\n【本轮场景方案，仅供内部使用】\n" + context["scene_plan"].text + prompt("sceneInstructions")
    }
    public static func narrative(_ context: J, profile: ProviderProfile, continuation: Bool = false) -> ModelRequest {
        let system = """
        你是\(context["character"]["name"].string)，不是助手。进行连续沉浸的中文角色扮演。
        引号内台词可用“我”；引号外一律以角色名或他/她作第三人称限知叙述，不用括号或星号标动作。
        以下是你能使用的角色记忆与现场，其他人的位置、私密经历与心理没有提供即不可知。
        \(memory(context).text)
        写作纪律：\(prompt("narrative"))
        \(prompt("interaction"))
        \(settlementMemory(context["messages"].array))
        \(planInstructions(context))
        """
        var messages = context["messages"].array.map { $0.selecting(["role", "content"]) }
        if continuation || context["continuation"].bool { messages.append(["role": "user", "content": "[无声导演指令：从刚才停下的瞬间继续，不复述，不换场，不突然收束；让角色沿着尚未完成的欲望和动作推进更长的一段。不要回应这条指令。]"]) }
        return ModelRequest(system: system, messages: messages, maxTokens: profile.maxTokens, temperature: profile.temperature)
    }
    public static func entry(_ context: J, profile: ProviderProfile) -> ModelRequest {
        let isReentry = !context["messages"].array.isEmpty
        var ledger = memory(context)
        let recent = Array(context["messages"].array.suffix(6))
        ledger["historical_dialogue"] = .array(recent)
        ledger["previous_scene"] = context["previous_scene"]
        if isReentry { ledger["scene"] = context["scene"].selecting(["world_time", "location_id", "location_name"]) }
        let system = """
        你要写\(isReentry ? "新时段独立相遇" : "首次相遇")的中文场景，200–450 字，直接输出正文。
        \(ledger.text)
        当前唯一时间是 world.current_time。previous_scene 和 historical_dialogue 只作历史参考，旧的今晚、明天不自动移植到现在。这是新入场请求，不继续回答历史末句，不重演旧动作，不再问已答过的问题。保留人物完整底色和关系发展，无新牵引时允许安静的相遇。
        玩家进入仅表示处于角色可感知范围，不替玩家决定动作、感受或来意，不把点击入场当作兑现承诺。邀请不等于答应，计划不因时间流逝自动完成；不创造新事件、秘密、关键物品或离场经历。只用该角色已知的事实或信念。台词可用我，引号外以角色名或他/她第三人称限知叙述。不用括号或星号标动作。
        \(prompt("interaction"))
        \(settlementMemory(recent))
        """
        return ModelRequest(system: system, messages: [["role": "user", "content": .string("[此刻是\(context["world"]["current_time"].string)，请呈现现在的相遇。]")]], maxTokens: profile.maxTokens, temperature: profile.temperature)
    }
    public static func entryReview(_ context: J, draft: String, profile: ProviderProfile) -> ModelRequest {
        var ledger: J = ["world": context["world"].selecting(["name", "current_time", "atmosphere", "rules"]), "character": context["character"].selecting(["name", "age", "role", "persona", "speech_style", "pronouns", "location_name", "location_description"]), "previous_scene": context["previous_scene"], "relationship_with_player": context["relationships"], "player": context["player"], "player_profile": context["player_profile"], "character_action_state": context["character_world_state"].selecting(["intention", "surface_state", "availability"]), "public_facts_known_by_character": context["public_events"], "private_facts_or_beliefs": context["knowledge"], "historical_dialogue": .array(Array(context["messages"].array.suffix(6)))]
        if context["pulse"]["number"].int == 0 { ledger["initial_situation"] = ["public_opening": context["world"]["opening_narration"], "encounter_hook": context["character"]["opening_hook"]] }
        return structured(prompt("entryReview"), payload: ["memory_ledger": ledger, "draft": .string(draft)], profile: profile, review: true)
    }
    public static func room(_ context: J, profile: ProviderProfile) -> ModelRequest {
        let system = narrative(context, profile: profile).system + """
        你处于多人同场。只对玩家最新发言或此刻现场做出独立反应，不接续、转述、评论其他角色的发言，不替他们说话，也不与他们循环对话。你可以选择沉默。
        只返回一个 JSON：开口时 {"reply":"你的完整叙事回复"}，沉默时 {"silent":true}。不输出其他字段或文字。
        """
        return structured(system, payload: ["房间历史": context["messages"], "本轮玩家输入": .string(turnUserText(context))], profile: profile)
    }
    public static func structured(_ system: String, payload: J, profile: ProviderProfile, review: Bool = false) -> ModelRequest {
        ModelRequest(system: system, messages: [["role": "user", "content": .string(payload.text)]], maxTokens: review ? max(profile.maxTokens, 8192) : profile.maxTokens, temperature: review ? 0.0 : 0.1, thinking: review)
    }
    public static func pulseLocations(world: StoryWorld, characterID: String) -> [J] {
        let current = world.character(characterID)["current_location_id"]
        return world.knownLocations(characterID: characterID).filter { $0["id"] == current || locationView($0)["route_known"].bool }
    }
    public static func pulseInput(world: StoryWorld, characterID: String, target: String) -> J {
        let locations = world.knownLocations(characterID: characterID)
        var c = J.object(world.character(characterID).object.filter { $0.key != "art_key" })
        let known = knowledge(world, characterID: characterID, includePendingPublic: true)
        c["location_name"] = ""; c["location_description"] = ""; c = projectLocation(c, locations: locations, idKey: "current_location_id")
        var player: J = [:]
        if !c["current_location_id"].string.isEmpty && c["current_location_id"] == world.player["location_id"] { player = projectLocation(observation(world), locations: locations) }
        player["commitments"] = .array(world.player["commitments"].array.filter { $0["character_id"].string == characterID })
        let unsynced = Set(world.events.filter { !$0["synced"].bool }.map { $0["id"].string })
        return ["世界": world.info.selecting(["name", "premise", "rules", "current_time"]), "用户希望推进到的时刻": .string(target), "可选地点": .strings(pulseLocations(world: world, characterID: characterID).map { $0["name"].string }), "角色": c.merging(["当前地点": c["location_name"]]), "原有行动": projectLocation(world.actions[characterID] ?? [:], locations: locations), "与玩家关系": world.relationship(characterID), "玩家可被观察的状态": player, "本次新获得或亲历的事实": .array(known.filter { unsynced.contains($0["id"].string) }), "此前认知": .array(known)]
    }
    public static func transitionInput(world: StoryWorld, actions: [String: J], target: String) -> J {
        var player = observation(world); player["commitments"] = .null
        return ["世界": world.info.selecting(["name", "premise", "atmosphere", "rules", "current_time"]), "玩家当前状态": player, "用户希望推进到的时刻": .string(target), "角色在下一时段可被玩家察觉的变化": .array(actions.values.filter { $0["participation"].string == "active" }.map { $0.selecting(["character_name", "surface_state", "location", "encounter_hook"]) }), "刚刚等待结算的公开事实": .array(world.events.filter { !$0["synced"].bool && $0["visibility"].string == "public" }.map { $0.selecting(["id", "title", "details", "world_time", "location_name"]) })]
    }
    public static func cognitionSummary(world: StoryWorld, characterID: String, profile: ProviderProfile) -> ModelRequest {
        let input = pulseInput(world: world, characterID: characterID, target: world.info["current_time"].string)
        return structured(prompt("summary"), payload: ["世界": world.info.selecting(["name", "premise", "current_time"]), "角色": input["角色"].selecting(["name", "role", "persona", "state_summary", "tagline"]), "与玩家的关系": world.relationship(characterID), "本次获得的认知": input["本次新获得或亲历的事实"], "此前认知": input["此前认知"]], profile: profile)
    }
}
