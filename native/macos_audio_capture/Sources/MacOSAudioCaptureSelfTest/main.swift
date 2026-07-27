
import Foundation
import MacOSAudioCaptureLib

var failures = 0
func expect(_ cond: @autoclosure () -> Bool, _ msg: String) {
    if !cond() {
        fputs("FAIL: \(msg)\n", stderr)
        failures += 1
    }
}

// PCM conversion
expect(PCMConversion.resampledFrameCapacity(frameCount: 512, sourceSampleRate: 0, targetSampleRate: 16000) == 0, "zero rate")
expect(PCMConversion.resampledFrameCapacity(frameCount: 512, sourceSampleRate: .infinity, targetSampleRate: 16000) == 0, "inf rate")
expect(PCMConversion.resampledFrameCapacity(frameCount: 480, sourceSampleRate: 48000, targetSampleRate: 16000) == 160, "resample 48k->16k")
expect(PCMConversion.floatToPCM16(2.0) == 32767, "clamp high")
expect(PCMConversion.floatToPCM16(-2.0) == -32768, "clamp low")
expect(PCMConversion.floatToPCM16(.nan) == 0, "nan")
let mono = PCMConversion.downmixToMono(interleaved: [1, -1, 0.5, 0.5], channels: 2)
expect(mono.count == 2, "downmix count")
expect(abs(mono[0]) < 0.0001, "downmix first")
expect(abs(mono[1] - 0.5) < 0.0001, "downmix second")

// Framing
let payload = Data("ready".utf8)
let header = CaptureFrameHeader(messageType: .ready, sequence: 7, monotonicNs: 123, payloadLength: UInt32(payload.count))
guard let encoded = CaptureFraming.encodeFrame(header: header, payload: payload),
      let decoded = CaptureFrameHeader.decode(encoded) else {
    fputs("FAIL: framing round-trip\n", stderr)
    failures += 1
    exit(1)
}
expect(decoded.sequence == 7, "seq")
expect(decoded.messageType == .ready, "type")
expect(decoded.payloadLength == UInt32(payload.count), "plen")
var bad = Data(repeating: 0, count: 48)
expect(CaptureFrameHeader.decode(bad) == nil, "bad magic")
let oversized = CaptureFrameHeader(messageType: .pcm, sequence: 1, monotonicNs: 1, payloadLength: UInt32(CaptureFrameHeader.maxPayload + 1))
expect(CaptureFraming.encodeFrame(header: oversized, payload: Data(count: 10)) == nil, "oversized")

// Silent mic watchdog
let w = SilentMicWatchdog()
w.windowThreshold = 2
expect(w.evaluate(peak: 0, isBluetooth: true, now: 0) == nil, "watchdog first window")
let det = w.evaluate(peak: 0, isBluetooth: true, now: 1)
expect(det != nil, "watchdog fire")
expect(det?.suggestedAction == "fallback_to_builtin", "watchdog action")
let w2 = SilentMicWatchdog()
w2.windowThreshold = 1
expect(w2.evaluate(peak: 0, isBluetooth: false, now: 0) == nil, "non-bt suppressed")

if failures == 0 {
    print("MacOSAudioCaptureSelfTest: OK")
    exit(0)
}
fputs("MacOSAudioCaptureSelfTest: \(failures) failures\n", stderr)
exit(1)
