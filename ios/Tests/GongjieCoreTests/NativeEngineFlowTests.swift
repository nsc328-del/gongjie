import XCTest
@testable import GongjieCore

private actor FlowModel: LanguageModel {
    enum Mode { case roomRecovery, destinationRejected, destinationArrival }
    let mode: Mode
    var requests: [(String, ModelRequest)] = []
    var failedOnce = false
    let body = "洛恩领着你走到河湾石阶。\n他收回手，停在石阶旁。"
    init(_ mode: Mode) { self.mode = mode }
    func generate(_ request: ModelRequest, profile: ProviderProfile, key: String, update: @escaping @Sendable (ModelReply) async -> Void) async throws -> ModelReply {
        let payload = (try? J.parse(request.messages.first?["content"].string ?? "")) ?? [:]
        let system = request.system
        var result: J?, text: String?, category = "body"
        if system.contains("你是同场观察编译员") {
            category = "observation"; let content = payload["content"].string
            result = .object(Dictionary(uniqueKeysWithValues: payload["members"].array.map { member in
                let id = member["id"].string
                let quotes: [String] = id == "daylight_evelyn" && content.contains("口令蓝鸢") ? ["洛恩把纸条推到你面前。"] : [content]
                return (id, ["quotes": .strings(quotes)])
            }))
        } else if system.contains("你是同场观察核对员") { category = "observation_review"; result = ["approved": true, "issues": []] }
        else if system.contains("你是场景规划员") {
            category = "plan"
            if mode == .roomRecovery { result = ["mode": "stay"] }
            else if mode == .destinationArrival { result = ["mode": "resolve", "purpose": "travel", "source_quote": payload["user"], "new_location": ["name": "河湾石阶", "parent_id": payload["world_region_id"], "description": "河湾旁一处普通石阶。"]] }
            else { result = ["mode": "resolve", "purpose": "appointment", "source_quote": payload["user"], "target_id": payload["known_locations"].array.first { $0["name"].string == "内阁书库" }!["id"]] }
        } else if system.contains("你是地点设定核对员") { category = "plan_review"; result = ["approved": true, "issues": []] }
        else if system.contains("你是转场正文核对员") { category = "body_review"; result = ["approved": true, "issues": [], "resolution": ["confirmed": true, "arrived": .bool(mode == .destinationArrival), "source_id": "p0"]] }
        else if system.contains("共享世界的状态裁判") {
            category = "proposal"
            if mode == .roomRecovery && !failedOnce { failedOnce = true; requests.append((category, request)); throw ProviderFailure("connection", "测试断网") }
            let isLorne = payload["角色"].string.contains("洛恩")
            result = ["scene": ["emotional_state": .string(mode == .roomRecovery && isLorne ? "暗自记着口令蓝鸢" : "平静")]]
        } else if system.contains("你是硬状态证据核对员") {
            category = "patch_review"
            result = mode == .destinationRejected ? ["approved": false, "issues": ["提案没有依据"]] : ["approved": true, "issues": []]
        } else if mode == .roomRecovery {
            let isLorne = system.contains("你是洛恩")
            result = ["reply": .string(isLorne ? "洛恩把纸条推到你面前。\n纸上写着口令蓝鸢。" : "伊芙琳看着纸条的背面，没有去碰它。")]
        } else { text = body }
        requests.append((category, request))
        var reply = ModelReply(); reply.text = text ?? result!.text; reply.completed = true; reply.finishReason = "stop"
        await update(reply)
        return reply
    }
    func kinds() -> [String] { requests.map { $0.0 } }
}

final class NativeEngineFlowTests: XCTestCase {
    var directory: URL!
    override func setUpWithError() throws { directory = FileManager.default.temporaryDirectory.appendingPathComponent(newID()); try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true) }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
    private func setup(_ model: FlowModel, room: Bool = false) async throws -> (WorldStore, StoryEngine, StoryWorld, String) {
        let store = try WorldStore(url: directory.appendingPathComponent(newID() + ".json")), world = await store.snapshot().worlds[0], engine = StoryEngine(store: store, model: model)
        if room {
            let id = try await engine.room(worldID: world.id, name: "见面", locationID: "daylight_throne", characters: ["daylight_lorne", "daylight_evelyn"])
            return (store, engine, world, id)
        }
        let id = try await engine.conversation(worldID: world.id, characterID: "daylight_lorne")
        var entry = Message(role: "assistant", content: "洛恩抬头看向你。", characterID: "daylight_lorne"); entry.entryPulse = 0; entry.recordedTime = world.info["current_time"].string
        let saved = entry
        try await store.transact(world.id) { value in value.threads[0].messages.append(saved) }
        // Even an idempotent opening must establish physical presence, without a model call.
        try await engine.enter(worldID: world.id, threadID: id, profile: ProviderProfile(), key: "") { _ in }
        return (store, engine, world, id)
    }
    func testRoomPartialObservationPersistsAcrossProviderFailureAndFIFORecovery() async throws {
        let model = FlowModel(.roomRecovery), (store, engine, world, roomID) = try await setup(model, room: true)
        try await engine.turn(worldID: world.id, threadID: roomID, content: "给我看看。", profile: ProviderProfile(), key: "test") { _ in }
        let pending = try await store.world(world.id)
        XCTAssertEqual(pending.pending.count, 2); XCTAssertEqual(pending.threads[0].messages.count, 3)
        XCTAssertEqual(pending.threads[0].messages[1].observations?["daylight_evelyn"], "洛恩把纸条推到你面前。")
        try await engine.recover(worldID: world.id, profile: ProviderProfile(), key: "test") { _ in }
        let result = try await store.world(world.id)
        XCTAssertTrue(result.pending.isEmpty)
        XCTAssertEqual(result.threads[0].characterScenes["daylight_lorne"]?["emotional_state"], "暗自记着口令蓝鸢")
        XCTAssertEqual(result.threads[0].characterScenes["daylight_evelyn"]?["emotional_state"], "平静")
        XCTAssertEqual(result.threads[0].scene["emotional_state"], .null)
        let requests = await model.requests
        let evelyn = requests.filter { kind, request in
            guard !kind.hasPrefix("observation") else { return false }
            let payload = (try? J.parse(request.messages.first?["content"].string ?? "")) ?? [:]
            return request.system.contains("你是伊芙琳") || payload["角色"].string.contains("伊芙琳") || payload["character"]["name"].string.contains("伊芙琳") || (kind == "patch_review" && payload["proposal"]["scene"]["emotional_state"] == "平静")
        }
        XCTAssertTrue(Set(evelyn.map { $0.0 }).isSuperset(of: ["plan", "body", "proposal", "patch_review"]))
        XCTAssertTrue(evelyn.allSatisfy { !$0.1.system.contains("口令蓝鸢") && !$0.1.messages.map(\.text).joined().contains("口令蓝鸢") })
        let before = requests.count
        try await engine.recover(worldID: world.id, profile: ProviderProfile(), key: "test") { _ in }
        let after = await model.requests.count; XCTAssertEqual(before, after)
    }
    func testDestinationProposalFailureKeepsSingleBodyAndSuccessUsesPreparedPatch() async throws {
        for mode in [FlowModel.Mode.destinationRejected, .destinationArrival] {
            let model = FlowModel(mode), (store, engine, world, threadID) = try await setup(model)
            do {
                try await engine.turn(worldID: world.id, threadID: threadID, content: mode == .destinationArrival ? "带我去河湾石阶。" : "明天在内阁书库见。", profile: ProviderProfile(), key: "test") { _ in }
                if mode == .destinationRejected { XCTFail("Rejected destination must not publish") }
            } catch let error as ProviderFailure { XCTAssertEqual(mode, .destinationRejected); XCTAssertEqual(error.code, "scene_settlement") }
            let result = try await store.world(world.id), kinds = await model.kinds()
            XCTAssertEqual(kinds.filter { $0 == "body" }.count, 1)
            XCTAssertTrue(result.pending.isEmpty)
            if mode == .destinationRejected {
                XCTAssertEqual(kinds.filter { $0 == "proposal" }.count, 2); XCTAssertFalse(kinds.contains("plan_review"))
                XCTAssertEqual(result.threads[0].messages.count, 2); XCTAssertEqual(result.locations.count, world.locations.count)
                XCTAssertEqual(result.character("daylight_lorne")["current_location_id"], world.character("daylight_lorne")["current_location_id"])
                let requests = await model.requests.filter { $0.0 == "proposal" }
                let catalogs = requests.map { try! J.parse($0.1.messages[0]["content"].string)["本轮来源表"] }
                XCTAssertEqual(catalogs[0], catalogs[1])
            } else {
                XCTAssertEqual(kinds, ["plan", "plan_review", "body", "body_review", "proposal", "patch_review"])
                XCTAssertEqual(result.threads[0].messages.count, 3); XCTAssertEqual(result.locations.count, world.locations.count + 1)
                XCTAssertEqual(result.player["location_id"], result.character("daylight_lorne")["current_location_id"])
                XCTAssertTrue(result.knownLocations(characterID: "player").contains { $0["name"] == "河湾石阶" })
                XCTAssertEqual(result.threads[0].messages.last?.settlement?["decision"]["outcome"], "accepted")
            }
        }
    }
    func testCreativeEditsPreserveProgressAndSavesRejectPending() async throws {
        let model = FlowModel(.destinationArrival), (store, engine, world, threadID) = try await setup(model)
        try await engine.turn(worldID: world.id, threadID: threadID, content: "带我去河湾石阶。", profile: ProviderProfile(), key: "test") { _ in }
        try await store.transact(world.id) { $0.info["current_time"] = "校时日正午" }
        let before = try await store.world(world.id)
        var book = before.baseline; book.world["premise"] = "新的背景介绍"; book.characters[0]["persona"] = "修改人物稳定个性"
        try await engine.edit(worldID: world.id, book: book)
        let edited = try await store.world(world.id)
        XCTAssertEqual(edited.info["current_time"], "校时日正午"); XCTAssertEqual(edited.info["premise"], "新的背景介绍")
        XCTAssertEqual(edited.character("daylight_lorne")["current_location_id"], before.character("daylight_lorne")["current_location_id"])
        XCTAssertEqual(edited.locations, before.locations); XCTAssertEqual(edited.relationships, before.relationships)
        XCTAssertFalse(edited.baseline.locations.contains { $0["name"] == "河湾石阶" })
        let saved = try await engine.createSave(worldID: world.id, name: "保留")
        let message = Message(role: "assistant", content: "等待记录。", characterID: "daylight_lorne")
        let job = SettlementJob(id: message.id, threadID: threadID, characterID: "daylight_lorne", reply: message.content, context: [:])
        try await store.transact(world.id) { value in value.threads[0].messages.append(message); value.pending.append(job) }
        do { try await engine.createSave(worldID: world.id, name: "不能存"); XCTFail("Pending blocks save") } catch GameError.pending {}
        do { try await engine.loadSave(worldID: world.id, saveID: saved.id); XCTFail("Pending blocks restore") } catch GameError.pending {}
        let blocked = try await store.world(world.id); XCTAssertEqual(blocked.pending.count, 1)
    }
}
