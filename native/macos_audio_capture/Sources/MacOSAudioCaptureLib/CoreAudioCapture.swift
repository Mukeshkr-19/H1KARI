// Selectively adapted from BasedHardware/Omi AudioCaptureService.swift
// Copyright (c) Based Hardware / Omi contributors — MIT License
// HIKARI ownership: wake/sleep/Brain/policy remain HIKARI-only.
@preconcurrency import AVFoundation
@preconcurrency import CoreAudio
import Foundation

public enum AudioCaptureError: Error, Equatable {
    case noInputAvailable
    case permissionDenied
    case converterCreationFailed
    case engineStartFailed
    case cancelled
    case alreadyRunning
}

/// CoreAudio input capture → 16 kHz mono PCM16 frames via callback.
/// Importing/constructing does not open the microphone.
public final class CoreAudioCapture: @unchecked Sendable {
    public typealias ChunkHandler = @Sendable (Data, UInt64) -> Void
    public typealias ErrorHandler = @Sendable (String) -> Void

    private let audioQueue = DispatchQueue(label: "com.hikari.audiocapture.device")
    private var deviceID: AudioDeviceID = kAudioObjectUnknown
    private var ioProcID: AudioDeviceIOProcID?
    private var isCapturing = false
    private var audioConverter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private var targetFormat: AVAudioFormat?
    private var detectedSampleRate: Double = 0
    private nonisolated(unsafe) var hasConsumedInput = false
    private var onChunk: ChunkHandler?
    private var onError: ErrorHandler?
    private var onSilent: (@Sendable () -> Void)?
    private let watchdog = SilentMicWatchdog()
    private var watchdogWindowPeak: Int16 = 0
    private var watchdogWindowStart: CFAbsoluteTime = 0
    private var sequence: UInt64 = 0
    private var cancelled = false

    public init() {}

    public func setHandlers(
        onChunk: @escaping ChunkHandler,
        onError: @escaping ErrorHandler,
        onSilent: (@Sendable () -> Void)? = nil
    ) {
        self.onChunk = onChunk
        self.onError = onError
        self.onSilent = onSilent
    }

    public func cancel() {
        cancelled = true
        stop()
    }

    public func start() throws {
        if cancelled { throw AudioCaptureError.cancelled }
        try audioQueue.sync {
            if isCapturing { throw AudioCaptureError.alreadyRunning }
            try startLocked()
        }
    }

    public func stop() {
        audioQueue.sync { stopLocked() }
    }

    private func startLocked() throws {
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        if status == .denied || status == .restricted {
            throw AudioCaptureError.permissionDenied
        }
        guard let device = Self.defaultInputDeviceID() else {
            throw AudioCaptureError.noInputAvailable
        }
        deviceID = device
        guard let asbd = Self.deviceFormat(deviceID: deviceID) else {
            throw AudioCaptureError.noInputAvailable
        }
        detectedSampleRate = asbd.mSampleRate
        guard detectedSampleRate > 0, asbd.mChannelsPerFrame >= 1 else {
            throw AudioCaptureError.noInputAvailable
        }
        guard let inFmt = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: detectedSampleRate,
            // The callback explicitly downmixes the device buffer before
            // conversion, so the converter input is always mono.
            channels: 1,
            interleaved: false
        ),
        let outFmt = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: PCMConversion.targetSampleRate,
            channels: 1,
            interleaved: false
        ),
        let converter = AVAudioConverter(from: inFmt, to: outFmt)
        else {
            throw AudioCaptureError.converterCreationFailed
        }
        inputFormat = inFmt
        targetFormat = outFmt
        audioConverter = converter
        watchdog.reset()
        sequence = 0

        let unmanagedSelf = Unmanaged.passUnretained(self)
        var procID: AudioDeviceIOProcID?
        let err = AudioDeviceCreateIOProcID(
            deviceID,
            { (_, inNow, inInputData, _, _, _, clientData) -> OSStatus in
                guard let clientData else { return noErr }
                let capture = Unmanaged<CoreAudioCapture>.fromOpaque(clientData).takeUnretainedValue()
                capture.handleAudioInput(inInputData)
                return noErr
            },
            unmanagedSelf.toOpaque(),
            &procID
        )
        guard err == noErr, let procID else {
            throw AudioCaptureError.engineStartFailed
        }
        ioProcID = procID
        let startErr = AudioDeviceStart(deviceID, procID)
        guard startErr == noErr else {
            AudioDeviceDestroyIOProcID(deviceID, procID)
            ioProcID = nil
            throw AudioCaptureError.engineStartFailed
        }
        isCapturing = true
    }

    private func stopLocked() {
        guard isCapturing else {
            audioConverter = nil
            inputFormat = nil
            targetFormat = nil
            return
        }
        isCapturing = false
        if let procID = ioProcID {
            AudioDeviceStop(deviceID, procID)
            AudioDeviceDestroyIOProcID(deviceID, procID)
            ioProcID = nil
        }
        audioConverter = nil
        inputFormat = nil
        targetFormat = nil
        detectedSampleRate = 0
    }

    private func handleAudioInput(_ inputData: UnsafePointer<AudioBufferList>?) {
        guard isCapturing, !cancelled,
              let bufferList = inputData?.pointee,
              let converter = audioConverter,
              let targetFmt = targetFormat,
              let inputFmt = inputFormat
        else { return }

        let buffer = bufferList.mBuffers
        guard let data = buffer.mData, buffer.mDataByteSize > 0 else { return }
        let bytesPerFrame = UInt32(MemoryLayout<Float32>.size) * buffer.mNumberChannels
        guard bytesPerFrame > 0 else { return }
        let frameCount = buffer.mDataByteSize / bytesPerFrame
        guard frameCount > 0 else { return }

        guard let inputBuffer = AVAudioPCMBuffer(pcmFormat: inputFmt, frameCapacity: frameCount) else { return }
        inputBuffer.frameLength = frameCount
        let srcPtr = data.assumingMemoryBound(to: Float32.self)
        let channelCount = Int(buffer.mNumberChannels)
        guard let floatData = inputBuffer.floatChannelData else { return }
        let monoPtr = floatData[0]
        if channelCount >= 2 {
            for i in 0..<Int(frameCount) {
                let left = srcPtr[i * channelCount]
                let right = srcPtr[i * channelCount + 1]
                monoPtr[i] = ((left.isFinite ? left : 0) + (right.isFinite ? right : 0)) / 2.0
            }
        } else {
            memcpy(monoPtr, srcPtr, Int(buffer.mDataByteSize))
        }

        let outputFrameCapacity = PCMConversion.resampledFrameCapacity(
            frameCount: frameCount,
            sourceSampleRate: detectedSampleRate,
            targetSampleRate: PCMConversion.targetSampleRate
        )
        guard outputFrameCapacity > 0,
              let outputBuffer = AVAudioPCMBuffer(pcmFormat: targetFmt, frameCapacity: outputFrameCapacity)
        else { return }

        var error: NSError?
        hasConsumedInput = false
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            if self.hasConsumedInput {
                outStatus.pointee = .noDataNow
                return nil
            }
            self.hasConsumedInput = true
            outStatus.pointee = .haveData
            return inputBuffer
        }
        converter.convert(to: outputBuffer, error: &error, withInputFrom: inputBlock)
        if error != nil { return }
        guard let channelData = outputBuffer.floatChannelData?[0] else { return }
        let processed = Int(outputBuffer.frameLength)
        var pcmData = [Int16]()
        pcmData.reserveCapacity(processed)
        for i in 0..<processed {
            let pcmSample = PCMConversion.floatToPCM16(channelData[i])
            pcmData.append(pcmSample)
            let absoluteSample = pcmSample == Int16.min ? Int16.max : Int16(pcmSample.magnitude)
            if absoluteSample > watchdogWindowPeak { watchdogWindowPeak = absoluteSample }
        }
        let byteData = pcmData.withUnsafeBufferPointer { Data(buffer: $0) }
        if byteData.count > PCMConversion.maxFrameBytes { return }

        let nowAbs = CFAbsoluteTimeGetCurrent()
        if watchdogWindowStart == 0 { watchdogWindowStart = nowAbs }
        if nowAbs - watchdogWindowStart >= 1.0 {
            let isBT = Self.isBluetoothTransport(deviceID: deviceID)
            if let _ = watchdog.evaluate(peak: watchdogWindowPeak, isBluetooth: isBT, now: nowAbs) {
                onSilent?()
            }
            watchdogWindowPeak = 0
            watchdogWindowStart = nowAbs
        }

        sequence &+= 1
        onChunk?(byteData, sequence)
    }

    public static func defaultInputDeviceID() -> AudioDeviceID? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var deviceID = kAudioObjectUnknown
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let err = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            0,
            nil,
            &size,
            &deviceID
        )
        guard err == noErr, deviceID != kAudioObjectUnknown else { return nil }
        return deviceID
    }

    public static func deviceFormat(deviceID: AudioDeviceID) -> AudioStreamBasicDescription? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamFormat,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let err = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &asbd)
        guard err == noErr else { return nil }
        return asbd
    }

    public static func isBluetoothTransport(deviceID: AudioDeviceID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var transport: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        let err = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &transport)
        guard err == noErr else { return false }
        return transport == kAudioDeviceTransportTypeBluetooth
            || transport == kAudioDeviceTransportTypeBluetoothLE
    }
}
