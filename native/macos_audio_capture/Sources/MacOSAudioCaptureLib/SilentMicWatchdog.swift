// Adapted from BasedHardware/Omi AudioCaptureService.evaluateSilentMicWindow
// Copyright (c) Based Hardware / Omi contributors — MIT License
import Foundation

public struct SilentMicDetection: Equatable {
    public let consecutiveSilentWindows: Int
    public let isBluetoothTransport: Bool

    public init(consecutiveSilentWindows: Int, isBluetoothTransport: Bool) {
        self.consecutiveSilentWindows = consecutiveSilentWindows
        self.isBluetoothTransport = isBluetoothTransport
    }

    public var suggestedAction: String {
        isBluetoothTransport ? "fallback_to_builtin" : "rebuild_stack"
    }
}

/// Bounded silent-mic watchdog. Pure state machine for unit tests.
public final class SilentMicWatchdog: @unchecked Sendable {
    public var windowThreshold: Int = 2
    public var recoveryCooldown: TimeInterval = 3.0
    public var maxFiresPerSession: Int = 3
    public var detectOnAnyTransport: Bool = false

    private var consecutiveSilentWindows: Int = 0
    private var fired: Bool = false
    private var fireCount: Int = 0
    private var lastFireTime: TimeInterval = 0

    public init() {}

    public func reset() {
        consecutiveSilentWindows = 0
        fired = false
        fireCount = 0
        lastFireTime = 0
    }

    /// Classify one closed ~1s window. Adapted from Omi evaluateSilentMicWindow.
    public func evaluate(peak: Int16, isBluetooth: Bool, now: TimeInterval) -> SilentMicDetection? {
        if peak <= PCMConversion.silentPeakThreshold {
            consecutiveSilentWindows += 1
        } else {
            consecutiveSilentWindows = 0
        }
        if fired, now - lastFireTime >= recoveryCooldown {
            fired = false
        }
        let transportOk = detectOnAnyTransport || isBluetooth
        guard !fired,
              fireCount < maxFiresPerSession,
              consecutiveSilentWindows >= windowThreshold,
              transportOk
        else { return nil }

        fired = true
        fireCount += 1
        lastFireTime = now
        consecutiveSilentWindows = 0
        return SilentMicDetection(
            consecutiveSilentWindows: windowThreshold,
            isBluetoothTransport: isBluetooth
        )
    }
}
