import SwiftUI
import GongjieCore

struct ConversationView: View {
    @Environment(AppModel.self) private var app
    @AppStorage("readingSize") private var readingSize = 19.0
    @ScaledMetric(relativeTo: .body) private var readingScale = 1.0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var worldID: String
    var threadID: String
    @State private var inspecting = false
    @State private var editingPlayer = false
    @State private var showingPortrait = false
    @State private var following = true
    @FocusState private var composing: Bool
    var world: StoryWorld? { app.world(worldID) }
    var thread: StoryThread? { world?.threads.first { $0.id == threadID } }
    var body: some View {
        Group {
            if let world, let thread {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 30) {
                            HStack { Spacer(); VStack(spacing: 10) {
                                if thread.isRoom { Image(systemName: "person.3.sequence").font(.title).foregroundStyle(Color.moss) } else { Button { showingPortrait = true } label: { Face(character: world.character(thread.characterIDs.first ?? ""), size: 68) }.buttonStyle(QuietButton()).accessibilityLabel("查看完整立绘") }
                                Text(thread.name).font(.system(.title2, design: .serif))
                                Text(thread.isRoom ? world.locationName(thread.locationID) : world.locationName(world.character(thread.characterIDs.first ?? "")["current_location_id"].string)).font(.caption).foregroundStyle(.secondary)
                                if thread.isRoom { Text(world.activeMembers(threadID: thread.id).map { world.character($0)["name"].string }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary) }
                            }; Spacer() }.padding(.vertical, 20)
                            ForEach(thread.messages) { message in messageView(message, world: world).id(message.id) }
                            if thread.messages.isEmpty && app.workingWorld != worldID {
                                if thread.isRoom { Text("等你开口。").font(.system(.body, design: .serif)).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(30) }
                                else { Button("让相遇展开") { app.enter(worldID, threadID) }.frame(maxWidth: .infinity).padding(30).disabled(!world.pending.isEmpty || world.availability(thread.characterIDs.first ?? "") == "unavailable") }
                            }
                            if !thread.isRoom && !thread.messages.isEmpty && world.pending.isEmpty && !thread.messages.contains(where: { $0.entryPulse == world.pulse }) && world.pulse > 0 && app.workingWorld == nil {
                                Button("再次相遇") { app.enter(worldID, threadID) }.frame(maxWidth: .infinity).disabled(world.availability(thread.characterIDs.first ?? "") == "unavailable")
                            }
                            if hasEntry(world: world, thread: thread) && !isCurrent(world: world, thread: thread) && canReturn(world: world, thread: thread) && app.workingWorld == nil && world.pending.isEmpty {
                                Button("回到现场", systemImage: "arrow.uturn.backward") { app.enter(worldID, threadID) }.frame(maxWidth: .infinity).padding(.vertical, 10)
                            }
                            PendingCard(world: world)
                            if app.workingWorld == worldID && (world.pending.isEmpty || app.operationPhase == "generating") { HStack(spacing: 10) { ProgressView().controlSize(.small); Text(app.stage).font(.caption).foregroundStyle(.secondary) }.padding(.vertical, 10) }
                            Color.clear.frame(height: 1).id("bottom")
                        }.padding(22).frame(maxWidth: 760).frame(maxWidth: .infinity)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .defaultScrollAnchor(.bottom)
                    .simultaneousGesture(DragGesture().onChanged { _ in following = false })
                    .onChange(of: thread.messages.count) { _, _ in if following { proxy.scrollTo("bottom", anchor: .bottom) } }
                    .onChange(of: app.streaming.values.reduce(0) { $0 + $1.count }) { _, _ in if following { proxy.scrollTo("bottom", anchor: .bottom) } }
                    .onChange(of: composing) { _, focused in if focused { following = true; withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) } } }
                    .overlay(alignment: .bottomTrailing) { if !following { Button { following = true; withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) } } label: { Image(systemName: "arrow.down").padding(14).background(.regularMaterial, in: Circle()) }.padding(20).accessibilityLabel("回到最新回复") } }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) { composer(world: world, thread: thread) }
            } else { ContentUnavailableView("这段对话已不存在", systemImage: "text.bubble") }
        }
        .background(Color.paper).navigationTitle(thread?.name ?? "相遇").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItemGroup(placement: .topBarTrailing) {
            Button { editingPlayer = true } label: { Image(systemName: "person.crop.rectangle") }.accessibilityLabel("我的角色").disabled(app.workingWorld != nil || !(world?.pending.isEmpty ?? true))
            Button { inspecting = true } label: { Image(systemName: "person.text.rectangle") }.accessibilityLabel("人物、关系与记忆")
        } }
        .sheet(isPresented: $inspecting) { if let world, let thread { InspectorView(world: world, thread: thread) } }
        .sheet(isPresented: $editingPlayer) { PlayerEditor(worldID: worldID) }
        .sheet(isPresented: $showingPortrait) { if let world, let thread { CharacterPortrait(character: world.character(thread.characterIDs.first ?? "")) } }
        .task(id: threadID) { app.enter(worldID, threadID) }
        .task(id: world?.stateRevision) { preparePreviewIfReady() }
        .onChange(of: app.workingWorld) { _, active in if active == nil { preparePreviewIfReady() } }
    }
    private func preparePreviewIfReady() {
        guard let world, let thread else { return }
        if hasEntry(world: world, thread: thread) { app.preparePreview(worldID) }
    }
    private func hasEntry(world: StoryWorld, thread: StoryThread) -> Bool {
        thread.isRoom || thread.messages.contains { $0.entryPulse == world.pulse } || (world.pulse == 0 && !thread.messages.isEmpty)
    }
    private func canReturn(world: StoryWorld, thread: StoryThread) -> Bool {
        if thread.isRoom { return !world.activeMembers(threadID: thread.id).isEmpty }
        guard let characterID = thread.characterIDs.first else { return false }
        return !world.character(characterID).object.isEmpty && world.availability(characterID) != "unavailable"
    }
    private func isCurrent(world: StoryWorld, thread: StoryThread) -> Bool {
        guard world.playerThreadID == thread.id, canReturn(world: world, thread: thread) else { return false }
        let location = thread.isRoom ? thread.locationID ?? "" : world.character(thread.characterIDs.first ?? "")["current_location_id"].string
        return world.player["location_id"].string == location
    }
    private func messageView(_ message: Message, world: StoryWorld) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            if let pulse = message.entryPulse { HStack { Rectangle().frame(height: 0.5); Text(pulse == 0 ? "初次相遇" : "再次相遇").fixedSize(); Rectangle().frame(height: 0.5) }.font(.caption).foregroundStyle(.secondary.opacity(0.55)).padding(.vertical, 12) }
            if message.role == "user" {
                HStack { Spacer(minLength: 36); Text(message.content).font(.system(size: (readingSize - 1) * readingScale)).lineSpacing(6).padding(18).background(Color.moss.opacity(0.09), in: RoundedRectangle(cornerRadius: 20)).textSelection(.enabled) }
            } else {
                if thread?.isRoom == true { HStack(spacing: 8) { Face(character: world.character(message.characterID ?? ""), size: 26); Text(world.character(message.characterID ?? "")["name"].string) }.font(.caption.weight(.medium)).foregroundStyle(Color.moss) }
                Text(app.streaming[message.id] ?? message.content).font(.system(size: readingSize * readingScale, design: .serif)).lineSpacing(9).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                if message.status == "streaming" && app.operationPhase == "generating" { Image(systemName: "ellipsis").font(.caption).foregroundStyle(Color.moss).accessibilityLabel("正在续写") }
                if message.status == "cancelled" || message.status == "error" { Label("这一段未写完", systemImage: "pause.circle").font(.caption).foregroundStyle(.secondary) }
                if let settlement = message.settlement { SettlementReceipt(patch: settlement) }
            }
        }
    }
    private func composer(world: StoryWorld, thread: StoryThread) -> some View {
        let draft = Binding<String>(get: { app.drafts[threadID] ?? thread.draft }, set: { app.setDraft($0, worldID: worldID, threadID: threadID) })
        let entryReady = hasEntry(world: world, thread: thread)
        let canSend = app.workingWorld == nil && world.pending.isEmpty && entryReady && isCurrent(world: world, thread: thread)
        return VStack(spacing: 8) {
            HStack(alignment: .bottom, spacing: 12) {
                TextField(thread.isRoom ? "对在场的人说……" : "写下这一句……", text: draft, axis: .vertical).lineLimit(1...6).font(.body).padding(13).background(Color.card, in: RoundedRectangle(cornerRadius: 18)).focused($composing).accessibilityLabel("对话草稿")
                if app.workingWorld == worldID && app.canCancel {
                    Button { app.cancel() } label: { Image(systemName: "stop.fill").frame(width: 46, height: 46).background(Color.moss.opacity(0.13), in: Circle()) }.accessibilityLabel("停止生成")
                } else {
                    Button { following = true; app.send(worldID, threadID, text: draft.wrappedValue) } label: { Image(systemName: "arrow.up").font(.body.weight(.semibold)).foregroundStyle(Color.paper).frame(width: 46, height: 46).background(Color.moss, in: Circle()) }.disabled(!canSend || draft.wrappedValue.trimmed.isEmpty).opacity(!canSend || draft.wrappedValue.trimmed.isEmpty ? 0.4 : 1).accessibilityLabel("发送")
                }
            }
            HStack(spacing: 24) {
                if !thread.isRoom && !thread.messages.isEmpty { Button("让话语继续") { following = true; app.send(worldID, threadID, text: "", continuation: true) }.disabled(!canSend || !draft.wrappedValue.trimmed.isEmpty) }
                Button("让世界向前") { app.pulse(worldID) }.disabled(app.workingWorld != nil || !world.pending.isEmpty || world.characters.isEmpty || world.timePreview == nil || !entryReady)
            }.font(.caption).padding(.top, 3)
        }.padding(.horizontal, 16).padding(.vertical, 10).background(.regularMaterial)
    }
}
struct SettlementReceipt: View {
    var patch: J
    private var event: J { patch["event"] }
    private var relationship: J { patch["relationship"] }
    private var player: J { patch["player"] }
    private func supported(_ field: String) -> Bool { !patch["evidence"][field]["quote"].string.isEmpty }
    private var hasEvent: Bool { supported("event") && !event["title"].string.isEmpty }
    private var hasRelationship: Bool { supported("relationship") && (!relationship["label"].string.isEmpty || !relationship["note"].string.isEmpty || ["trust_delta", "desire_delta", "tension_delta"].contains { relationship[$0].int != 0 }) }
    private var hasChanges: Bool {
        hasEvent || hasRelationship || supported("scene.world_time") || supported("scene.location") || supported("player.location") || supported("player.commitments")
    }
    var body: some View {
        if patch["decision"]["outcome"].string == "rejected" {
            Text("正文已保留 · 本轮变化未被确认").font(.caption).foregroundStyle(.secondary)
        } else if hasChanges {
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    if hasEvent { Text(event["details"].string) }
                    if hasRelationship {
                        if !relationship["note"].string.isEmpty { Text(relationship["note"].string) }
                        else if !relationship["label"].string.isEmpty { Text(relationship["label"].string) }
                        else { Text("你们之间的关系有所变化") }
                    }
                    if supported("scene.world_time") { Text(patch["scene"]["world_time"].string) }
                    if supported("scene.location") { Label(patch["scene"]["location"].string, systemImage: "location") }
                    else if supported("player.location") { Label(player["location"].string, systemImage: "location") }
                    if supported("player.commitments") {
                        if player["commitments"].array.isEmpty { Text("此前的承诺已不再悬着") }
                        ForEach(player["commitments"].array, id: \.self) { item in Text(item.string.isEmpty ? item["text"].string : item.string) }
                    }
                }.font(.caption).foregroundStyle(.secondary).padding(.top, 5)
            } label: { Label(hasEvent ? event["title"].string : "这一轮留下的变化", systemImage: "leaf").font(.caption).foregroundStyle(Color.moss) }
        }
    }
}
