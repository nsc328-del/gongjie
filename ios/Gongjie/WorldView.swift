import SwiftUI
import GongjieCore

struct WorldView: View {
    @Environment(AppModel.self) private var app
    var worldID: String
    @Binding var path: [Route]
    @State private var editing = false
    @State private var editingPlayer = false
    @State private var creatingRoom = false
    @State private var showingSaves = false
    @State private var settings = false
    @State private var portraitID: String?
    @State private var resetPrompt = false
    @State private var resetConfirm = false
    @State private var resetName = ""
    @State private var exporting = false
    @State private var document: JSONDocument?
    @State private var exportName = "共界"
    @State private var timeline = false
    var world: StoryWorld? { app.world(worldID) }
    private var busy: Bool { app.workingWorld != nil || !(world?.pending.isEmpty ?? true) }

    var body: some View {
        Group {
            if let world {
                ScrollView {
                    VStack(alignment: .leading, spacing: 26) {
                        ChapterTitle(title: world.name, subtitle: world.info["atmosphere"].string)
                        hero(world)
                        PendingCard(world: world)
                        playerCard(world)
                        HStack { Eyebrow(text: "此刻的相遇"); Spacer(); Button { editing = true } label: { Image(systemName: "person.badge.plus") }.accessibilityLabel("编辑人物与世界").disabled(busy) }
                        if world.characters.isEmpty { Button("添加第一个人物") { editing = true }.buttonStyle(.bordered).disabled(busy) }
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 14)], spacing: 14) {
                            ForEach(world.characters, id: \.self) { c in characterCard(c, world: world) }
                        }
                        HStack { Eyebrow(text: "多人同场"); Spacer(); Button { creatingRoom = true } label: { Image(systemName: "plus.circle") }.accessibilityLabel("开启同场").disabled(busy) }
                        ForEach(world.threads.filter(\.isRoom)) { room in roomCard(room, world: world) }
                        if world.threads.filter(\.isRoom).isEmpty { Button("开启一场会面") { creatingRoom = true }.font(.subheadline).disabled(busy) }
                        advancement(world)
                        StoryCard { DisclosureGroup("世界介绍") { Text(world.info["premise"].string).font(.system(.body, design: .serif)).lineSpacing(8).textSelection(.enabled).padding(.top, 14) } }
                        places(world)
                        Button { timeline = true } label: { Label("世界留下的事", systemImage: "clock.arrow.circlepath").frame(maxWidth: .infinity).padding(.vertical, 8) }
                        VStack(spacing: 16) {
                            Button { showingSaves = true } label: { Label("存档 / 读档", systemImage: "tray.full").frame(maxWidth: .infinity, alignment: .leading) }.disabled(busy)
                            Button { settings = true } label: { HStack { Image(systemName: "circle.fill").font(.system(size: 7)); Text(app.archive.profile.model).font(.caption); Spacer(); Image(systemName: "gearshape") }.foregroundStyle(.secondary) }
                        }.padding(.top, 12)
                    }.padding(22).frame(maxWidth: 1000).frame(maxWidth: .infinity)
                }.background(Color.paper)
                    .task(id: world.stateRevision) { app.preparePreview(worldID) }
                    .onChange(of: app.workingWorld) { _, active in if active == nil { app.preparePreview(worldID) } }
            } else { ContentUnavailableView("世界已不存在", systemImage: "book.closed") }
        }
        .navigationTitle(world?.name ?? "世界").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Menu {
            Button("我的角色", systemImage: "person.crop.rectangle") { editingPlayer = true }.disabled(busy)
            Button("存档 / 读档", systemImage: "tray.full") { showingSaves = true }.disabled(busy)
            Button("创作世界", systemImage: "pencil.and.outline") { editing = true }.disabled(busy)
            Button("导出世界书", systemImage: "book.closed") { export(book: true) }
            Button("导出完整故事", systemImage: "square.and.arrow.up") { export(book: false) }.disabled(busy)
            Divider()
            Button("重新开始", systemImage: "arrow.counterclockwise", role: .destructive) { resetName = ""; resetPrompt = true }.disabled(busy)
        } label: { Image(systemName: "ellipsis.circle") }.accessibilityLabel("世界操作") } }
        .safeAreaInset(edge: .bottom) {
            if app.workingWorld == worldID {
                HStack { ProgressView(); Text(app.stage).font(.subheadline); Spacer(); if app.canCancel { Button("停止") { app.cancel() } } }.padding().background(.regularMaterial)
            }
        }
        .sheet(isPresented: $editing) { WorldEditor(worldID: worldID) { _ in } }
        .sheet(isPresented: $editingPlayer) { PlayerEditor(worldID: worldID) }
        .sheet(isPresented: $creatingRoom) { RoomEditor(worldID: worldID) { id in path.append(.thread(worldID, id)) } }
        .sheet(isPresented: $showingSaves) { WorldSavesView(worldID: worldID) { path = [.world(worldID)] } }
        .sheet(isPresented: $settings) { SettingsView() }
        .sheet(isPresented: Binding(get: { portraitID != nil }, set: { if !$0 { portraitID = nil } })) {
            if let id = portraitID, let world { CharacterPortrait(character: world.character(id), canEnter: !busy && world.availability(id) != "unavailable", enter: { open(id) }) }
        }
        .sheet(isPresented: $timeline) { if let world { TimelineView(world: world) } }
        .alert("重新开始这个世界？", isPresented: $resetPrompt) { TextField("输入世界名称", text: $resetName); Button("下一步", role: .destructive) { resetConfirm = true }.disabled(resetName != world?.name); Button("取消", role: .cancel) {} } message: { Text("人物与世界底稿保留，对话、经历和关系进展将清空。") }
        .confirmationDialog("确定清空所有故事进度？此操作无法撤销。", isPresented: $resetConfirm, titleVisibility: .visible) { Button("清空并重新开始", role: .destructive) { Task { do { try await app.resetWorld(worldID: worldID, confirmation: resetName) } catch { app.error = error.localizedDescription } } } }
        .fileExporter(isPresented: $exporting, document: document, contentType: .json, defaultFilename: exportName) { result in if case .failure(let error) = result { app.error = error.localizedDescription } }
    }
    private func hero(_ world: StoryWorld) -> some View {
        let played = world.threads.contains { !$0.messages.isEmpty }
        let hasPeriod = world.pulse > 0 && !world.narration.isEmpty
        let label = hasPeriod ? "本时段开场" : played ? "世界背景" : "故事开场"
        let text = hasPeriod ? world.narration : played ? world.info["premise"].string : (world.info["opening_narration"].string.isEmpty ? world.info["premise"].string : world.info["opening_narration"].string)
        return StoryCard { VStack(alignment: .leading, spacing: 18) {
            Eyebrow(text: label)
            Label(world.info["current_time"].string.isEmpty ? "此刻" : world.info["current_time"].string, systemImage: "sun.horizon").font(.caption.weight(.medium)).foregroundStyle(Color.moss)
            Text(text).font(.system(.body, design: .serif)).lineSpacing(9).textSelection(.enabled)
        } }
    }
    private func advancement(_ world: StoryWorld) -> some View {
        StoryCard { VStack(alignment: .leading, spacing: 16) {
            Eyebrow(text: "世界向前")
            Text("这一刻还可以继续，也可以向前").font(.system(.title3, design: .serif))
            if let preview = world.timePreview {
                Label(preview["world_time"].string, systemImage: "clock.arrow.circlepath").font(.subheadline).foregroundStyle(Color.moss)
            } else if app.previewingWorld == worldID {
                HStack { ProgressView().controlSize(.small); Text("正在确定下一时刻…").font(.caption).foregroundStyle(.secondary) }
            } else if app.previewError != nil {
                Button("重新确定下一时刻", systemImage: "arrow.clockwise") { app.preparePreview(worldID) }.font(.subheadline).disabled(busy)
            }
            PrimaryButton(title: "让这一刻继续", icon: "sparkle") { app.pulse(worldID) }.disabled(busy || world.characters.isEmpty || world.timePreview == nil)
        } }
    }
    private func places(_ world: StoryWorld) -> some View {
        let locations = world.knownLocations(characterID: "player")
        return Group {
            if !locations.isEmpty {
                StoryCard { DisclosureGroup("仍在呼吸的地方") {
                    VStack(alignment: .leading, spacing: 20) {
                        ForEach(locations, id: \.self) { place in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(place["name"].string).font(.system(.headline, design: .serif))
                                if !place["mood"].string.isEmpty { Text(place["mood"].string).font(.caption).foregroundStyle(Color.moss) }
                                if !place["description"].string.isEmpty { Text(place["description"].string).font(.subheadline).lineSpacing(5).foregroundStyle(.secondary) }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }.padding(.top, 16)
                } }
            }
        }
    }
    private func export(book: Bool) {
        guard let world else { return }
        do { document = JSONDocument(data: try book ? JSONEncoder().encode(BookFile(book: world.baseline)) : JSONEncoder().encode(WorldBackup(world: world))); exportName = world.name + (book ? "-世界书" : "-故事"); exporting = true }
        catch { app.error = error.localizedDescription }
    }
    private func open(_ characterID: String) {
        Task { do { let id = try await app.openConversation(worldID: worldID, characterID: characterID); path.append(.thread(worldID, id)) } catch { app.error = error.localizedDescription } }
    }
    private func playerCard(_ world: StoryWorld) -> some View {
        StoryCard { VStack(alignment: .leading, spacing: 14) {
            HStack { Eyebrow(text: "此刻的你"); Spacer(); Button("我的角色") { editingPlayer = true }.font(.subheadline).disabled(busy) }
            if !world.player["location_id"].string.isEmpty { Label(world.locationName(world.player["location_id"].string), systemImage: "location").font(.subheadline) }
            if !world.player["condition"].string.isEmpty { Text(world.player["condition"].string).font(.subheadline).foregroundStyle(.secondary) }
            ForEach(world.player["visible_traces"].strings, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
            if !world.player["commitments"].array.isEmpty { Eyebrow(text: "还悬着的承诺").padding(.top, 4) }
            ForEach(world.player["commitments"].array, id: \.self) { promise in
                VStack(alignment: .leading, spacing: 10) {
                    Text(promise["text"].string).font(.subheadline)
                    HStack(alignment: .top) {
                        let time = world.timePreview?["appointment_times"][promise["id"].string].string ?? ""
                        Text(time.isEmpty ? (world.timePreview == nil ? "约定时间待确认" : "时间未定") : time).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        if promise["ready_to_visit"].bool && world.availability(promise["character_id"].string) != "unavailable" {
                            Button("前往赴约", systemImage: "arrow.up.right") { open(promise["character_id"].string) }.font(.caption.weight(.medium)).disabled(busy)
                        } else { Text(world.character(promise["character_id"].string)["name"].string).font(.caption).foregroundStyle(.secondary) }
                    }
                }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Color.moss.opacity(0.07), in: RoundedRectangle(cornerRadius: 14))
            }
        } }
    }
    private func roomCard(_ room: StoryThread, world: StoryWorld) -> some View {
        let members = world.activeMembers(threadID: room.id)
        return NavigationLink(value: Route.thread(worldID, room.id)) { StoryCard { HStack(spacing: 14) {
            Image(systemName: "person.3.sequence").foregroundStyle(Color.moss)
            VStack(alignment: .leading, spacing: 5) { Text(room.name).foregroundStyle(.primary); Text("\(world.locationName(room.locationID)) · \(members.count) 人在场").font(.caption).foregroundStyle(.secondary) }
            Spacer(); Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
        } } }.buttonStyle(QuietButton())
    }
    private func characterCard(_ c: J, world: StoryWorld) -> some View {
        let id = c["id"].string, availability = world.availability(id)
        let hasThread = world.threads.contains { !$0.isRoom && $0.characterIDs == [id] }
        let canOpen = hasThread || (!busy && availability != "unavailable")
        let labels = ["present": "就在眼前", "reachable": "可以去见", "incoming": "正在靠近", "unavailable": "暂别片刻"]
        let action = world.actions[id] ?? [:]
        let hasMessages = world.threads.contains { $0.messages.contains { $0.characterID == id } }
        let notes = world.relationship(id)["notes"].string
        return StoryCard { VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 14) {
                Button { portraitID = id } label: { Face(character: c) }.buttonStyle(QuietButton()).accessibilityLabel("查看\(c["name"].string)的立绘")
                Button { open(id) } label: { VStack(alignment: .leading, spacing: 5) { Text(c["name"].string).font(.system(.title3, design: .serif).weight(.medium)).foregroundStyle(.primary); Text(c["role"].string).font(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading) }.buttonStyle(QuietButton()).disabled(!canOpen)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
            }
            Button { open(id) } label: {
                VStack(alignment: .leading, spacing: 14) {
                    Text(!action["encounter_hook"].string.isEmpty ? action["encounter_hook"].string : (!action["surface_state"].string.isEmpty ? action["surface_state"].string : c["tagline"].string)).font(.subheadline).foregroundStyle(.secondary).lineSpacing(5).lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
                    if hasMessages && !notes.isEmpty { Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(3) }
                    HStack { Text(world.locationName(c["current_location_id"].string)).font(.caption).foregroundStyle(.secondary); Spacer(); Text(labels[availability] ?? "可以去见").font(.caption.weight(.medium)).foregroundStyle(Color.moss) }
                }.contentShape(Rectangle())
            }.buttonStyle(QuietButton()).disabled(!canOpen)
        } }
    }
}
