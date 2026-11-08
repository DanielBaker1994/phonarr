import SwiftUI

struct TitleDetailView: View {
    @Environment(AppModel.self) private var model
    let type: MediaType
    let id: Int
    var seed: MediaCard? = nil

    @State private var detail: TitleDetail?
    @State private var error: String?

    private var status: MediaStatus { model.status(type, id, fallback: detail?.status ?? seed?.status ?? .idle) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                actionArea
                if let overview = detail?.overview ?? seed?.overview, !overview.isEmpty {
                    Text(overview).font(.body).foregroundStyle(.primary.opacity(0.9))
                }
                metadata
                if type == .series, let eps = detail?.episodes, !eps.isEmpty { EpisodeList(episodes: eps) }
                MoreLikeThisRow(type: type, id: id, title: detail?.title ?? seed?.title ?? "")
                if let error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
            }
            .padding()
        }
        .background(alignment: .top) { backdrop }
        .navigationTitle(detail?.title ?? seed?.title ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .onAppear { model.beginWatchingActivity() }
        .onDisappear { model.endWatchingActivity() }
        .onChange(of: status) { old, new in
            if old.state != new.state { Task { await load() } }
        }
    }

    private func load() async {
        do { detail = try await model.api.detail(type, id: id); error = nil } catch { self.error = error.localizedDescription }
    }

    private var posterURL: URL? { (detail?.poster ?? seed?.poster).flatMap(URL.init(string:)) }

    private var backdrop: some View {
        RemoteImage(url: (detail?.fanart).flatMap(URL.init(string:)) ?? posterURL, maxPixel: 1200)
            .frame(height: 320)
            .clipped()
            .overlay(LinearGradient(colors: [.clear, Color(.systemBackground)], startPoint: .top, endPoint: .bottom))
            .ignoresSafeArea(edges: .top)
            .opacity(0.55)
    }

    private var header: some View {
        HStack(alignment: .bottom, spacing: 16) {
            PosterCard(title: nil, year: nil, posterURL: posterURL, status: status, width: 130)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(detail?.title ?? seed?.title ?? "").font(.title2.bold()).lineLimit(3)
                HStack(spacing: 8) {
                    if let y = detail?.year ?? seed?.year { Text(String(y)) }
                    if let r = detail?.runtime, r > 0 { Text("\(r) min") }
                    if let c = detail?.certification, !c.isEmpty {
                        Text(c).padding(.horizontal, 4).overlay(RoundedRectangle(cornerRadius: 3).stroke(.secondary))
                    }
                }
                .font(.subheadline).foregroundStyle(.secondary)
                StatusLine(status: status)
            }
        }
        .padding(.top, 120)
    }

    private var card: MediaCard {
        seed ?? MediaCard(type: type, id: id, title: detail?.title, year: detail?.year, genres: detail?.genres ?? [],
                          poster: detail?.poster, overview: detail?.overview, status: status)
    }

    /// The app-wide plan is refreshed when defaults change, so it wins over the one loaded with the details.
    private var plan: RequestPlan? { model.requestPlans[type] ?? detail?.requestPlan }

    private func customize() { model.customizing = CustomizeTarget(cards: [card]) }

    /// Not requested: Add to Library (uses the plan shown under it) + Change for this request.
    /// Failed: reason + Retry. Anything else: the status line in the header says it all.
    @ViewBuilder private var actionArea: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch status.state {
            case .notRequested:
                Button {
                    if plan?.problem != nil { customize() } else { Task { await model.add(type, id: id) } }
                } label: {
                    Label("Add to Library", systemImage: "plus").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(model.isServiceDown(type))
                RequestPlanSummary(plan: plan, change: customize)
            case .submitting:
                Button {} label: { ProgressView().frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).controlSize(.large).disabled(true)
            case .failed:
                FailureBox(status: status) { Task { await model.retry(type, id: id) } }
            default:
                EmptyView()
            }
        }
    }

    @ViewBuilder private var metadata: some View {
        let genres = detail?.genres ?? seed?.genres ?? []
        VStack(alignment: .leading, spacing: 6) {
            if !genres.isEmpty { MetaRow(label: "Genres", value: genres.joined(separator: ", ")) }
            if let s = detail?.studio, !s.isEmpty { MetaRow(label: type == .movie ? "Studio" : "Network", value: s) }
            if let n = detail?.seasonCount, n > 0 { MetaRow(label: "Seasons", value: String(n)) }
            if let s = detail?.seriesStatus { MetaRow(label: "Status", value: s.capitalized) }
            if let l = detail?.language { MetaRow(label: "Language", value: l) }
            if let c = detail?.collection { MetaRow(label: "Collection", value: c) }
            if let r = detail?.ratings, !r.isEmpty {
                MetaRow(label: "Ratings", value: r.sorted { $0.key < $1.key }
                    .map { "\(Self.ratingSource($0.key)) \(Self.ratingText($0.key, $0.value))" }.joined(separator: " · "))
            }
            if let k = detail?.keywords, !k.isEmpty { MetaRow(label: "Themes", value: k.prefix(6).joined(separator: ", ")) }
        }
    }
}

extension TitleDetailView {
    static func ratingSource(_ key: String) -> String {
        switch key {
        case "imdb": "IMDb"
        case "tmdb": "TMDB"
        case "tvdb": "TVDB"
        case "rottenTomatoes": "RT"
        case "metacritic": "Metacritic"
        case "trakt": "Trakt"
        default: key
        }
    }

    /// Rotten Tomatoes and Metacritic are 0–100; the rest are 0–10.
    static func ratingText(_ key: String, _ value: Double) -> String {
        key == "rottenTomatoes" || key == "metacritic" ? "\(Int(value))%" : String(format: "%.1f", value)
    }
}

struct MetaRow: View {
    let label: String
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary).frame(width: 80, alignment: .leading)
            Text(value)
        }
        .font(.subheadline)
    }
}

struct StatusLine: View {
    let status: MediaStatus
    var body: some View {
        if status.state != .notRequested {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Image(systemName: status.state.systemImage)
                    Text(status.state.label)
                    if let p = status.progress, status.state == .downloading {
                        Text("\(Int((p * 100).rounded()))%").monospacedDigit()
                    }
                    if status.stale == true { Text("(last known)").foregroundStyle(.secondary) }
                }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(status.state == .failed ? Color.red : status.state == .complete ? Color.green : Color.primary)
                if let p = status.progress, status.state.isActive {
                    ProgressView(value: p).tint(status.state == .stalled || status.state == .paused ? .orange : .accentColor)
                }
                if let have = status.episodesHave, let want = status.episodesWanted, want > 0 {
                    Text("\(have) of \(want) episodes").font(.caption).foregroundStyle(.secondary)
                }
                if let m = status.message, status.state != .failed {
                    Text(m).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
        }
    }
}

struct FailureBox: View {
    let status: MediaStatus
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(status.isConfigProblem ? "Needs attention in Settings" : "Download failed",
                  systemImage: status.isConfigProblem ? "gearshape.fill" : "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(.red)
            if let m = status.message { Text(m).font(.subheadline) }
            HStack {
                Button("Retry", systemImage: "arrow.clockwise", action: retry).buttonStyle(.borderedProminent).tint(.red)
                if status.isConfigProblem {
                    NavigationLink("Open request defaults") { DefaultsView() }.font(.subheadline)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
    }
}

/// "Uses HD-1080p · /movies · All seasons   Change" under Add to Library, so you know what a tap will do.
struct RequestPlanSummary: View {
    let plan: RequestPlan?
    let change: () -> Void

    var body: some View {
        if let plan {
            VStack(alignment: .leading, spacing: 6) {
                if let problem = plan.problem {
                    Label(problem.message, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(.orange)
                    HStack(spacing: 16) {
                        Button("Choose for this one", action: change)
                        NavigationLink("Set defaults") { DefaultsView() }
                    }
                    .font(.footnote.weight(.semibold))
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        Image(systemName: "slider.horizontal.3").foregroundStyle(.secondary)
                        Text("Uses ").foregroundColor(.secondary) + Text(plan.summary)
                        Spacer(minLength: 8)
                        Button("Change", action: change).fontWeight(.semibold)
                    }
                    .font(.footnote)
                    Text("Change applies to this request only. Your defaults are in Settings.")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .accessibilityElement(children: .contain)
        }
    }
}

struct EpisodeList: View {
    let episodes: [Episode]

    var body: some View {
        let grouped = Dictionary(grouping: episodes, by: \.season)
        let seasons = grouped.keys.sorted()
        VStack(alignment: .leading, spacing: 14) {
            Text("Episodes").font(.title3.bold())
            ForEach(seasons, id: \.self) { season in
                let eps = grouped[season] ?? []
                DisclosureGroup {
                    ForEach(eps) { ep in
                        HStack {
                            Text("\(ep.episode)").monospacedDigit().foregroundStyle(.secondary).frame(width: 28, alignment: .leading)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(ep.title ?? "Episode \(ep.episode)").lineLimit(1)
                                if let p = ep.progress, ep.state.isActive { ProgressView(value: p) }
                                if ep.state == .failed, let m = ep.message {
                                    Text(m).font(.caption).foregroundStyle(.red).lineLimit(2)
                                }
                            }
                            Spacer()
                            Image(systemName: ep.state.systemImage)
                                .foregroundStyle(ep.state == .failed ? Color.red : ep.state == .complete ? Color.green : Color.secondary)
                                .accessibilityLabel(ep.state.label)
                        }
                        .font(.subheadline)
                        .padding(.vertical, 2)
                    }
                } label: {
                    let done = eps.filter { $0.state == .complete }.count
                    HStack {
                        Text(season == 0 ? "Specials" : "Season \(season)").font(.headline)
                        Spacer()
                        Text("\(done)/\(eps.count)").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

/// One-off choices for one title or a multi-select batch. Never changes the saved defaults.
struct CustomizeRequestSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let target: CustomizeTarget

    @State private var choices: [MediaType: RequestOverride] = [:]

    private var single: MediaCard? { target.cards.count == 1 ? target.cards[0] : nil }

    var body: some View {
        NavigationStack {
            Form {
                ForEach(target.types) { type in
                    let opts = model.options?.options(for: type)
                    let count = target.cards.filter { $0.type == type }.count
                    Section {
                        picker("Quality profile", opts?.qualityProfiles ?? [], binding(type, \.qualityProfileId))
                        folderPicker(opts?.rootFolders ?? [], binding(type, \.rootFolder))
                        if type == .series {
                            Picker("Seasons", selection: binding(type, \.seasonMonitor)) {
                                ForEach(Defaults.seasonModes, id: \.self) { Text(Defaults.seasonLabel($0)).tag(String?.some($0)) }
                            }
                        }
                        if let err = opts?.error { Text(err.message).font(.footnote).foregroundStyle(.orange) }
                    } header: {
                        if single == nil { Text(type == .movie ? "\(count) film\(count == 1 ? "" : "s")" : "\(count) show\(count == 1 ? "" : "s")") }
                    }
                }
                Section {
                    Text(single == nil
                         ? "Applies to these \(target.cards.count) titles only. Titles already in your library are skipped, failed ones are retried. Your saved defaults stay the same."
                         : "Applies to this request only. Your saved defaults stay the same.")
                        .font(.footnote).foregroundStyle(.secondary)
                    NavigationLink("Change my defaults instead") { DefaultsView() }.font(.footnote)
                }
            }
            .navigationTitle(single?.title ?? "Add \(target.cards.count) titles")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(single == nil ? "Add \(target.cards.count)" : "Request") { send() }
                        .disabled(!ready)
                }
            }
        }
        .task {
            if model.options == nil { await model.refreshOptions() }
            prefill()   // after options load, so the pickers start on real values
        }
    }

    /// Every type needs a profile and a folder before we can send.
    private var ready: Bool {
        target.types.allSatisfy { choices[$0]?.qualityProfileId != nil && choices[$0]?.rootFolder != nil }
    }

    private func send() {
        let movie = choices[.movie], series = choices[.series]
        let cards = target.cards, onSent = target.onSent
        dismiss()
        Task {
            if let one = cards.first, cards.count == 1 {
                await model.add(one.type, id: one.id, override: one.type == .movie ? movie : series)
            } else {
                await model.addBatch(cards, movie: movie, series: series)
            }
            onSent?()
        }
    }

    private func prefill() {
        for type in target.types {
            let plan = model.requestPlans[type]
            let d = model.options?.defaults
            let opts = model.options?.options(for: type)
            var o = RequestOverride()
            o.qualityProfileId = plan?.qualityProfileId ?? (type == .movie ? d?.radarrProfileId : d?.sonarrProfileId)
                ?? opts?.qualityProfiles?.first?.id
            o.rootFolder = plan?.rootFolder ?? (type == .movie ? d?.radarrRoot : d?.sonarrRoot) ?? opts?.rootFolders?.first?.path
            if type == .series { o.seasonMonitor = plan?.seasonMonitor ?? d?.seasonMonitor ?? "all" }
            choices[type] = o
        }
    }

    private func binding<T>(_ type: MediaType, _ key: WritableKeyPath<RequestOverride, T>) -> Binding<T> {
        Binding(get: { (choices[type] ?? RequestOverride())[keyPath: key] },
                set: { var o = choices[type] ?? RequestOverride(); o[keyPath: key] = $0; choices[type] = o })
    }

    @ViewBuilder private func picker(_ label: String, _ profiles: [QualityProfile], _ sel: Binding<Int?>) -> some View {
        if profiles.count == 1 {
            LabeledContent(label, value: profiles[0].name)
        } else {
            Picker(label, selection: sel) {
                if sel.wrappedValue == nil { Text("Choose…").tag(Int?.none) }
                ForEach(profiles) { Text($0.name).tag(Int?.some($0.id)) }
            }
        }
    }

    @ViewBuilder private func folderPicker(_ roots: [RootFolder], _ sel: Binding<String?>) -> some View {
        if roots.count == 1 {
            LabeledContent("Root folder", value: roots[0].path)
        } else {
            Picker("Root folder", selection: sel) {
                if sel.wrappedValue == nil { Text("Choose…").tag(String?.none) }
                ForEach(roots) { Text($0.path).tag(String?.some($0.path)) }
            }
        }
    }
}
