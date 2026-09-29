import XCTest
@testable import GongjieCore

actor ScriptedModel: LanguageModel {
    enum Step: Sendable { case text(String), partial(String, Bool), failure, wait }
    var steps: [Step]
    var requests: [ModelRequest] = []
    init(_ steps: [Step]) { self.steps = steps }
    func generate(_ request: ModelRequest, profile: ProviderProfile, key: String, update: @escaping @Sendable (ModelReply) async -> Void) async throws -> ModelReply {
        requests.append(request)
        guard !steps.isEmpty else { throw GameError.invalid("测试缺少回应") }
        let step = steps.removeFirst()
        var reply = ModelReply()
        switch step {
        case .text(let text): reply.text = text; reply.completed = true; await update(reply); return reply
        case .partial(let text, let cancel): reply.text = text; await update(reply); if cancel { throw CancellationError() }; throw ProviderFailure("response_incomplete", "测试响应未完成")
        case .failure: throw ProviderFailure("test_network", "测试连接中断")
        case .wait: try await Task.sleep(for: .seconds(30)); throw CancellationError()
        }
    }
    func count() -> Int { requests.count }
}
final class CoreTests: XCTestCase {
    let profile = ProviderProfile()
    let approved = ScriptedModel.Step.text("{\"approved\":true,\"issues\":[]}")
    let cleanPatch = #"{"event":null,"scene":{"physical_state":"","emotional_state":"平静","active_threads":[]},"player":{"condition":"","visible_traces":[]},"relationship":{}}"#
    let stay = ScriptedModel.Step.text(#"{"mode":"stay"}"#)
    func observation(_ text: String) -> ScriptedModel.Step {
        .text(J.object(["daylight_lorne": ["quotes": [.string(text)]], "daylight_evelyn": ["quotes": [.string(text)]]]).text)
    }
    var directory: URL!
    override func setUpWithError() throws { directory = FileManager.default.temporaryDirectory.appendingPathComponent(newID()); try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true) }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
    func setupWorld() async throws -> (WorldStore, StoryWorld) { let store = try WorldStore(url: directory.appendingPathComponent("worlds.json")); let archive = await store.snapshot(); return (store, archive.worlds[0]) }
    func ready(_ store: WorldStore, world: StoryWorld, room: Bool = false) async throws -> String {
        var thread = StoryThread(name: "会面", characterIDs: room ? ["daylight_lorne", "daylight_evelyn"] : ["daylight_lorne"], isRoom: room, locationID: "daylight_throne")
        if !room { var entry = Message(role: "assistant", content: "洛恩·维瑟尔望向门边。", characterID: "daylight_lorne"); entry.entryPulse = 0; thread.messages.append(entry) }
        let saved = thread
        try await store.transact(world.id) { w in
            w.threads.append(saved)
            if room { try w.gatherRoom(threadID: saved.id) } else { try w.enterThread(threadID: saved.id) }
        }; return thread.id
    }
    func testNativeJourneyOpeningSettlementPulseAndReentry() async throws {
        let (store, world) = try await setupWorld()
        let quiet = ScriptedModel.Step.text(#"{"participation":"quiet"}"#)
        let model = ScriptedModel([
            .text("洛恩·维瑟尔放下手中的书。"), .text(#"{"text":"洛恩·维瑟尔放下手中的书。","removed":[]}"#),
            stay, .text("洛恩·维瑟尔点了点头。"), .text(cleanPatch), approved,
            .text(#"{"world_time":"次日清晨","ready_commitment_ids":[],"appointment_times":{}}"#),
            quiet, quiet, quiet, quiet,
            .text(#"{"world_time":"次日清晨","narration":"晨光落在海面。","player_condition_action":"clear","retained_trace_indices":[]}"#),
            .text(#"{"narration":"晨光落在海面。"}"#),
            .text("洛恩·维瑟尔向窗边侧过身。"), .text(#"{"text":"洛恩·维瑟尔向窗边侧过身。","removed":[]}"#)
        ])
        let engine = StoryEngine(store: store, model: model)
        let thread = try await engine.conversation(worldID: world.id, characterID: "daylight_lorne")
        try await engine.enter(worldID: world.id, threadID: thread, profile: profile, key: "test-key") { _ in }
        try await engine.enter(worldID: world.id, threadID: thread, profile: profile, key: "test-key") { _ in }
        var count = await model.count(); XCTAssertEqual(count, 2)
        try await engine.turn(worldID: world.id, threadID: thread, content: "你好。", profile: profile, key: "test-key") { _ in }
        var saved = try await store.world(world.id)
        XCTAssertTrue(saved.pending.isEmpty); XCTAssertNotNil(saved.threads[0].messages.last?.settlement)
        let preview = try await engine.preview(worldID: world.id, profile: profile, key: "test-key") { _ in }
        try await engine.pulse(worldID: world.id, previewID: preview["id"].string, profile: profile, key: "test-key") { _ in }
        try await engine.enter(worldID: world.id, threadID: thread, profile: profile, key: "test-key") { _ in }
        saved = try await store.world(world.id)
        XCTAssertEqual(saved.pulse, 1); XCTAssertEqual(saved.threads[0].messages.last?.entryPulse, 1)
        XCTAssertEqual(saved.info["current_time"], "次日清晨")
        let requests = await model.requests
        let reentry = try XCTUnwrap(requests.last { $0.system.contains("新时段独立相遇") })
        XCTAssertTrue(reentry.system.contains("historical_dialogue")); XCTAssertEqual(reentry.messages.count, 1)
        let reopened = try WorldStore(url: directory.appendingPathComponent("worlds.json")), restored = await reopened.snapshot()
        XCTAssertEqual(restored.worlds[0], saved)
        count = await model.count(); XCTAssertEqual(count, 15)
    }
    func testRoomFailureQueueSurvivesRestartAndAppliesOnlyOnce() async throws {
        let (store, world) = try await setupWorld(), thread = try await ready(store, world: world, room: true)
        let broken = ScriptedModel([
            observation("我会明天来。"), approved,
            stay, .text(#"{"reply":"洛恩·维瑟尔点头。"}"#), observation("洛恩·维瑟尔点头。"), approved, .failure,
            stay, .text(#"{"reply":"伊芙琳·薇尔静静听着。"}"#), observation("伊芙琳·薇尔静静听着。"), approved
        ])
        let engine = StoryEngine(store: store, model: broken)
        try await engine.turn(worldID: world.id, threadID: thread, content: "我会明天来。", profile: profile, key: "test") { _ in }
        let saved = try await store.world(world.id)
        XCTAssertEqual(saved.pending.count, 2); XCTAssertEqual(saved.threads[0].messages.count, 3)
        let copyID = try await store.importBackup(JSONEncoder().encode(WorldBackup(world: saved)))
        let copy = try await store.world(copyID)
        XCTAssertNotEqual(copy.pending[0].id, saved.pending[0].id)
        XCTAssertEqual(copy.pending[0].threadID, copy.threads[0].id)
        XCTAssertEqual(copy.pending[0].context["room"]["id"].string, copy.threads[0].id)
        XCTAssertTrue(copy.threads[0].messages.contains { $0.id == copy.pending[0].id })
        do { _ = try await engine.preview(worldID: world.id, profile: profile, key: "test") { _ in }; XCTFail("Must block") } catch GameError.pending {} catch { XCTFail("\(error)") }
        let reopened = try WorldStore(url: directory.appendingPathComponent("worlds.json"))
        let patch = #"{"event":null,"relationship":{"trust_delta":1,"evidence":{"source_id":"user_0","kind":"expressed"}}}"#
        let recoveryModel = ScriptedModel([.text(patch), approved, .text(cleanPatch), approved])
        let recovery = StoryEngine(store: reopened, model: recoveryModel)
        try await recovery.recover(worldID: world.id, profile: profile, key: "test") { _ in }
        try await recovery.recover(worldID: world.id, profile: profile, key: "test") { _ in }
        let result = try await reopened.world(world.id)
        XCTAssertTrue(result.pending.isEmpty)
        XCTAssertEqual(result.relationship("daylight_lorne")["trust"].int, world.relationship("daylight_lorne")["trust"].int + 1)
        let count = await recoveryModel.count(); XCTAssertEqual(count, 4)
        XCTAssertTrue(result.threads[0].messages.filter { $0.role == "assistant" }.allSatisfy { $0.settlement != nil })
    }
    func testKnowledgeBoundaryEvidenceAndBeliefProjection() throws {
        var world = StoryWorld(book: try BuiltIn.daylight())
        var thread = StoryThread(name: "会面", characterIDs: ["daylight_lorne"], isRoom: false)
        thread.messages = [Message(role: "user", content: "我把事实告诉你。")]
        let context = ContextCompiler.context(world: world, thread: thread, characterID: "daylight_lorne")
        let reply = "洛恩·维瑟尔听完了那段秘密。"
        var patch: J = ["event": ["title": "秘密被告知", "details": "玩家向洛恩·维瑟尔讲述了一段秘密。", "visibility": "secret", "participants": ["洛恩·维瑟尔"], "witnesses": [], "knowledge_sources": ["洛恩·维瑟尔": ["role": "assistant", "quote": .string(reply), "kind": "told"]]], "evidence": ["event": ["role": "assistant", "quote": .string(reply), "kind": "occurred"]]]
        XCTAssertNoThrow(try PatchValidator.validate(patch, context: context, reply: reply))
        patch["event"]["witnesses"] = ["伊芙琳·薇尔"]
        XCTAssertThrowsError(try PatchValidator.validate(patch, context: context, reply: reply))
        patch["event"]["witnesses"] = []; patch["evidence"]["event"]["kind"] = "intent"
        XCTAssertThrowsError(try PatchValidator.validate(patch, context: context, reply: reply))
        world.events = [["id": "e", "visibility": "public", "title": "真正的答案", "details": "真正的答案在码头", "world_time": "昨夜", "location_name": "玻璃冬园", "synced": false], ["id": "s", "visibility": "secret", "details": "隐藏的经历"]]
        world.knowledge = [["character_id": "daylight_evelyn", "event_id": "e", "state": "misunderstood", "belief": "相信另一种说法"]]
        let known = ContextCompiler.knowledge(world, characterID: "daylight_evelyn", includePendingPublic: true)
        XCTAssertEqual(known.count, 1); XCTAssertFalse(known[0].text.contains("真正的答案")); XCTAssertEqual(known[0]["world_time"], .null)
        let shen = ContextCompiler.context(world: world, thread: thread, characterID: "daylight_lorne")
        XCTAssertFalse(shen.text.contains("隐藏的经历")); XCTAssertEqual(shen["characters"].array[1]["current_location_id"], .null)
        world.player = ["location_id": "daylight_throne", "condition": "袖口微湿", "visible_traces": ["与洛恩·维瑟尔相距一步", "袖口微湿"], "commitments": []]
        let pulse = ContextCompiler.pulseInput(world: world, characterID: "daylight_evelyn", target: "次日")
        XCTAssertEqual(pulse["玩家可被观察的状态"]["condition"], .null)
        XCTAssertEqual(ContextCompiler.observation(world)["visible_traces"], ["袖口微湿"])
    }
    func testInterruptedReplyCannotSettleOrPolluteNextContext() async throws {
        for cancelled in [false, true] {
            let url = directory.appendingPathComponent("\(cancelled).json"), store = try WorldStore(url: url)
            let world = await store.snapshot().worlds[0], thread = try await ready(store, world: world)
            let model = ScriptedModel([stay, .partial("没有完成的动作", cancelled)])
            let engine = StoryEngine(store: store, model: model)
            do { try await engine.turn(worldID: world.id, threadID: thread, content: "继续聊。", profile: profile, key: "test") { _ in }; XCTFail("Must fail") } catch {}
            let result = try await store.world(world.id)
            XCTAssertTrue(result.pending.isEmpty); XCTAssertTrue(result.events.isEmpty)
            XCTAssertEqual(result.threads[0].messages.last?.status, cancelled ? "cancelled" : "error")
            XCTAssertFalse(ContextCompiler.narrative(ContextCompiler.context(world: result, thread: result.threads[0], characterID: "daylight_lorne"), profile: profile).messages.map(\.text).joined().contains("没有完成的动作"))
            try await engine.edit(worldID: world.id, book: world.baseline) // lock released
        }
    }
    func testAtomicPersistenceDraftBackupAndReset() async throws {
        let (store, world) = try await setupWorld(), thread = try await ready(store, world: world)
        let engine = StoryEngine(store: store, model: ScriptedModel([]))
        try await engine.saveDraft(worldID: world.id, threadID: thread, text: "保留这段草稿")
        let before = try Data(contentsOf: directory.appendingPathComponent("worlds.json"))
        do { try await store.transact(world.id) { w in w.info["name"] = "不可写入"; throw GameError.invalid("abort") }; XCTFail("Must throw") } catch {}
        XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent("worlds.json")), before)
        let saved = try await store.world(world.id)
        let duplicate = try await store.importBackup(JSONEncoder().encode(WorldBackup(world: saved)))
        XCTAssertNotEqual(duplicate, world.id)
        let copy = try await store.world(duplicate); XCTAssertEqual(copy.threads[0].draft, "保留这段草稿"); XCTAssertNotEqual(copy.threads[0].id, saved.threads[0].id); XCTAssertNotEqual(copy.threads[0].messages[0].id, saved.threads[0].messages[0].id)
        do { try await engine.reset(worldID: world.id, confirmation: "错误"); XCTFail("Must require name") } catch {}
        try await engine.reset(worldID: world.id, confirmation: world.name)
        let reset = try await store.world(world.id)
        XCTAssertTrue(reset.threads.isEmpty); XCTAssertEqual(reset.characters, world.baseline.characters)
        XCTAssertFalse(String(data: before, encoding: .utf8)!.contains("api_key"))
    }
    func testPulseRetainsOnlyExistingStateAndGroundsAvailability() throws {
        var world = StoryWorld(book: try BuiltIn.daylight())
        world.player = ["condition": "衣袖微湿", "visible_traces": ["一点灰尘"], "commitments": []]
        var transition: J = ["world_time": "次日清晨", "narration": "晨风吹过。", "player_condition_action": "keep", "retained_trace_indices": [0], "player_condition": "模型编造的新状态"]
        XCTAssertEqual(try PulseRules.transition(transition, world: world)["condition"], "衣袖微湿")
        transition["retained_trace_indices"] = [true]; XCTAssertThrowsError(try PulseRules.transition(transition, world: world))
        transition["retained_trace_indices"] = [0, 0]; XCTAssertThrowsError(try PulseRules.transition(transition, world: world))
        transition["retained_trace_indices"] = []; transition["world_time"] = "18:34:20"; XCTAssertThrowsError(try PulseRules.transition(transition, world: world))
        world.actions["daylight_lorne"] = ["availability": "unavailable"]
        let action: J = ["participation": "active", "active_reason": "visible_change", "active_source": "availability", "availability": "reachable", "location": "金枝厅"]
        XCTAssertEqual(PulseRules.action(action, world: world, characterID: "daylight_lorne", input: [:])["participation"], "active")
        var invented = action; invented["active_reason"] = "new_fact"; invented["active_source"] = "does-not-exist"
        XCTAssertEqual(PulseRules.action(invented, world: world, characterID: "daylight_lorne", input: [:])["participation"], "quiet")
    }
    func testProviderWireCompletionAndTokenAccounting() throws {
        var bytes = SSEByteDecoder(), events: [String] = []
        for byte in "data: 中文分片\r\n\r\ndata: [DONE]\n\n".utf8 { if let event = try bytes.append(byte) { events.append(event) } }
        XCTAssertEqual(events, ["中文分片", "[DONE]"])
        var profile = ProviderProfile(), reply = ModelReply(), decoder = SSEDecoder()
        XCTAssertNil(decoder.line(": heartbeat")); XCTAssertNil(decoder.line("data: {\"a\":")); XCTAssertNil(decoder.line("data: 1}")); XCTAssertEqual(decoder.line(""), "{\"a\":\n1}")
        try WireAdapter.consume(#"{"choices":[{"delta":{"content":"中文"}}]}"#, protocol: profile.wireProtocol, reply: &reply)
        XCTAssertEqual(reply.text, "中文"); XCTAssertFalse(reply.completed)
        try WireAdapter.consume("[DONE]", protocol: profile.wireProtocol, reply: &reply); XCTAssertTrue(reply.completed)
        XCTAssertNoThrow(try reply.validateComplete())
        var truncated = reply
        truncated.finishReason = "length"
        XCTAssertThrowsError(try truncated.validateComplete())
        let request = try WireAdapter.request(ModelRequest(system: "规则", messages: [], thinking: true), profile: profile, key: "local-test-key")
        let body = try JSONDecoder().decode(J.self, from: request.httpBody!)
        XCTAssertEqual(body["thinking"]["type"], "enabled"); XCTAssertEqual(body["reasoning_effort"], "low")
        profile.wireProtocol = "anthropic-messages"; reply = ModelReply()
        try WireAdapter.consume(#"{"type":"message_start","message":{"usage":{"input_tokens":91,"cache_read_input_tokens":30}}}"#, protocol: profile.wireProtocol, reply: &reply)
        try WireAdapter.consume(#"{"type":"message_delta","delta":{},"usage":{"output_tokens":7}}"#, protocol: profile.wireProtocol, reply: &reply)
        XCTAssertFalse(reply.completed); XCTAssertEqual(reply.usage["input_tokens"].int, 91); XCTAssertEqual(reply.usage["cache_read_input_tokens"].int, 30)
        try WireAdapter.consume(#"{"type":"message_stop"}"#, protocol: profile.wireProtocol, reply: &reply); XCTAssertTrue(reply.completed)
    }
    func testMissingCredentialsAndValidationNeverChangeHistory() async throws {
        let (store, world) = try await setupWorld(), thread = try await ready(store, world: world)
        let engine = StoryEngine(store: store, model: ScriptedModel([]))
        do { try await engine.turn(worldID: world.id, threadID: thread, content: "你好", profile: profile, key: "") { _ in }; XCTFail("Must fail") } catch {}
        let result = try await store.world(world.id); XCTAssertEqual(result.threads[0].messages.count, 1)
        var book = world.baseline; book.characters[0]["age"] = true; XCTAssertThrowsError(try WorldStore.validate(book))
        let context = ContextCompiler.context(world: world, thread: result.threads[0], characterID: "daylight_lorne")
        let absurd: J = ["scene": ["tension": .number(1e200)]]
        XCTAssertThrowsError(try PatchValidator.validate(absurd, context: context, reply: ""))
    }
    func testLiveNativeModelJourneyWhenConfigured() async throws {
        guard let key = ProcessInfo.processInfo.environment["GONGJIE_SMOKE_KEY"], let json = ProcessInfo.processInfo.environment["GONGJIE_SMOKE_PROFILE"] else { throw XCTSkip("真实连接测试仅在显式提供测试密钥时运行") }
        let selected = try JSONDecoder().decode(ProviderProfile.self, from: Data(json.utf8))
        let store = try WorldStore(url: directory.appendingPathComponent("live.json"), seed: false)
        var book = try BuiltIn.daylight()
        book.characters = [book.characters[0]]
        book.relationships = book.relationships.filter { $0["from_character_id"] == book.characters[0]["id"] && $0["to_user"].bool }
        book.world["rules"] = "所有角色为成年人。本次是平静日常对话测试，不出现性行为或露骨内容。人物不编造未被提供的过去经历。"
        let id = try await store.add(book: book), engine = StoryEngine(store: store)
        let thread = try await engine.conversation(worldID: id, characterID: "daylight_lorne")
        try await engine.enter(worldID: id, threadID: thread, profile: selected, key: key) { _ in }
        try await engine.turn(worldID: id, threadID: thread, content: "我向你点头：你好，今天只想安静聊一会儿。", profile: selected, key: key) { _ in }
        let saved = try await store.world(id)
        XCTAssertTrue(saved.pending.isEmpty)
        XCTAssertEqual(saved.threads[0].messages.count, 3)
        XCTAssertNotNil(saved.threads[0].messages.last?.settlement)
        let preview = try await engine.preview(worldID: id, profile: selected, key: key) { _ in }
        try await engine.pulse(worldID: id, previewID: preview["id"].string, profile: selected, key: key) { _ in }
        try await engine.enter(worldID: id, threadID: thread, profile: selected, key: key) { _ in }
        let advanced = try await store.world(id)
        XCTAssertEqual(advanced.pulse, 1)
        XCTAssertEqual(advanced.threads[0].messages.last?.entryPulse, 1)
    }

}
