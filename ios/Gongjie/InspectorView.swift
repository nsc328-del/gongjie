import SwiftUI
import GongjieCore

struct InspectorView: View {
    @Environment(\.dismiss) private var dismiss
    var world: StoryWorld
    var thread: StoryThread
    @State private var selected = ""
    @State private var showingPortrait = false
    var body: some View {
        NavigationStack {
            let members = thread.isRoom ? world.activeMembers(threadID: thread.id) : thread.characterIDs
            let id = members.contains(selected) ? selected : (members.first ?? "")
            let c = world.character(id), relationship = world.relationship(id)
            let scene = thread.isRoom ? (thread.characterScenes[id] ?? [:]) : thread.scene
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if thread.isRoom && members.count > 1 { Picker("人物", selection: $selected) { ForEach(members, id: \.self) { id in Text(world.character(id)["name"].string).tag(id) } }.pickerStyle(.segmented) }
                    if members.isEmpty { ContentUnavailableView("此刻无人留在这里", systemImage: "person.slash") }
                    else {
                    HStack(spacing: 18) { Button { showingPortrait = true } label: { Face(character: c, size: 70) }.buttonStyle(QuietButton()).accessibilityLabel("查看完整立绘"); ChapterTitle(title: c["name"].string, subtitle: "\(c["age"].int) 岁 · \(c["role"].string)") }
                    StoryCard { VStack(alignment: .leading, spacing: 12) { Eyebrow(text: "你们之间"); Text(relationship["label"].string.isEmpty ? "尚未命名" : relationship["label"].string).font(.system(.title3, design: .serif)); Text(relationship["notes"].string).font(.subheadline).foregroundStyle(.secondary); HStack { metric("信任", value: relationship["trust"].int); metric("牵引", value: relationship["desire"].int); metric("张力", value: relationship["tension"].int) } } }
                    if !scene["physical_state"].string.isEmpty { StoryCard { VStack(alignment: .leading, spacing: 10) { Eyebrow(text: "现场余波"); Text(scene["physical_state"].string).font(.subheadline); Text(scene["emotional_state"].string).font(.subheadline).foregroundStyle(.secondary) } } }
                    Eyebrow(text: "\(c["name"].string)所知的事")
                    let known = ContextCompiler.knowledge(world, characterID: id)
                    if known.isEmpty { Text("还没有留下共同的经历。").font(.subheadline).foregroundStyle(.secondary) }
                    ForEach(known.reversed(), id: \.self) { event in StoryCard { VStack(alignment: .leading, spacing: 10) { Text(event["title"].string).font(.headline); Text(event["details"].string).font(.subheadline).lineSpacing(5); Text(["misunderstood", "believed"].contains(event["knowledge_state"].string) ? "此人相信的版本" : event["source"].string).font(.caption).foregroundStyle(Color.moss) } } }
                    }
                }.padding(22)
            }.background(Color.paper).navigationTitle("人物与记忆").navigationBarTitleDisplayMode(.inline).toolbar { Button("完成") { dismiss() } }
        }.onAppear { selected = (thread.isRoom ? world.activeMembers(threadID: thread.id) : thread.characterIDs).first ?? "" }
        .sheet(isPresented: $showingPortrait) { CharacterPortrait(character: world.character(selected.isEmpty ? (thread.characterIDs.first ?? "") : selected)) }
    }
    private func metric(_ label: String, value: Int) -> some View { VStack(spacing: 5) { Text("\(value)").font(.system(.title2, design: .serif)); Text(label).font(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity).padding(.top, 8) }
}
struct TimelineView: View {
    @Environment(\.dismiss) private var dismiss
    var world: StoryWorld
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    let events = world.events.filter { $0["visibility"].string == "public" }
                    if events.isEmpty { ContentUnavailableView("世界尚且安静", systemImage: "leaf", description: Text("公开的经历会留在这里。")) }
                    ForEach(events.reversed(), id: \.self) { e in StoryCard { VStack(alignment: .leading, spacing: 12) { Eyebrow(text: e["world_time"].string); Text(e["title"].string).font(.system(.title3, design: .serif)); Text(e["details"].string).font(.body).lineSpacing(7); Text(e["location_name"].string).font(.caption).foregroundStyle(.secondary) } } }
                }.padding(22)
            }.background(Color.paper).navigationTitle("世界留下的事").navigationBarTitleDisplayMode(.inline).toolbar { Button("完成") { dismiss() } }
        }
    }
}
