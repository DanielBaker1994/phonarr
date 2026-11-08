import SwiftUI

/// Filters built from facets: only filters with real choices in the current catalogue are shown.
struct FilterSheet: View {
    @Environment(\.dismiss) private var dismiss
    let type: MediaType
    @Binding var filters: FilterState
    let facets: Facets
    let resultCount: () -> Int

    var body: some View {
        NavigationStack {
            Form {
                Section("Sort") {
                    Picker("Sort by", selection: $filters.sort) {
                        ForEach(SortOption.allCases.filter { type == .movie || $0 != .popularity }) { Text($0.label).tag($0) }
                    }
                }
                if let genres = facets.genres, !genres.isEmpty {
                    Section("Genres") {
                        ChipFlow(values: genres, isOn: { filters.genres.contains($0) }) { v in
                            if filters.genres.contains(v) { filters.genres.remove(v) } else { filters.genres.insert(v) }
                        }
                    }
                }
                if let decades = facets.decades, decades.count > 1 {
                    Section("Decade") {
                        ChipFlow(values: decades, label: { "\($0)s" }, isOn: { filters.decade == Int($0) }) { v in
                            filters.decade = filters.decade == Int(v) ? nil : Int(v)
                        }
                    }
                }
                if let bands = facets.runtime, !bands.isEmpty {
                    Section(type == .movie ? "Length" : "Episode length") {
                        ChipFlow(values: bands, label: { RuntimeBand(rawValue: $0)?.label ?? $0 },
                                 isOn: { filters.runtime?.rawValue == $0 }) { v in
                            filters.runtime = filters.runtime?.rawValue == v ? nil : RuntimeBand(rawValue: v)
                        }
                    }
                }
                if (facets.rated ?? 0) > 0 {
                    Section {
                        HStack {
                            Slider(value: $filters.minRating, in: 0...9, step: 0.5)
                            Text(filters.minRating == 0 ? "Any" : String(format: "%.1f+", filters.minRating))
                                .monospacedDigit().frame(width: 44, alignment: .trailing)
                        }
                    } header: {
                        Text("Minimum rating")
                    } footer: {
                        Text("\(facets.rated ?? 0) titles have a rating. Unrated titles are hidden when this is set.")
                    }
                }
                Section("More") {
                    if let sources = facets.sources, sources.count > 1 {
                        Picker("Found in", selection: $filters.source) {
                            Text("Anywhere").tag(String?.none)
                            ForEach(sources) { Text("\(DiscoverySource.label($0.value)) (\($0.count))").tag(String?.some($0.value)) }
                        }
                    }
                    optionPicker("Language", facets.languages, $filters.language)
                    optionPicker(type == .movie ? "Studio" : "Network", facets.studios, $filters.studio)
                    optionPicker("Collection", facets.collections, $filters.collection)
                    optionPicker("Certification", facets.certifications, $filters.certification)
                    optionPicker("Status", facets.statuses, $filters.seriesStatus)
                }
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Reset") { filters = FilterState(sort: filters.sort) }.disabled(filters.activeCount == 0)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Show \(resultCount())") { dismiss() }
                }
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

/// Removable chips for the filters that are on, plus a result count.
struct ActiveFilterBar: View {
    @Binding var filters: FilterState
    let shown: Int
    let total: Int

    var body: some View {
        if filters.activeCount > 0 || filters.sort != .title {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    Text("\(shown) of \(total)").font(.caption).foregroundStyle(.secondary)
                    if filters.sort != .title {
                        chip(filters.sort.label, systemImage: "arrow.up.arrow.down") { filters.sort = .title }
                    }
                    ForEach(filters.genres.sorted(), id: \.self) { g in chip(g) { filters.genres.remove(g) } }
                    if let d = filters.decade { chip("\(String(d))s") { filters.decade = nil } }
                    if let r = filters.runtime { chip(r.label) { filters.runtime = nil } }
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
