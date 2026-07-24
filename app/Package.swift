// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "CharoiteApp",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "CharoiteApp",
            path: "Sources"
        ),
    ]
)
