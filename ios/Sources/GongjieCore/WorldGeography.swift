import Foundation
import CryptoKit

public struct PatchError: LocalizedError, Sendable, Equatable {
    public let reason: String
    public let issues: [String]
    public init(_ reason: String, issues: [String] = []) { self.reason = reason; self.issues = issues }
    public var errorDescription: String? { issues.isEmpty ? reason : issues.joined(separator: "；") }
}

public enum GeographyRules {
    public static func key(_ name: String) -> String {
        name.precomposedStringWithCompatibilityMapping.lowercased().filter { !$0.isWhitespace }
    }
    public static func metadata(_ location: J) -> J {
        if case .string(let value) = location["geography"] { return (try? J.parse(value)) ?? [:] }
        return location["geography"].object.isEmpty ? [:] : location["geography"]
    }
    public static func turnUserText(_ context: J) -> String {
        if context["continuation"].bool { return "" }
        let messages = context["messages"].array
        let id = context["current_user_message_id"].string
        if !id.isEmpty { return messages.first { $0["id"].string == id && $0["role"].string == "user" }?["content"].string ?? "" }
        if context["room"] != .null { return messages.last { $0["role"].string == "user" }?["content"].string ?? "" }
        guard let last = messages.last, last["role"].string == "user" else { return "" }
        return last["content"].string
    }
    private static func view(_ location: J) -> J {
        let geo = metadata(location)
        return location.selecting(["id", "name", "description"]).merging([
            "parent_id": geo["parent_id"], "aliases": geo["aliases"] == .null ? [] : geo["aliases"],
            "route_known": geo["route_known"] == .null ? true : geo["route_known"]
        ])
    }
    public static func plan(_ value: J, context: J) throws -> J {
        func require(_ condition: Bool, _ issue: String) throws { if !condition { throw PatchError("scene_plan_invalid", issues: [issue]) } }
        guard case .object = value else { throw PatchError("scene_plan_invalid") }
        try require(Set(value.object.keys).isSubset(of: ["mode", "purpose", "source_quote", "target_id", "new_location"]), "场景方案存在多余字段")
        let mode = value["mode"].string
        try require(["stay", "mention", "resolve"].contains(mode), "mode 只能为 stay、mention、resolve")
        if mode != "resolve" {
            try require(["purpose", "source_quote", "target_id", "new_location"].allSatisfy { value[$0] == .null || value[$0] == "" }, "stay/mention 不提交目的地和动作")
            return ["mode": .string(mode)]
        }
        let purpose = value["purpose"].string, quote = value["source_quote"].string
        try require(["information", "appointment", "travel"].contains(purpose), "缺少目的地用途")
        try require(!quote.trimmed.isEmpty && turnUserText(context).contains(quote), "必须引用本轮玩家询问、约定或出发的原文")
        let id = value["target_id"].string, new = value["new_location"]
        try require(!id.isEmpty != (new != .null), "请选择已有地点或提出一个新地点")
        let locations = context["locations"].array
        var result: J = ["mode": "resolve", "purpose": .string(purpose), "source_quote": .string(quote)]
        if !id.isEmpty {
            guard let target = locations.first(where: { $0["id"].string == id }) else { throw PatchError("scene_plan_invalid", issues: ["目标不在当前可知地点中"]) }
            result["target"] = view(target); return result
        }
        try require(Set(new.object.keys) == ["name", "parent_id", "description"] && new.object.values.allSatisfy { if case .string(let s) = $0 { return !s.trimmed.isEmpty }; return false }, "新地点只允许非空的 name、parent_id、description")
        try require(new["name"].string.count <= 60 && new["description"].string.count <= 300, "地点名称或环境描述过长")
        let parent = new["parent_id"].string, worldID = context["world"]["id"].string
        try require(parent == worldID || locations.contains { $0["id"].string == parent }, "新地点必须归属已有区域或当前世界根区域")
        let normalized = key(new["name"].string)
        let matches = locations.filter { place in ([place["name"].string] + metadata(place)["aliases"].strings).contains { key($0) == normalized } }
        try require(matches.count <= 1, "地点名称有歧义，需选择已有地点 ID")
        if let existing = matches.first { result["target"] = view(existing); return result }
        let digest = SHA256.hash(data: Data("\(worldID):\(parent):\(normalized)".utf8)).map { String(format: "%02x", $0) }.joined()
        result["target"] = ["id": .string("place_" + digest), "name": .string(new["name"].string.trimmed), "parent_id": .string(parent), "description": .string(new["description"].string.trimmed), "aliases": []]
        result["create"] = true
        return result
    }
    public static func resolution(_ value: J, plan: J, reply: String) throws -> J {
        guard Set(value.object.keys) == ["confirmed", "arrived", "quote"], case .bool(let confirmed) = value["confirmed"], case .bool(let arrived) = value["arrived"], case .string(let quote) = value["quote"] else { throw PatchError("location_resolution_invalid") }
        if arrived && (!confirmed || plan["purpose"] != "travel") { throw PatchError("location_arrival_not_authorized") }
        if confirmed && (quote.trimmed.isEmpty || !reply.contains(quote)) { throw PatchError("location_resolution_source") }
        return value
    }
    public static func checkPlan(_ plan: J, world: StoryWorld) throws {
        guard plan["create"].bool else { return }
        let name = key(plan["target"]["name"].string)
        if world.locations.contains(where: { ([ $0["name"].string ] + metadata($0)["aliases"].strings).contains { key($0) == name } }) {
            throw PatchError("location_duplicate", issues: ["该新地点方案无法作为独立地点登记；只能使用当前可知的地点。"])
        }
    }
    public static func locationPatch(_ proposal: J, context: J) -> J {
        var patch = proposal
        let plan = context["scene_plan"], result = plan["resolution"]
        guard result["arrived"].bool else { return patch }
        for section in ["scene", "player"] {
            patch[section]["location_id"] = plan["target"]["id"]; patch[section]["location"] = plan["target"]["name"]
            patch["evidence"][section + ".location"] = ["role": "assistant", "quote": result["quote"], "kind": "occurred"]
        }
        return patch
    }
    public static func mentionedLocations(world: StoryWorld, context: J) -> J {
        let text = turnUserText(context), normalized = key(text), known = Set(context["locations"].array.map { $0["id"].string })
        guard !text.isEmpty else { return context }
        var matches: [String: [String: (J, String)]] = [:]
        for place in world.locations {
            for name in [place["name"].string] + metadata(place)["aliases"].strings where !name.isEmpty && normalized.contains(key(name)) {
                matches[key(name), default: [:]][place["id"].string] = (place, name)
            }
        }
        var result = context, mentions: [J] = [], visible = context["locations"].array, added: Set<String> = []
        for name in matches.keys.sorted() {
            guard let candidates = matches[name], candidates.count == 1, let (place, heard) = candidates.values.first,
                  !matches.keys.contains(where: { $0 != name && $0.contains(name) }), !known.contains(place["id"].string), added.insert(place["id"].string).inserted else { continue }
            mentions.append(["id": place["id"], "name": .string(heard), "character_id": context["character"]["id"], "quote": .string(text), "role": "user"])
            visible.append(["id": place["id"], "world_id": .string(world.id), "name": .string(heard), "description": .string("玩家提及（未因此证实路线或通行权限）：" + text), "mood": "", "geography": ["generated": true, "parent_id": .null, "aliases": [], "route_known": false]])
        }
        result["locations"] = .array(visible); result["location_mentions"] = .array(mentions)
        return result
    }
}

public extension StoryWorld {
    func knownLocations(characterID: String) -> [J] {
        locations.compactMap { place in
            let geo = GeographyRules.metadata(place), generated = geo["generated"].bool
            guard !generated || geo["known_by"].strings.contains(characterID) else { return nil }
            let version = geo["known_versions"][characterID], hasVersion = !version.object.isEmpty, visible = hasVersion ? version : geo
            var result = place.selecting(["id", "name", "description", "mood"])
            result["world_id"] = .string(id)
            if hasVersion { if !version["name"].string.isEmpty { result["name"] = version["name"] }; result["description"] = version["description"]; result["mood"] = "" }
            result["geography"] = ["generated": .bool(generated), "parent_id": visible["parent_id"], "aliases": visible["aliases"] == .null ? [] : visible["aliases"], "route_known": hasVersion ? .bool(version["route_known"].bool) : (geo["route_known"] == .null ? true : geo["route_known"])]
            return result
        }
    }
    mutating func witnessLocation(characterID: String, locationID: String, sourceID: String) {
        guard let index = locations.firstIndex(where: { $0["id"].string == locationID }) else { return }
        var geo = GeographyRules.metadata(locations[index])
        guard geo["generated"].bool else { return }
        if !geo["known_by"].strings.contains(characterID) {
            geo["known_by"] = .strings(geo["known_by"].strings + [characterID])
            geo["known_versions"][characterID] = ["description": "当前所在的地点。", "parent_id": .null, "aliases": [], "route_known": true]
        } else if geo["known_versions"][characterID] != .null { geo["known_versions"][characterID]["route_known"] = true }
        geo["knowledge_sources"][characterID] = ["source_id": .string(sourceID), "kind": "witnessed"]
        locations[index]["geography"] = geo
    }
    mutating func commitGeography(context: J, sourceID: String, reply: String) throws {
        let plan = context["scene_plan"], result = plan["resolution"], characterID = context["character"]["id"].string
        let text = GeographyRules.turnUserText(context)
        for mention in context["location_mentions"].array {
            let quote = mention["quote"].string, name = mention["name"].string
            guard mention["character_id"].string == characterID, mention["role"] == "user", !quote.isEmpty, text.contains(quote), !name.isEmpty,
                  let index = locations.firstIndex(where: { $0["id"] == mention["id"] }) else { throw PatchError("location_knowledge_source") }
            var geo = GeographyRules.metadata(locations[index])
            let names = [locations[index]["name"].string] + geo["aliases"].strings
            guard names.contains(where: { GeographyRules.key($0) == GeographyRules.key(name) }), GeographyRules.key(quote).contains(GeographyRules.key(name)) else { throw PatchError("location_knowledge_name") }
            if !geo["generated"].bool || geo["known_by"].strings.contains(characterID) { continue }
            geo["known_by"] = .strings(geo["known_by"].strings + [characterID])
            geo["known_versions"][characterID] = ["name": .string(name), "description": .string("玩家提及（未因此证实路线或通行权限）：" + quote), "parent_id": .null, "aliases": [], "route_known": false]
            geo["knowledge_sources"][characterID] = ["source_id": .string(sourceID), "kind": "told", "quote": .string(quote)]
            locations[index]["geography"] = geo
        }
        if result["confirmed"].bool {
            _ = try GeographyRules.resolution(result, plan: plan, reply: reply)
            let target = plan["target"], targetID = target["id"].string
            var index = locations.firstIndex { $0["id"].string == targetID }
            if index == nil {
                guard plan["create"].bool, !targetID.isEmpty, !target["name"].string.trimmed.isEmpty,
                      target["parent_id"].string == id || locations.contains(where: { $0["id"] == target["parent_id"] }) else { throw PatchError("location_parent_missing") }
                try GeographyRules.checkPlan(plan, world: self)
                locations.append(target.selecting(["id", "name", "description"]).merging(["mood": "", "geography": ["generated": true, "parent_id": target["parent_id"], "aliases": target["aliases"] == .null ? [] : target["aliases"], "source_id": .string(sourceID), "known_by": []]]))
                index = locations.count - 1
            }
            var geo = GeographyRules.metadata(locations[index!])
            if geo["generated"].bool {
                for id in [characterID, "player"] where !geo["known_by"].strings.contains(id) { geo["known_by"] = .strings(geo["known_by"].strings + [id]) }
                geo["knowledge_sources"]["player"] = ["source_id": .string(sourceID), "kind": result["arrived"].bool ? "witnessed" : "told", "quote": result["quote"]]
                geo["knowledge_sources"][characterID] = ["source_id": .string(sourceID), "kind": result["arrived"].bool ? "witnessed" : "told", "quote": result["quote"]]
                if result["arrived"].bool && geo["known_versions"][characterID] != .null { geo["known_versions"][characterID]["route_known"] = true; geo["known_versions"][characterID]["description"] = result["quote"] }
                locations[index!]["geography"] = geo
            }
        }
    }
}
