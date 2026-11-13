import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ondoway/services/audio_service.dart';
import 'package:ondoway/services/trip_service.dart';

void main() {
  // AudioService now owns a native MethodChannel (com.ondoway/native_audio),
  // and registering its handler needs an initialized binding — even for these
  // HTTP-only tests that construct the service but never play.
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AudioService', () {
    // TOMBSTONE: the three `checkAudioStatus` tests pinned the phone's PER-BEAT
    // status poll (GET /audio/status/{beatId}) — the per-beat audio library,
    // which is gone. Deleted, not adapted; the per-STOP poll below survives.

    test(
      'checkStopAudioStatus hits the per-STOP endpoint and parses on 200',
      () async {
        final client = MockClient((request) async {
          expect(request.method, 'GET');
          // Per-stop endpoint, NOT the per-beat /audio/status/{beatId}.
          expect(request.url.path, contains('/audio/stop-status/stop-7'));
          return http.Response(
            jsonEncode({
              'stop_id': 'stop-7',
              'has_audio': true,
              'audio_url': 'https://cdn.example.com/stop-7.mp3',
              'duration_sec': 95,
            }),
            200,
          );
        });

        final service = AudioService(httpClient: client);
        final result = await service.checkStopAudioStatus(
          'http://localhost:8000/api/v1',
          'stop-7',
        );

        expect(result, isNotNull);
        expect(result!['has_audio'], true);
        expect(result['audio_url'], 'https://cdn.example.com/stop-7.mp3');
        expect(result['duration_sec'], 95);
      },
    );

    test('checkStopAudioStatus returns null on 404', () async {
      final client = MockClient((request) async {
        return http.Response('not found', 404);
      });

      final service = AudioService(httpClient: client);
      final result = await service.checkStopAudioStatus(
        'http://localhost:8000/api/v1',
        'stop-missing',
      );

      expect(result, isNull);
    });

    test('checkStopAudioStatus returns null on network error', () async {
      final client = MockClient((request) async {
        throw Exception('Network error');
      });

      final service = AudioService(httpClient: client);
      final result = await service.checkStopAudioStatus(
        'http://localhost:8000/api/v1',
        'stop-7',
      );

      expect(result, isNull);
    });
  });

  group('AudioService.audioUri', () {
    test(
      'a path on the server is read against the server the app talks to',
      () {
        expect(
          AudioService.audioUri(
            '/api/v1/audio/files/stops/hall/item-1.mp3',
            apiBaseUrl: 'http://localhost:8000/api/v1',
          ).toString(),
          'http://localhost:8000/api/v1/audio/files/stops/hall/item-1.mp3',
        );
      },
    );

    test('a full address is used as given', () {
      expect(
        AudioService.audioUri(
          'https://audio.ondoway.com/stops/hall/item-1.mp3',
          apiBaseUrl: 'http://localhost:8000/api/v1',
        ).toString(),
        'https://audio.ondoway.com/stops/hall/item-1.mp3',
      );
    });
  });

  // The two places a stop's audio address leaves the app: streaming it when it
  // is not cached, and downloading it ahead of the walk. A server that stores
  // audio on its own disk hands back a path on itself; both must reach the
  // network as a full address on the server the app talks to.
  group('a relative audio path reaches the network as a full address', () {
    const relativeAudioUrl = '/api/v1/audio/files/stops/hall/item-1.mp3';
    final expectedAddress =
        '${Uri.parse(TripService.baseUrl).origin}$relativeAudioUrl';
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

    test('uncached playback hands the player the full address', () async {
      // just_audio talks to its platform over these channels: one shared
      // channel creates a player, which then gets its own method channel and
      // two event channels named after its id. The recorded `load` is the
      // source the platform player is asked to open.
      const justAudio = MethodChannel('com.ryanheise.just_audio.methods');
      final loaded = Completer<Map<dynamic, dynamic>>();
      final playerChannels = <String>[];
      messenger.setMockMethodCallHandler(justAudio, (call) async {
        if (call.method == 'init') {
          final id = (call.arguments as Map)['id'] as String;
          playerChannels.add(id);
          messenger.setMockMethodCallHandler(
            MethodChannel('com.ryanheise.just_audio.methods.$id'),
            (call) async {
              if (call.method == 'load' && !loaded.isCompleted) {
                loaded.complete(call.arguments as Map<dynamic, dynamic>);
              }
              return <dynamic, dynamic>{};
            },
          );
          for (final events in [
            'com.ryanheise.just_audio.events.$id',
            'com.ryanheise.just_audio.data.$id',
          ]) {
            messenger.setMockStreamHandler(
              EventChannel(events),
              MockStreamHandler.inline(onListen: (_, _) {}),
            );
          }
        }
        return <dynamic, dynamic>{};
      });
      addTearDown(() {
        messenger.setMockMethodCallHandler(justAudio, null);
        for (final id in playerChannels) {
          messenger.setMockMethodCallHandler(
            MethodChannel('com.ryanheise.just_audio.methods.$id'),
            null,
          );
        }
      });

      final service = AudioService(
        cachedPathResolver: (_) async => null, // nothing is cached
      );
      addTearDown(service.dispose);

      // The load never reports ready here, so playback is left pending and
      // stopped below; only the address the player was given is under test.
      unawaited(service.play('stop-1', relativeAudioUrl).catchError((_) {}));
      final request = await loaded.future.timeout(const Duration(seconds: 5));

      final source = request['audioSource'] as Map<dynamic, dynamic>;
      expect(source['type'], 'progressive');
      expect(source['uri'], expectedAddress);

      await service.stop();
    }, tags: ['vm']);

    test('prefetching downloads from the full address', () async {
      const pathProvider = MethodChannel('plugins.flutter.io/path_provider');
      final tempDir = Directory.systemTemp.createTempSync('audio_prefetch_');
      addTearDown(() => tempDir.deleteSync(recursive: true));
      messenger.setMockMethodCallHandler(pathProvider, (call) async {
        if (call.method == 'getTemporaryDirectory') return tempDir.path;
        return null;
      });
      addTearDown(() => messenger.setMockMethodCallHandler(pathProvider, null));

      final requested = <Uri>[];
      final client = MockClient((request) async {
        requested.add(request.url);
        return http.Response.bytes([1, 2, 3], 200);
      });
      final service = AudioService(httpClient: client);
      addTearDown(service.dispose);

      final cached = await service.prefetchAudio(const [
        BeatAudioInfo(beatId: 'stop-1', audioUrl: relativeAudioUrl),
      ]);

      expect(requested.map((u) => u.toString()), [expectedAddress]);
      expect(cached, 1);
      expect(
        File('${tempDir.path}/ondoway_audio/stop-1.mp3').readAsBytesSync(),
        [1, 2, 3],
      );
    }, tags: ['vm']);
  });

  group('BeatAudioInfo', () {
    test('stores beatId and audioUrl', () {
      const info = BeatAudioInfo(
        beatId: 'beat-123',
        audioUrl: 'https://audio.ondoway.com/beats/beat-123.mp3',
      );
      expect(info.beatId, 'beat-123');
      expect(info.audioUrl, 'https://audio.ondoway.com/beats/beat-123.mp3');
    });

    test('audioUrl can be null', () {
      const info = BeatAudioInfo(beatId: 'beat-456');
      expect(info.beatId, 'beat-456');
      expect(info.audioUrl, isNull);
    });
  });
}
