import Foundation
import Darwin

/// Optional, user-authored hints. They never become replacement rules.
public struct InputContextProfile: Codable, Equatable, Identifiable, Sendable {
    public static let maximumProfiles = 64
    public static let maximumStoredBytes = 2 * 1024 * 1024
    public enum Kind: String, Codable, CaseIterable, Sendable { case application, website }
    public var id: UUID
    public var kind: Kind
    public var identifier: String
    public var topic: String
    public var terms: [String]
    public var enabled: Bool
    public init(id: UUID = UUID(), kind: Kind, identifier: String, topic: String = "", terms: [String] = [], enabled: Bool = true) {
        self.id = id; self.kind = kind; self.identifier = identifier
        self.topic = String(topic.prefix(400)); self.terms = Self.boundedTerms(terms); self.enabled = enabled
    }
    public var normalized: Self? {
        guard let identifier = Self.normalizedIdentifier(identifier, kind: kind) else { return nil }
        return Self(id: id, kind: kind, identifier: identifier, topic: topic, terms: terms, enabled: enabled)
    }
    public static func restored(from data: Data) -> [Self] {
        guard data.count <= maximumStoredBytes,
              let rows = try? JSONSerialization.jsonObject(with: data) as? [Any] else { return [] }
        return rows.prefix(maximumProfiles).compactMap { row in
            guard JSONSerialization.isValidJSONObject(row),
                  let data = try? JSONSerialization.data(withJSONObject: row),
                  let profile = try? JSONDecoder().decode(Self.self, from: data) else { return nil }
            return profile.normalized
        }
    }
    public static func canAddOrEdit(_ profiles: [Self], kind: Kind, identifier: String) -> Bool {
        guard let identifier = normalizedIdentifier(identifier, kind: kind) else { return false }
        return profiles.count < maximumProfiles || profiles.prefix(maximumProfiles).contains {
            $0.kind == kind && $0.identifier == identifier
        }
    }
    public static func normalizedIdentifier(_ raw: String, kind: Kind) -> String? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, value.utf8.count <= 4096,
              !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { return nil }
        if kind == .application {
            guard value.utf8.count <= 255,
                  value.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]*$"#, options: .regularExpression) != nil else { return nil }
            return value
        }
        let url = value.contains("://") ? value : "https://" + value
        let authority = url.components(separatedBy: "://").dropFirst().joined(separator: "://").prefix { !"/?#".contains($0) }
        guard !authority.contains("%") else { return nil }
        guard let parsed = URLComponents(string: url), parsed.user == nil, parsed.password == nil,
              var host = LogDestination.host(from: url), host.utf8.count <= 253, !host.contains("%") else { return nil }
        if host.hasSuffix(".") { host.removeLast() }
        if host.hasPrefix("["), host.hasSuffix("]") {
            var address = in6_addr()
            guard inet_pton(AF_INET6, String(host.dropFirst().dropLast()), &address) == 1 else { return nil }
        } else {
            let labels = host.split(separator: ".", omittingEmptySubsequences: false)
            guard labels.allSatisfy({ label in
                !label.isEmpty && label.utf8.count <= 63 && label.first != "-" && label.last != "-" &&
                label.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" }
            }) else { return nil }
        }
        return host
    }
    public static func boundedTerms(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return Array(values.compactMap { value -> String? in
            let word = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !word.isEmpty, word.count <= 80,
                  !word.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
                  !word.contains(where: { $0 == "=" || $0 == "," }),
                  seen.insert(word).inserted else { return nil }
            return word
        }.prefix(32))
    }
}

public struct InputContextSupplement: Equatable, Sendable {
    public var topic = ""
    public var terms: [String] = []
    public init(topic: String = "", terms: [String] = []) { self.topic = String(topic.prefix(800)); self.terms = terms }
    public static func resolve(_ profiles: [InputContextProfile], bundleID: String, scope: ContextScope,
                               excluded: Bool, siteHost: () -> String?) -> Self {
        let bounded = Array(profiles.prefix(InputContextProfile.maximumProfiles))
        let needsSite = !excluded && !bundleID.isEmpty && scope != .appOnly &&
            bounded.contains { $0.enabled && $0.kind == .website }
        return resolve(bounded, bundleID: bundleID, siteHost: needsSite ? siteHost() : nil, excluded: excluded)
    }
    public static func resolve(_ profiles: [InputContextProfile], bundleID: String, siteHost: String?, excluded: Bool = false) -> Self {
        guard !excluded, !bundleID.isEmpty else { return Self() }
        let valid = profiles.prefix(InputContextProfile.maximumProfiles).compactMap(\.normalized).filter(\.enabled)
        let app = valid.first { $0.kind == .application && $0.identifier == bundleID }
        let host = siteHost.flatMap { InputContextProfile.normalizedIdentifier($0, kind: .website) }
        let site = host.flatMap { host in valid.first { $0.kind == .website && $0.identifier == host } }
        let topic = [app?.topic, site?.topic].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "\n")
        var seen = Set<String>()
        let terms = ((site?.terms ?? []) + (app?.terms ?? [])).filter { seen.insert($0).inserted }
        return Self(topic: topic, terms: terms)
    }
    public func recognitionHints(fallback: [String]) -> [String] {
        var seen = Set<String>()
        return Array((terms + fallback).filter { seen.insert($0).inserted }.prefix(64))
    }
}
