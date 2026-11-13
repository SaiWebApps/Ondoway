@Tags(['vm'])
library;

// Native iOS audio routing: this exercises a MethodChannel and the platform
// override, and local's AudioService configures an audio session on the way in.
// Under `--platform chrome` there is no such platform and the session call
// hangs, so this runs on flutter's native engine with the other vm-tagged
// tests. The fork left it untagged because the fork's service had no session
// code to hang on.

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/services/audio_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const nativeChannel = MethodChannel('com.ondoway/native_audio');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  final calls = <MethodCall>[];

  // Injected cache resolver: beat-1 is "cached" at this path, nothing else is.
  // Keeps the test off the real filesystem, so it needs no path_provider plugin
  // and stays hermetic on the native engine. (The seam was written to make this
  // runnable on the web runner; the file is vm-tagged now and never goes there,
  // but a test that touches no real cache directory is worth keeping either way.)
  Future<String?> resolver(String beatId) async =>
      beatId == 'beat-1' ? '/cache/ondoway_audio/beat-1.mp3' : null;

  setUp(() {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    calls.clear();
    messenger.setMockMethodCallHandler(nativeChannel, (call) async {
      calls.add(call);
      switch (call.method) {
        case 'play':
          return 30.0; // seconds
        case 'getPosition':
          return 0;
        default:
          return null;
      }
    });
  });

  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
    messenger.setMockMethodCallHandler(nativeChannel, null);
  });

  Future<void> fireNativeComplete() async {
    await messenger.handlePlatformMessage(
      nativeChannel.name,
      const StandardMethodCodec()
          .encodeMethodCall(const MethodCall('onComplete')),
      (_) {},
    );
  }

  test('cached-file playback on iOS routes to the native player, not just_audio',
      () async {
    final service = AudioService(cachedPathResolver: resolver);
    addTearDown(service.dispose);

    await service.play('beat-1', 'https://cdn.example.com/beat-1.mp3');

    final playCall = calls.singleWhere((c) => c.method == 'play');
    expect((playCall.arguments as Map)['path'], '/cache/ondoway_audio/beat-1.mp3');
    expect(service.isPlaying, isTrue);
    expect(service.currentBeatId, 'beat-1');
    expect(service.duration, const Duration(seconds: 30));

    await service.stop();
  });

  test('native completion flips isPlaying false so the tour can auto-advance',
      () async {
    final service = AudioService(cachedPathResolver: resolver);
    addTearDown(service.dispose);

    await service.play('beat-1', 'https://cdn.example.com/beat-1.mp3');
    expect(service.isPlaying, isTrue);

    await fireNativeComplete();

    expect(service.isPlaying, isFalse);
    // currentBeatId is retained (matches just_audio) so the tour engine's
    // completion check can identify which stop just finished.
    expect(service.currentBeatId, 'beat-1');
    expect(service.position, service.duration);
  });

  test('playFrom seeks the native player, so a cut piece does not restart',
      () async {
    // The keep-listening tap at a door resumes the cut piece from the
    // START of its cut sentence. The native player has no play-at-position
    // call, so the service must seek after starting it. Without that seek the
    // piece begins again from zero and the tourist re-hears what they already
    // heard — which is the whole failure this rule exists to prevent.
    final service = AudioService(cachedPathResolver: resolver);
    addTearDown(service.dispose);

    service.playFrom(
      'beat-1',
      'https://cdn.example.com/beat-1.mp3',
      const Duration(seconds: 12),
    );
    // playFrom is void by the provider's contract, so there is no future to
    // await. Yield the event loop until the seek lands rather than sleeping a
    // guessed interval — the start path has two awaits before it reaches the
    // native player.
    for (var i = 0; i < 50 && !calls.any((c) => c.method == 'seek'); i++) {
      await Future<void>.delayed(Duration.zero);
    }

    expect(calls.any((c) => c.method == 'play'), isTrue);
    final seekCall = calls.singleWhere((c) => c.method == 'seek');
    expect((seekCall.arguments as Map)['positionMs'], 12000);
    expect(service.position, const Duration(seconds: 12));

    await service.stop();
  });

  test('stop halts the native player and clears state', () async {
    final service = AudioService(cachedPathResolver: resolver);
    addTearDown(service.dispose);

    await service.play('beat-1', 'https://cdn.example.com/beat-1.mp3');
    await service.stop();

    expect(calls.any((c) => c.method == 'stop'), isTrue);
    expect(service.isPlaying, isFalse);
    expect(service.currentBeatId, isNull);
  });
}
