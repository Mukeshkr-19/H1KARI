import XCTest
@testable import MacOSAudioCaptureLib

final class PCMConversionTests: XCTestCase {
    func testResampledFrameCapacityRejectsZeroRate() {
        XCTAssertEqual(PCMConversion.resampledFrameCapacity(frameCount: 512, sourceSampleRate: 0, targetSampleRate: 16000), 0)
        XCTAssertEqual(PCMConversion.resampledFrameCapacity(frameCount: 512, sourceSampleRate: .infinity, targetSampleRate: 16000), 0)
    }

    func testResampledFrameCapacityValid() {
        let cap = PCMConversion.resampledFrameCapacity(frameCount: 480, sourceSampleRate: 48000, targetSampleRate: 16000)
        XCTAssertEqual(cap, 160)
    }

    func testFloatClamp() {
        XCTAssertEqual(PCMConversion.floatToPCM16(2.0), 32767)
        XCTAssertEqual(PCMConversion.floatToPCM16(-2.0), -32768)
        XCTAssertEqual(PCMConversion.floatToPCM16(.nan), 0)
    }

    func testStereoDownmix() {
        let mono = PCMConversion.downmixToMono(interleaved: [1, -1, 0.5, 0.5], channels: 2)
        XCTAssertEqual(mono.count, 2)
        XCTAssertEqual(mono[0], 0, accuracy: 0.0001)
        XCTAssertEqual(mono[1], 0.5, accuracy: 0.0001)
    }
}

final class FramingTests: XCTestCase {
    func testRoundTrip() throws {
        let payload = Data("ready".utf8)
        let header = CaptureFrameHeader(
            messageType: .ready,
            sequence: 7,
            monotonicNs: 123,
            payloadLength: UInt32(payload.count)
        )
        let encoded = try XCTUnwrap(CaptureFraming.encodeFrame(header: header, payload: payload))
        let decoded = try XCTUnwrap(CaptureFrameHeader.decode(encoded))
        XCTAssertEqual(decoded.sequence, 7)
        XCTAssertEqual(decoded.messageType, .ready)
        XCTAssertEqual(decoded.payloadLength, UInt32(payload.count))
    }

    func testInvalidMagicRejected() {
        var bad = Data(repeating: 0, count: 48)
        XCTAssertNil(CaptureFrameHeader.decode(bad))
    }

    func testOversizedPayloadRejected() {
        let header = CaptureFrameHeader(
            messageType: .pcm,
            sequence: 1,
            monotonicNs: 1,
            payloadLength: UInt32(CaptureFrameHeader.maxPayload + 1)
        )
        // encode header alone still has length; framing encode rejects mismatch
        let payload = Data(count: 10)
        XCTAssertNil(CaptureFraming.encodeFrame(header: header, payload: payload))
    }
}

final class SilentMicWatchdogTests: XCTestCase {
    func testFiresAfterThreshold() {
        let w = SilentMicWatchdog()
        w.windowThreshold = 2
        XCTAssertNil(w.evaluate(peak: 0, isBluetooth: true, now: 0))
        let det = w.evaluate(peak: 0, isBluetooth: true, now: 1)
        XCTAssertNotNil(det)
        XCTAssertEqual(det?.suggestedAction, "fallback_to_builtin")
    }

    func testNonBluetoothSuppressedByDefault() {
        let w = SilentMicWatchdog()
        w.windowThreshold = 1
        XCTAssertNil(w.evaluate(peak: 0, isBluetooth: false, now: 0))
    }
}
