import Foundation

/// Binary framing protocol between hikari-macos-audio-capture and Python.
/// Little-endian, fixed 48-byte header + bounded payload.
/// Layout:
/// magic(4) version(u16) type(u16) sequence(u64) monotonicNs(u64)
/// sampleRate(u32) channels(u16) sampleWidth(u16) payloadLength(u32)
/// reserved0(u32) reserved1(u32) reserved2(u32)  => 48 bytes
public enum CaptureFrameType: UInt16 {
    case ready = 1
    case pcm = 2
    case error = 3
    case end = 4
    case cancelAck = 5
}

public struct CaptureFrameHeader {
    public static let magic = Data([0x48, 0x49, 0x4B, 0x41]) // "HIKA"
    public static let version: UInt16 = 1
    public static let size = 48
    public static let maxPayload = 65_536

    public var messageType: CaptureFrameType
    public var sequence: UInt64
    public var monotonicNs: UInt64
    public var sampleRate: UInt32
    public var channels: UInt16
    public var sampleWidth: UInt16
    public var payloadLength: UInt32

    public init(
        messageType: CaptureFrameType,
        sequence: UInt64,
        monotonicNs: UInt64,
        sampleRate: UInt32 = 16_000,
        channels: UInt16 = 1,
        sampleWidth: UInt16 = 2,
        payloadLength: UInt32 = 0
    ) {
        self.messageType = messageType
        self.sequence = sequence
        self.monotonicNs = monotonicNs
        self.sampleRate = sampleRate
        self.channels = channels
        self.sampleWidth = sampleWidth
        self.payloadLength = payloadLength
    }

    public func encode() -> Data {
        var data = Data()
        data.reserveCapacity(Self.size)
        data.append(Self.magic)
        func appendU16(_ v: UInt16) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        func appendU32(_ v: UInt32) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        func appendU64(_ v: UInt64) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        appendU16(Self.version)
        appendU16(messageType.rawValue)
        appendU64(sequence)
        appendU64(monotonicNs)
        appendU32(sampleRate)
        appendU16(channels)
        appendU16(sampleWidth)
        appendU32(payloadLength)
        appendU32(0)
        appendU32(0)
        appendU32(0)
        precondition(data.count == Self.size)
        return data
    }

    public static func decode(_ data: Data) -> CaptureFrameHeader? {
        guard data.count >= size else { return nil }
        guard data.prefix(4) == magic else { return nil }
        func u16(_ o: Int) -> UInt16 {
            UInt16(data[o]) | (UInt16(data[o + 1]) << 8)
        }
        func u32(_ o: Int) -> UInt32 {
            UInt32(data[o])
                | (UInt32(data[o + 1]) << 8)
                | (UInt32(data[o + 2]) << 16)
                | (UInt32(data[o + 3]) << 24)
        }
        func u64(_ o: Int) -> UInt64 {
            var value: UInt64 = 0
            for index in 0..<8 {
                value |= UInt64(data[o + index]) << UInt64(index * 8)
            }
            return value
        }
        let version = u16(4)
        guard version == Self.version else { return nil }
        guard let type = CaptureFrameType(rawValue: u16(6)) else { return nil }
        let plen = u32(32)
        guard plen <= maxPayload else { return nil }
        guard u32(36) == 0, u32(40) == 0, u32(44) == 0 else { return nil }
        guard u32(24) == 16_000, u16(28) == 1, u16(30) == 2 else { return nil }
        return CaptureFrameHeader(
            messageType: type,
            sequence: u64(8),
            monotonicNs: u64(16),
            sampleRate: u32(24),
            channels: u16(28),
            sampleWidth: u16(30),
            payloadLength: plen
        )
    }
}

public enum CaptureFraming {
    public static func encodeFrame(header: CaptureFrameHeader, payload: Data) -> Data? {
        guard payload.count == Int(header.payloadLength),
              payload.count <= CaptureFrameHeader.maxPayload else { return nil }
        var out = header.encode()
        out.append(payload)
        return out
    }
}
