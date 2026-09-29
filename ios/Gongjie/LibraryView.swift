import SwiftUI
import UniformTypeIdentifiers
import GongjieCore

enum Route: Hashable { case world(String), thread(String, String) }
struct LibraryView: View {
    @Environment(AppModel.self) private var app
    @State private var path: [Route] = []
    @State private var settings = false
    @State private var create = false
    @State private var importing = false
    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    HStack(alignment: .top) { VStack(alignment: .leading, spacing: 10) { Eyebrow(text: "每一次相遇，都留下回声"); Text("共界").font(.system(size: 48, weight: .medium, design: .serif)) }; Spacer(); Image(systemName: "circle.hexagongrid").font(.system(size: 38, weight: .ultraLight)).foregroundStyle(Color.moss).padding(.top, 15).accessibilityHidden(true) }.padding(.top, 24)
                    HStack { Eyebrow(text: "正在发生的故事"); Spacer(); Text("\(app.archive.worlds.count) 个世界").font(.caption).foregroundStyle(.secondary) }
                    if app.archive.worlds.isEmpty { ContentUnavailableView("故事从这里开始", systemImage: "book.closed", description: Text("写下一个世界，或打开一份世界书。")) }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 310), spacing: 18)], spacing: 18) {
                        ForEach(app.archive.worlds.sorted { $0.updatedAt > $1.updatedAt }) { world in
                            NavigationLink(value: Route.world(world.id)) {
                                StoryCard {
                                    VStack(alignment: .leading, spacing: 20) {
                                        HStack { Label(world.info["current_time"].string.isEmpty ? "故事未启" : world.info["current_time"].string, systemImage: "clock").font(.caption); Spacer(); Image(systemName: "arrow.up.right") }.foregroundStyle(Color.moss)
                                        Text(world.name).font(.system(.title, design: .serif).weight(.medium)).foregroundStyle(.primary)
                                        Text(world.info["premise"].string).font(.subheadline).foregroundStyle(.secondary).lineSpacing(6).lineLimit(4).frame(maxWidth: .infinity, alignment: .leading)
                                        HStack(spacing: -5) { ForEach(Array(world.characters.prefix(4).enumerated()), id: \.offset) { _, c in Face(character: c, size: 32).overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.paper, lineWidth: 2)) }; Spacer(); Text(app.workingWorld == world.id ? "故事正在继续" : (world.pending.isEmpty ? "进入世界" : "余波待记")).font(.caption.weight(.medium)).foregroundStyle(Color.moss) }
                                    }
                                }
                            }.buttonStyle(QuietButton())
                        }
                    }
                    Button { create = true } label: { Label("写下新的世界", systemImage: "plus").font(.body.weight(.medium)).frame(maxWidth: .infinity).padding(24).background(RoundedRectangle(cornerRadius: 22).strokeBorder(Color.moss.opacity(0.35), style: StrokeStyle(lineWidth: 1, dash: [5, 5]))) }.buttonStyle(QuietButton())
                }.padding(22).frame(maxWidth: 1100).frame(maxWidth: .infinity)
            }.background(Color.paper).navigationTitle("").toolbar {
                ToolbarItem(placement: .topBarLeading) { Button { importing = true } label: { Image(systemName: "square.and.arrow.down") }.accessibilityLabel("导入世界书或故事") }
                ToolbarItem(placement: .topBarTrailing) { Button { settings = true } label: { Image(systemName: "slider.horizontal.3") }.accessibilityLabel("模型与阅读设置") }
            }
            .navigationDestination(for: Route.self) { route in
                switch route { case .world(let id): WorldView(worldID: id, path: $path); case .thread(let world, let thread): ConversationView(worldID: world, threadID: thread) }
            }
            .sheet(isPresented: $settings) { SettingsView() }
            .sheet(isPresented: $create) { WorldEditor(worldID: nil) { id in path.append(.world(id)) } }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.json], allowsMultipleSelection: false) { result in
                switch result { case .success(let urls): if let url = urls.first { Task { await app.importFile(url) } }; case .failure(let error): app.error = error.localizedDescription }
            }
        }
    }
}
struct JSONDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.json] }
    var data: Data
    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws { data = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }
}
