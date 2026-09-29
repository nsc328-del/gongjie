import SwiftUI

@main struct GongjieApp: App {
    @State private var app = AppModel()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            Group {
                if let error = app.startupError { ContentUnavailableView("故事暂时无法打开", systemImage: "externaldrive.badge.exclamationmark", description: Text(error)) }
                else { LibraryView() }
            }
            .environment(app).tint(.moss)
            .overlay(alignment: .top) {
                if let error = app.error { ErrorBanner(text: error) { app.error = nil }.padding(.top, 8) }
                else if let notice = app.notice { ErrorBanner(text: notice) { app.notice = nil }.padding(.top, 8) }
            }
            .onChange(of: scenePhase) { _, value in if value == .background { app.background() } }
        }
    }
}
