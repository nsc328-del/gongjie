import SwiftUI
import GongjieCore

struct SettingsView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @AppStorage("readingSize") private var readingSize = 19.0
    @State private var profile = ProviderProfile()
    @State private var key = ""
    @State private var hasKey = false
    @State private var checking = false
    @State private var saving = false
    @State private var status = ""
    @State private var failed = false
    @State private var removeKey = false
    @State private var testTask: Task<Void, Never>?
    var body: some View {
        NavigationStack {
            Form {
                Section("模型连接") {
                    Button("使用 DeepSeek V4 Flash") { profile.wireProtocol = "openai-compatible"; profile.baseURL = "https://api.deepseek.com"; profile.path = "/chat/completions"; profile.model = "deepseek-v4-flash"; status = "" }.disabled(checking || saving)
                    Picker("协议", selection: Binding(get: { profile.wireProtocol }, set: { profile.wireProtocol = $0; profile.path = $0 == "anthropic-messages" ? "/v1/messages" : "/chat/completions" })) { Text("OpenAI 兼容").tag("openai-compatible"); Text("Anthropic Messages").tag("anthropic-messages") }
                    TextField("服务地址", text: $profile.baseURL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("接口路径", text: $profile.path).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("模型名称", text: $profile.model).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField(hasKey ? "已保存密钥 · 留空保持" : "API Key", text: $key).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button { test() } label: { HStack { Text("测试连接"); Spacer(); if checking { ProgressView() } } }.disabled(checking || saving)
                    if !status.isEmpty { Text(status).foregroundStyle(failed ? Color.red : Color.moss) }
                }
                Section("回复") {
                    TextField("回复长度", value: $profile.maxTokens, format: .number).keyboardType(.numberPad)
                    LabeledContent("创造性", value: profile.temperature.formatted(.number.precision(.fractionLength(2))))
                    Slider(value: $profile.temperature, in: 0...2, step: 0.05).accessibilityLabel("创造性")
                }
                Section("阅读") { LabeledContent("正文字号", value: "\(Int(readingSize))"); Slider(value: $readingSize, in: 16...28, step: 1).accessibilityLabel("正文字号"); Text("每一次相遇，都留下回声。").font(.system(size: readingSize, design: .serif)).lineSpacing(8).padding(.vertical, 12) }
                if hasKey { Section { Button("移除模型密钥", role: .destructive) { removeKey = true }.disabled(checking || saving || app.workingWorld != nil) } }
            }.scrollContentBackground(.hidden).background(Color.paper)
            .navigationTitle("连接与阅读").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() } }; ToolbarItem(placement: .confirmationAction) { Button("保存") { save() }.disabled(checking || saving || app.workingWorld != nil) } }
        }
        .onAppear { profile = app.archive.profile; hasKey = (try? KeyVault.read()) != nil }
        .onDisappear { testTask?.cancel() }
        .confirmationDialog("移除设备上的模型密钥？", isPresented: $removeKey, titleVisibility: .visible) { Button("移除密钥", role: .destructive) { do { try KeyVault.delete(); key = ""; hasKey = false } catch { failed = true; status = error.localizedDescription } } }
    }
    private func test() {
        checking = true; status = ""; failed = false
        let selected = profile, enteredKey = key
        testTask = Task { defer { checking = false }; do {
            try selected.validate()
            let secret = enteredKey.trimmed.isEmpty ? try KeyVault.read() : enteredKey.trimmed
            let reply = try await ModelClient().generate(ModelRequest(system: "只回复：连接成功", messages: [["role": "user", "content": "测试"]], maxTokens: 512, temperature: 0), profile: selected, key: secret) { _ in }
            if !Task.isCancelled { status = reply.text }
        } catch { if !Task.isCancelled { failed = true; status = error.localizedDescription } } }
    }
    private func save() {
        saving = true
        Task { defer { saving = false }; do {
            try profile.validate()
            if !key.trimmed.isEmpty { try KeyVault.save(key) }
            try await app.engine?.store.saveProfile(profile); await app.refresh(); dismiss()
        } catch { failed = true; status = error.localizedDescription } }
    }
}
