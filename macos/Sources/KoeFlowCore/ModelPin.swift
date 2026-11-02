import Foundation

public struct ModelPin: Sendable, Equatable {
    public let fileName: String
    public let bytes: Int64
    public let sha256: String
    public let revision: String
    public let url: URL
    public init(fileName: String, bytes: Int64, sha256: String, revision: String, url: URL) {
        self.fileName = fileName; self.bytes = bytes; self.sha256 = sha256
        self.revision = revision; self.url = url
    }
    public static let haasJapanese = ModelPin(
        fileName: "Haas-616M-ja-v1.0-Q4_K.gguf", bytes: 351_901_216,
        sha256: "d24d53f29b75214a933ff482b0de1217e0a800f4118cca39ad6cf03609a89d6f",
        revision: "6b965d5007ba780585de51549d57a8c1c0d67c7e",
        url: URL(string: "https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF/resolve/6b965d5007ba780585de51549d57a8c1c0d67c7e/Haas-616M-ja-v1.0-Q4_K.gguf")!)
}

public struct HaasPreparationProgress: Sendable, Equatable {
    public enum Stage: String, Sendable { case checking, downloading, verifying, warming, ready }
    public let stage: Stage
    public let bytes: Int64
    public let total: Int64
    public var fraction: Double { stage == .ready ? 1 : total > 0 ? min(1, max(0, Double(bytes) / Double(total))) : 0 }
    public init(stage: Stage, bytes: Int64 = 0, total: Int64 = ModelPin.haasJapanese.bytes) {
        self.stage = stage; self.bytes = max(0, bytes); self.total = max(0, total)
    }
}
