import Foundation
import MacOSAudioCaptureLib

/// hikari-macos-audio-capture — framed PCM stdout helper.
/// Does not open the microphone until `--capture` is passed.
/// Content-free stderr diagnostics only.

enum HelperMode: String {
    case probe
    case capture
}

func monotonicNs() -> UInt64 {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    let t = mach_absolute_time()
    return t &* UInt64(info.numer) / UInt64(info.denom)
}

func writeFrame(_ type: CaptureFrameType, sequence: UInt64, payload: Data, sampleRate: UInt32 = 16000) {
    let header = CaptureFrameHeader(
        messageType: type,
        sequence: sequence,
        monotonicNs: monotonicNs(),
        sampleRate: sampleRate,
        channels: 1,
        sampleWidth: 2,
        payloadLength: UInt32(payload.count)
    )
    guard let frame = CaptureFraming.encodeFrame(header: header, payload: payload) else { return }
    FileHandle.standardOutput.write(frame)
}

func parseArgs(_ args: [String]) -> HelperMode {
    if args.contains("--capture") { return .capture }
    return .probe
}

let mode = parseArgs(CommandLine.arguments)

if mode == .probe {
    // Capability probe — no microphone open.
    let ready = #"{"capability":"frame_stream","sample_rate":16000,"channels":1,"sample_width":2}"#
    writeFrame(.ready, sequence: 0, payload: Data(ready.utf8))
    writeFrame(.end, sequence: 1, payload: Data())
    exit(0)
}

let capture = CoreAudioCapture()
let lock = NSLock()
var running = true

capture.setHandlers(
    onChunk: { data, seq in
        lock.lock(); defer { lock.unlock() }
        guard running else { return }
        writeFrame(.pcm, sequence: seq, payload: data)
    },
    onError: { code in
        let payload = Data(code.utf8.prefix(64))
        writeFrame(.error, sequence: 0, payload: payload)
    },
    onSilent: {
        FileHandle.standardError.write(Data("silent_mic\n".utf8))
    }
)

do {
    let ready = #"{"capability":"frame_stream","sample_rate":16000,"channels":1,"sample_width":2}"#
    writeFrame(.ready, sequence: 0, payload: Data(ready.utf8))
    try capture.start()
} catch {
    writeFrame(.error, sequence: 0, payload: Data("start_failed".utf8))
    exit(1)
}

// Run until stdin closes (parent death) or SIGTERM via stdin EOF.
FileHandle.standardInput.readabilityHandler = { handle in
    let data = handle.availableData
    if data.isEmpty {
        lock.lock(); running = false; lock.unlock()
        capture.stop()
        writeFrame(.end, sequence: UInt64.max, payload: Data())
        exit(0)
    }
    if data.first == UInt8(ascii: "q") || data.first == UInt8(ascii: "c") {
        lock.lock(); running = false; lock.unlock()
        capture.cancel()
        writeFrame(.cancelAck, sequence: 0, payload: Data())
        writeFrame(.end, sequence: UInt64.max, payload: Data())
        exit(0)
    }
}

RunLoop.main.run()
