// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "GongjieCore", platforms: [.iOS(.v17), .macOS(.v14)], products: [.library(name: "GongjieCore", targets: ["GongjieCore"])], targets: [.target(name: "GongjieCore", resources: [.process("Resources/Daylight.json"), .process("Resources/Prompts.json")]), .testTarget(name: "GongjieCoreTests", dependencies: ["GongjieCore"])])
