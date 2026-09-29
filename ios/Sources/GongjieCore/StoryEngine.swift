import Foundation

public enum EngineEvent: Sendable {
    case changed, accepted(threadID: String, content: String), phase(String), stage(String), stream(threadID: String, messageID: String, text: String), warning(String)
}
public actor StoryEngine {
    public let store: WorldStore
    let model: any LanguageModel
    var busy: Set<String> = []
    private var partials: [String: ModelReply] = [:]
    private var lastCheckpoint: [String: Date] = [:]
    public init(store: WorldStore, model: any LanguageModel = ModelClient()) { self.store = store; self.model = model }
    func begin(_ id: String, recovery: Bool = false) async throws {
        guard !busy.contains(id) else { throw GameError.busy }; busy.insert(id)
        do { let world = try await store.world(id); if !recovery && !world.pending.isEmpty { throw GameError.pending } }
        catch { busy.remove(id); throw error }
    }
    func credentials(_ profile: ProviderProfile, _ key: String) throws { try profile.validate(); if key.trimmed.isEmpty { throw GameError.keyMissing } }
    public func saveDraft(worldID: String, threadID: String, text: String) async throws {
        try await store.transact(worldID, invalidatePreview: false) { world in
            guard let i = world.threads.firstIndex(where: { $0.id == threadID }) else { throw GameError.missing }; world.threads[i].draft = text
        }
    }
    public func savePlayerProfile(worldID: String, profile: J) async throws {
        try await begin(worldID); defer { busy.remove(worldID) }
        try WorldStore.validatePlayerProfile(profile)
        try await store.transact(worldID) { $0.playerProfile = profile }
    }
    @discardableResult public func createSave(worldID: String, name: String) async throws -> WorldSave {
        try await begin(worldID); defer { busy.remove(worldID) }
        return try await store.createSave(worldID: worldID, name: name)
    }
    @discardableResult public func loadSave(worldID: String, saveID: String) async throws -> WorldSave {
        try await begin(worldID); defer { busy.remove(worldID) }
        return try await store.loadSave(worldID: worldID, saveID: saveID)
    }
    public func edit(worldID: String, book: WorldBook) async throws {
        try await begin(worldID); defer { busy.remove(worldID) }
        try WorldStore.validate(book)
        try await store.transact(worldID) { world in
            let oldIDs = Set(world.characters.map { $0["id"].string }), newIDs = Set(book.characters.map { $0["id"].string })
            guard oldIDs.isSubset(of: newIDs) else { throw GameError.invalid("人物已有世界记录，不能从底稿直接删除") }
            let oldTime = world.info["current_time"], started = world.pulse > 0 || !world.events.isEmpty || world.threads.contains { !$0.messages.isEmpty }
            let authoredIDs = Set(book.locations.map { $0["id"].string })
            let oldAuthoredIDs = Set(world.baseline.locations.map { $0["id"].string })
            guard oldAuthoredIDs.isSubset(of: authoredIDs) else { throw GameError.invalid("已有地点不能从创作底稿直接删除") }
            func authoredChanges(_ edited: J, baseline: J, live: J) -> J {
                var result = live.object
                for field in Set(edited.object.keys).union(baseline.object.keys) where edited[field] != baseline[field] {
                    if let value = edited.object[field] { result[field] = value } else { result.removeValue(forKey: field) }
                }
                return .object(result)
            }
            world.info = authoredChanges(book.world, baseline: world.baseline.world, live: world.info)
            world.locations = book.locations.map { place in
                guard let current = world.locations.first(where: { $0["id"] == place["id"] }) else { return place }
                // Edits may change public description, while geographic provenance remains one record.
                let previous = world.baseline.locations.first { $0["id"] == place["id"] } ?? [:]
                var updated = authoredChanges(place, baseline: previous, live: current)
                if current["geography"] != .null { updated["geography"] = current["geography"] }
                return updated
            } + world.locations.filter { !authoredIDs.contains($0["id"].string) && GeographyRules.metadata($0)["generated"].bool }
            world.characters = book.characters.map { card in
                guard started, let current = world.characters.first(where: { $0["id"] == card["id"] }) else { return card }
                let previous = world.baseline.characters.first { $0["id"] == card["id"] } ?? [:]
                return authoredChanges(card, baseline: previous, live: current)
            }
            if !started { world.relationships = book.relationships }
            else { for relation in book.relationships where !world.relationships.contains(where: { $0["from_character_id"] == relation["from_character_id"] && $0["to_user"] == relation["to_user"] && $0["to_character_id"] == relation["to_character_id"] }) { world.relationships.append(relation) } }
            world.baseline = book
            if !started { world.narration = book.world["opening_narration"].string }
            if oldTime != world.info["current_time"] {
                for i in world.threads.indices {
                    world.threads[i].scene["world_time"] = world.info["current_time"]
                    for cid in world.threads[i].characterScenes.keys { world.threads[i].characterScenes[cid]?["world_time"] = world.info["current_time"] }
                }
            }
            world.clearDepartedPresence()
        }
    }
    public func reset(worldID: String, confirmation: String) async throws {
        try await begin(worldID, recovery: true); defer { busy.remove(worldID) }
        try await store.reset(worldID, confirmation: confirmation)
    }
    public func conversation(worldID: String, characterID: String) async throws -> String {
        let initial = try await store.world(worldID)
        if let thread = initial.threads.first(where: { !$0.isRoom && $0.characterIDs == [characterID] }) { return thread.id }
        try await begin(worldID); defer { busy.remove(worldID) }
        guard !initial.character(characterID).object.isEmpty else { throw GameError.missing }
        let thread = StoryThread(name: initial.character(characterID)["name"].string, characterIDs: [characterID], isRoom: false)
        try await store.transact(worldID) { $0.threads.append(thread) }
        return thread.id
    }
    public func room(worldID: String, name: String, locationID: String, characters: [String]) async throws -> String {
        try await begin(worldID); defer { busy.remove(worldID) }
        let world = try await store.world(worldID)
        guard !name.trimmed.isEmpty, name.count <= 60, world.knownLocations(characterID: "player").contains(where: { $0["id"].string == locationID }), !characters.isEmpty, Set(characters).count == characters.count, characters.allSatisfy({ !world.character($0).object.isEmpty && world.availability($0) != "unavailable" }) else { throw GameError.invalid("请选择地点、在场人物，并填写场景名称") }
        let thread = StoryThread(name: name.trimmed, characterIDs: characters, isRoom: true, locationID: locationID)
        try await store.transact(worldID) { world in world.threads.append(thread); try world.gatherRoom(threadID: thread.id) }
        return thread.id
    }
    public func enter(worldID: String, threadID: String, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        try await begin(worldID); defer { busy.remove(worldID) }
        let initial = try await store.world(worldID)
        guard let thread = initial.threads.first(where: { $0.id == threadID }) else { throw GameError.missing }
        let existingEntry = thread.messages.contains { $0.entryPulse == initial.pulse && $0.status == "complete" } || (initial.pulse == 0 && thread.messages.contains { $0.status == "complete" })
        if !thread.isRoom && !existingEntry { try credentials(profile, key) }
        try await store.transact(worldID) { try $0.enterThread(threadID: threadID) }
        await notify(.changed)
        if thread.isRoom || existingEntry { return }
        let world = try await store.world(worldID), active = world.threads.first { $0.id == threadID }!, cid = active.characterIDs[0]
        let context = ContextCompiler.context(world: world, thread: active, characterID: cid)
        await notify(.phase("generating")); await notify(.stage("相遇正在展开"))
        let draft = try await model.generate(ContextCompiler.entry(context, profile: profile), profile: profile, key: key) { _ in }
        try draft.validateComplete()
        await notify(.stage("记忆正在归位"))
        let review = try await model.generate(ContextCompiler.entryReview(context, draft: draft.text, profile: profile), profile: profile, key: key) { _ in }
        try review.validateComplete()
        let result = try parsed(review.text, reason: "entry_review_invalid"), text = result["text"].string.trimmed
        guard !text.isEmpty, Set(result.object.keys) == ["text", "removed"], case .array(let removed) = result["removed"], removed.allSatisfy({ if case .string = $0 { return true }; return false }) else { throw GameError.invalid("相遇没有通过记忆校对，请重试") }
        try Task.checkCancellation()
        var entry = Message(role: "assistant", content: text, characterID: cid); entry.entryPulse = world.pulse; entry.recordedTime = world.info["current_time"].string
        let saved = entry
        try await store.transact(worldID) { world in
            guard let index = world.threads.firstIndex(where: { $0.id == threadID }) else { throw GameError.missing }
            if !world.threads[index].messages.contains(where: { $0.entryPulse == world.pulse && $0.status == "complete" }) { world.threads[index].messages.append(saved) }
        }
        await notify(.changed)
    }
    private func checkpoint(worldID: String, threadID: String, messageID: String, reply: ModelReply) async {
        partials[messageID] = reply
        if Date().timeIntervalSince(lastCheckpoint[messageID] ?? .distantPast) < 0.5 { return }
        lastCheckpoint[messageID] = Date()
        try? await store.transact(worldID, invalidatePreview: false) { world in
            guard let ti = world.threads.firstIndex(where: { $0.id == threadID }), let mi = world.threads[ti].messages.firstIndex(where: { $0.id == messageID }) else { return }
            world.threads[ti].messages[mi].content = reply.text
        }
    }
    public func turn(worldID: String, threadID: String, content: String, continuation: Bool = false, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        try await begin(worldID); defer { busy.remove(worldID) }
        try credentials(profile, key)
        let world = try await store.world(worldID)
        guard let thread = world.threads.first(where: { $0.id == threadID }) else { throw GameError.missing }
        try world.assertCurrent(threadID: threadID)
        guard (!continuation && !content.trimmed.isEmpty) || (continuation && content.trimmed.isEmpty && !thread.isRoom && thread.messages.contains(where: { $0.role == "assistant" && $0.status == "complete" })) else { throw GameError.invalid("先写下这一句") }
        if !thread.isRoom {
            guard thread.messages.contains(where: { $0.entryPulse == world.pulse && $0.status == "complete" }) || (world.pulse == 0 && thread.messages.contains { $0.status == "complete" }) else { throw GameError.invalid("先让这一场相遇展开") }
        }
        await notify(.phase("generating"))
        var currentUserID: String?
        if !continuation {
            var message = Message(role: "user", content: content.trimmed); message.recordedTime = world.info["current_time"].string
            if thread.isRoom { message.observationMembers = world.activeMembers(threadID: threadID) }
            let saved = message; currentUserID = message.id
            try await store.transact(worldID) { world in let index = world.threads.firstIndex { $0.id == threadID }!; world.threads[index].messages.append(saved); world.threads[index].draft = "" }
            await notify(.accepted(threadID: threadID, content: content)); await notify(.changed)
        }
        if thread.isRoom {
            try await ensureObservations(worldID: worldID, threadID: threadID, profile: profile, key: key)
            try await roomTurn(worldID: worldID, threadID: threadID, currentUserID: currentUserID!, profile: profile, key: key, notify: notify)
            return
        }
        let updated = try await store.world(worldID), active = updated.threads.first { $0.id == threadID }!, cid = thread.characterIDs[0]
        let base = ContextCompiler.context(world: updated, thread: active, characterID: cid, continuation: continuation, currentUserMessageID: currentUserID)
        let context = try await prepareScene(world: updated, context: base, profile: profile, key: key)
        let request = ContextCompiler.narrative(context, profile: profile, continuation: continuation)
        if context["scene_plan"]["mode"] == "resolve" {
            await notify(.stage("正在回应"))
            let (reply, text, prepared) = try await destinationReply(context: context, request: request, isRoom: false, profile: profile, key: key, notify: notify)
            guard let text else { throw GameError.incomplete }
            try await publish(worldID: worldID, threadID: threadID, characterID: cid, content: text, reply: reply, context: prepared, profile: profile, key: key, notify: notify)
            return
        }
        var message = Message(role: "assistant", content: "", characterID: cid); message.status = "streaming"; message.recordedTime = updated.info["current_time"].string
        let streaming = message, messageID = message.id
        try await store.transact(worldID, invalidatePreview: false) { world in let index = world.threads.firstIndex { $0.id == threadID }!; world.threads[index].messages.append(streaming) }
        await notify(.changed); await notify(.stage("正在回应"))
        var completed = false
        defer { partials.removeValue(forKey: messageID); lastCheckpoint.removeValue(forKey: messageID) }
        do {
            let reply = try await model.generate(request, profile: profile, key: key) { reply in
                await self.checkpoint(worldID: worldID, threadID: threadID, messageID: messageID, reply: reply)
                await notify(.stream(threadID: threadID, messageID: messageID, text: reply.text))
            }
            try reply.validateComplete(); try Task.checkCancellation()
            let job = SettlementJob(id: messageID, threadID: threadID, characterID: cid, reply: reply.text, context: context)
            try await store.transact(worldID) { world in
                let ti = world.threads.firstIndex { $0.id == threadID }!, mi = world.threads[ti].messages.firstIndex { $0.id == messageID }!
                world.threads[ti].messages[mi].content = reply.text; world.threads[ti].messages[mi].status = "complete"; world.threads[ti].messages[mi].usage = reply.usage; world.pending.append(job)
            }
            completed = true; await notify(.changed); await notify(.phase("settling")); await notify(.stage("世界正在留下痕迹"))
            try await settle(worldID: worldID, sourceID: messageID, profile: profile, key: key)
            await notify(.changed)
        } catch {
            if !completed {
                let partial = partials[messageID]?.text ?? "", cancelled = Task.isCancelled || error is CancellationError
                try await store.transact(worldID) { world in
                    let ti = world.threads.firstIndex { $0.id == threadID }!
                    if let mi = world.threads[ti].messages.firstIndex(where: { $0.id == messageID }) {
                        if partial.isEmpty { world.threads[ti].messages.remove(at: mi) }
                        else { world.threads[ti].messages[mi].content = partial; world.threads[ti].messages[mi].status = cancelled ? "cancelled" : "error" }
                    }
                }
            }
            await notify(.changed); throw error
        }
    }
    private func publish(worldID: String, threadID: String, characterID: String, content: String, reply: ModelReply, context: J, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        try Task.checkCancellation()
        var message = Message(role: "assistant", content: content, characterID: characterID); message.recordedTime = context["world"]["current_time"].string; message.usage = reply.usage
        if context["room"] != .null {
            message.observationMembers = context["room_characters"].array.map { $0["id"].string }
            guard context["reply_observations"]["reply"].string == content, Set(context["reply_observations"]["views"].object.keys) == Set(message.observationMembers) else { throw ProviderFailure("room_observation", "回应缺少经过核对的在场视角") }
            message.observations = context["reply_observations"]["views"].object.mapValues(\.string)
        }
        let saved = message, job = SettlementJob(id: message.id, threadID: threadID, characterID: characterID, reply: content, context: context)
        try await store.transact(worldID) { world in
            guard let index = world.threads.firstIndex(where: { $0.id == threadID }) else { throw GameError.missing }
            world.threads[index].messages.append(saved); world.pending.append(job)
        }
        await notify(.changed)
        if try await store.world(worldID).pending.first?.id == job.id {
            await notify(.phase("settling")); await notify(.stage("世界正在留下痕迹"))
            try await settle(worldID: worldID, sourceID: job.id, profile: profile, key: key)
            await notify(.changed)
        }
    }
    private func roomTurn(worldID: String, threadID: String, currentUserID: String, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        let initial = try await store.world(worldID), initialMembers = initial.activeMembers(threadID: threadID)
        for cid in initialMembers {
            try Task.checkCancellation()
            let current = try await store.world(worldID), thread = current.threads.first { $0.id == threadID }!
            // A previous speaker may actually have left with the player.
            guard current.playerThreadID == threadID, current.activeMembers(threadID: threadID).contains(cid) else { continue }
            let base = ContextCompiler.context(world: current, thread: thread, characterID: cid, currentUserMessageID: currentUserID)
            await notify(.phase("generating")); await notify(.stage("\(current.character(cid)["name"].string)正在斟酌"))
            do {
                let context = try await prepareScene(world: current, context: base, profile: profile, key: key), request = ContextCompiler.room(context, profile: profile)
                if context["scene_plan"]["mode"] == "resolve" {
                    let (reply, text, prepared) = try await destinationReply(context: context, request: request, isRoom: true, profile: profile, key: key, notify: notify)
                    if let text { try await publish(worldID: worldID, threadID: threadID, characterID: cid, content: text, reply: reply, context: prepared, profile: profile, key: key, notify: notify) }
                } else {
                    let reply = try await model.generate(request, profile: profile, key: key) { _ in }
                    try reply.validateComplete()
                    if let text = try roomReply(reply.text) {
                        let observed = try await observeReply(context: context, reply: text, profile: profile, key: key)
                        try await publish(worldID: worldID, threadID: threadID, characterID: cid, content: text, reply: reply, context: observed, profile: profile, key: key, notify: notify)
                    }
                }
            } catch {
                if Task.isCancelled || error is CancellationError { throw CancellationError() }
                await notify(.warning("\(current.character(cid)["name"].string)：\(error.localizedDescription)")); await notify(.changed)
            }
        }
    }
    private func settle(worldID: String, sourceID: String, profile: ProviderProfile, key: String) async throws {
        let world = try await store.world(worldID)
        guard let job = world.pending.first, job.id == sourceID else { throw GameError.pending }
        do {
            let patch: J
            let prepared = job.context["prepared_patch"]
            if prepared != .null {
                guard prepared["reply"].string == job.reply, case .object = prepared["patch"] else { throw PatchError("prepared_patch_source_mismatch") }
                patch = try PatchValidator.validate(prepared["patch"], context: job.context, reply: job.reply)
            } else { patch = try await reviewedPatch(context: job.context, reply: job.reply, profile: profile, key: key) }
            try Task.checkCancellation()
            try await store.transact(worldID) { world in try PatchValidator.apply(patch, job: job, to: &world) }
        } catch let error as PatchError {
            try await store.transact(worldID) { world in try PatchValidator.reject(job: job, reason: error.reason, issues: error.issues, to: &world) }
        } catch {
            let failure = error is CancellationError ? "记录被中断，可以重新记录" : error.localizedDescription
            try await store.transact(worldID) { world in if let index = world.pending.firstIndex(where: { $0.id == sourceID }) { world.pending[index].failure = failure } }
            throw error
        }
    }
    public func recover(worldID: String, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws {
        try await begin(worldID, recovery: true); defer { busy.remove(worldID) }
        try credentials(profile, key)
        await notify(.phase("recovering")); await notify(.stage("正在重新记录"))
        while let job = try await store.world(worldID).pending.first {
            if try await store.world(worldID).threads.first(where: { $0.id == job.threadID })?.isRoom == true {
                try await ensureObservations(worldID: worldID, threadID: job.threadID, profile: profile, key: key)
                let current = try await store.world(worldID), frozen = try recoveryContext(world: current, job: job)
                try await store.transact(worldID) { world in guard let index = world.pending.firstIndex(where: { $0.id == job.id }) else { throw GameError.missing }; world.pending[index].context = frozen }
            }
            try await settle(worldID: worldID, sourceID: job.id, profile: profile, key: key)
            await notify(.changed)
        }
    }
}
