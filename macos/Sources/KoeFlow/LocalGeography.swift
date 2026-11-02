import Foundation
import KoeFlowCore

enum LocalGeography {
    // Lazy, bounded local file load; no network or background service.
    static let dictionary: GeographyDictionary? = {
        guard let url = Bundle.main.url(forResource: "geography", withExtension: "tsv"),
              let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 500_000,
              let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        return try? GeographyDictionary(tsv: text)
    }()
}
