import SwiftUI

/// Poster with a subtle status badge and a thin progress bar along the bottom edge.
struct PosterCard: View {
    let title: String?
    let year: Int?
    let posterURL: URL?
    let status: MediaStatus
    var width: CGFloat? = nil
    var mark: Mark = .none
    var quickAction: QuickAction? = nil

    enum Mark { case none, unselected, selected }

    /// The small round button on the poster: + adds with your defaults, ↻ retries a failure.
    struct QuickAction {
        enum Kind { case add, retry }
        let kind: Kind
        let action: () -> Void
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            RemoteImage(url: posterURL, maxPixel: 500)
                .aspectRatio(2/3, contentMode: .fit)
                .frame(width: width)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(alignment: .topTrailing) { StatusBadge(status: status).padding(6) }
                .overlay(alignment: .bottom) { PosterProgress(status: status) }
                .overlay {
                    if status.state == .failed {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(Color.red.opacity(0.9), lineWidth: 2)
                    }
                }
                .overlay(alignment: .bottomTrailing) {
                    if let quickAction {
                        Button(action: quickAction.action) {
                            Image(systemName: quickAction.kind == .add ? "plus" : "arrow.clockwise")
                                .font(.system(size: 15, weight: .bold))
                                .foregroundStyle(.white)
                                .frame(width: 32, height: 32)
                                .background(quickAction.kind == .add ? Color.accentColor : Color.red, in: Circle())
                                .shadow(color: .black.opacity(0.3), radius: 3, y: 1)
                                .contentShape(Circle().inset(by: -6))   // 44 pt hit area
                        }
                        .buttonStyle(.plain)
                        .padding(6)
                        .padding(.bottom, 4)
                        .accessibilityLabel(quickAction.kind == .add ? "Add \(title ?? "") to library" : "Retry \(title ?? "")")
                    }
                }
                .overlay(alignment: .topLeading) {
                    if mark != .none {
                        Image(systemName: mark == .selected ? "checkmark.circle.fill" : "circle")
                            .font(.title2)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, mark == .selected ? Color.accentColor : Color.black.opacity(0.35))
                            .padding(6)
                    }
                }
                .overlay {
                    if mark == .selected {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(Color.accentColor, lineWidth: 3)
                    }
                }
                .shadow(color: .black.opacity(0.18), radius: 6, y: 3)
            Text(title ?? "Untitled")
                .font(.footnote.weight(.medium))
                .lineLimit(2, reservesSpace: true)   // two lines, reserved so grid rows line up
                .frame(width: width, alignment: .leading)
            if let year {
                Text(String(year)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title ?? "Untitled"), \(year.map(String.init) ?? ""), \(status.accessibilityText)")
        .accessibilityActions {
            if let quickAction {
                Button(quickAction.kind == .retry ? "Retry" : "Add to library", action: quickAction.action)
            }
        }
    }
}

struct StatusBadge: View {
    let status: MediaStatus

    var body: some View {
        switch status.state {
        case .notRequested, .unknown:
            EmptyView()
        case .submitting:
            ProgressView().controlSize(.mini).tint(.white)
                .padding(6).background(.ultraThinMaterial, in: Circle())
        case .failed:
            Image(systemName: status.isConfigProblem ? "gearshape.fill" : "exclamationmark.triangle.fill")
                .font(.caption.weight(.bold)).foregroundStyle(.white)
                .padding(6).background(Color.red, in: Circle())
        case .complete:
            Image(systemName: "checkmark").font(.caption2.weight(.heavy)).foregroundStyle(.white)
                .padding(6).background(Color.green, in: Circle())
        default:
            HStack(spacing: 3) {
                Image(systemName: status.state.systemImage)
                if status.state == .downloading, let p = status.progress {
                    Text("\(Int((p * 100).rounded()))%").monospacedDigit()
                }
            }
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 6).padding(.vertical, 4)
            .background(.black.opacity(0.55), in: Capsule())
        }
    }
}

struct PosterProgress: View {
    let status: MediaStatus

    var body: some View {
        if status.state.isActive || status.state == .searching {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle().fill(.black.opacity(0.35))
                    if let p = status.progress {
                        Rectangle().fill(tint).frame(width: geo.size.width * p)
                            .animation(.easeInOut(duration: 0.4), value: p)
                    } else if status.state != .paused {
                        IndeterminateBar(tint: tint)
                    }
                }
            }
            .frame(height: 4)
            .clipShape(UnevenRoundedRectangle(bottomLeadingRadius: 10, bottomTrailingRadius: 10))
        }
    }

    private var tint: Color {
        switch status.state {
        case .paused, .stalled: .orange
        case .importing: .green
        case .searching, .queued: .gray
        default: .accentColor
        }
    }
}

private struct IndeterminateBar: View {
    let tint: Color
    @State private var phase: CGFloat = -0.3

    var body: some View {
        GeometryReader { geo in
            Rectangle().fill(tint.opacity(0.8))
                .frame(width: geo.size.width * 0.3)
                .offset(x: geo.size.width * phase)
                .onAppear {
                    withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) { phase = 1.0 }
                }
        }
    }
}

extension MediaStatus {
    var accessibilityText: String {
        var s = state.label
        if let p = progress, state == .downloading { s += ", \(Int(p * 100)) percent" }
        if state == .failed, let m = message { s += ", \(m)" }
        return s
    }
}
