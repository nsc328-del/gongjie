import Foundation

extension StoryEngine {
    func parsed(_ text: String, reason: String = "proposal_json") throws -> J {
        do { return try J.parse(text) }
        catch { throw PatchError(reason, issues: ["模型必须返回完整 JSON 对象。"] ) }
    }
    func checkVerdict(_ verdict: J, resolution: Bool = false) throws {
        let fields: Set<String> = resolution ? ["approved", "issues", "resolution"] : ["approved", "issues"]
        guard Set(verdict.object.keys) == fields, case .bool(let approved) = verdict["approved"], case .array(let issues) = verdict["issues"], issues.allSatisfy({ if case .string = $0 { return true }; return false }) else { throw PatchError("review_invalid") }
        if !approved || !issues.isEmpty { throw PatchError("hard_state_not_supported", issues: issues.map(\.string)) }
    }
    func prepareScene(world: StoryWorld, context input: J, profile: ProviderProfile, key: String) async throws -> J {
        var context = GeographyRules.mentionedLocations(world: world, context: input)
        var feedback = ""
        for attempt in 0..<2 {
            do {
                let response = try await model.generate(ContextCompiler.scenePlanning(context, feedback: feedback, profile: profile), profile: profile, key: key) { _ in }
                try response.validateComplete()
                let plan = try GeographyRules.plan(parsed(response.text, reason: "scene_plan_json"), context: context)
                try GeographyRules.checkPlan(plan, world: world)
                if plan["create"].bool {
                    let review = try await model.generate(ContextCompiler.scenePlanReview(context, plan: plan, profile: profile), profile: profile, key: key) { _ in }
                    try review.validateComplete(); try checkVerdict(parsed(review.text))
                }
                context["scene_plan"] = plan
                if plan["create"].bool { context["locations"] = .array(context["locations"].array + [plan["target"].merging(["world_id": .string(world.id)])]) }
                return context
            } catch let error as PatchError {
                feedback = ([error.reason] + error.issues).joined(separator: "；")
                if attempt == 1 { throw ProviderFailure("scene_plan", "这次去向尚未理清，请再试一次") }
            }
        }
        throw ProviderFailure("scene_plan", "这次去向尚未理清，请再试一次")
    }
    func roomReply(_ text: String) throws -> String? {
        let value = try parsed(text, reason: "room_reply_invalid")
        if value == ["silent": true] { return nil }
        guard Set(value.object.keys) == ["reply"], case .string(let reply) = value["reply"], !reply.trimmed.isEmpty else { throw PatchError("room_reply_invalid", issues: ["开口只能返回 reply，沉默只能返回 silent:true。"] ) }
        return reply
    }
    func observeMessage(content: String, authorID: String?, members: [J], profile: ProviderProfile, key: String) async throws -> [String: String] {
        let ids = members.map { $0["id"].string }
        guard ids.count == Set(ids).count, ids.allSatisfy({ !$0.isEmpty }), members.allSatisfy({ !$0["name"].string.isEmpty }), authorID == nil || ids.contains(authorID!) else { throw PatchError("observation_members_invalid") }
        if ids.isEmpty || content.trimmed.isEmpty { return Dictionary(uniqueKeysWithValues: ids.map { ($0, "") }) }
        let payload: J = ["content": .string(content), "author_id": authorID.map(J.string) ?? .null, "members": .array(members.map { $0.selecting(["id", "name"]) })]
        var feedback = ""
        for attempt in 0..<2 {
            do {
                let response = try await model.generate(ContextCompiler.observationRequest(payload: payload, feedback: feedback, profile: profile), profile: profile, key: key) { _ in }
                try response.validateComplete()
                let proposed = try parsed(response.text, reason: "observation_json")
                guard Set(proposed.object.keys) == Set(ids) else { throw PatchError("observation_recipients", issues: ["必须且只能包含本条发生时的完整成员 ID。"] ) }
                var observations: [String: [String]] = [:]
                for id in ids {
                    let entry = proposed[id]
                    guard Set(entry.object.keys) == ["quotes"], case .array(let items) = entry["quotes"], items.allSatisfy({ if case .string(let quote) = $0 { return !quote.trimmed.isEmpty }; return false }) else { throw PatchError("observation_quotes_invalid") }
                    let quotes = items.map(\.string)
                    guard Set(quotes).count == quotes.count else { throw PatchError("observation_quotes_duplicate") }
                    var cursor = content.startIndex
                    for quote in quotes {
                        guard let range = content.range(of: quote, range: cursor..<content.endIndex) else { throw PatchError("observation_quotes_not_verbatim", issues: ["片段须逐字存在、按原顺序排列且不交叉。"] ) }
                        cursor = range.upperBound
                    }
                    observations[id] = quotes
                }
                if let authorID { observations[authorID] = [content] }
                var reviewPayload = payload
                reviewPayload["observations"] = .object(observations.mapValues { ["quotes": .strings($0)] })
                let review = try await model.generate(ContextCompiler.observationReview(payload: reviewPayload, profile: profile), profile: profile, key: key) { _ in }
                try review.validateComplete(); try checkVerdict(parsed(review.text, reason: "observation_review_invalid"))
                return observations.mapValues { $0.joined(separator: "\n") }
            } catch let error as PatchError {
                feedback = ([error.reason] + error.issues).joined(separator: "；")
                if attempt == 1 { throw ProviderFailure("room_observation", "这段话的在场视角尚未理清，请再试一次") }
            }
        }
        throw ProviderFailure("room_observation", "这段话的在场视角尚未理清，请再试一次")
    }
    func ensureObservations(worldID: String, threadID: String, profile: ProviderProfile, key: String) async throws {
        let world = try await store.world(worldID)
        guard let thread = world.threads.first(where: { $0.id == threadID }), thread.isRoom else { return }
        for message in thread.messages where message.status == "complete" && message.observations == nil {
            try Task.checkCancellation()
            let members = message.observationMembers.map { world.character($0).selecting(["id", "name"]) }
            let views = try await observeMessage(content: message.content, authorID: message.characterID, members: members, profile: profile, key: key)
            try await store.transact(worldID) { current in
                guard let ti = current.threads.firstIndex(where: { $0.id == threadID }), let mi = current.threads[ti].messages.firstIndex(where: { $0.id == message.id }), current.threads[ti].messages[mi].content == message.content else { throw GameError.missing }
                current.threads[ti].messages[mi].observations = views
            }
        }
    }
    func observeReply(context: J, reply: String, profile: ProviderProfile, key: String) async throws -> J {
        guard context["room"] != .null else { return context }
        let views = try await observeMessage(content: reply, authorID: context["character"]["id"].string, members: context["room_characters"].array, profile: profile, key: key)
        var result = context
        result["reply_observations"] = ["reply": .string(reply), "views": .object(views.mapValues(J.string))]
        for (id, view) in views { result["observation_sources"][id]["assistant"] = .string(view) }
        return result
    }
    func reviewedPatch(context: J, reply: String, profile: ProviderProfile, key: String) async throws -> J {
        var feedback = "", repair: J?
        for attempt in 0..<2 {
            do {
                let response = try await model.generate(ContextCompiler.adjudication(context, reply: reply, feedback: feedback, repairProposal: repair, profile: profile), profile: profile, key: key) { _ in }
                try response.validateComplete()
                let raw = try parsed(response.text)
                repair = raw
                let domain = try ContextCompiler.decodeProposal(raw, context: context, reply: reply)
                let patch = try PatchValidator.validate(GeographyRules.locationPatch(domain, context: context), context: context, reply: reply)
                if PatchValidator.hasChanges(patch) {
                    let review = try await model.generate(ContextCompiler.patchReview(context, reply: reply, patch: patch, profile: profile), profile: profile, key: key) { _ in }
                    try review.validateComplete(); try checkVerdict(parsed(review.text, reason: "review_invalid"))
                }
                return patch
            } catch let error as PatchError {
                if attempt == 1 { throw error }
                feedback = ([error.reason] + error.issues).joined(separator: "；")
            }
        }
        throw PatchError("proposal_invalid")
    }
    func destinationReply(context input: J, request: ModelRequest, isRoom: Bool, profile: ProviderProfile, key: String, notify: @escaping @Sendable (EngineEvent) async -> Void) async throws -> (ModelReply, String?, J) {
        var context = input, feedback = "", acceptedReply: ModelReply?, acceptedText: String?
        for attempt in 0..<2 {
            var retryRequest = request; retryRequest.system += feedback
            let candidate = try await model.generate(retryRequest, profile: profile, key: key) { _ in }
            try candidate.validateComplete()
            do {
                guard let text = try isRoom ? roomReply(candidate.text) : candidate.text else { return (candidate, nil, context) }
                let review = try await model.generate(ContextCompiler.sceneReplyReview(context, reply: text, profile: profile), profile: profile, key: key) { _ in }
                try review.validateComplete()
                let verdict = try parsed(review.text, reason: "scene_reply_review_invalid"); try checkVerdict(verdict, resolution: true)
                let cited = verdict["resolution"]
                guard Set(cited.object.keys) == ["confirmed", "arrived", "source_id"], case .string(let id) = cited["source_id"] else { throw PatchError("location_resolution_invalid") }
                let paragraphs = Dictionary(uniqueKeysWithValues: text.components(separatedBy: "\n").enumerated().filter { !$0.element.trimmed.isEmpty }.map { ("p\($0.offset)", $0.element) })
                if cited["confirmed"].bool && paragraphs[id] == nil { throw PatchError("location_resolution_source") }
                context["scene_plan"]["resolution"] = try GeographyRules.resolution(["confirmed": cited["confirmed"], "arrived": cited["arrived"], "quote": .string(paragraphs[id] ?? "")], plan: context["scene_plan"], reply: text)
                acceptedReply = candidate; acceptedText = text; break
            } catch let error as PatchError {
                if attempt == 1 { throw ProviderFailure("scene_reply", "这段回应尚未衔接好，请再试一次") }
                feedback = "\n待发布正文未通过校对，请重新生成完整正文，修正这些问题：" + ([error.reason] + error.issues).joined(separator: "；")
            }
        }
        guard let candidate = acceptedReply, let text = acceptedText else { throw ProviderFailure("scene_reply", "这段回应尚未衔接好，请再试一次") }
        context = try await observeReply(context: context, reply: text, profile: profile, key: key)
        await notify(.phase("settling")); await notify(.stage("世界正在留下痕迹"))
        do {
            let patch = try await reviewedPatch(context: context, reply: text, profile: profile, key: key)
            context["prepared_patch"] = ["reply": .string(text), "patch": patch]
        } catch is PatchError { throw ProviderFailure("scene_settlement", "这段回应的变化尚未确认，请再试一次") }
        return (candidate, text, context)
    }
    func recoveryContext(world: StoryWorld, job: SettlementJob) throws -> J {
        guard let thread = world.threads.first(where: { $0.id == job.threadID }), thread.isRoom,
              let index = thread.messages.firstIndex(where: { $0.id == job.id }) else { return job.context }
        let source = thread.messages[index], prior = Array(thread.messages.prefix(index)), user = prior.last { $0.role == "user" && $0.status == "complete" }
        guard let views = source.observations else { throw ProviderFailure("room_observation", "先恢复这段话的在场视角") }
        var context = job.context
        let members = source.observationMembers
        context["room_characters"] = .array(members.map { world.character($0).selecting(["id", "name", "age", "role"]) })
        context["current_user_message_id"] = user.map { .string($0.id) } ?? .null
        context["messages"] = .array(prior.filter { $0.status == "complete" }.compactMap { message in
            guard let visible = message.observations?[job.characterID], !visible.trimmed.isEmpty else { return nil }
            return ContextCompiler.messageValue(message, content: visible)
        }.suffix(40).map { $0 })
        context["observation_sources"] = .object(Dictionary(uniqueKeysWithValues: members.map { id in (id, ["user": .string(user?.observations?[id] ?? ""), "assistant": .string(views[id] ?? "")]) }))
        context["reply_observations"] = ["reply": .string(job.reply), "views": .object(views.mapValues(J.string))]
        let projected = ContextCompiler.knowledge(world, characterID: job.characterID)
        for field in ["knowledge", "public_events"] {
            let previousIDs = Set(job.context[field].array.map { $0["id"].string })
            context[field] = .array(projected.filter { previousIDs.contains($0["id"].string) })
        }
        if job.context["observation_sources"] == .null {
            var own: J = [:]
            for message in prior where message.characterID == job.characterID && message.settlement?["decision"]["outcome"] != "rejected" {
                if let settlement = message.settlement { own = own.merging(settlement["scene"].selecting(["tension", "active_threads", "physical_state", "emotional_state"])) }
            }
            context["scene"] = job.context["scene"].selecting(["location_id", "location", "location_name", "world_time"]).merging(own)
        }
        return context
    }
}
