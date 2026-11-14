import 'package:flutter/services.dart';

/// Thin Dart wrapper over the native `com.ondoway/native_audio` channel, which
/// plays a local file through `AVAudioPlayer(data:)` on iOS.
///
/// Why this exists: just_audio's `AVPlayer` emits NO audible output while the
/// app is backgrounded / the screen is locked on iOS 26, even on a correctly
/// active `.playback`/`.duckOthers` session (measured on a device). A native
/// `AVAudioPlayer` on that same session DOES play locked,
/// so tour narration routes through here. State the caller needs — duration,
/// position, and crucially **completion** (tour auto-advance depends on it) —
/// is bridged back: duration/position via return values, completion via the
/// [onComplete] callback the native `AVAudioPlayerDelegate` drives.
class NativeAudioBackend {
  NativeAudioBackend({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('com.ondoway/native_audio') {
    _channel.setMethodCallHandler(_handleNativeCall);
  }

  final MethodChannel _channel;

  /// Invoked when the native player finishes the current clip. The owning
  /// [AudioService] uses this to flip `isPlaying` false and let the tour engine
  /// auto-advance — the reason we cannot rely on just_audio here.
  void Function()? onComplete;

  /// Start playing the file at [path]. Returns its duration (zero if the native
  /// player could not report one).
  Future<Duration> play(String path) async {
    final seconds =
        await _channel.invokeMethod<double>('play', {'path': path}) ?? 0.0;
    return Duration(milliseconds: (seconds * 1000).round());
  }

  Future<void> pause() => _channel.invokeMethod<void>('pause');

  Future<void> resume() => _channel.invokeMethod<void>('resume');

  Future<void> stop() => _channel.invokeMethod<void>('stop');

  Future<void> seek(Duration position) =>
      _channel.invokeMethod<void>('seek', {'positionMs': position.inMilliseconds});

  /// Current playback position, polled for the UI progress bar. Zero when the
  /// native player is idle.
  Future<Duration> getPosition() async {
    final ms = await _channel.invokeMethod<int>('getPosition') ?? 0;
    return Duration(milliseconds: ms);
  }

  Future<dynamic> _handleNativeCall(MethodCall call) async {
    if (call.method == 'onComplete') {
      onComplete?.call();
    }
    return null;
  }
}
