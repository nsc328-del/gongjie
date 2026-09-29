import SwiftUI
import GongjieCore

extension Color {
    static let paper = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(red: 0.07, green: 0.10, blue: 0.09, alpha: 1) : UIColor(red: 0.96, green: 0.95, blue: 0.91, alpha: 1) })
    static let card = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(red: 0.12, green: 0.16, blue: 0.14, alpha: 1) : .white.withAlphaComponent(0.7) })
    static let moss = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(red: 0.63, green: 0.79, blue: 0.69, alpha: 1) : UIColor(red: 0.22, green: 0.39, blue: 0.31, alpha: 1) })
    static let ink = Color.primary
    init(hex: String) {
        let value = UInt(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0x668475
        self.init(red: Double((value >> 16) & 255)/255, green: Double((value >> 8) & 255)/255, blue: Double(value & 255)/255)
    }
}
struct Eyebrow: View {
    var text: String
    var body: some View { Text(text).font(.caption.weight(.medium)).tracking(2).foregroundStyle(.secondary) }
}
struct ChapterTitle: View {
    var title: String
    var subtitle: String = ""
    var body: some View { VStack(alignment: .leading, spacing: 10) { Text(title).font(.system(.largeTitle, design: .serif).weight(.medium)); if !subtitle.isEmpty { Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineSpacing(5) } }.frame(maxWidth: .infinity, alignment: .leading) }
}
struct StoryCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View { content.padding(20).frame(maxWidth: .infinity, alignment: .leading).background(Color.card, in: RoundedRectangle(cornerRadius: 22)).overlay(RoundedRectangle(cornerRadius: 22).stroke(.primary.opacity(0.045), lineWidth: 1)) }
}
struct QuietButton: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View { configuration.label.opacity(configuration.isPressed ? 0.65 : 1).scaleEffect(configuration.isPressed && !reduceMotion ? 0.985 : 1).animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: configuration.isPressed) }
}
struct PrimaryButton: View {
    var title: String
    var icon: String
    var action: () -> Void
    var body: some View { Button(action: action) { Label(title, systemImage: icon).font(.body.weight(.medium)).frame(maxWidth: .infinity).padding(.vertical, 16).foregroundStyle(Color.paper).background(Color.moss, in: RoundedRectangle(cornerRadius: 18)) }.buttonStyle(QuietButton()) }
}
struct Face: View {
    var character: J
    var size: CGFloat = 52
    var body: some View {
        Group {
            if let art = CharacterArt(character) {
                Image(art.name).resizable().scaledToFill().frame(width: size, height: size, alignment: .top).clipped()
            } else {
                Text(String(character["name"].string.prefix(1))).font(.system(size: size * 0.4, weight: .medium, design: .serif)).foregroundStyle(Color(hex: character["accent"].string)).frame(width: size, height: size).background(Color(hex: character["accent"].string).opacity(0.13))
            }
        }.frame(width: size, height: size).clipShape(RoundedRectangle(cornerRadius: size * 0.25)).accessibilityHidden(true)
    }
}
struct CharacterArt {
    let name: String
    init?(_ character: J) {
        let key = character["art_key"].string
        guard ["daylight_lorne", "daylight_severin", "daylight_evelyn", "daylight_vesper"].contains(key) else { return nil }
        name = key
    }
}
struct CharacterPortrait: View {
    @Environment(\.dismiss) private var dismiss
    var character: J
    var canEnter = false
    var enter: (() -> Void)?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if let art = CharacterArt(character) {
                        Image(art.name).resizable().scaledToFit().frame(maxWidth: 560).clipShape(RoundedRectangle(cornerRadius: 22)).accessibilityLabel(character["name"].string + "的完整立绘")
                    } else { Face(character: character, size: 110).frame(maxWidth: .infinity).padding(40) }
                    ChapterTitle(title: character["name"].string, subtitle: "\(character["role"].string) · \(character["age"].int) 岁")
                    if !character["tagline"].string.isEmpty { Text(character["tagline"].string).font(.system(.body, design: .serif)).lineSpacing(7) }
                }.padding(22).frame(maxWidth: 604).frame(maxWidth: .infinity)
            }.background(Color.paper).navigationTitle("人物").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("完成") { dismiss() } }
                .safeAreaInset(edge: .bottom) {
                    if let enter { PrimaryButton(title: canEnter ? "去见\(character["pronouns"].string == "她" ? "她" : "他")" : "暂时无法相遇", icon: "arrow.up.right") { dismiss(); enter() }.disabled(!canEnter).padding().background(.regularMaterial) }
                }
        }
    }
}
struct ErrorBanner: View {
    var text: String
    var dismiss: () -> Void
    var body: some View { HStack(alignment: .top, spacing: 12) { Image(systemName: "exclamationmark.circle"); Text(text).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading); Button(action: dismiss) { Image(systemName: "xmark") }.accessibilityLabel("关闭提示") }.foregroundStyle(.primary).padding(16).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18)).padding(.horizontal) }
}
struct PendingCard: View {
    @Environment(AppModel.self) private var app
    var world: StoryWorld
    var body: some View {
        if !world.pending.isEmpty {
            StoryCard {
                if app.workingWorld == world.id {
                    HStack(spacing: 12) { ProgressView(); Text(["settling", "recovering"].contains(app.operationPhase) ? "正在记下这一刻的变化" : "余波等待记录").font(.subheadline) }
                } else {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("还有未记下的余波", systemImage: "bookmark.circle").font(.headline)
                        Button("重新记录") { app.recover(world.id) }.buttonStyle(.borderedProminent).tint(.moss).disabled(app.workingWorld != nil)
                    }
                }
            }
        }
    }
}
