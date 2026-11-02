import Foundation

public struct GeographyEntry: Identifiable, Equatable {
    public let id: String
    public let prefecture: String
    public let name: String
    public let kana: String
    public var reading: String { GeographyDictionary.normalize(kana) }
}

/// A read-only lookup index. Entries never become replacement rules implicitly.
public struct GeographyDictionary {
    public let entries: [GeographyEntry]
    private let searchKeys: [(name: String, reading: String, prefecture: String)]
    public enum LoadError: Error { case invalidData }
    public init(tsv: String) throws {
        guard tsv.utf8.count <= 500_000 else { throw LoadError.invalidData }
        var rows: [GeographyEntry] = []
        var ids = Set<String>()
        for line in tsv.split(separator: "\n") {
            let parts = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard parts.count == 4, [2, 5].contains(parts[0].count),
                  parts[0].allSatisfy({ $0.isASCII && $0.isNumber }),
                  parts.allSatisfy({ !$0.isEmpty && $0.count <= 80 && !$0.contains("\r") }),
                  ids.insert(parts[0]).inserted, rows.count < 2500 else { throw LoadError.invalidData }
            rows.append(GeographyEntry(id: parts[0], prefecture: parts[1], name: parts[2], kana: parts[3]))
        }
        guard !rows.isEmpty else { throw LoadError.invalidData }
        entries = rows
        searchKeys = rows.map { (Self.normalize($0.name), $0.reading, Self.normalize($0.prefecture)) }
    }
    public static func normalize(_ value: String) -> String {
        let width = value.folding(options: [.widthInsensitive], locale: Locale(identifier: "ja_JP"))
        return (width.applyingTransform(.hiraganaToKatakana, reverse: true) ?? width)
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
    public func search(_ query: String, limit: Int = 5) -> [GeographyEntry] {
        guard query.count <= 100, limit > 0 else { return [] }
        let tokens = Self.normalize(query).split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard !tokens.isEmpty else { return [] }
        var matches: [(index: Int, rank: Int)] = []
        for (index, key) in searchKeys.enumerated() {
            guard tokens.allSatisfy({ key.name.contains($0) || key.reading.contains($0) || key.prefecture.contains($0) }) else { continue }
            let exact = tokens.contains(key.name) || tokens.contains(key.reading)
            let prefix = tokens.contains { key.name.hasPrefix($0) || key.reading.hasPrefix($0) }
            matches.append((index, exact ? 0 : prefix ? 1 : 2))
        }
        return matches.sorted { $0.rank == $1.rank ? $0.index < $1.index : $0.rank < $1.rank }
            .prefix(min(limit, 20)).map { entries[$0.index] }
    }
}
