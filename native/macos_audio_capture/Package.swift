// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MacOSAudioCapture",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "hikari-macos-audio-capture", targets: ["MacOSAudioCapture"]),
        .executable(name: "hikari-macos-audio-capture-tests", targets: ["MacOSAudioCaptureSelfTest"]),
        .library(name: "MacOSAudioCaptureLib", targets: ["MacOSAudioCaptureLib"]),
    ],
    targets: [
        .target(
            name: "MacOSAudioCaptureLib",
            path: "Sources/MacOSAudioCaptureLib"
        ),
        .executableTarget(
            name: "MacOSAudioCapture",
            dependencies: ["MacOSAudioCaptureLib"],
            path: "Sources/MacOSAudioCapture"
        ),
        .executableTarget(
            name: "MacOSAudioCaptureSelfTest",
            dependencies: ["MacOSAudioCaptureLib"],
            path: "Sources/MacOSAudioCaptureSelfTest"
        ),
    ]
)
