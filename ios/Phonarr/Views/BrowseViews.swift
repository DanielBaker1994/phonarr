import SwiftUI

struct DiscoverView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    ServiceBanner()
                    if !inProgress.isEmpty {
                        PosterRow(title: "In progress", items: inProgress)
                    }
                    if !recommended.isEmpty {
                        PosterRow(title: "Recommended by Radarr", items: recommended)
                    }
                    if model.hasSection(.movie) {
                        PosterRow(title: "Films to add", items: model.catalog[.movie] ?? [], seeAll: .movie)
                    }
                    if model.hasSection(.series) {
                        PosterRow(title: "TV to add", items: model.catalog[.series] ?? [], seeAll: .series)
                    }
                    // Shelves generated from metadata (rating, era, runtime, genre) across films and TV.
                    ForEach(shelves) { shelf in PosterRow(title: shelf.title, items: shelf.items) }
                    ForEach(MediaType.allCases.filter { !model.hasSection($0) }) { type in
                        ConnectServerCard(type: type).padding(.horizontal)
                    }
                    if model.hasSection(.movie) || model.hasSection(.series),
                       (model.catalog[.movie] ?? []).isEmpty && (model.catalog[.series] ?? []).isEmpty
                        && model.loading.isEmpty {
                        EmptyCatalogView()
                    }
                }
                .padding(.vertical)
            }
            .navigationTitle("Discover")
            .refreshable {
                await model.reloadDiscovery()
                await model.loadActivity()
            }
            .task { await model.loadActivity() }
            .navigationDestination(for: MediaCard.self) { TitleDetailView(type: $0.type, id: $0.id, seed: $0) }
            .navigationDestination(for: MediaType.self) { MediaGridView(type: $0) }
        }
    }

    private var shelves: [Shelf] {
        ShelfBuilder.build((model.catalog[.movie] ?? []) + (model.catalog[.series] ?? []))
    }

    private var recommended: [MediaCard] {
        (model.catalog[.movie] ?? []).filter { $0.source == DiscoverySource.recommended.rawValue
            && model.status(for: $0).state == .notRequested }
    }

    /// From Activity (the catalogue hides requested titles after a reload), plus anything just added here.
    private var inProgress: [MediaCard] {
        let fromActivity = model.activity.map {
            MediaCard(type: $0.type, id: $0.id, title: $0.title, year: $0.year, genres: [], poster: $0.poster,
                      overview: nil, status: $0.status)
        }
        var seen = Set<String>()
        return (fromActivity + (model.catalog[.movie] ?? []) + (model.catalog[.series] ?? [])).filter { card in
            let s = model.status(for: card).state
            return (s.isActive || s == .searching || s == .failed) && seen.insert(card.key).inserted
        }
    }
}

struct PosterRow: View {
    @Environment(AppModel.self) private var model
    let title: String
    let items: [MediaCard]
    var seeAll: MediaType? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.title3.bold())
                Spacer()
                if let seeAll, !items.isEmpty {
                    NavigationLink("See all", value: seeAll).font(.subheadline)
                }
            }
            .padding(.horizontal)
            if items.isEmpty {
                Text(model.loading.isEmpty ? "Nothing new to add." : "Loading…")
                    .font(.subheadline).foregroundStyle(.secondary).padding(.horizontal)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 14) {
                        ForEach(items, id: \.key) { card in
                            PosterCell(card: card, width: 124)
                        }
                    }
                    .padding(.horizontal)
                    .scrollTargetLayout()
                }
                .scrollTargetBehavior(.viewAligned)
            }
        }
    }
}

/// A poster in a row or grid.
/// - Tap the poster: opens details (no popup).
/// - Tap the + on the poster: adds it right away with your defaults (no popup). On a failed title it's a Retry.
/// - Long-press: Add (shows the profile it will use), Customize Request…, Select (multi-select grids).
/// - In multi-select mode (`selection` non-nil) a tap toggles the checkmark instead.
struct PosterCell: View {
    @Environment(AppModel.self) private var model
    let card: MediaCard
    var width: CGFloat? = nil
    var caption: String? = nil
    var selection: Binding<Set<String>>? = nil
    var onSelect: (() -> Void)? = nil

    var body: some View {
        let status = model.status(for: card)
        if let selection {
            let selectable = status.state == .notRequested || status.state == .failed
            let selected = selection.wrappedValue.contains(card.key)
            Button {
                if selected { selection.wrappedValue.remove(card.key) } else { selection.wrappedValue.insert(card.key) }
                UISelectionFeedbackGenerator().selectionChanged()
            } label: {
                cell(status, mark: selectable ? (selected ? .selected : .unselected) : .none)
            }
            .buttonStyle(.plain)
            .disabled(!selectable)
            .opacity(selectable ? 1 : 0.45)
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityHint(selectable ? "Double-tap to select" : "Already requested")
        } else {
            NavigationLink(value: card) { cell(status, mark: .none) }
                .buttonStyle(.plain)
                .contextMenu { menu(status) } preview: {
                    PosterCard(title: card.title, year: card.year, posterURL: card.posterURL, status: status, width: 220)
                        .padding()
                }
        }
    }

    private func cell(_ status: MediaStatus, mark: PosterCard.Mark) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            PosterCard(title: card.title, year: card.year, posterURL: card.posterURL, status: status, width: width,
                       mark: mark, quickAction: selection == nil ? quickAction(status) : nil)
            if let rating = card.rating {
                Text("★ \(String(format: "%.1f", rating))").font(.caption2.weight(.semibold)).monospacedDigit()
                    .frame(width: width, alignment: .leading)
            }
            let strip = RatingStrip.items(card)
            if !strip.isEmpty {
                Text(strip.map { "\($0.value) \($0.label)" }.joined(separator: "  "))
                    .font(.system(size: 10)).monospacedDigit().foregroundStyle(.secondary).lineLimit(1)
                    .frame(width: width, alignment: .leading)
                    .accessibilityLabel("Ratings: " + strip.map { "\($0.label) \($0.value)" }.joined(separator: ", "))
            }
            if let caption {
                Text(caption).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    .frame(width: width, alignment: .leading)
            }
        }
    }

    private func quickAction(_ status: MediaStatus) -> PosterCard.QuickAction? {
        switch status.state {
        case .notRequested:
            if model.isServiceDown(card.type) { return nil }   // the banner explains; a tap would just fail
            if model.requestPlans[card.type]?.problem != nil {
                // No usable default yet: ask once instead of failing.
                return PosterCard.QuickAction(kind: .add) { model.customizing = CustomizeTarget(cards: [card]) }
            }
            return PosterCard.QuickAction(kind: .add) { Task { await model.add(card.type, id: card.id) } }
        case .failed:
            return PosterCard.QuickAction(kind: .retry) { Task { await model.retry(card.type, id: card.id) } }
        default:
            return nil
        }
    }

    @ViewBuilder private func menu(_ status: MediaStatus) -> some View {
        if status.state == .notRequested {
            Button { Task { await model.add(card.type, id: card.id) } } label: {
                Label("Add to Library", systemImage: "plus")
                if let plan = model.requestPlans[card.type] {
                    Text(plan.problem?.message ?? "Uses \(plan.summary)")
                }
            }
            .disabled(model.requestPlans[card.type]?.problem != nil)
            Button("Customize Request…", systemImage: "slider.horizontal.3") {
                model.customizing = CustomizeTarget(cards: [card])
            }
        } else if status.state == .failed {
            Button("Retry", systemImage: "arrow.clockwise") { Task { await model.retry(card.type, id: card.id) } }
        }
        if let onSelect, status.state == .notRequested || status.state == .failed {
            Button("Select", systemImage: "checkmark.circle", action: onSelect)
        }
    }
}

struct MediaGridView: View {
    @Environment(AppModel.self) private var model
    let type: MediaType
    @State private var query = ""
    @State private var showFilters = false
    @State private var selecting = false
    @State private var selection: Set<String> = []
    @State private var searchResults: [MediaCard] = []
    @State private var searchError: String?
    @State private var searchingRemote = false
    @State private var searchRun = 0   // the newest runSearch; older ones never touch the results or spinner
    @State private var sending = false
    /// No cap: every match is reachable. A page is shown at a time and the next is added as the end comes into view.
    @State private var limit = AppConfig.int("filters.page_size", default: 60)

    private let columns = [GridItem(.adaptive(minimum: 104, maximum: 160), spacing: 14, alignment: .top)]

    private var filters: Binding<FilterState> {
        Binding(get: { model.filters[type] ?? FilterState() }, set: { model.filters[type] = $0 })
    }

    private var noun: String { type == .movie ? "films" : "shows" }

    var body: some View {
        if !model.hasSection(type) {
            ScrollView { ConnectServerCard(type: type).padding() }
                .navigationTitle(type == .movie ? "Movies" : "TV Shows")
        } else {
            grid
        }
    }

    private var grid: some View {
        let all = model.catalog[type] ?? []
        let neighbourMap = model.facets[type]?.genreNeighbours
        let shown = filters.wrappedValue.apply(all, query: query, neighbourMap: neighbourMap)
        let shownKeys = Set(shown.map(\.key))
        let remote = filters.wrappedValue.apply(searchResults, query: "", neighbourMap: neighbourMap)
            .filter { !shownKeys.contains($0.key) }
        let visible = shown + remote
        return ScrollView {
            ServiceBanner(only: type)
            if let err = model.discoverError[type] {
                Label(err, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.orange)
                    .padding(.horizontal)
            }
            ActiveFilterBar(filters: filters, shown: shown.count, total: all.count)
            LazyVGrid(columns: columns, spacing: 18) {
                ForEach(shown.prefix(limit), id: \.key) { card in cell(card) }
            }
            .padding(.horizontal)
            .padding(.bottom)
            if shown.count > limit {
                Button("Show more (\(shown.count - limit) left)") { limit += pageSize }
                    .buttonStyle(.bordered)
                    .padding(.bottom)
                    .onAppear { limit += pageSize }
            }
            if query.count >= 2 {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("From \(type == .movie ? "Radarr" : "Sonarr") search").font(.headline)
                        if searchingRemote { ProgressView().controlSize(.small) }
                    }
                    if let searchError { Text(searchError).font(.footnote).foregroundStyle(.orange) }
                    if !searchingRemote && remote.isEmpty && searchError == nil {
                        Text("No other matches for “\(query)”.").font(.subheadline).foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: columns, spacing: 18) {
                        ForEach(remote, id: \.key) { card in cell(card) }
                    }
                }
                .padding(.horizontal)
                .padding(.bottom)
            }
            if shown.isEmpty && query.count < 2 && !model.loading.contains(type) {
                ContentUnavailableView(all.isEmpty ? "Nothing to add" : "No matches",
                                       systemImage: type == .movie ? "film" : "tv",
                                       description: Text(all.isEmpty
                                           ? "Everything discovery found is already requested. Search to find more \(noun)."
                                           : "Try removing a filter, or search to find more \(noun)."))
            }
        }
        .overlay { if model.loading.contains(type) && all.isEmpty { ProgressView() } }
        .navigationTitle(selecting ? (selection.isEmpty ? "Select \(noun)" : "\(selection.count) selected")
                                   : (type == .movie ? "Movies" : "TV Shows"))
        .navigationBarTitleDisplayMode(selecting ? .inline : .automatic)
        .searchable(text: $query, prompt: "Search \(noun) or find new ones")
        .task(id: query) { await runSearch() }
        .toolbar { toolbar(visible: visible) }
        .toolbar(selecting ? .hidden : .visible, for: .tabBar)
        .safeAreaInset(edge: .bottom) {
            if selecting { selectionBar(visible) }
        }
        .sheet(isPresented: $showFilters) {
            FilterSheet(type: type, filters: filters, facets: model.facets[type] ?? Facets(),
                        resultCount: { filters.wrappedValue.apply(all, query: query, neighbourMap: neighbourMap).count })
                .presentationDetents([.medium, .large])
        }
        .onChange(of: filters.wrappedValue) { _, _ in limit = pageSize }
        .onChange(of: query) { _, _ in limit = pageSize }
        .refreshable { await model.loadDiscover(type) }
        .task { if all.isEmpty { await model.loadDiscover(type) } }
        .onChange(of: model.statuses) { _, _ in
            // Drop anything that got requested elsewhere (another device, Radarr itself) from the selection.
            selection = selection.filter { key in
                let s = model.statuses[key]?.state ?? .notRequested
                return s == .notRequested || s == .failed
            }
        }
    }

    private func cell(_ card: MediaCard) -> some View {
        PosterCell(card: card, caption: caption(for: card), selection: selecting ? $selection : nil,
                   onSelect: { selecting = true; selection = [card.key] })
    }

    @ToolbarContentBuilder
    private func toolbar(visible: [MediaCard]) -> some ToolbarContent {
        if selecting {
            ToolbarItem(placement: .topBarLeading) {
                let selectable = visible.filter { let s = model.status(for: $0).state; return s == .notRequested || s == .failed }
                if selection.count < selectable.count {
                    Button("Select All") { selection = Set(selectable.map(\.key)) }
                } else {
                    Button("Deselect All") { selection = [] }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { selecting = false; selection = [] }.bold()
            }
        } else {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Select") { selecting = true }
                    .disabled(visible.isEmpty)
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Sort", selection: filters.sort) {
                        ForEach(SortOption.allCases) { Text($0.label).tag($0) }
                    }
                } label: {
                    Image(systemName: "arrow.up.arrow.down.circle")
                }
                .accessibilityLabel("Sort")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { showFilters = true } label: {
                    Image(systemName: filters.wrappedValue.activeCount > 0
                          ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                }
                .accessibilityLabel("Filters")
            }
        }
    }

    /// Bottom bar in multi-select: what will be used, Options… (one-off overrides for the batch), Add N.
    private func selectionBar(_ visible: [MediaCard]) -> some View {
        let chosen = visible.filter { selection.contains($0.key) }
        let plan = model.requestPlans[type]
        let retries = chosen.filter { model.status(for: $0).state == .failed }.count
        return VStack(alignment: .leading, spacing: 8) {
            if let problem = plan?.problem {
                Label(problem.message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote).foregroundStyle(.orange)
            } else if let plan {
                Text("Uses \(plan.summary)").font(.footnote).foregroundStyle(.secondary).lineLimit(1)
            }
            HStack {
                Button("Options…") {
                    model.customizing = CustomizeTarget(cards: chosen) { finish(failed: []) }
                }
                .buttonStyle(.bordered)
                .disabled(chosen.isEmpty || sending)
                Spacer()
                Button {
                    if plan?.problem != nil {
                        model.customizing = CustomizeTarget(cards: chosen) { finish(failed: []) }
                        return
                    }
                    sending = true
                    Task {
                        let failed = await model.addBatch(chosen)
                        sending = false
                        finish(failed: failed)
                    }
                } label: {
                    if sending { ProgressView() } else {
                        Text(chosen.isEmpty ? "Add" : retries == chosen.count ? "Retry \(chosen.count)"
                             : "Add \(chosen.count)").bold().frame(minWidth: 90)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(chosen.isEmpty || sending || model.isServiceDown(type))
            }
        }
        .padding()
        .background(.bar)
    }

    private func finish(failed: Set<String>) {
        selection = failed
        if failed.isEmpty { selecting = false }
    }

    private func runSearch() async {
        searchRun += 1
        let run = searchRun
        // Every exit of the newest search clears the spinner, including cancellation (KB-05).
        defer { if run == searchRun { searchingRemote = false } }
        let q = query.trimmingCharacters(in: .whitespaces)
        guard q.count >= 2 else { searchResults = []; searchError = nil; return }
        try? await Task.sleep(for: .milliseconds(450))   // debounce while typing
        guard !Task.isCancelled else { return }
        searchingRemote = true
        let r = await model.search(type, query: q)
        guard !Task.isCancelled, run == searchRun else { return }
        searchResults = r.items
        searchError = r.error
    }

    private var pageSize: Int { AppConfig.int("filters.page_size", default: 60) }

    /// Under each poster: the genre a title matched through ("via Thriller"), or the length when sorting by it.
    /// The rating is always shown by the poster cell itself.
    private func caption(for card: MediaCard) -> String? {
        if let via = filters.wrappedValue.viaNeighbour(card, model.facets[type]?.genreNeighbours ?? [:]) { return "via \(via)" }
        switch filters.wrappedValue.sort {
        case .runtimeAsc, .runtimeDesc: return card.runtime.map { "\($0) min" }
        default: return nil
        }
    }
}

struct BrowseTab: View {
    let type: MediaType
    var body: some View {
        NavigationStack {
            MediaGridView(type: type)
                .navigationDestination(for: MediaCard.self) { TitleDetailView(type: $0.type, id: $0.id, seed: $0) }
        }
    }
}

struct ServiceBanner: View {
    @Environment(AppModel.self) private var model
    var only: MediaType? = nil

    var body: some View {
        if !model.gatewayReachable {
            Label("Can't reach the gateway. Showing saved results.", systemImage: "icloud.slash")
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal)
        }
        let down = ["radarr", "sonarr"].filter { svc in
            let type: MediaType = svc == "radarr" ? .movie : .series
            return (only == nil || only == type) && model.services[svc]?.reachable == false
        }
        ForEach(down, id: \.self) { svc in
            Label(model.services[svc]?.error?.message ?? "\(svc.capitalized) is unavailable.",
                  systemImage: "wifi.exclamationmark")
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal)
        }
    }
}

struct EmptyCatalogView: View {
    var body: some View {
        ContentUnavailableView {
            Label("Nothing to discover yet", systemImage: "sparkles")
        } description: {
            Text("Search in Movies or TV Shows to find anything Radarr or Sonarr can look up. Discovery also fills in from your Radarr import lists, Radarr's recommendations, and the titles list on the gateway (titles.json).")
        }
        .padding(.top, 40)
    }
}

/// Shown in place of a section when its server isn't connected. Never blocks the other section.
struct ConnectServerCard: View {
    @Environment(AppModel.self) private var model
    let type: MediaType

    private var service: String { type == .movie ? "radarr" : "sonarr" }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(type == .movie ? "Films need Radarr" : "TV shows need Sonarr",
                  systemImage: type == .movie ? "film" : "tv")
                .font(.headline)
            Text("Connect \(service.capitalized) to browse and add \(type == .movie ? "films" : "TV shows"). Everything else keeps working without it.")
                .font(.subheadline).foregroundStyle(.secondary)
            NavigationLink("Connect \(service.capitalized)") {
                ServiceConnectionView(service: service, existing: type == .movie ? model.config?.radarr : model.config?.sonarr)
            }
            .buttonStyle(.bordered)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}
