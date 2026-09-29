import Foundation

public actor WorldStore {
    private var archive: Archive
    private let url: URL
    public init(url: URL, seed: Bool = true) throws {
        self.url = url
        if FileManager.default.fileExists(atPath: url.path) {
            archive = try JSONDecoder().decode(Archive.self, from: Data(contentsOf: url))
            guard (1...2).contains(archive.version) else { throw GameError.invalid("这个存档来自更新版本，请更新 App") }
        } else {
            archive = Archive()
            if seed { archive.worlds = [StoryWorld(book: try BuiltIn.daylight())] }
        }
        if archive.version == 1 {
            for i in archive.worlds.indices { Self.migrate(&archive.worlds[i]) }
            for i in archive.saves.indices { Self.migrate(&archive.saves[i].world) }
            archive.version = 2
        }
        // A process may have died while receiving a stream. Never replay partial output as fact.
        for w in archive.worlds.indices {
            for t in archive.worlds[w].threads.indices {
                for m in archive.worlds[w].threads[t].messages.indices where archive.worlds[w].threads[t].messages[m].status == "streaming" {
                    archive.worlds[w].threads[t].messages[m].status = "cancelled"
                }
                archive.worlds[w].threads[t].messages.removeAll { $0.status != "complete" && $0.content.isEmpty }
            }
        }
        try Self.write(archive, to: url)
    }
    private static func write(_ archive: Archive, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(archive)
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: url, options: .atomic)
        #endif
    }
    public func snapshot() -> Archive { archive }
    public func world(_ id: String) throws -> StoryWorld { guard let world = archive.worlds.first(where: { $0.id == id }) else { throw GameError.missing }; return world }
    public func transact(_ id: String, invalidatePreview: Bool = true, _ body: @Sendable (inout StoryWorld) throws -> Void) throws {
        var next = archive
        guard let index = next.worlds.firstIndex(where: { $0.id == id }) else { throw GameError.missing }
        try body(&next.worlds[index]); next.worlds[index].updatedAt = Date()
        if invalidatePreview { next.worlds[index].stateRevision = archive.worlds[index].stateRevision + 1; next.worlds[index].timePreview = nil }
        try Self.write(next, to: url); archive = next
    }
    public func saveProfile(_ profile: ProviderProfile) throws { try profile.validate(); var next = archive; next.profile = profile; try Self.write(next, to: url); archive = next }
    public func add(book: WorldBook) throws -> String {
        try Self.validate(book)
        let world = StoryWorld(book: book)
        var next = archive; next.worlds.append(world); try Self.write(next, to: url); archive = next; return world.id
    }
    public func importBackup(_ data: Data) throws -> String {
        guard data.count <= 100_000_000 else { throw GameError.invalid("文件过大") }
        let file = try JSONDecoder().decode(WorldBackup.self, from: data)
        guard (1...2).contains(file.version), file.format == "gongjie-story" else { throw GameError.invalid("不支持的故事文件") }
        var world = file.world
        if file.version == 1 { Self.migrate(&world) }
        try Self.validate(WorldBook(world: world.info, locations: world.locations, characters: world.characters, relationships: world.relationships))
        try Self.validate(world.baseline)
        guard Set(world.threads.map(\.id)).count == world.threads.count,
              Set(world.threads.flatMap(\.messages).map(\.id)).count == world.threads.flatMap(\.messages).count,
              Set(world.pending.map(\.id)).count == world.pending.count else { throw GameError.invalid("故事记录存在重复标识") }
        let ids = Set(world.characters.map { $0["id"].string })
        for thread in world.threads {
            guard !thread.characterIDs.isEmpty, Set(thread.characterIDs).isSubset(of: ids), thread.isRoom || thread.characterIDs.count == 1 else { throw GameError.invalid("故事人物引用无效") }
        }
        for job in world.pending {
            guard let thread = world.threads.first(where: { $0.id == job.threadID }), thread.characterIDs.contains(job.characterID), thread.messages.contains(where: { $0.id == job.id && $0.status == "complete" && $0.content == job.reply }), job.context["character"]["id"].string == job.characterID else { throw GameError.invalid("待记录任务的来源不完整") }
        }
        try Self.validateProgress(world)
        // Imported copies must not share thread/message identities used by UI drafts and streams.
        var identities: [String: String] = [:]
        identities[world.id] = newID()
        for thread in world.threads {
            identities[thread.id] = newID()
            for message in thread.messages { identities[message.id] = newID() }
        }
        func remap(_ value: J) -> J {
            switch value {
            case .string(let string): return .string(identities[string] ?? string)
            case .array(let values): return .array(values.map(remap))
            case .object(let values): return .object(values.mapValues(remap))
            default: return value
            }
        }
        let encoded = try JSONDecoder().decode(J.self, from: JSONEncoder().encode(world))
        world = try JSONDecoder().decode(StoryWorld.self, from: JSONEncoder().encode(remap(encoded)))
        world.timePreview = nil; world.stateRevision += 1; world.info["name"] = .string(String(world.name.prefix(54)) + " · 副本")
        for t in world.threads.indices { for m in world.threads[t].messages.indices where world.threads[t].messages[m].status == "streaming" { world.threads[t].messages[m].status = "cancelled" } }
        var next = archive; next.worlds.append(world); try Self.write(next, to: url); archive = next; return world.id
    }
    public func reset(_ id: String, confirmation: String) throws {
        try transact(id) { world in
            guard confirmation == world.name else { throw GameError.invalid("请输入完整世界名称") }
            var reset = StoryWorld(book: world.baseline); reset.id = world.id; reset.playerProfile = world.playerProfile; world = reset
        }
    }
    public func saves(worldID: String) -> [WorldSave] {
        archive.saves.filter { $0.worldID == worldID }.sorted { $0.createdAt > $1.createdAt }
    }
    @discardableResult public func createSave(worldID: String, name: String) throws -> WorldSave {
        let current = try world(worldID)
        try Self.assertSaveable(current)
        let label = name.trimmed.isEmpty ? current.info["current_time"].string : String(name.trimmed.prefix(80))
        let save = WorldSave(world: current, name: label)
        var next = archive; next.saves.append(save)
        try Self.write(next, to: url); archive = next
        return save
    }
    @discardableResult public func loadSave(worldID: String, saveID: String) throws -> WorldSave {
        let current = try world(worldID)
        try Self.assertSaveable(current)
        guard let saved = archive.saves.first(where: { $0.id == saveID && $0.worldID == worldID }), saved.world.id == worldID else { throw GameError.missing }
        try Self.validateProgress(saved.world); try Self.assertSaveable(saved.world)
        let backup = WorldSave(world: current, name: "读档前自动备份", automatic: true)
        var next = archive
        let index = next.worlds.firstIndex { $0.id == worldID }!
        var restored = saved.world; restored.timePreview = nil; restored.stateRevision = current.stateRevision + 1; restored.updatedAt = Date()
        next.worlds[index] = restored; next.saves.append(backup)
        try Self.write(next, to: url); archive = next
        return backup
    }
    private static func assertSaveable(_ world: StoryWorld) throws {
        guard world.pending.isEmpty else { throw GameError.pending }
        guard !world.threads.flatMap(\.messages).contains(where: { $0.status == "streaming" }) else { throw GameError.busy }
    }
    private static func migrate(_ world: inout StoryWorld) {
        world.timePreview = nil
        for t in world.threads.indices {
            for m in world.threads[t].messages.indices {
                let message = world.threads[t].messages[m]
                if var settlement = message.settlement {
                    if settlement["decision"] == .null {
                        let changed = settlement["event"] != .null || ["scene", "relationship", "player"].contains { !settlement[$0].object.isEmpty }
                        settlement["decision"] = ["outcome": changed ? "accepted" : "unchanged", "reason": "", "issues": []]
                        world.threads[t].messages[m].settlement = settlement
                    }
                    if world.threads[t].isRoom, settlement["decision"]["outcome"] != "rejected", let cid = message.characterID {
                        world.threads[t].characterScenes[cid] = (world.threads[t].characterScenes[cid] ?? [:]).merging(settlement["scene"])
                    }
                    if world.threads[t].messages[m].recordedTime == nil { world.threads[t].messages[m].recordedTime = settlement["scene"]["world_time"].string.isEmpty ? nil : settlement["scene"]["world_time"].string }
                }
                // Historical roster is evidence of who may need compilation, not permission to see raw text.
                if world.threads[t].isRoom && message.observationMembers.isEmpty { world.threads[t].messages[m].observationMembers = world.threads[t].characterIDs }
            }
            if world.threads[t].isRoom { world.threads[t].scene = world.threads[t].scene.selecting(["location_id", "location", "location_name", "world_time"]) }
        }
    }
    public static func validateProgress(_ world: StoryWorld) throws {
        try validate(WorldBook(world: world.info, locations: world.locations, characters: world.characters, relationships: world.relationships)); try validate(world.baseline)
        let ids = Set(world.characters.map { $0["id"].string }), places = Set(world.locations.map { $0["id"].string })
        guard world.player["location_id"].string.isEmpty || places.contains(world.player["location_id"].string) else { throw GameError.invalid("玩家地点已不存在") }
        guard Set(world.threads.map(\.id)).count == world.threads.count, Set(world.threads.flatMap(\.messages).map(\.id)).count == world.threads.flatMap(\.messages).count else { throw GameError.invalid("故事记录存在重复标识") }
        if let active = world.playerThreadID, !world.threads.contains(where: { $0.id == active }) { throw GameError.invalid("当前现场已不存在") }
        for (cid, tid) in world.characterRooms { guard ids.contains(cid), world.threads.contains(where: { $0.id == tid && $0.isRoom && $0.characterIDs.contains(cid) }) else { throw GameError.invalid("当前在场身份无效") } }
        for thread in world.threads {
            guard !thread.characterIDs.isEmpty, Set(thread.characterIDs).isSubset(of: ids), Set(thread.characterIDs).count == thread.characterIDs.count,
                  thread.isRoom || thread.characterIDs.count == 1, thread.locationID == nil || places.contains(thread.locationID!) else { throw GameError.invalid("故事人物或地点引用无效") }
            guard Set(thread.characterScenes.keys).isSubset(of: Set(thread.characterIDs)) else { throw GameError.invalid("个人现场引用无效") }
            for message in thread.messages {
                guard Set(message.observationMembers).isSubset(of: Set(thread.characterIDs)), Set(message.observationMembers).count == message.observationMembers.count else { throw GameError.invalid("观察者引用无效") }
                if let views = message.observations {
                    guard Set(views.keys) == Set(message.observationMembers), views.values.allSatisfy({ view in view.isEmpty || view.components(separatedBy: "\n").allSatisfy { message.content.contains($0) } }) else { throw GameError.invalid("观察投影与原文不一致") }
                }
            }
        }
        try validatePlayerProfile(world.playerProfile)
    }
    public static func validatePlayerProfile(_ profile: J) throws {
        guard case .object = profile, Set(profile.object.keys).isSubset(of: ["name", "age", "pronouns", "appearance", "identity"]) else { throw GameError.invalid("玩家角色卡字段无效") }
        for (key, value) in profile.object {
            if key == "age" { if value == .null { continue }; guard case .number(let n) = value, n.rounded() == n, (18...999).contains(n) else { throw GameError.invalid("玩家角色必须明确成年") } }
            else { guard case .string(let text) = value, text.count <= (key == "name" ? 60 : 3000) else { throw GameError.invalid("角色卡文字过长或格式有误") } }
        }
    }
    public static func validate(_ book: WorldBook) throws {
        func name(_ s: String) -> Bool { !s.trimmed.isEmpty && s.count <= 60 }
        guard name(book.world["name"].string) else { throw GameError.invalid("世界名称应为 1–60 字") }
        let locations = book.locations.map { $0["id"].string }, characters = book.characters.map { $0["id"].string }
        guard Set(locations).count == locations.count, Set(characters).count == characters.count, !locations.contains(""), !characters.contains("") else { throw GameError.invalid("人物或地点标识重复") }
        guard Set(book.characters.map { $0["name"].string }).count == characters.count, Set(book.locations.map { $0["name"].string }).count == locations.count else { throw GameError.invalid("同一世界的人物与地点不能重名") }
        for location in book.locations { guard name(location["name"].string) else { throw GameError.invalid("请填写地点名称") } }
        for c in book.characters {
            guard name(c["name"].string), case .number(let age) = c["age"], age.rounded() == age, (18...999).contains(age), c["current_location_id"].string.isEmpty || locations.contains(c["current_location_id"].string) else { throw GameError.invalid("人物需填写姓名、成年年龄和有效地点") }
            guard c["desire_profile"].object.values.allSatisfy({ if case .number(let n) = $0 { return n.rounded() == n && (0...100).contains(n) }; return false }) else { throw GameError.invalid("人物倾向应为 0–100 的整数") }
        }
        for r in book.relationships { guard characters.contains(r["from_character_id"].string), r["to_user"].bool || characters.contains(r["to_character_id"].string) else { throw GameError.invalid("关系指向了不存在的人物") } }
    }
}
public struct WorldBackup: Codable, Sendable {
    public var format = "gongjie-story"
    public var version = 2
    public var world: StoryWorld
    public init(world: StoryWorld) { self.world = world }
}
