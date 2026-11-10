import SwiftUI

/// Filters built from facets: only filters with real choices in the current catalogue are shown.
/// Mirrors web/js/views/filters.js.
struct FilterSheet: View {
    @Environment(\.dismiss) private var dismiss
    let type: MediaType
    @Binding var filters: FilterState
    let facets: Facets
    let resultCount: () -> Int

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("English only", isOn: $filters.englishOnly)
                    Picker("Sort by", selection: $filters.sort) {
                        ForEach(SortOption.allCases.filter { type == .movie || $0 != .popularity }) { Text($0.label).tag($0) }
                    }
                } footer: {
                    if let n = facets.nonEnglish, n > 0 {
                        Text("\(n) titles aren't in English. Titles with no language listed are kept.")
                    }
                }
                if let genres = facets.genres, !genres.isEmpty {
                    Section {
                        GenreChipFlow(values: genres, filters: $filters)
                    } header: {
                        Text("Genres")
                    } footer: {
                        Text("Tap once to include, again to exclude.")
                    }
                }
                neighbourSection
                yearSection
                if (facets.rated ?? 0) > 0 {
                    Section {
                        HStack {
                            Slider(value: $filters.minRating, in: 0...9, step: 0.1)
                            Text(filters.minRating == 0 ? "Any" : String(format: "%.1f+", filters.minRating))
                                .monospacedDigit().frame(width: 44, alignment: .trailing)
                        }
                    } header: {
                        Text("Minimum rating")
                    } footer: {
                        Text("\(facets.rated ?? 0) titles have a rating. Unrated titles are hidden when this is set.")
                    }
                }
                Section(type == .movie ? "Length" : "Episode length") {
                    lengthPicker("At least", $filters.runtimeMin)
                    lengthPicker("At most", $filters.runtimeMax)
                }
                Section("More") {
                    if let sources = facets.sources, sources.count > 1 {
                        Picker("Found in", selection: $filters.source) {
                            Text("Anywhere").tag(String?.none)
                            ForEach(sources) { Text("\(DiscoverySource.label($0.value)) (\($0.count))").tag(String?.some($0.value)) }
                        }
                    }
                    if !filters.englishOnly { optionPicker("Language", facets.languages, $filters.language) }
                    optionPicker(type == .movie ? "Studio" : "Network", facets.studios, $filters.studio)
                    optionPicker("Collection", facets.collections, $filters.collection)
                    optionPicker("Certification", facets.certifications, $filters.certification)
                    optionPicker("Status", facets.statuses, $filters.seriesStatus)
                }
            }
            .onChange(of: filters.englishOnly) { _, on in if on { filters.language = nil } }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Reset") { filters = FilterState() }.disabled(filters.isDefault)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Show \(resultCount())") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private var neighbourSection: some View {
        let near = GenreNeighbours.of(filters.genres, in: facets.genreNeighbours ?? [:], skip: filters.excludeGenres)
        if !filters.genres.isEmpty && !near.isEmpty {
            Section {
                Toggle("Include neighbours", isOn: $filters.neighbours)
                if filters.neighbours {
                    FlowLayout(spacing: 8) {
                        ForEach(near) { n in
                            let on = !filters.neighboursOff.contains(n.value)
                            Button {
                                if on { filters.neighboursOff.insert(n.value) } else { filters.neighboursOff.remove(n.value) }
                            } label: {
                                Text("\(n.value) \(Text("\(Int((n.overlap * 100).rounded()))%").foregroundStyle(Color.secondary))")
                                    .font(.subheadline)
                                    .padding(.horizontal, 12).padding(.vertical, 7)
                                    .background(on ? Color.accentColor.opacity(0.18) : Color.clear, in: Capsule())
                                    .overlay(Capsule().strokeBorder(on ? Color.accentColor : Color.secondary.opacity(0.4),
                                                                    style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                            }
                            .buttonStyle(.plain)
                            .accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                    .padding(.vertical, 4)
                }
            } header: {
                Text("Neighbouring genres")
            } footer: {
                Text("Genres whose titles overlap with your picks. Titles with these count as matches. Tap one to leave it out.")
            }
        }
    }

    @ViewBuilder
    private var yearSection: some View {
        Section("Year") {
            Picker("Year", selection: yearMode) {
                ForEach(YearMode.allCases.filter { $0 != .range || facets.years != nil }) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            if filters.yearMode == .decades, let decades = facets.decades, !decades.isEmpty {
                ChipFlow(values: decades, label: { "\($0)s" }, isOn: { filters.decades.contains(Int($0) ?? -1) }) { v in
                    guard let d = Int(v) else { return }
                    if filters.decades.contains(d) { filters.decades.remove(d) } else { filters.decades.insert(d) }
                }
            }
            if filters.yearMode == .range, let years = facets.years {
                yearPicker("From", Binding(get: { filters.yearMin ?? years.min }, set: { filters.yearMin = $0 }), years)
                yearPicker("To", Binding(get: { filters.yearMax ?? years.max }, set: { filters.yearMax = $0 }), years)
            }
        }
    }

    /// Switching between Any, Decades and Range clears the other kind, so only one applies.
    private var yearMode: Binding<YearMode> {
        Binding(get: { filters.yearMode }, set: { mode in
            filters.yearMode = mode
            if mode != .decades { filters.decades = [] }
            if mode == .range {
                filters.yearMin = filters.yearMin ?? facets.years?.min
                filters.yearMax = filters.yearMax ?? facets.years?.max
            } else {
                filters.yearMin = nil
                filters.yearMax = nil
            }
        })
    }

    private func yearPicker(_ title: String, _ value: Binding<Int>, _ years: YearSpan) -> some View {
        Picker(title, selection: value) {
            ForEach(Array(stride(from: years.max, through: years.min, by: -1)), id: \.self) { Text(String($0)).tag($0) }
        }
    }

    private func lengthPicker(_ title: String, _ value: Binding<Int>) -> some View {
        Picker(title, selection: value) {
            ForEach(AppConfig.ints("filters.runtime_steps", default: [0, 30, 45, 60, 80, 90, 100, 110, 120, 135, 150, 180]),
                    id: \.self) { m in
                Text(m == 0 ? "Any" : "\(m) min").tag(m)
            }
        }
    }

    @ViewBuilder
    private func optionPicker(_ title: String, _ values: [FacetValue]?, _ selection: Binding<String?>) -> some View {
        if let values, values.count > 1 {
            Picker(title, selection: selection) {
                Text("Any").tag(String?.none)
                ForEach(values) { Text("\($0.value) (\($0.count))").tag(String?.some($0.value)) }
            }
        }
    }
}

/// Genre chips that cycle off → include → exclude → off.
struct GenreChipFlow: View {
    let values: [FacetValue]
    @Binding var filters: FilterState

    var body: some View {
        FlowLayout(spacing: 8) {
            ForEach(values) { v in
                let included = filters.genres.contains(v.value)
                let excluded = filters.excludeGenres.contains(v.value)
                Button { cycle(v.value) } label: {
                    Text("\(excluded ? "− " : "")\(v.value) \(Text(String(v.count)).foregroundStyle(included ? Color.white.opacity(0.8) : Color.secondary))")
                        .font(.subheadline)
                        .strikethrough(excluded)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .foregroundStyle(chipText(included: included, excluded: excluded))
                        .background(chipFill(included: included, excluded: excluded), in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(v.value), \(included ? "included" : (excluded ? "excluded" : "off"))")
            }
        }
        .padding(.vertical, 4)
    }

    private func chipText(included: Bool, excluded: Bool) -> Color {
        if included { return Color.white }
        if excluded { return Color.red }
        return Color.primary
    }

    private func chipFill(included: Bool, excluded: Bool) -> Color {
        if included { return Color.accentColor }
        if excluded { return Color.red.opacity(0.12) }
        return Color(.tertiarySystemFill)
    }

    private func cycle(_ g: String) {
        filters.neighboursOff.remove(g)
        if filters.genres.contains(g) {
            filters.genres.remove(g)
            filters.excludeGenres.insert(g)
        } else if filters.excludeGenres.contains(g) {
            filters.excludeGenres.remove(g)
        } else {
            filters.genres.insert(g)
        }
    }
}

/// Removable chips for the filters that are on, plus a result count.
struct ActiveFilterBar: View {
    @Binding var filters: FilterState
    let shown: Int
    let total: Int

    var body: some View {
        if filters.activeCount > 0 || filters.sort != .rating || filters.englishOnly {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    Text("\(shown) of \(total)").font(.caption).foregroundStyle(.secondary)
                    if filters.sort != .rating {
                        chip(filters.sort.label, systemImage: "arrow.up.arrow.down") { filters.sort = .rating }
                    }
                    if filters.englishOnly { chip("English only") { filters.englishOnly = false } }
                    ForEach(filters.genres.sorted(), id: \.self) { g in chip(g) { filters.genres.remove(g) } }
                    ForEach(filters.excludeGenres.sorted(), id: \.self) { g in chip("No \(g)") { filters.excludeGenres.remove(g) } }
                    if let y = filters.yearLabel {
                        chip(y) { filters.yearMode = .any; filters.decades = []; filters.yearMin = nil; filters.yearMax = nil }
                    }
                    if let l = filters.lengthLabel { chip(l) { filters.runtimeMin = 0; filters.runtimeMax = 0 } }
                    if filters.minRating > 0 { chip(String(format: "★ %.1f+", filters.minRating)) { filters.minRating = 0 } }
                    if let v = filters.source { chip(DiscoverySource.label(v)) { filters.source = nil } }
                    if let v = filters.language { chip(v) { filters.language = nil } }
                    if let v = filters.studio { chip(v) { filters.studio = nil } }
                    if let v = filters.collection { chip(v) { filters.collection = nil } }
                    if let v = filters.certification { chip(v) { filters.certification = nil } }
                    if let v = filters.seriesStatus { chip(v.capitalized) { filters.seriesStatus = nil } }
                    if filters.activeCount > 1 {
                        Button("Clear all") { filters = FilterState(sort: filters.sort) }.font(.caption.weight(.semibold))
                    }
                }
                .padding(.horizontal)
            }
            .padding(.vertical, 4)
        }
    }

    private func chip(_ text: String, systemImage: String = "xmark", remove: @escaping () -> Void) -> some View {
        Button(action: remove) {
            HStack(spacing: 4) {
                Text(text)
                Image(systemName: systemImage).font(.caption2.weight(.bold))
            }
            .font(.caption.weight(.medium))
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Color.accentColor.opacity(0.15), in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Remove filter \(text)")
    }
}

/// Toggleable chips that wrap onto several lines.
struct ChipFlow: View {
    let values: [FacetValue]
    var label: (String) -> String = { $0 }
    let isOn: (String) -> Bool
    let toggle: (String) -> Void

    var body: some View {
        FlowLayout(spacing: 8) {
            ForEach(values) { v in
                let on = isOn(v.value)
                Button { toggle(v.value) } label: {
                    Text("\(label(v.value)) \(Text(String(v.count)).foregroundStyle(on ? Color.white.opacity(0.8) : Color.secondary))")
                        .font(.subheadline)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .foregroundStyle(on ? Color.white : Color.primary)
                        .background(on ? Color.accentColor : Color(.tertiarySystemFill), in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(.vertical, 4)
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.last.map { $0.y + $0.height } ?? 0
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = arrange(width: bounds.width, subviews: subviews)
        for row in rows {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
        }
    }

    private struct Row { var indices: [Int] = []; var y: CGFloat = 0; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = [Row()]
        for (i, sub) in subviews.enumerated() {
            let size = sub.sizeThatFits(.unspecified)
            if !rows[rows.count - 1].indices.isEmpty && rows[rows.count - 1].width + spacing + size.width > width {
                let last = rows[rows.count - 1]
                rows.append(Row(y: last.y + last.height + spacing))
            }
            var row = rows[rows.count - 1]
            row.width += (row.indices.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.indices.append(i)
            rows[rows.count - 1] = row
        }
        return rows.filter { !$0.indices.isEmpty }
    }
}
