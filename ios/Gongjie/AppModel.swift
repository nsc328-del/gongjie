import SwiftUI
import GongjieCore

@MainActor @Observable final class AppModel {
    var archive = Archive()
    var engine: StoryEngine?
    var startupError: String?
    var error: String?
    var notice: String?
    var workingWorld: String?
    var stage = ""
    var operationPhase = "idle"
    var previewingWorld: String?
    var previewError: String?
    var streaming: [String: String] = [:]
    var drafts: [String: String] = [:]
    var task: Task<Void, Never>?
    private var previewTask: Task<Void, Never>?
    private var previewToken: UUID?
    private var draftTask: Task<Void, Never>?
    private var draftLocations: [String: String] = [:]
    var canCancel: Bool { operationPhase == "generating" }

    init() {
        do {
            let directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("Gongjie", isDirectory: true)
            let store = try WorldStore(url: directory.appendingPathComponent("worlds.json"))
            engine = StoryEngine(store: store)
            Task { await refresh() }
        } catch { startupError = "无法打开本地故事：\(error.localizedDescription)" }
    }
    func world(_ id: String) -> StoryWorld? { archive.worlds.first { $0.id == id } }
    func refresh() async {
        guard let engine else { return }
        archive = await engine.store.snapshot()
        let active = Set(archive.worlds.flatMap(\.threads).flatMap(\.messages).filter { $0.status == "streaming" }.map(\.id))
        streaming = streaming.filter { active.contains($0.key) }
    }
    func handle(_ event: EngineEvent) async {
        switch event {
        case .changed: await refresh()
        case .accepted(let threadID, let content):
            if let workingWorld { draftLocations[threadID] = workingWorld }
            if drafts[threadID] == nil || drafts[threadID] == content { drafts[threadID] = "" }
        case .phase(let value): operationPhase = value
        case .stage(let text): stage = text
        case .stream(_, let messageID, let text): streaming[messageID] = text
        case .warning(let text): notice = text
        }
    }
    func setDraft(_ text: String, worldID: String, threadID: String) {
        drafts[threadID] = text; draftLocations[threadID] = worldID
        draftTask?.cancel()
        draftTask = Task { try? await Task.sleep(for: .milliseconds(300)); guard !Task.isCancelled else { return }; await flushDrafts() }
    }
    func flushDrafts() async {
        guard let engine else { return }
        let snapshot = drafts, locations = draftLocations
        for (threadID, text) in snapshot {
            guard let worldID = locations[threadID] else { continue }
            do { try await engine.saveDraft(worldID: worldID, threadID: threadID, text: text) }
            catch { self.error = "草稿没有保存成功：\(error.localizedDescription)" }
        }
    }
    private func finishDrafts() async {
        let previous = draftTask; draftTask?.cancel(); await previous?.value
        await flushDrafts()
    }
    private func stopPreview() async {
        let previous = previewTask, token = previewToken
        previous?.cancel(); await previous?.value
        if previewToken == token { previewTask = nil; previewToken = nil; previewingWorld = nil }
    }
    func preparePreview(_ worldID: String) {
        guard workingWorld == nil, let world = world(worldID), world.pending.isEmpty, !world.characters.isEmpty,
              world.timePreview?["state_revision"].int != world.stateRevision, let engine else { return }
        guard previewingWorld != worldID else { return }
        let previous = previewTask; previous?.cancel()
        let token = UUID(), profile = archive.profile
        previewToken = token; previewingWorld = worldID; previewError = nil
        previewTask = Task { [weak self] in
            guard let self else { return }
            await previous?.value
            defer { if previewToken == token { previewTask = nil; previewToken = nil; previewingWorld = nil } }
            do {
                try Task.checkCancellation()
                let key = try KeyVault.read()
                _ = try await engine.preview(worldID: worldID, profile: profile, key: key) { [weak self] event in
                    if case .changed = event { await self?.refresh() }
                }
            } catch {
                if !Task.isCancelled && previewToken == token { previewError = error.localizedDescription }
            }
        }
    }
    func run(worldID: String, phase: String = "generating", requiresKey: Bool = true, _ operation: @escaping @Sendable (StoryEngine, ProviderProfile, String, @escaping @Sendable (EngineEvent) async -> Void) async throws -> Void) {
        guard workingWorld == nil, let engine else { return }
        workingWorld = worldID; operationPhase = phase; error = nil; notice = nil; stage = "正在准备"
        let profile = archive.profile
        task = Task { [weak self] in
            guard let self else { return }
            defer { workingWorld = nil; stage = ""; operationPhase = "idle"; streaming = [:]; task = nil }
            do {
                await stopPreview(); await finishDrafts(); try Task.checkCancellation()
                let key = requiresKey ? try KeyVault.read() : ((try? KeyVault.read()) ?? "")
                try await operation(engine, profile, key) { [weak self] event in await self?.handle(event) }
            } catch {
                if !(error is CancellationError) && !Task.isCancelled { self.error = error.localizedDescription }
            }
            await refresh()
        }
    }
    func enter(_ worldID: String, _ threadID: String) {
        guard let w = world(worldID), let thread = w.threads.first(where: { $0.id == threadID }), w.pending.isEmpty,
              thread.isRoom || thread.characterIDs.first.map({ w.availability($0) != "unavailable" }) == true else { return }
        run(worldID: worldID, requiresKey: false) { engine, profile, key, notify in
            try await engine.enter(worldID: worldID, threadID: threadID, profile: profile, key: key, notify: notify)
        }
    }
    func send(_ worldID: String, _ threadID: String, text: String, continuation: Bool = false) {
        run(worldID: worldID) { engine, profile, key, notify in try await engine.turn(worldID: worldID, threadID: threadID, content: text, continuation: continuation, profile: profile, key: key, notify: notify) }
    }
    func recover(_ id: String) { run(worldID: id, phase: "recovering") { engine, profile, key, notify in try await engine.recover(worldID: id, profile: profile, key: key, notify: notify) } }
    func pulse(_ id: String) {
        run(worldID: id, phase: "pulse") { engine, profile, key, notify in
            let preview = try await engine.preview(worldID: id, profile: profile, key: key, notify: notify)
            try await engine.pulse(worldID: id, previewID: preview["id"].string, profile: profile, key: key, notify: notify)
        }
    }
    func cancel() { task?.cancel() }
    func background() {
        let active = task
        let token = UIApplication.shared.beginBackgroundTask(withName: "保存故事") { [weak self] in self?.cancel() }
        cancel(); previewTask?.cancel()
        Task {
            await stopPreview(); await finishDrafts(); await active?.value
            if token != .invalid { UIApplication.shared.endBackgroundTask(token) }
        }
    }
    private func mutate<T>(worldID: String, _ operation: (StoryEngine) async throws -> T) async throws -> T {
        guard workingWorld == nil else { throw GameError.busy }
        guard let engine else { throw GameError.missing }
        workingWorld = worldID; operationPhase = "editing"
        defer { workingWorld = nil; operationPhase = "idle" }
        await stopPreview(); await finishDrafts()
        do { let result = try await operation(engine); await refresh(); return result }
        catch { await refresh(); throw error }
    }
    func openConversation(worldID: String, characterID: String) async throws -> String {
        if let existing = world(worldID)?.threads.first(where: { !$0.isRoom && $0.characterIDs == [characterID] }) { return existing.id }
        return try await mutate(worldID: worldID) { try await $0.conversation(worldID: worldID, characterID: characterID) }
    }
    func createRoom(worldID: String, name: String, locationID: String, characters: [String]) async throws -> String {
        try await mutate(worldID: worldID) { try await $0.room(worldID: worldID, name: name, locationID: locationID, characters: characters) }
    }
    func saveBook(worldID: String, book: WorldBook) async throws {
        try await mutate(worldID: worldID) { try await $0.edit(worldID: worldID, book: book) }
    }
    func savePlayerProfile(_ worldID: String, profile: J) async throws {
        try await mutate(worldID: worldID) { try await $0.savePlayerProfile(worldID: worldID, profile: profile) }
    }
    func saves(_ worldID: String) async throws -> [WorldSave] {
        guard let engine else { throw GameError.missing }
        return await engine.store.saves(worldID: worldID)
    }
    func createSave(_ worldID: String, name: String) async throws {
        try await mutate(worldID: worldID) { _ = try await $0.createSave(worldID: worldID, name: name) }
    }
    func loadSave(_ worldID: String, saveID: String) async throws {
        try await mutate(worldID: worldID) { engine in
            _ = try await engine.loadSave(worldID: worldID, saveID: saveID)
            clearDrafts(worldID)
        }
    }
    func resetWorld(worldID: String, confirmation: String) async throws {
        try await mutate(worldID: worldID) { engine in
            try await engine.reset(worldID: worldID, confirmation: confirmation)
            clearDrafts(worldID)
        }
    }
    private func clearDrafts(_ worldID: String) {
        let ids = Set(draftLocations.filter { $0.value == worldID }.map(\.key)).union(world(worldID)?.threads.map(\.id) ?? [])
        for id in ids { drafts.removeValue(forKey: id); draftLocations.removeValue(forKey: id) }
        streaming = [:]
    }
    func create(_ book: WorldBook) async throws -> String {
        try await mutate(worldID: "library") { try await $0.store.add(book: book) }
    }
    func importFile(_ url: URL) async {
        let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= 100_000_000 else { throw GameError.invalid("文件过大") }
            let data = try Data(contentsOf: url), header = try JSONDecoder().decode(J.self, from: data)
            try await mutate(worldID: "library") { engine in
                if header["format"].string == "gongjie-worldbook" {
                    let file = try JSONDecoder().decode(BookFile.self, from: data)
                    guard file.version == 1 else { throw GameError.invalid("世界书版本暂不支持") }
                    _ = try await engine.store.add(book: file.book)
                } else { _ = try await engine.store.importBackup(data) }
            }
        } catch { self.error = "导入失败：\(error.localizedDescription)" }
    }
}
