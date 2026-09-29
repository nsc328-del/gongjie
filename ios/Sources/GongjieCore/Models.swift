import Foundation

public struct Message: Codable, Identifiable, Sendable, Equatable {
    public var id = newID()
    public var role: String
    public var characterID: String?
    public var content: String
    public var status = "complete"
    public var createdAt = Date()
    public var recordedTime: String?
    public var entryPulse: Int?
    public var settlement: J?
    public var usage: J?
    public var observations: [String: String]?
    public var observationMembers: [String] = []
    public init(role: String, content: String, characterID: String? = nil) { self.role = role; self.content = content; self.characterID = characterID }
    public var contextValue: J {
        var value: J = ["id": .string(id), "role": .string(role), "content": .string(content), "status": .string(status)]
        if let settlement { value["settlement"] = settlement }
        if let recordedTime { value["world_time"] = .string(recordedTime) }
        return value
    }
    enum CodingKeys: String, CodingKey { case id, role, characterID, content, status, createdAt, recordedTime, entryPulse, settlement, usage, observations, observationMembers }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); role = try c.decode(String.self, forKey: .role)
        characterID = try c.decodeIfPresent(String.self, forKey: .characterID); content = try c.decode(String.self, forKey: .content)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "complete"; createdAt = try c.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        recordedTime = try c.decodeIfPresent(String.self, forKey: .recordedTime)
        entryPulse = try c.decodeIfPresent(Int.self, forKey: .entryPulse); settlement = try c.decodeIfPresent(J.self, forKey: .settlement); usage = try c.decodeIfPresent(J.self, forKey: .usage)
        observations = try c.decodeIfPresent([String: String].self, forKey: .observations)
        observationMembers = try c.decodeIfPresent([String].self, forKey: .observationMembers) ?? []
    }
}
public struct StoryThread: Codable, Identifiable, Sendable, Equatable {
    public var id = newID()
    public var name: String
    public var characterIDs: [String]
    public var isRoom: Bool
    public var locationID: String?
    public var messages: [Message] = []
    public var scene: J = [:]
    public var previousScene: J = [:]
    public var characterScenes: [String: J] = [:]
    public var draft = ""
    public init(name: String, characterIDs: [String], isRoom: Bool, locationID: String? = nil) { self.name = name; self.characterIDs = characterIDs; self.isRoom = isRoom; self.locationID = locationID }
    enum CodingKeys: String, CodingKey { case id, name, characterIDs, isRoom, locationID, messages, scene, previousScene, characterScenes, draft }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); name = try c.decode(String.self, forKey: .name)
        characterIDs = try c.decode([String].self, forKey: .characterIDs); isRoom = try c.decode(Bool.self, forKey: .isRoom)
        locationID = try c.decodeIfPresent(String.self, forKey: .locationID); messages = try c.decodeIfPresent([Message].self, forKey: .messages) ?? []
        scene = try c.decodeIfPresent(J.self, forKey: .scene) ?? [:]; previousScene = try c.decodeIfPresent(J.self, forKey: .previousScene) ?? [:]
        characterScenes = try c.decodeIfPresent([String: J].self, forKey: .characterScenes) ?? [:]
        draft = try c.decodeIfPresent(String.self, forKey: .draft) ?? ""
    }
}
public struct SettlementJob: Codable, Identifiable, Sendable, Equatable {
    public var id: String
    public var threadID: String
    public var characterID: String
    public var reply: String
    public var context: J
    public var failure = ""
}
public struct WorldBook: Codable, Sendable, Equatable {
    public var world: J
    public var locations: [J]
    public var characters: [J]
    public var relationships: [J]
    public init(world: J, locations: [J] = [], characters: [J] = [], relationships: [J] = []) { self.world = world; self.locations = locations; self.characters = characters; self.relationships = relationships }
}
public struct StoryWorld: Codable, Identifiable, Sendable, Equatable {
    public var id = newID()
    public var info: J
    public var locations: [J]
    public var characters: [J]
    public var relationships: [J]
    public var baseline: WorldBook
    public var threads: [StoryThread] = []
    public var events: [J] = []
    public var knowledge: [J] = []
    public var actions: [String: J] = [:]
    public var player: J = ["condition": "", "visible_traces": [], "commitments": []]
    public var playerProfile: J = [:]
    public var pulse = 0
    public var narration = ""
    public var pending: [SettlementJob] = []
    public var updatedAt = Date()
    public var playerThreadID: String?
    public var characterRooms: [String: String] = [:]
    public var timePreview: J?
    public var stateRevision = 0
    public init(book: WorldBook) {
        info = book.world; locations = book.locations; characters = book.characters; relationships = book.relationships; baseline = book
        narration = info["opening_narration"].string
    }
    enum CodingKeys: String, CodingKey { case id, info, locations, characters, relationships, baseline, threads, events, knowledge, actions, player, playerProfile, pulse, narration, pending, updatedAt, playerThreadID, characterRooms, timePreview, stateRevision }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); info = try c.decode(J.self, forKey: .info)
        locations = try c.decode([J].self, forKey: .locations); characters = try c.decode([J].self, forKey: .characters)
        relationships = try c.decode([J].self, forKey: .relationships); baseline = try c.decode(WorldBook.self, forKey: .baseline)
        threads = try c.decodeIfPresent([StoryThread].self, forKey: .threads) ?? []; events = try c.decodeIfPresent([J].self, forKey: .events) ?? []
        knowledge = try c.decodeIfPresent([J].self, forKey: .knowledge) ?? []; actions = try c.decodeIfPresent([String: J].self, forKey: .actions) ?? [:]
        player = try c.decodeIfPresent(J.self, forKey: .player) ?? ["condition": "", "visible_traces": [], "commitments": []]
        playerProfile = try c.decodeIfPresent(J.self, forKey: .playerProfile) ?? [:]
        pulse = try c.decodeIfPresent(Int.self, forKey: .pulse) ?? 0; narration = try c.decodeIfPresent(String.self, forKey: .narration) ?? ""
        pending = try c.decodeIfPresent([SettlementJob].self, forKey: .pending) ?? []; updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? Date()
        playerThreadID = try c.decodeIfPresent(String.self, forKey: .playerThreadID); characterRooms = try c.decodeIfPresent([String: String].self, forKey: .characterRooms) ?? [:]
        timePreview = try c.decodeIfPresent(J.self, forKey: .timePreview); stateRevision = try c.decodeIfPresent(Int.self, forKey: .stateRevision) ?? 0
    }
    public var name: String { info["name"].string }
    public func locationName(_ id: String?) -> String { locations.first { $0["id"].string == id }?["name"].string ?? "未定" }
    public func character(_ id: String) -> J { characters.first { $0["id"].string == id } ?? [:] }
    public func relationship(_ id: String) -> J { relationships.first { $0["from_character_id"].string == id && $0["to_user"].bool } ?? [:] }
    public func availability(_ id: String) -> String {
        let raw = actions[id]?["availability"].string ?? "reachable"
        if raw == "unavailable" { return raw }
        let location = character(id)["current_location_id"].string
        if !location.isEmpty && location == player["location_id"].string { return "present" }
        return raw == "present" ? "reachable" : raw
    }
}
public struct ProviderProfile: Codable, Sendable, Equatable {
    public var wireProtocol = "openai-compatible"
    public var baseURL = "https://api.deepseek.com"
    public var path = "/chat/completions"
    public var model = "deepseek-v4-flash"
    public var maxTokens = 16384
    public var temperature = 0.95
    public init() {}
    public func validate() throws {
        guard ["openai-compatible", "anthropic-messages"].contains(wireProtocol), let url = URL(string: baseURL), url.scheme == "https", url.host != nil, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil, !model.trimmed.isEmpty, (256...200000).contains(maxTokens), (0...2).contains(temperature), !path.contains("://"), !path.contains("?"), !path.contains("#") else { throw GameError.invalid("请检查 HTTPS 地址、接口路径、模型名称与回复长度") }
    }
}
public struct Archive: Codable, Sendable {
    public var version = 2
    public var worlds: [StoryWorld] = []
    public var saves: [WorldSave] = []
    public var profile = ProviderProfile()
    public init() {}
    enum CodingKeys: String, CodingKey { case version, worlds, saves, profile }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decode(Int.self, forKey: .version); worlds = try c.decode([StoryWorld].self, forKey: .worlds)
        saves = try c.decodeIfPresent([WorldSave].self, forKey: .saves) ?? []; profile = try c.decodeIfPresent(ProviderProfile.self, forKey: .profile) ?? ProviderProfile()
    }
}
public struct WorldSave: Codable, Identifiable, Sendable, Equatable {
    public var id = newID()
    public var worldID: String
    public var name: String
    public var createdAt = Date()
    public var automatic: Bool
    public var world: StoryWorld
    public var worldTime: String { world.info["current_time"].string }
    public init(world: StoryWorld, name: String, automatic: Bool = false) {
        self.world = world; worldID = world.id; self.name = name; self.automatic = automatic
        self.world.timePreview = nil
    }
}
public struct BookFile: Codable, Sendable {
    public var format = "gongjie-worldbook"
    public var version = 1
    public var book: WorldBook
    public init(book: WorldBook) { self.book = book }
}
public enum BuiltIn {
    public static func daylight() throws -> WorldBook { try JSONDecoder().decode(BookFile.self, from: Data(contentsOf: Bundle.module.url(forResource: "Daylight", withExtension: "json")!)).book }
}
