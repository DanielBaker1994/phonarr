import SwiftUI
import UIKit

/// NSCache is thread-safe; wrapped so it can be read synchronously from views.
final class ImageMemoryCache: @unchecked Sendable {
    static let shared = ImageMemoryCache()
    let cache: NSCache<NSURL, UIImage> = {
        let c = NSCache<NSURL, UIImage>()
        c.countLimit = 300
        return c
    }()
}

/// Memory + disk cached remote image, decoded off the main thread for smooth scrolling.
actor ImagePipeline {
    static let shared = ImagePipeline()

    private var memory: NSCache<NSURL, UIImage> { ImageMemoryCache.shared.cache }
    private let session: URLSession
    private var tasks: [URL: Task<UIImage?, Never>] = [:]

    init() {
        let config = URLSessionConfiguration.default
        config.urlCache = URLCache(memoryCapacity: 32 << 20, diskCapacity: 512 << 20)
        config.requestCachePolicy = .returnCacheDataElseLoad
        session = URLSession(configuration: config)
    }

    nonisolated func cached(_ url: URL) -> UIImage? { ImageMemoryCache.shared.cache.object(forKey: url as NSURL) }

    func image(for url: URL, maxPixel: CGFloat) async -> UIImage? {
        if let img = memory.object(forKey: url as NSURL) { return img }
        if let t = tasks[url] { return await t.value }
        let task = Task<UIImage?, Never> { [session] in
            guard let result = try? await session.data(from: url),
                  let img = UIImage(data: result.0) else { return nil }
            return await img.byPreparingThumbnail(ofSize: Self.fit(img.size, maxPixel)) ?? img
        }
        tasks[url] = task
        let img = await task.value
        tasks[url] = nil
        if let img { memory.setObject(img, forKey: url as NSURL) }
        return img
    }

    private static func fit(_ size: CGSize, _ maxPixel: CGFloat) -> CGSize {
        let scale = min(1, maxPixel / max(size.width, size.height))
        return CGSize(width: size.width * scale, height: size.height * scale)
    }
}

struct RemoteImage: View {
    let url: URL?
    var maxPixel: CGFloat = 600
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
                    .transition(.opacity)
            } else {
                Rectangle().fill(.quaternary)
                    .overlay(Image(systemName: "film").font(.title).foregroundStyle(.tertiary))
            }
        }
        .task(id: url) {
            guard let url else { image = nil; return }
            if let hit = ImagePipeline.shared.cached(url) { image = hit; return }
            let loaded = await ImagePipeline.shared.image(for: url, maxPixel: maxPixel)
            withAnimation(.easeOut(duration: 0.2)) { image = loaded }
        }
    }
}
