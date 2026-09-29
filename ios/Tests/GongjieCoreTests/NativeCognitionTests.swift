import XCTest
@testable import GongjieCore

private actor CognitionModel: LanguageModel {
    var responses: [String]
    var requests: [ModelRequest] = []
    init(_ responses: [String]) { self.responses = responses }
    func generate(_ request: ModelRequest, profile: ProviderProfile, key: String, update: @escaping @Sendable (ModelReply) async -> Void) async throws -> ModelReply {
        requests.append(request)
        guard !responses.isEmpty else { throw GameError.invalid("测试回应不足") }
        var reply = ModelReply(); reply.text = responses.removeFirst(); reply.completed = true; reply.finishReason = "stop"
        await update(reply); return reply
    }
    func count() -> Int { requests.count }
}

final class NativeCognitionTests: XCTestCase {
    let lorne = "daylight_lorne", evelyn = "daylight_evelyn"
    var profile: ProviderProfile { ProviderProfile() }
    func privateThread(_ character: String) -> StoryThread {
        StoryThread(name: "谈话", characterIDs: [character], isRoom: false)
    }
    func testAcquiredFragmentAndFalseBeliefStayScopedAcrossEveryRequest() throws {
        var world = StoryWorld(book: try BuiltIn.daylight())
        world.locations.append(["id": "hidden_place", "name": "王后密谋会所", "description": "绝密通道", "geography": ["generated": true, "known_by": [.string(lorne), .string(evelyn)], "known_versions": [evelyn: ["name": "旧屋", "description": "玩家提及旧屋", "route_known": false]], "aliases": ["旧屋"]]])
        world.events = [
            ["id": "secret", "title": "暗号是午夜", "details": "密谋的完整内容", "visibility": "secret", "location_name": "王后密谋会所", "world_time": "秘密日期", "synced": false],
            ["id": "public", "title": "真正的凶手", "details": "客观答案不能泄漏", "visibility": "public", "synced": false]
        ]
        world.knowledge = [
            ["character_id": .string(evelyn), "event_id": "secret", "state": "witnessed", "source": ["role": "assistant", "kind": "witnessed", "quote": "洛恩递出一张纸。"]],
            ["character_id": .string(evelyn), "event_id": "public", "state": "misunderstood", "belief": "她相信守门人无辜", "source": "旧认知"]
        ]
        let knowledge = ContextCompiler.knowledge(world, characterID: evelyn, includePendingPublic: true)
        XCTAssertEqual(knowledge[0]["details"], "洛恩递出一张纸。")
        XCTAssertEqual(knowledge[0]["location_name"], .null)
        XCTAssertEqual(knowledge[1]["details"], "她相信守门人无辜")
        XCTAssertEqual(ContextCompiler.knowledgeProjection(knowledge[0]), knowledge[0])
        let ci = world.characters.firstIndex { $0["id"].string == evelyn }!
        world.characters[ci]["current_location_id"] = "hidden_place"
        world.actions[evelyn] = ["location_id": "hidden_place", "location": "王后密谋会所", "intention": "休息"]
        var thread = privateThread(evelyn); thread.messages = [Message(role: "user", content: "你好。")]
        let context = ContextCompiler.context(world: world, thread: thread, characterID: evelyn)
        XCTAssertEqual(context["character"]["location_name"], "旧屋")
        let requests = [
            ContextCompiler.narrative(context, profile: profile), ContextCompiler.entry(context, profile: profile),
            ContextCompiler.entryReview(context, draft: "她望着窗外。", profile: profile),
            ContextCompiler.adjudication(context, reply: "她点头。", feedback: "", profile: profile),
            ContextCompiler.patchReview(context, reply: "她点头。", patch: [:], profile: profile),
            ContextCompiler.scenePlanning(context, profile: profile),
            ContextCompiler.structured(ContextCompiler.prompt("pulse"), payload: ContextCompiler.pulseInput(world: world, characterID: evelyn, target: "次日"), profile: profile),
            ContextCompiler.cognitionSummary(world: world, characterID: evelyn, profile: profile)
        ]
        let all = requests.map { $0.system + $0.messages.map(\.text).joined() }.joined()
        for secret in ["暗号是午夜", "密谋的完整内容", "绝密通道", "王后密谋会所", "秘密日期", "真正的凶手", "客观答案不能泄漏"] { XCTAssertFalse(all.contains(secret), secret) }
        XCTAssertTrue(all.contains("她相信守门人无辜"))
        let untouched = ContextCompiler.context(world: world, thread: privateThread("daylight_severin"), characterID: "daylight_severin")
        XCTAssertFalse(untouched["locations"].text.contains("hidden_place"))
    }
    func testRoomObservationAndContinuationNeverFallBackToOldOrPrivateInput() throws {
        var world = StoryWorld(book: try BuiltIn.daylight())
        var room = StoryThread(name: "同场", characterIDs: [lorne, evelyn], isRoom: true, locationID: "daylight_throne")
        var old = Message(role: "user", content: "明天见。"); old.observations = [lorne: old.content, evelyn: old.content]
        var answered = Message(role: "assistant", content: "洛恩点头。", characterID: lorne)
        answered.observations = [lorne: answered.content, evelyn: answered.content]
        answered.settlement = ["decision": ["outcome": "rejected"]]
        var whisper = Message(role: "user", content: "只对洛恩耳语：暗号是午夜。")
        whisper.observations = [lorne: whisper.content, evelyn: ""]
        for i in world.characters.indices where [lorne, evelyn].contains(world.characters[i]["id"].string) { world.characters[i]["current_location_id"] = "daylight_throne" }
        room.messages = [old, answered, whisper]
        room.characterScenes[evelyn] = ["physical_state": "坐在窗边"]
        room.characterScenes[lorne] = ["emotional_state": "不能让旁人知道的忧虑"]
        world.threads = [room]; world.playerThreadID = room.id; world.characterRooms = [lorne: room.id, evelyn: room.id]
        world.player["location_id"] = "daylight_throne"
        let context = ContextCompiler.context(world: world, thread: room, characterID: evelyn, currentUserMessageID: whisper.id)
        XCTAssertEqual(ContextCompiler.turnUserText(context), "")
        XCTAssertFalse(ContextCompiler.sourceCatalog(context, reply: "她静静坐着。").object.keys.contains { $0.hasPrefix("user_") })
        XCTAssertEqual(context["scene"]["physical_state"], "坐在窗边")
        let requests = [ContextCompiler.room(context, profile: profile), ContextCompiler.adjudication(context, reply: "她静静坐着。", feedback: "", profile: profile), ContextCompiler.scenePlanning(context, profile: profile)]
        for request in requests {
            let text = request.system + request.messages.map(\.text).joined()
            XCTAssertFalse(text.contains("暗号是午夜")); XCTAssertFalse(text.contains("不能让旁人知道的忧虑"))
        }
        var thread = privateThread(lorne); thread.messages = [old, answered]
        let continuing = ContextCompiler.context(world: world, thread: thread, characterID: lorne, continuation: true)
        XCTAssertEqual(ContextCompiler.turnUserText(continuing), "")
        let narrative = ContextCompiler.narrative(continuing, profile: profile)
        XCTAssertTrue(narrative.system.contains("历史消息序号 [2]"))
        XCTAssertFalse(narrative.messages.map(\.text).joined().contains("状态变化未确认"))
        // A historical room knowledge row cannot bypass missing observations.
        world.events = [["id": "e", "source_message_id": .string(answered.id), "visibility": "secret", "details": "完整秘密"]]
        world.knowledge = [["event_id": "e", "character_id": .string(evelyn), "state": "witnessed", "source": ["role": "assistant", "quote": "洛恩点头。", "kind": "witnessed"]]]
        world.threads[0].messages[1].observations = nil
        XCTAssertTrue(ContextCompiler.knowledge(world, characterID: evelyn).isEmpty)
    }
    func testSingleWireUsesActualSourcesAndRejectsOldFormatAndInventedCitations() throws {
        let world = StoryWorld(book: try BuiltIn.daylight())
        var thread = privateThread(lorne)
        thread.messages = [Message(role: "user", content: "明天我会来。")]
        let context = ContextCompiler.context(world: world, thread: thread, characterID: lorne), reply = "洛恩微微点头。"
        let raw: J = ["relationship": ["trust_delta": 1, "evidence": ["source_id": "assistant_0", "kind": "occurred"]], "player": ["commitments": ["value": ["明天来见洛恩"], "source_id": "user_0", "kind": "expressed"]]]
        let decoded = try ContextCompiler.decodeProposal(raw, context: context, reply: reply)
        XCTAssertEqual(decoded["evidence"]["relationship"]["quote"], .string(reply))
        XCTAssertEqual(decoded["evidence"]["player.commitments"]["role"], "user")
        XCTAssertEqual(decoded["player"]["commitments"], ["明天来见洛恩"])
        XCTAssertNotNil(raw["relationship"]["evidence"].object["source_id"])
        XCTAssertThrowsError(try ContextCompiler.decodeProposal(decoded, context: context, reply: reply))
        var invalid = raw; invalid["relationship"]["evidence"]["source_id"] = "made_up"
        XCTAssertThrowsError(try ContextCompiler.decodeProposal(invalid, context: context, reply: reply)) { error in XCTAssertTrue(error is PatchError) }
        invalid = ["scene": ["location": "金枝厅"]]
        XCTAssertThrowsError(try ContextCompiler.decodeProposal(invalid, context: context, reply: reply))
        var small = profile; small.maxTokens = 256
        let request = ContextCompiler.adjudication(context, reply: reply, feedback: "evidence[\"relationship\"] 未支持关系变化；evidence.player.commitments 无来源", repairProposal: raw, profile: small)
        XCTAssertTrue(request.messages[0]["content"].string.contains("待修复的原始提案"))
        XCTAssertTrue(request.system.contains("relationship.evidence 未支持关系变化"))
        XCTAssertTrue(request.system.contains("player.commitments 的 source_id/kind 无来源"))
        XCTAssertFalse(request.messages[0]["content"].string.contains("evidence.player.commitments"))
        XCTAssertFalse(request.system.contains("附加必需契约"))
        let review = ContextCompiler.patchReview(context, reply: reply, patch: decoded, profile: small)
        XCTAssertTrue(review.thinking); XCTAssertGreaterThanOrEqual(review.maxTokens, 8192)
    }
    func testKnownNameWithoutRouteIsNotPulseDestinationAndPrivateClockHistoryStaysPrivate() throws {
        var world = StoryWorld(book: try BuiltIn.daylight())
        world.locations.append(["id": "place", "name": "王后密谋会所", "description": "秘密路线", "geography": ["generated": true, "known_by": [.string(lorne)], "known_versions": [lorne: ["name": "旧屋", "description": "只听说名字", "route_known": false]]]])
        world.player["commitments"] = [["id": "promise", "character_id": .string(lorne), "text": "秘密约定"]]
        var thread = privateThread(lorne); var message = Message(role: "user", content: "仅时间规划可看的暗语，明日第七声钟。"); message.recordedTime = "前一日夜间"; thread.messages = [message]; world.threads = [thread]
        let input = ContextCompiler.pulseInput(world: world, characterID: lorne, target: "次日第七声钟")
        XCTAssertFalse(input["可选地点"].strings.contains("旧屋"))
        let proposal: J = ["participation": "active", "active_reason": "visible_change", "active_source": "location", "location": "旧屋", "availability": "reachable"]
        XCTAssertEqual(PulseRules.action(proposal, world: world, characterID: lorne, input: input)["participation"], "quiet")
        let clock = ContextCompiler.appointment(world: world, profile: profile)
        XCTAssertTrue(clock.messages[0].text.contains("仅时间规划可看的暗语"))
        XCTAssertFalse(ContextCompiler.pulseInput(world: world, characterID: evelyn, target: "次日第七声钟").text.contains("秘密约定"))
        XCTAssertFalse(ContextCompiler.transitionInput(world: world, actions: [:], target: "次日第七声钟").text.contains("秘密约定"))
        XCTAssertThrowsError(try PulseRules.appointment(["world_time": "次日", "ready_commitment_ids": ["not-real"], "appointment_times": [:]], world: world))
    }
    func testPreviewIsReusedThenConsumedAtomicallyAtItsExactTime() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(newID())
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try WorldStore(url: directory.appendingPathComponent("world.json")), world = await store.snapshot().worlds[0]
        try await store.transact(world.id) { w in
            let room = StoryThread(name: "会面", characterIDs: ["daylight_lorne", "daylight_evelyn"], isRoom: true, locationID: "daylight_throne")
            w.threads.append(room); try w.gatherRoom(threadID: room.id)
            w.player["commitments"] = [["id": "appointment", "character_id": "daylight_lorne", "text": "参加校准"]]
        }
        let movedTo = world.locations.first { $0["id"] != "daylight_throne" }!
        let model = CognitionModel([
            #"{"world_time":"次日第七声钟","ready_commitment_ids":["appointment"],"appointment_times":{"appointment":"次日第七声钟"}}"#,
            J.object(["participation": "active", "active_reason": "visible_change", "active_source": "location", "location": movedTo["name"], "availability": "reachable"]).text,
            #"{"participation":"quiet"}"#,
            #"{"participation":"active","active_reason":"visible_change","active_source":"availability","availability":"unavailable"}"#,
            #"{"participation":"quiet"}"#,
            #"{"world_time":"模型自选错误时段","narration":"灯火亮起。","player_condition_action":"clear","retained_trace_indices":[]}"#,
            #"{"narration":"第七声钟后，灯火亮起。"}"#
        ])
        let engine = StoryEngine(store: store, model: model)
        let preview = try await engine.preview(worldID: world.id, profile: profile, key: "test") { _ in }
        let repeated = try await engine.preview(worldID: world.id, profile: profile, key: "test") { _ in }
        XCTAssertEqual(preview, repeated)
        let count = await model.count(); XCTAssertEqual(count, 1)
        try await engine.pulse(worldID: world.id, previewID: preview["id"].string, profile: profile, key: "test") { _ in }
        let advanced = try await store.world(world.id)
        XCTAssertEqual(advanced.info["current_time"], "次日第七声钟")
        XCTAssertEqual(advanced.player["location_id"], "daylight_throne")
        XCTAssertEqual(advanced.character(lorne)["current_location_id"], movedTo["id"])
        XCTAssertTrue(advanced.characterRooms.isEmpty)
        XCTAssertTrue(advanced.activeMembers(threadID: advanced.threads[0].id).isEmpty)
        XCTAssertTrue(advanced.player["commitments"].array[0]["ready_to_visit"].bool)
        XCTAssertNil(advanced.timePreview)
        do { try await engine.pulse(worldID: world.id, previewID: preview["id"].string, profile: profile, key: "test") { _ in }; XCTFail("Consumed preview must fail") } catch {}
        let restored = try WorldStore(url: directory.appendingPathComponent("world.json")), saved = try await restored.world(world.id)
        XCTAssertEqual(saved, advanced)
    }
}
