// The REAL player on the iOS Simulator (just_audio through the app's own
// AudioService), no doubles:
//
//  1. Durations: the player's OWN duration for every real MP3 key passed in
//     the files dart-define (PLAYER DUR <path> <ms>) — a third measure beside
//     afinfo/ffprobe for the header estimate `pipeline._get_duration` writes
//     onto the wire;
//  2. An interruption: the test plays a real stop's narration and logs the
//     player's state once a second (PLAYER t=<s> … playing= pos=) while the
//     host locks the device, wakes it, invokes Siri and returns. AudioService
//     configures the audio session and translates its interruptions, so this
//     records what the phone does with them: continue, pause, resume, or crash.
//
// The host signals "go" by creating a file in the app's Documents directory
// (`xcrun simctl get_app_container <udid> com.ondoway.app data`/Documents/w71-go)
// so the button presses land at KNOWN offsets from the logged GO time. Every
// transcript line starts with "PLAYER ".

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:just_audio/just_audio.dart';
import 'package:ondoway/services/audio_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:path_provider/path_provider.dart';

const _token = String.fromEnvironment('W71_TOKEN');
const _trip = String.fromEnvironment('W71_TRIP');
const _files = String.fromEnvironment('W71_FILES'); // comma-separated keys under /audio/files/
const _seconds = int.fromEnvironment('W71_SECONDS', defaultValue: 130);

String _hms(DateTime d) =>
    '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}'
    ':${d.second.toString().padLeft(2, '0')}';

String _origin(String baseUrl) =>
    baseUrl.endsWith('/api/v1') ? baseUrl.substring(0, baseUrl.length - 7) : baseUrl;

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('the player as it is: durations, and an interruption', (tester) async {
    expect(_token.isNotEmpty, isTrue, reason: 'run with the dart-defines');
    expect(_trip.isNotEmpty, isTrue);
    final origin = _origin(TripService.baseUrl);
    final lines = <String>[];
    void say(String l) {
      lines.add(l);
      debugPrint(l);
    }

    // ---- 1. the player's own duration per real file ------------------------
    for (final key in _files.split(',').where((k) => k.trim().isNotEmpty)) {
      final url = '$origin/api/v1/audio/files/$key';
      final p = AudioPlayer();
      try {
        final d = await p.setUrl(url);
        say('PLAYER DUR $key ${d?.inMilliseconds ?? -1}');
      } catch (e) {
        say('PLAYER DUR $key ERR $e');
      } finally {
        await p.dispose();
      }
    }

    // ---- 2. a real piece, the real service, an interruption from outside ---
    final tripService = TripService();
    final session = await tripService.fetchSession(_trip, _token);
    final stop = session.stops.firstWhere((s) => s.audioUrl != null);
    final rel = stop.audioUrl!;
    final url = rel.startsWith('/') ? '$origin$rel' : rel;
    final audio = AudioService();
    say('PLAYER PLAY ${stop.poiName} $url wire_duration=${stop.audioDurationSec}');
    // NOT awaited: just_audio's play() future completes only when the piece
    // ENDS (or is paused/stopped) — the app's playback service calls this door
    // fire-and-forget too (AudioProvider.play is void). Awaiting it would hold
    // the test here for the whole piece.
    unawaited(audio.play(stop.stopId ?? stop.beatId ?? 'w71', url).catchError((Object e) {
      say('PLAYER PLAY-ERROR $e');
    }));
    // Wait until the piece is really moving.
    for (var i = 0; i < 40; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
      if (audio.isPlaying && audio.position.inMilliseconds > 0) break;
    }
    say('PLAYER READY ${_hms(DateTime.now())} playing=${audio.isPlaying} '
        'pos=${audio.position.inMilliseconds} dur=${audio.duration.inMilliseconds}');

    // The host's go signal.
    final docs = await getApplicationDocumentsDirectory();
    final go = File('${docs.path}/w71-go');
    say('PLAYER WAITING ${go.path}');
    final deadline = DateTime.now().add(const Duration(minutes: 6));
    while (!go.existsSync() && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
    final t0 = DateTime.now();
    say('PLAYER GO ${_hms(t0)} go_file=${go.existsSync()}');

    for (var t = 0; t <= _seconds; t++) {
      say('PLAYER t=$t ${_hms(DateTime.now())} playing=${audio.isPlaying} '
          'buffering=${audio.isBuffering} completed=${audio.isCompleted} '
          'active=${audio.isActive} pos=${audio.position.inMilliseconds} '
          'dur=${audio.duration.inMilliseconds}');
      await Future<void>.delayed(const Duration(seconds: 1));
    }
    say('PLAYER END ${_hms(DateTime.now())}');
    await audio.stop();
    audio.dispose();
    for (final l in lines) {
      debugPrint('PLAYER-SUM $l');
    }
  }, timeout: const Timeout(Duration(minutes: 15)));
}
