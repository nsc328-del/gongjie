import XCTest
@testable import GongjieCore

final class NativeStateTests: XCTestCase {
    let lorne = "daylight_lorne", evelyn = "daylight_evelyn"
    var directory: URL!
    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(newID())
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
    func sceneWorld() throws -> (StoryWorld, String) {
        var world = StoryWorld(book: try BuiltIn.daylight())
        let room = StoryThread(name: "临时会面", characterIDs: [lorne, evelyn], isRoom: true, locationID: "daylight_throne")
        world.threads.append(room); try world.gatherRoom(threadID: room.id)
        return (world, room.id)
    }
    func context(_ world: StoryWorld, roomID: String, user: String, reply: String) -> J {
        let room = world.threads.first { $0.id == roomID }!
        let playerMessage = Message(role: "user", content: user)
        var info = world.info; info["id"] = .string(world.id)
        var scene: J = ["location_id": .string(room.locationID!), "world_time": info["current_time"], "tension": 20, "active_threads": ["桌上有一封待拆的信"], "physical_state": "坐在桌前", "emotional_state": "平静"]
        scene["location_name"] = .string(world.locationName(room.locationID))
        return ["world": info, "character": world.character(lorne), "room": ["id": .string(roomID)], "room_characters": .array(world.characters.filter { room.characterIDs.contains($0["id"].string) }), "scene": scene, "player": world.player, "relationships": [world.relationship(lorne)], "locations": .array(world.knownLocations(characterID: lorne)), "messages": [playerMessage.contextValue], "current_user_message_id": .string(playerMessage.id), "observation_sources": [.init(stringLiteral: lorne): ["user": .string(user), "assistant": .string(reply)], .init(stringLiteral: evelyn): ["user": .string(user), "assistant": ""]]]
    }
    func enqueue(_ world: inout StoryWorld, roomID: String, reply: String, context: J) -> SettlementJob {
        var message = Message(role: "assistant", content: reply, characterID: lorne)
        message.observationMembers = [lorne, evelyn]; message.observations = [lorne: reply, evelyn: ""]
        let job = SettlementJob(id: message.id, threadID: roomID, characterID: lorne, reply: reply, context: context)
        world.threads[0].messages.append(message); world.pending.append(job)
        return job
    }
    func testCompleteCheckpointIsolationAtomicRestoreAndLegacyMigration() async throws {
        let url = directory.appendingPathComponent("archive.json"), store = try WorldStore(url: url)
        var world = await store.snapshot().worlds[0]
        let room = StoryThread(name: "冬园", characterIDs: [lorne, evelyn], isRoom: true, locationID: "daylight_winter")
        world.threads.append(room)
        // Use an actual authored location; the checkpoint includes its complete geographic provenance.
        world.threads[0].locationID = "daylight_throne"; try world.gatherRoom(threadID: room.id)
        world.threads[0].characterScenes[lorne] = ["emotional_state": "只有洛恩自己的心情"]
        var message = Message(role: "assistant", content: "他公开点头。随后他只向你低语：暗号蓝鸢。", characterID: lorne)
        message.observationMembers = [lorne, evelyn]; message.observations = [lorne: message.content, evelyn: "他公开点头。"]
        message.settlement = ["scene": ["physical_state": "抬起手"], "decision": ["outcome": "accepted", "reason": "", "issues": []]]
        world.threads[0].messages = [message]
        world.playerProfile = ["name": "测试旅人", "age": 24]
        world.timePreview = ["id": "preview-old", "revision": 0]
        let initial = world
        try await store.transact(world.id) { $0 = initial }
        let saved = try await store.createSave(worldID: world.id, name: "进入冬园之前")
        let otherID = try await store.add(book: world.baseline)
        let otherSaves = await store.saves(worldID: otherID); XCTAssertTrue(otherSaves.isEmpty)
        do { try await store.loadSave(worldID: otherID, saveID: saved.id); XCTFail("Cannot cross world") } catch GameError.missing {}
        var profile = ProviderProfile(); profile.model = "another-model"; try await store.saveProfile(profile)
        try await store.transact(world.id) { value in value.info["current_time"] = "下一日"; value.threads[0].messages.removeAll(); value.playerProfile["name"] = "改名后" }
        let before = try await store.world(world.id)
        let backup = try await store.loadSave(worldID: world.id, saveID: saved.id)
        let restored = try await store.world(world.id), archive = await store.snapshot()
        XCTAssertTrue(backup.automatic); XCTAssertEqual(backup.world.info["current_time"], "下一日")
        XCTAssertEqual(restored.threads, saved.world.threads); XCTAssertEqual(restored.playerProfile, saved.world.playerProfile)
        XCTAssertEqual(restored.characterRooms, saved.world.characterRooms); XCTAssertEqual(restored.playerThreadID, saved.world.playerThreadID)
        XCTAssertNil(restored.timePreview); XCTAssertGreaterThan(restored.stateRevision, before.stateRevision)
        XCTAssertEqual(archive.profile.model, "another-model"); XCTAssertEqual(archive.saves.count, 2)
        // Synthesize the previous document shape, retaining its history and decisions.
        var legacy = try JSONDecoder().decode(J.self, from: JSONEncoder().encode(archive)); legacy["version"] = 1
        var worlds = legacy["worlds"].array
        var raw = worlds[0].object
        for key in ["playerThreadID", "characterRooms", "timePreview", "stateRevision", "playerProfile"] { raw.removeValue(forKey: key) }
        var threads = raw["threads"]!.array, rawThread = threads[0].object, messages = rawThread["messages"]!.array, rawMessage = messages[0].object
        rawMessage.removeValue(forKey: "observations"); rawMessage.removeValue(forKey: "observationMembers")
        var settlement = rawMessage["settlement"]!.object; settlement.removeValue(forKey: "decision"); rawMessage["settlement"] = .object(settlement)
        messages[0] = .object(rawMessage); rawThread["messages"] = .array(messages); rawThread.removeValue(forKey: "characterScenes")
        threads[0] = .object(rawThread); raw["threads"] = .array(threads); worlds[0] = .object(raw); legacy["worlds"] = .array(worlds)
        let legacyURL = directory.appendingPathComponent("legacy.json")
        try JSONEncoder().encode(legacy).write(to: legacyURL)
        let migrated = try WorldStore(url: legacyURL), migration = await migrated.snapshot()
        XCTAssertEqual(migration.version, 2); XCTAssertEqual(migration.worlds[0].threads[0].messages[0].content, message.content)
        XCTAssertNil(migration.worlds[0].threads[0].messages[0].observations)
        XCTAssertEqual(migration.worlds[0].threads[0].messages[0].settlement?["decision"]["outcome"], "accepted")
        XCTAssertEqual(migration.worlds[0].threads[0].characterScenes[lorne]?["physical_state"], "抬起手")
    }
    func testFrozenRepeatedSnapshotCannotUndoEarlierMovementAndClock() throws {
        var (world, roomID) = try sceneWorld()
        world.player["condition"] = "袖口干净"; world.player["visible_traces"] = []
        let reply = "洛恩点了点头。", user = "我答应现在协助你。"
        let frozen = context(world, roomID: roomID, user: user, reply: reply)
        let job = enqueue(&world, roomID: roomID, reply: reply, context: frozen)
        let nextLocation = world.locations.first { $0["id"].string != "daylight_throne" }!["id"]
        world.player["location_id"] = nextLocation; world.player["condition"] = "袖口沾墨"; world.player["visible_traces"] = ["一滴墨"]
        world.characters[world.characters.firstIndex { $0["id"].string == lorne }!]["current_location_id"] = nextLocation
        world.info["current_time"] = "次日晨钟后"
        var patch: J = ["scene": frozen["scene"].selecting(["location_id", "world_time", "tension", "active_threads", "physical_state", "emotional_state"]), "player": frozen["player"].selecting(["location_id", "condition", "visible_traces"]), "relationship": ["trust_delta": 0, "label": world.relationship(lorne)["label"], "note": world.relationship(lorne)["notes"]]]
        // The fixture only supplies defined string fields, even when the authored note is absent.
        if patch["relationship"]["note"] == .null { var r = patch["relationship"].object; r.removeValue(forKey: "note"); patch["relationship"] = .object(r) }
        let noop = try PatchValidator.validate(patch, context: frozen, reply: reply)
        XCTAssertFalse(PatchValidator.hasChanges(noop))
        patch["relationship"]["trust_delta"] = 1
        patch["evidence"]["relationship"] = ["role": "user", "quote": .string(user), "kind": "expressed"]
        let speaker = world.character(lorne)["name"].string
        patch["event"] = ["title": "洛恩点头回应", "details": .string(reply), "visibility": "private", "participants": .strings([speaker]), "knowledge_sources": .object([speaker: ["role": "assistant", "quote": .string(reply), "kind": "witnessed"]])]
        patch["evidence"]["event"] = ["role": "assistant", "quote": .string(reply), "kind": "occurred"]
        let trust = world.relationship(lorne)["trust"].int
        try PatchValidator.apply(patch, job: job, to: &world)
        XCTAssertEqual(world.player["location_id"], nextLocation); XCTAssertEqual(world.player["condition"], "袖口沾墨")
        XCTAssertEqual(world.character(lorne)["current_location_id"], nextLocation)
        XCTAssertEqual(world.info["current_time"], "次日晨钟后"); XCTAssertEqual(world.threads[0].scene["world_time"], "次日晨钟后")
        XCTAssertEqual(world.relationship(lorne)["trust"].int, trust + 1)
        XCTAssertTrue(world.threads[0].scene["emotional_state"] == .null)
        XCTAssertEqual(world.events.last?["world_time"], frozen["world"]["current_time"])
    }
    func testWhisperCannotGrantRoomKnowledgeAndRejectionClosesQueue() throws {
        var (world, roomID) = try sceneWorld()
        let publicText = "洛恩递来纸条。", secret = "纸上写着口令蓝鸢。", reply = publicText + secret
        let frozen = context(world, roomID: roomID, user: "给我看看。", reply: reply)
        let job = enqueue(&world, roomID: roomID, reply: reply, context: frozen)
        let name = world.character(lorne)["name"].string, otherName = world.character(evelyn)["name"].string
        var proposal: J = ["event": ["title": "递来纸条", "details": .string(reply), "visibility": "secret", "participants": .strings([name, otherName]), "knowledge_sources": .object([name: ["role": "assistant", "quote": .string(reply), "kind": "witnessed"], otherName: ["role": "assistant", "quote": .string(secret), "kind": "witnessed"]])], "evidence": ["event": ["role": "assistant", "quote": .string(reply), "kind": "occurred"]]]
        XCTAssertThrowsError(try PatchValidator.validate(proposal, context: frozen, reply: reply)) { error in XCTAssertEqual((error as? PatchError)?.reason, "knowledge_source_not_observed") }
        proposal["event"]["participants"] = .strings([name]); proposal["event"]["knowledge_sources"] = .object([name: ["role": "assistant", "quote": .string(reply), "kind": "witnessed"]])
        XCTAssertNoThrow(try PatchValidator.validate(proposal, context: frozen, reply: reply))
        try PatchValidator.reject(job: job, reason: "hard_state_not_supported", issues: ["只有传闻，不能确认为真"], to: &world)
        let after = world
        try PatchValidator.apply(proposal, job: job, to: &world)
        XCTAssertEqual(world, after); XCTAssertTrue(world.pending.isEmpty); XCTAssertTrue(world.events.isEmpty)
        XCTAssertEqual(world.threads[0].messages.last?.settlement?["decision"]["outcome"], "rejected")
        // A conversation continuation cannot reuse the previous explicit promise.
        var continuing = frozen; continuing["continuation"] = true
        XCTAssertThrowsError(try PatchValidator.validate(["player": ["commitments": ["给我看看。"]], "evidence": ["player.commitments": ["role": "user", "quote": "给我看看。", "kind": "expressed"]]], context: continuing, reply: reply))
    }
    func testPlaceRegistrationAliasKnowledgeAndTravelAreAtomic() throws {
        var (world, roomID) = try sceneWorld()
        let user = "明天在旧渡口见。", reply = "洛恩指了指宫墙外：旧渡口就在河湾边，明天可以在那里见面。"
        var frozen = context(world, roomID: roomID, user: user, reply: reply)
        let raw: J = ["mode": "resolve", "purpose": "appointment", "source_quote": .string(user), "new_location": ["name": "旧渡口", "parent_id": .string(world.id), "description": "宫墙外河湾边的石阶。"]]
        var plan = try GeographyRules.plan(raw, context: frozen)
        XCTAssertEqual(plan["target"]["id"], try GeographyRules.plan(raw, context: frozen)["target"]["id"])
        XCTAssertThrowsError(try GeographyRules.resolution(["confirmed": true, "arrived": true, "quote": .string(reply)], plan: plan, reply: reply))
        plan["resolution"] = try GeographyRules.resolution(["confirmed": true, "arrived": false, "quote": .string(reply)], plan: plan, reply: reply)
        frozen["scene_plan"] = plan
        let job = enqueue(&world, roomID: roomID, reply: reply, context: frozen), originalLocation = world.player["location_id"]
        let count = world.locations.count
        var bad = frozen; bad["location_mentions"] = [["id": "missing", "name": "虚构", "character_id": .string(lorne), "quote": .string(user), "role": "user"]]
        var invalidJob = job; invalidJob.context = bad
        XCTAssertThrowsError(try PatchValidator.apply([:], job: invalidJob, to: &world))
        XCTAssertEqual(world.locations.count, count); XCTAssertEqual(world.pending.count, 1)
        try PatchValidator.apply([:], job: job, to: &world)
        XCTAssertEqual(world.locations.count, count + 1); XCTAssertEqual(world.player["location_id"], originalLocation)
        XCTAssertFalse(world.knownLocations(characterID: evelyn).contains { $0["id"] == plan["target"]["id"] })
        let index = world.locations.firstIndex { $0["id"] == plan["target"]["id"] }!
        world.locations[index]["geography"]["aliases"] = ["雾阶"]
        world.locations[index]["description"] = "秘密登记的完整路线。"
        var listener = context(world, roomID: roomID, user: "有人提过雾阶。", reply: "伊芙琳记住了这个名字。")
        listener["character"] = world.character(evelyn); listener["locations"] = .array(world.knownLocations(characterID: evelyn))
        listener = GeographyRules.mentionedLocations(world: world, context: listener)
        XCTAssertEqual(listener["location_mentions"].array.count, 1)
        var heardPlan = try GeographyRules.plan(["mode": "resolve", "purpose": "information", "source_quote": "有人提过雾阶。", "target_id": plan["target"]["id"]], context: listener)
        heardPlan["resolution"] = try GeographyRules.resolution(["confirmed": true, "arrived": false, "quote": "伊芙琳记住了这个名字。"], plan: heardPlan, reply: "伊芙琳记住了这个名字。")
        listener["scene_plan"] = heardPlan
        try world.commitGeography(context: listener, sourceID: "source-told", reply: "伊芙琳记住了这个名字。")
        let known = world.knownLocations(characterID: evelyn).first { $0["id"] == plan["target"]["id"] }!
        XCTAssertEqual(known["name"], "雾阶"); XCTAssertFalse(known.text.contains("秘密登记")); XCTAssertFalse(known.text.contains("旧渡口"))
        XCTAssertEqual(known["geography"]["route_known"], false)
        let travelUser = "带我去旧渡口。", arrival = "洛恩领着你来到旧渡口的石阶旁。"
        var travel = context(world, roomID: roomID, user: travelUser, reply: arrival)
        var travelPlan = try GeographyRules.plan(["mode": "resolve", "purpose": "travel", "source_quote": .string(travelUser), "target_id": plan["target"]["id"]], context: travel)
        travelPlan["resolution"] = try GeographyRules.resolution(["confirmed": true, "arrived": true, "quote": .string(arrival)], plan: travelPlan, reply: arrival)
        travel["scene_plan"] = travelPlan
        let arrivalJob = enqueue(&world, roomID: roomID, reply: arrival, context: travel)
        try PatchValidator.apply(GeographyRules.locationPatch([:], context: travel), job: arrivalJob, to: &world)
        XCTAssertEqual(world.player["location_id"], plan["target"]["id"])
        XCTAssertEqual(world.character(lorne)["current_location_id"], plan["target"]["id"])
        XCTAssertEqual(world.character(evelyn)["current_location_id"], originalLocation)
        XCTAssertEqual(world.threads[0].locationID, originalLocation.string)
        XCTAssertEqual(world.activeMembers(threadID: roomID), [evelyn])
        // Leaving a room does not recall or move the other member when the old room is reopened.
        let privateThread = StoryThread(name: "洛恩", characterIDs: [lorne], isRoom: false)
        world.threads.append(privateThread); try world.enterThread(threadID: privateThread.id)
        XCTAssertEqual(world.activeMembers(threadID: roomID), [evelyn])
        try world.enterThread(threadID: roomID)
        XCTAssertEqual(world.activeMembers(threadID: roomID), [evelyn])
    }
}
