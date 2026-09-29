import SwiftUI
import GongjieCore

struct WorldEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    var worldID: String?
    var completion: (String) -> Void
    @State private var book = WorldBook(world: ["name": "", "premise": "", "atmosphere": "", "rules": "所有角色均为成年人，意图不是事实。", "opening_narration": "", "current_time": "第一日傍晚"])
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                if worldID == nil { Section { Button("从白昼将死开始") { do { book = try BuiltIn.daylight() } catch { self.error = error.localizedDescription } } } }
                Section("世界") {
                    TextField("名称", text: text("name"))
                    TextField("此刻", text: text("current_time"))
                    TextField("氛围", text: text("atmosphere"), axis: .vertical).lineLimit(2...4)
                    LongField(title: "世界介绍", value: text("premise"))
                    LongField(title: "世界规则", value: text("rules"))
                    LongField(title: "开场此刻", value: text("opening_narration"))
                }
                Section("地点") {
                    ForEach(book.locations.indices, id: \.self) { i in NavigationLink(book.locations[i]["name"].string.isEmpty ? "新地点" : book.locations[i]["name"].string) { LocationEditor(location: $book.locations[i]) } }
                    Button("添加地点", systemImage: "plus") { book.locations.append(["id": .string(newID()), "name": "", "description": "", "mood": ""]) }
                }
                Section("人物") {
                    ForEach(book.characters.indices, id: \.self) { i in NavigationLink { CharacterEditor(character: $book.characters[i], relationships: $book.relationships, locations: book.locations) } label: { HStack(spacing: 12) { Face(character: book.characters[i], size: 36); Text(book.characters[i]["name"].string.isEmpty ? "新人物" : book.characters[i]["name"].string) } } }
                    Button("添加人物", systemImage: "person.badge.plus") { book.characters.append(["id": .string(newID()), "name": "", "age": 25, "pronouns": "", "role": "", "tagline": "", "persona": "", "speech_style": "", "opening_hook": "", "loss_of_control": "", "signature_behaviors": [], "desire_profile": ["control": 50, "devotion": 50, "jealousy": 50, "risk": 50, "tenderness": 50], "current_location_id": book.locations.first?["id"] ?? "", "accent": "#668475"]) }
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }.scrollContentBackground(.hidden).background(Color.paper)
            .navigationTitle(worldID == nil ? "新的世界" : "创作世界").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }; ToolbarItem(placement: .confirmationAction) { Button("保存") { save() }.disabled(saving || book.world["name"].string.trimmed.isEmpty) } }
        }
        .interactiveDismissDisabled(saving)
        .onAppear {
            guard !loaded else { return }; loaded = true
            if let worldID, let world = app.world(worldID) { book = world.baseline }
        }
    }
    private func text(_ key: String) -> Binding<String> { Binding(get: { book.world[key].string }, set: { book.world[key] = .string($0) }) }
    private func save() {
        saving = true
        Task { defer { saving = false }; do {
            for key in ["name", "premise", "atmosphere", "rules", "opening_narration", "current_time"] { book.world[key] = .string(book.world[key].string.trimmed) }
            let id: String
            if let worldID { try await app.saveBook(worldID: worldID, book: book); id = worldID }
            else { id = try await app.create(book) }
            completion(id); dismiss()
        } catch { self.error = error.localizedDescription } }
    }
}
struct LongField: View {
    var title: String
    @Binding var value: String
    var body: some View { VStack(alignment: .leading, spacing: 8) { Text(title).font(.caption).foregroundStyle(.secondary); TextField(title, text: $value, axis: .vertical).lineLimit(3...12).font(.body).accessibilityLabel(title) }.padding(.vertical, 5) }
}
struct LocationEditor: View {
    @Binding var location: J
    var body: some View { Form { TextField("地点名称", text: text("name")); LongField(title: "地点样貌", value: text("description")); LongField(title: "氛围", value: text("mood")) }.navigationTitle("地点").navigationBarTitleDisplayMode(.inline) }
    private func text(_ key: String) -> Binding<String> { Binding(get: { location[key].string }, set: { location[key] = .string($0) }) }
}
struct CharacterEditor: View {
    @Binding var character: J
    @Binding var relationships: [J]
    var locations: [J]
    private let tendencies = [("control", "掌控"), ("devotion", "依恋"), ("jealousy", "嫉妒"), ("risk", "冒险"), ("tenderness", "温柔")]
    var body: some View {
        Form {
            Section("身份") {
                TextField("姓名", text: text("name"))
                Stepper("\(character["age"].int) 岁", value: Binding(get: { character["age"].int }, set: { character["age"] = .number(Double($0)) }), in: 18...999)
                TextField("称谓", text: text("pronouns")); TextField("身份", text: text("role"))
                Picker("所在地点", selection: text("current_location_id")) { Text("未定").tag(""); ForEach(locations, id: \.self) { Text($0["name"].string).tag($0["id"].string) } }
                TextField("一句话印象", text: text("tagline"), axis: .vertical)
            }
            Section("人物底色") {
                LongField(title: "人格", value: text("persona"))
                LongField(title: "说话方式", value: text("speech_style"))
                LongField(title: "开场牵引", value: text("opening_hook"))
                LongField(title: "标志行为 · 每行一条", value: Binding(get: { character["signature_behaviors"].strings.joined(separator: "\n") }, set: { character["signature_behaviors"] = .strings($0.components(separatedBy: "\n")) }))
                LongField(title: "失控方式", value: text("loss_of_control"))
            }
            Section("欲望指纹") { ForEach(tendencies, id: \.0) { key, label in VStack { HStack { Text(label); Spacer(); Text("\(character["desire_profile"][key].int)").foregroundStyle(.secondary) }; Slider(value: Binding(get: { character["desire_profile"][key].double }, set: { character["desire_profile"][key] = .number($0.rounded()) }), in: 0...100, step: 1).accessibilityLabel(label) } } }
            Section("初始关系") {
                TextField("关系名称", text: relationText("label"))
                LongField(title: "已有的牵引", value: relationText("notes"))
                ForEach([("trust", "信任"), ("desire", "牵引"), ("tension", "张力")], id: \.0) { key, label in Stepper("\(label) · \(relation[key].int)", value: Binding(get: { relation[key].int }, set: { setRelation(key, .number(Double($0))) }), in: 0...100) }
            }
            Section("人物色彩") { HStack { ForEach(["#668475", "#b65e4b", "#8f6ac7", "#4d9085", "#ad8957", "#6a7da4"], id: \.self) { hex in Button { character["accent"] = .string(hex) } label: { Circle().fill(Color(hex: hex)).frame(width: 30, height: 30).overlay { if character["accent"].string == hex { Image(systemName: "checkmark").foregroundStyle(.white).font(.caption) } } .buttonStyle(.plain).accessibilityLabel("人物色 \(hex)").accessibilityAddTraits(character["accent"].string == hex ? .isSelected : []) } } } }
        }.navigationTitle(character["name"].string.isEmpty ? "新人物" : character["name"].string).navigationBarTitleDisplayMode(.inline)
    }
    private var relation: J { relationships.first { $0["from_character_id"] == character["id"] && $0["to_user"].bool } ?? ["label": "尚未命名", "notes": "", "trust": 0, "desire": 0, "tension": 0] }
    private func text(_ key: String) -> Binding<String> { Binding(get: { character[key].string }, set: { character[key] = .string($0) }) }
    private func relationText(_ key: String) -> Binding<String> { Binding(get: { relation[key].string }, set: { setRelation(key, .string($0)) }) }
    private func setRelation(_ key: String, _ value: J) {
        var r = relation; r["from_character_id"] = character["id"]; r["to_user"] = true; r[key] = value
        if let i = relationships.firstIndex(where: { $0["from_character_id"] == character["id"] && $0["to_user"].bool }) { relationships[i] = r } else { relationships.append(r) }
    }
}
struct RoomEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    var worldID: String
    var completion: (String) -> Void
    @State private var name = ""
    @State private var location = ""
    @State private var members: Set<String> = []
    @State private var error: String?
    @State private var saving = false
    var body: some View {
        NavigationStack {
            Form {
                if let world = app.world(worldID) {
                    Section { TextField("会面名称", text: $name); Picker("地点", selection: $location) { Text("选择地点").tag(""); ForEach(world.knownLocations(characterID: "player"), id: \.self) { Text($0["name"].string).tag($0["id"].string) } } }
                    Section("在场的人") { ForEach(world.characters, id: \.self) { c in Toggle(c["name"].string, isOn: Binding(get: { members.contains(c["id"].string) }, set: { if $0 { members.insert(c["id"].string) } else { members.remove(c["id"].string) } })).disabled(world.availability(c["id"].string) == "unavailable") } }
                }
                if let error { Text(error).foregroundStyle(.red) }
            }.navigationTitle("新的会面").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }; ToolbarItem(placement: .confirmationAction) { Button("开启") { save() }.disabled(saving || name.trimmed.isEmpty || location.isEmpty || members.isEmpty) } }
        }.interactiveDismissDisabled(saving)
    }
    private func save() {
        saving = true
        Task { defer { saving = false }; do { let ordered = app.world(worldID)?.characters.map { $0["id"].string }.filter { members.contains($0) } ?? []; let id = try await app.createRoom(worldID: worldID, name: name, locationID: location, characters: ordered); completion(id); dismiss() } catch { self.error = error.localizedDescription } }
    }
}

struct PlayerEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    var worldID: String
    @State private var profile: J = [:]
    @State private var age = ""
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Section("我的角色") {
                    TextField("姓名", text: text("name"))
                    TextField("成年年龄", text: $age).keyboardType(.numberPad)
                    TextField("称谓", text: text("pronouns"))
                    LongField(title: "外貌", value: text("appearance"))
                    LongField(title: "公开身份", value: text("identity"))
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }.scrollContentBackground(.hidden).background(Color.paper)
                .navigationTitle("我的角色").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) { Button("保存") { save() }.disabled(saving || app.workingWorld != nil || !(app.world(worldID)?.pending.isEmpty ?? true)) }
                }
        }.interactiveDismissDisabled(saving)
            .onAppear {
                guard !loaded else { return }; loaded = true
                profile = app.world(worldID)?.playerProfile ?? [:]
                if profile["age"].int > 0 { age = String(profile["age"].int) }
            }
    }
    private func text(_ key: String) -> Binding<String> { Binding(get: { profile[key].string }, set: { profile[key] = .string($0) }) }
    private func save() {
        if !age.trimmed.isEmpty {
            guard let value = Int(age.trimmed), (18...999).contains(value) else { error = "年龄需为 18 岁及以上"; return }
            profile["age"] = .number(Double(value))
        } else { profile["age"] = .null }
        for field in ["name", "pronouns", "appearance", "identity"] { profile[field] = .string(profile[field].string.trimmed) }
        saving = true
        Task { defer { saving = false }; do { try await app.savePlayerProfile(worldID, profile: profile); dismiss() } catch { self.error = error.localizedDescription } }
    }
}

struct WorldSavesView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    var worldID: String
    var didLoad: () -> Void
    @State private var saves: [WorldSave] = []
    @State private var name = ""
    @State private var saving = false
    @State private var loading = true
    @State private var selected: WorldSave?
    @State private var error: String?
    private var busy: Bool { saving || app.workingWorld != nil || !(app.world(worldID)?.pending.isEmpty ?? true) }
    var body: some View {
        NavigationStack {
            Form {
                Section("新的存档") {
                    TextField("存档名称", text: $name)
                    Button { create() } label: { HStack { Label("保存此刻", systemImage: "bookmark"); Spacer(); if saving { ProgressView() } } }.disabled(busy)
                }
                Section(app.world(worldID)?.name ?? "当前世界") {
                    if loading { ProgressView().frame(maxWidth: .infinity) }
                    else if saves.isEmpty { Text("还没有存档").foregroundStyle(.secondary) }
                    ForEach(saves) { save in
                        Button { selected = save } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                HStack { Text(save.name).font(.headline).foregroundStyle(.primary); Spacer(); Image(systemName: "arrow.counterclockwise").font(.caption) }
                                Text(save.worldTime).font(.subheadline).foregroundStyle(Color.moss)
                                Text(save.createdAt.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                            }.padding(.vertical, 5)
                        }.disabled(busy)
                    }
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }.scrollContentBackground(.hidden).background(Color.paper).navigationTitle("存档 / 读档").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("完成") { dismiss() }.disabled(saving) }
        }.interactiveDismissDisabled(saving).task { await refresh() }
            .confirmationDialog("读取这个存档？", isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } }), titleVisibility: .visible) {
                if let save = selected { Button("读取「\(save.name)」") { load(save) } }
            } message: { Text("当前进度会先自动存档。") }
    }
    private func refresh() async {
        defer { loading = false }
        do { saves = try await app.saves(worldID) } catch { self.error = error.localizedDescription }
    }
    private func create() {
        saving = true; error = nil
        Task { defer { saving = false }; do { try await app.createSave(worldID, name: name.trimmed); name = ""; await refresh() } catch { self.error = error.localizedDescription } }
    }
    private func load(_ save: WorldSave) {
        saving = true; error = nil
        Task { defer { saving = false }; do { try await app.loadSave(worldID, saveID: save.id); didLoad(); dismiss() } catch { self.error = error.localizedDescription } }
    }
}
