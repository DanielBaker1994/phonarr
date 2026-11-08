import SwiftUI

@main
struct PhonarrApp: App {
    @State private var model = AppModel()

    init() {
        let info = Bundle.main.infoDictionary ?? [:]
        let v = "\(info["CFBundleShortVersionString"] as? String ?? "?") (\(info["CFBundleVersion"] as? String ?? "?"), \(info["AYRGitCommit"] as? String ?? "?"))"
        AppLog.info("launch", ["version": v, "os": ProcessInfo.processInfo.operatingSystemVersionString])
        CrashReporter.shared.start()
    }
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .onChange(of: scenePhase) { _, phase in model.scenePhaseChanged(phase) }
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if !model.isPaired {
                OnboardingView()
            } else if !model.onboardingComplete {
                SetupFlowView()
            } else {
                MainTabs()
            }
        }
        .task(id: model.isPaired) {
            guard model.isPaired else { return }
            await model.refreshConfig()
            async let o: Void = model.refreshOptions()
            async let d: Void = model.reloadDiscovery()
            _ = await (o, d)
            model.refreshNow()
        }
        .overlay(alignment: .bottom) { ToastView() }
    }
}

struct MainTabs: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        TabView {
            DiscoverView().tabItem { Label("Discover", systemImage: "sparkles") }
            BrowseTab(type: .movie).tabItem { Label("Movies", systemImage: "film") }
            BrowseTab(type: .series).tabItem { Label("TV Shows", systemImage: "tv") }
            ActivityView().tabItem { Label("Activity", systemImage: "arrow.down.circle") }
            SettingsView().tabItem { Label("Settings", systemImage: "gearshape") }
        }
        // One Customize Request sheet for the whole app: details, poster menus and multi-select all use it.
        .sheet(item: $model.customizing) { target in
            CustomizeRequestSheet(target: target)
                .presentationDetents([.medium, .large])
        }
    }
}

struct ToastView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let text = model.toast {
            Text(text)
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(.regularMaterial, in: Capsule())
                .padding(.bottom, 64)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .task(id: text) {
                    try? await Task.sleep(for: .seconds(2))
                    withAnimation { model.toast = nil }
                }
        }
    }
}
