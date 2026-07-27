// Adapted patterns from BasedHardware/Omi AudioCaptureService.swift
// Copyright (c) Based Hardware / Omi contributors — MIT License
// See docs/OMI_DERIVED_VOICE_PIPELINE.md and THIRD_PARTY_NOTICES.md
import Foundation

/// Pure audio conversion helpers (unit-testable; no CoreAudio side effects).
public enum PCMConversion {
    public static let targetSampleRate: Double = 16_000
    public static let maxFrameBytes: Int = 65_536
    public static let silentPeakThreshold: Int16 = 5

    /// Frames a source→target conversion of `frameCount` input frames produces.
    /// Returns 0 when rates are invalid to avoid infinity traps on AVAudioFrameCount.
    /// Adapted from Omi `resampledFrameCapacity`.
    public static func resampledFrameCapacity(
        frameCount: UInt32,
        sourceSampleRate: Double,
        targetSampleRate: Double
    ) -> UInt32 {
        guard frameCount > 0, sourceSampleRate > 0, targetSampleRate > 0,
              sourceSampleRate.isFinite, targetSampleRate.isFinite
        else { return 0 }
        let capacity = ceil(Double(frameCount) * targetSampleRate / sourceSampleRate)
        guard capacity.isFinite, capacity > 0, capacity <= Double(UInt32.max) else { return 0 }
        return UInt32(capacity)
    }

    /// Clamp Float32 sample into Int16 PCM range. Adapted from Omi conversion loop.
    public static func floatToPCM16(_ sample: Float) -> Int16 {
        guard sample.isFinite else { return 0 }
        let scaled = sample * 32767.0
        let clamped = max(-32768.0, min(32767.0, Double(scaled)))
        return Int16(clamped)
    }

    /// Stereo/multichannel → mono average. Channels must be >= 1.
    public static func downmixToMono(interleaved: [Float], channels: Int) -> [Float] {
        guard channels >= 1, !interleaved.isEmpty, interleaved.count % channels == 0 else { return [] }
        if channels == 1 { return interleaved }
        let frames = interleaved.count / channels
        var mono = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            var sum: Float = 0
            for c in 0..<channels {
                let s = interleaved[i * channels + c]
                sum += s.isFinite ? s : 0
            }
            mono[i] = sum / Float(channels)
        }
        return mono
    }

    public static func floatsToPCM16Data(_ samples: [Float]) -> Data {
        var pcm = [Int16]()
        pcm.reserveCapacity(samples.count)
        for s in samples {
            pcm.append(floatToPCM16(s))
        }
        return pcm.withUnsafeBufferPointer { Data(buffer: $0) }
    }

    public static func peakAbsolutePCM16(_ data: Data) -> Int16 {
        guard data.count >= 2 else { return 0 }
        return data.withUnsafeBytes { raw -> Int16 in
            let count = raw.count / 2
            var peak: Int16 = 0
            let ptr = raw.bindMemory(to: Int16.self)
            for i in 0..<count {
                let sample = ptr[i]
                let absSample = sample == Int16.min ? Int16.max : Int16(sample.magnitude)
                if absSample > peak { peak = absSample }
            }
            return peak
        }
    }
}
