// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "KoeFlow", platforms: [.macOS("26.0")],
    products: [.executable(name: "KoeFlow", targets: ["KoeFlow"])],
    targets: [
        .target(name: "KoeFlowCore", exclude: ["Resources"]),
        .executableTarget(name: "KoeFlow", dependencies: ["KoeFlowCore"]),
        .testTarget(name: "KoeFlowCoreTests", dependencies: ["KoeFlowCore"], path: "Tests/KoeFlowCoreTests")
    ], swiftLanguageModes: [.v5]
)
