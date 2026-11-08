import SwiftUI

/// "More like this": a radio-style list seeded from one title, ranked by the gateway
/// over shared genres, era, studio/network, collection, keywords and rating.
/// Candidates are the discovery pool (library, titles list, Radarr Discover).
struct MoreLikeThisRow: View {
    @Environment(AppModel.self) private var model
    let type: MediaType
    let id: Int
    let title: String

    @State private var items: [MediaCard] = []
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("More like this").font(.title3.bold())
                Spacer()
                if items.count > 3 {
                    NavigationLink("See all") { SimilarListView(type: type, id: id, title: title) }.font(.subheadline)
                }
            }
            if items.isEmpty {
                Text(loaded ? "Nothing similar found yet. This fills in as Radarr recommends more titles or you add to your titles list."
                            : "Finding similar titles…")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 12) {
                        ForEach(items, id: \.key) { card in
                            PosterCell(card: card, width: 104, caption: card.reasons?.first)
                        }
                    }
                }
            }
        }
        .task(id: "\(type.rawValue):\(id)") {
            do { items = try await model.api.similar(type, id: id, mix: true, limit: 12).items } catch {
                AppLog.warn("similar_failed", ["type": type.rawValue, "id": id, "error": error.localizedDescription])
            }
            loaded = true
        }
    }
}

struct SimilarListView: View {
    @Environment(AppModel.self) private var model
    let type: MediaType
    let id: Int
    let title: String

    @State private var mix = true
    @State private var items: [MediaCard] = []
    @State private var candidates = 0
    @State private var loading = true
    @State private var error: String?

    private let columns = [GridItem(.adaptive(minimum: 104, maximum: 160), spacing: 14, alignment: .top)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Toggle(type == .movie ? "Include TV shows" : "Include films", isOn: $mix)
                    .padding(.horizontal)
                if let error { Text(error).font(.footnote).foregroundStyle(.red).padding(.horizontal) }
                LazyVGrid(columns: columns, spacing: 18) {
                    ForEach(items, id: \.key) { card in
                        PosterCell(card: card, caption: card.reasons?.joined(separator: " · "))
                    }
                }
                .padding(.horizontal)
                if !loading && items.isEmpty {
                    ContentUnavailableView("Nothing similar yet", systemImage: "sparkles",
                                           description: Text("Compared against \(candidates) titles. More titles in Radarr Discover or your titles list give better matches."))
                }
            }
            .padding(.vertical)
        }
        .overlay { if loading && items.isEmpty { ProgressView() } }
        .navigationTitle("Like \(title)")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: mix) {
            loading = true
            defer { loading = false }
            do {
                let r = try await model.api.similar(type, id: id, mix: mix, limit: 60)
                items = r.items
                candidates = r.candidates
                error = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
