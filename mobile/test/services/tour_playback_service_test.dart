import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

// Every fixture STATES its footprint. These
// tests were written against the phone's own 10 m circle; that circle is gone
// and "at the stop" is the wire's per-place radius, so the doorway-sized
// footprint the old tests assumed is now an explicit 10 m trigger on each stop
// (re-derived with the same meaning). A
// null radius is a legacy item: no geometry.
ItineraryStop _makeStop({
  required int sortOrder,
  required String beatId,
  required double lat,
  required double lng,
  String? audioUrl,
  String? stopId,
  double? radiusM = 10,
  int queueSeconds = 0,
  bool door = false,
  int outsideSeconds = 0,
  String? narration,
  double? audioDurationSec,
  String? closeText,
  String? legAudioUrl,
  String? legNarration,
  List<StopSegment> segments = const [],
}) {
  return ItineraryStop(
    sortOrder: sortOrder,
    stopId: stopId,
    poiId: 'poi-$sortOrder',
    poiName: 'Stop $sortOrder',
    lat: lat,
    lng: lng,
    beatId: beatId,
    lensName: 'history',
    lensDisplay: 'History',
    durationMin: 5,
    importanceTier: sortOrder == 1 ? 5 : 3,
    startTime: '09:0$sortOrder',
    audioUrl: audioUrl,
    narration: narration,
    audioDurationSec: audioDurationSec,
    closeText: closeText,
    legAudioUrl: legAudioUrl,
    legNarration: legNarration,
    segments: segments,
    trigger: radiusM == null
        ? null
        : StopTrigger(
            radiusM: radiusM,
            queueSeconds: queueSeconds,
            door: door,
            outsideSeconds: outsideSeconds,
          ),
  );
}

/// A player that records every door it is handed.
class _DoorPlayer extends MockAudioService {
  final List<String> said = [];
  final List<(String, Duration)> playedFrom = [];
  int stops = 0;
  Duration pos = Duration.zero;
  @override
  Duration get position => pos;
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
  @override
  void playFrom(String beatId, String audioUrl, Duration from, {String? title}) {
    playedFrom.add((beatId, from));
    super.playFrom(beatId, audioUrl, from, title: title);
  }

  @override
  void stop() {
    stops++;
    super.stop();
  }
}

// A 40-second piece of four sentences of 10 words each: boundaries at 10, 20,
// 30 and 40 s.
const _fourSentences =
    'One two three four five six seven eight nine ten. '
    'Eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty. '
    'Twenty-one two three four five six seven eight nine thirty. '
    "That's the stop, the close that ends it all here.";

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

void main() {
  group('TourPlaybackService.haversineDistance', () {
    test('returns 0 for same point', () {
      final d = TourPlaybackService.haversineDistance(
        48.8584, 2.2945, 48.8584, 2.2945,
      );
      expect(d, closeTo(0.0, 0.01));
    });

    test('returns correct distance for known points', () {
      // Eiffel Tower to Arc de Triomphe: ~2.2 km
      final d = TourPlaybackService.haversineDistance(
        48.8584, 2.2945, // Eiffel Tower
        48.8738, 2.2950, // Arc de Triomphe (approx)
      );
      expect(d, closeTo(1713, 50)); // ~1.7 km
    });

    test('returns correct short distance (within geofence)', () {
      // Two points ~5 meters apart
      // 0.00005 degrees latitude ~ 5.5 meters
      final d = TourPlaybackService.haversineDistance(
        48.858400, 2.294500,
        48.858445, 2.294500,
      );
      expect(d, closeTo(5.0, 1.0));
    });

    test('handles antipodal points', () {
      // North pole to south pole: ~20,000 km
      final d = TourPlaybackService.haversineDistance(
        90.0, 0.0, -90.0, 0.0,
      );
      expect(d, closeTo(20015086, 1000));
    });
  });

  group('TourPlaybackService lifecycle', () {
    late MockLocationService locationService;
    late MockAudioService audioService;
    late TourPlaybackService service;

    setUp(() {
      locationService = MockLocationService();
      audioService = MockAudioService();
      service = TourPlaybackService(
        locationService: locationService,
        audioService: audioService,
      );
    });

    tearDown(() {
      service.dispose();
    });

    test('initial state is idle', () {
      expect(service.state, TourState.idle);
      expect(service.currentStopIndex, -1);
      expect(service.currentStop, isNull);
      expect(service.nextStop, isNull);
      expect(service.isActive, false);
      expect(service.hasPendingStop, false);
      expect(service.distanceToNext, isNull);
    });

    test('startTour with empty stops returns false', () async {
      final result = await service.startTour([]);
      expect(result, false);
      expect(service.state, TourState.idle);
    });

    test('startTour prepares the audio session before background tracking (AC1)',
        () async {
      final log = <String>[];
      audioService.callLog = log;
      locationService.callLog = log;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'b1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://example.test/1.mp3',
        ),
      ];

      await service.startTour(stops);

      expect(log, ['prepare', 'track']);
      expect(locationService.lastBackground, isTrue);
      expect(audioService.prepareSessionCount, 1);
    });

    test('startTour with valid stops sets state to active', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      final result = await service.startTour(stops);

      expect(result, true);
      expect(service.state, TourState.active);
      expect(service.currentStopIndex, 0);
      expect(service.currentStop, isNotNull);
      expect(service.currentStop!.beatId, 'beat-1');
      expect(service.isActive, true);
      expect(locationService.isTracking, true);
    });

    test('startTour returns false when tracking fails', () async {
      locationService.trackingWillSucceed = false;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      final result = await service.startTour(stops);

      expect(result, false);
      expect(service.state, TourState.idle);
    });

    test('stopTour resets all state', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);
      service.stopTour();

      expect(service.state, TourState.idle);
      expect(service.currentStopIndex, -1);
      expect(service.currentStop, isNull);
      expect(service.nextStop, isNull);
      expect(service.isActive, false);
      expect(service.pendingStopIndex, isNull);
      expect(service.distanceToNext, isNull);
      expect(locationService.isTracking, false);
    });

    test('skipToStop updates currentStopIndex', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
        _makeStop(
          sortOrder: 3,
          beatId: 'beat-3',
          lat: 48.8530,
          lng: 2.3499,
          audioUrl: 'https://cdn.ondoway.com/beat-3.mp3',
        ),
      ];

      await service.startTour(stops);
      service.skipToStop(2);

      expect(service.currentStopIndex, 2);
      expect(service.currentStop!.beatId, 'beat-3');
      expect(audioService.currentBeatId, 'beat-3');
    });

    test('plays per-stop audio addressed by stopId (not beatId)', () async {
      // When a stop carries a per-stop ItineraryItem id, the playback
      // key is the stopId — the per-stop narration — not the legacy beatId.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          stopId: 'stop-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/stop-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          stopId: 'stop-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/stop-2.mp3',
        ),
      ];

      await service.startTour(stops);
      service.skipToStop(1);

      // The play key is the stopId, not the beatId.
      expect(service.currentStop!.beatId, 'beat-2');
      expect(audioService.currentBeatId, 'stop-2');

      // Completion uses the SAME key, so auto-advance still fires.
      audioService.simulateComplete();
      expect(service.currentStopIndex, greaterThanOrEqualTo(1));
    });

    test('falls back to beatId for the play key when stopId is null', () async {
      // Legacy stops with no per-stop id keep playing by beatId — no regression.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      await service.startTour(stops);
      service.skipToStop(1);

      expect(audioService.currentBeatId, 'beat-2');
    });

    test('skipToStop ignores invalid index', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);
      service.skipToStop(5); // out of bounds

      expect(service.currentStopIndex, 0);
    });

    test('skipToStop ignores negative index', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);
      service.skipToStop(-1);

      expect(service.currentStopIndex, 0);
    });

    test('acceptPendingStop advances to pending', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      await service.startTour(stops);

      // Simulate: play current stop, then user arrives at next
      audioService.play('beat-1', 'https://cdn.ondoway.com/beat-1.mp3');

      // Simulate position at stop 2 (inside its 10 m footprint)
      locationService.simulatePosition(48.8606, 2.3376);

      expect(service.state, TourState.approaching);
      expect(service.pendingStopIndex, 1);

      service.acceptPendingStop();

      expect(service.currentStopIndex, 1);
      expect(service.pendingStopIndex, isNull);
      expect(audioService.currentBeatId, 'beat-2');
    });

    test('dismissPending clears pending and returns to active', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      await service.startTour(stops);

      // Simulate: play current stop, then user arrives at next
      audioService.play('beat-1', 'https://cdn.ondoway.com/beat-1.mp3');
      locationService.simulatePosition(48.8606, 2.3376);

      expect(service.state, TourState.approaching);
      expect(service.hasPendingStop, true);

      service.dismissPending();

      expect(service.state, TourState.active);
      expect(service.pendingStopIndex, isNull);
      expect(service.hasPendingStop, false);
      // Still at stop 0
      expect(service.currentStopIndex, 0);
    });

    test('geofence triggers auto-play when user enters radius', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);

      // User is far away — no auto-play
      locationService.simulatePosition(48.8600, 2.2945);
      expect(audioService.isPlaying, false);

      // User enters the stop's footprint (its 10 m trigger)
      locationService.simulatePosition(48.8584, 2.2945);
      expect(audioService.isPlaying, true);
      expect(audioService.currentBeatId, 'beat-1');
    });

    test('the radius rides the STOP, so a wide stop fires where a narrow one '
        'does not', () async {
      // Was "geofence radius is configurable" against a service-wide override.
      // That override was deleted on 2026-08-31: one number for every stop
      // cannot say that a 140 m courtyard and a doorway are different places,
      // and the phone reads each stop's server-placed footprint instead. The
      // requirement the test protects is unchanged — a wider circle must fire
      // further out — so it is asserted where the width actually lives.
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
          radiusM: 20,
        ),
      ];
      await service.startTour(stops);

      // ~15.5 m north of the stop: outside a 10 m footprint, inside this 20 m one.
      locationService.simulatePosition(48.8584 + 0.00014, 2.2945);
      expect(audioService.isPlaying, true);
      expect(audioService.currentBeatId, 'beat-1');
    });

    test('a narrow stop stays silent at the same distance', () async {
      // The other half of the pair: without it, the test above would pass on a
      // service that ignored the radius entirely and fired on any fix.
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
          radiusM: 10,
        ),
      ];
      await service.startTour(stops);

      locationService.simulatePosition(48.8584 + 0.00014, 2.2945);
      expect(audioService.isPlaying, false);
      expect(audioService.currentBeatId, isNull);
    });

    test('does not auto-play if audio already playing', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);

      // Already playing
      audioService.play('beat-1', 'https://cdn.ondoway.com/beat-1.mp3');

      // Enter geofence again — should not restart
      locationService.simulatePosition(48.8584, 2.2945);
      // isPlaying is still true (not restarted)
      expect(audioService.isPlaying, true);
      expect(audioService.playCount, 1);
    });

    test('tour-audio completion auto-advances to the next stop (KE6 control)',
        () async {
      // Control for the deeper-dive guard: a completed SCHEDULED per-stop tour clip
      // still advances the itinerary index.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          stopId: 'stop-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/stop-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          stopId: 'stop-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/stop-2.mp3',
        ),
      ];

      await service.startTour(stops);
      // Play the current stop's SCHEDULED audio (keyed by the stop key, NOT
      // deeper-dive), then complete it.
      service.skipToStop(0);
      expect(service.currentStopIndex, 0);
      expect(audioService.isDeeperDive, isFalse);

      audioService.simulateComplete();

      // Scheduled tour audio finished -> advance to the next stop.
      expect(service.currentStopIndex, 1);
    });

    test('deeper-dive completion does NOT auto-advance the tour (KE6)',
        () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          stopId: 'stop-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/stop-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          stopId: 'stop-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/stop-2.mp3',
        ),
      ];

      await service.startTour(stops);
      expect(service.currentStopIndex, 0);

      // User taps "Keep exploring here" — a DEEPER-DIVE clip plays off-budget.
      audioService.play(
        'stop-1-keep-exploring',
        'https://cdn.ondoway.com/stop-1-ke.mp3',
        isDeeperDive: true,
      );
      expect(audioService.isDeeperDive, isTrue);

      // Deep-dive clip finishes.
      audioService.simulateComplete();

      // The tour MUST NOT advance — the index stays on the current stop.
      expect(service.currentStopIndex, 0);
      expect(service.state, isNot(TourState.completed));
    });

    test('deeper-dive completion does not advance even when a stop is pending '
        '(KE6)', () async {
      // Guards the FIRST advance branch (approaching + pendingStopIndex): a
      // deep dive completing mid-approach must not steal the auto-advance.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      await service.startTour(stops);
      // Scheduled current-stop audio playing, then user reaches next stop.
      audioService.play('beat-1', 'https://cdn.ondoway.com/beat-1.mp3');
      locationService.simulatePosition(48.8606, 2.3376);
      expect(service.state, TourState.approaching);
      expect(service.pendingStopIndex, 1);

      // A deep-dive clip takes over and completes.
      audioService.play(
        'stop-1-keep-exploring',
        'https://cdn.ondoway.com/stop-1-ke.mp3',
        isDeeperDive: true,
      );
      audioService.simulateComplete();

      // Still at stop 0; the pending nudge is untouched (no auto-advance).
      expect(service.currentStopIndex, 0);
      expect(service.pendingStopIndex, 1);
    });

    test('a play that never starts does NOT phantom-advance the tour', () async {
      // On-device regression: a geofence fire whose play() fails to start (the
      // native player threw) leaves isPlaying=false with currentBeatId==the
      // current stop. The old completion check (`!isPlaying`) read that as a
      // completion and jumped to the next stop before any audio played.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
        _makeStop(
          sortOrder: 2,
          beatId: 'beat-2',
          lat: 48.8606,
          lng: 2.3376,
          audioUrl: 'https://cdn.ondoway.com/beat-2.mp3',
        ),
      ];

      await service.startTour(stops);
      audioService.playSucceeds = false; // the native player will "throw"

      // Enter the geofence for stop 0 — play fires but never reaches playing=true.
      locationService.simulatePosition(48.8584, 2.2945);

      expect(audioService.currentBeatId, 'beat-1');
      expect(audioService.isPlaying, isFalse);
      // The tour must stay on stop 0 — no phantom advance, not completed.
      expect(service.currentStopIndex, 0);
      expect(service.state, isNot(TourState.completed));
    });

    test('a stop fires only ONCE — no replay while lingering in its radius',
        () async {
      // On-device regression: the terminal stop replayed on every GPS tick
      // because nothing marked it already-fired once state=completed.
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);

      // Enter the geofence — fires once.
      locationService.simulatePosition(48.8584, 2.2945);
      expect(audioService.playCount, 1);

      // Audio completes -> single-stop tour is now completed.
      audioService.simulateComplete();
      expect(service.state, TourState.completed);

      // Still standing in the radius: more GPS ticks must NOT replay it.
      locationService.simulatePosition(48.8584, 2.2945);
      locationService.simulatePosition(48.85841, 2.2945);
      expect(audioService.playCount, 1);
    });

    test('releases the ducked audio session when the tour completes', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);
      locationService.simulatePosition(48.8584, 2.2945); // fire the only stop
      expect(audioService.releaseSessionCount, 0); // still playing

      audioService.simulateComplete(); // last stop done -> tour completed
      expect(service.state, TourState.completed);
      expect(audioService.releaseSessionCount, 1); // podcast un-ducks
    });

    test('releases the ducked audio session when the tour is stopped', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: 'https://cdn.ondoway.com/beat-1.mp3',
        ),
      ];

      await service.startTour(stops);
      service.stopTour();

      expect(audioService.releaseSessionCount, 1);
    });

    test('stop without audioUrl does not trigger play', () async {
      locationService.trackingWillSucceed = true;
      final stops = [
        _makeStop(
          sortOrder: 1,
          beatId: 'beat-1',
          lat: 48.8584,
          lng: 2.2945,
          audioUrl: null, // no audio
        ),
      ];

      await service.startTour(stops);
      locationService.simulatePosition(48.8584, 2.2945);

      expect(audioService.isPlaying, false);
      expect(audioService.currentBeatId, isNull);
    });
  });

  // THE AUDIO PLACEMENT RULE, the phone's half. "At the stop" is the place's
  // OWN footprint, placed on the server and read off the wire; the phone draws
  // no circle. The piece ARMS
  // at the first touch of the footprint (the edge you arrive by, never the pin)
  // and STARTS at arrival — the FAMILY day starts at the first standstill inside
  // it. Told once is told: re-entering never replays; a tap
  // does. A stop with no trigger has no geometry: nothing auto-plays there.
  group('the footprint is the place (S7.3; design §5.6; W7.2 R1)', () {
    const base = 48.85;
    const degPerMeterLat = 1.0 / 111320.0;
    const url = 'https://cdn.ondoway.com/piece.mp3';
    late MockLocationService gps;
    late MockAudioService audio;
    late _Clock clock;
    late TourPlaybackService service;

    setUp(() {
      gps = MockLocationService();
      audio = MockAudioService();
      clock = _Clock(DateTime(2026, 8, 22, 10, 0));
      service = TourPlaybackService(
        locationService: gps,
        audioService: audio,
        now: () => clock.now,
      );
    });

    tearDown(() => service.dispose());

    test('a 140 m courtyard triggers at its edge, ~130 m from the centroid — '
        'never 10 m from the pin (Place des Vosges, Nadia)', () async {
      await service.startTour([
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 140),
      ]);
      gps.simulatePosition(base + 160 * degPerMeterLat, 2.35); // outside
      expect(audio.isPlaying, isFalse, reason: '160 m out is not the square');
      gps.simulatePosition(base + 130 * degPerMeterLat, 2.35); // the arcade edge
      expect(audio.isPlaying, isTrue, reason: 'the first touch of the footprint arms and starts');
      expect(audio.currentBeatId, 'b1');
    });

    test('a 10 m doorway stays silent at 30 m and plays at 5 m', () async {
      await service.startTour([
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 10),
      ]);
      gps.simulatePosition(base + 30 * degPerMeterLat, 2.35);
      expect(audio.isPlaying, isFalse, reason: 'a door is not a 40 m circle');
      gps.simulatePosition(base + 5 * degPerMeterLat, 2.35);
      expect(audio.isPlaying, isTrue);
    });

    test('a stop with no trigger never auto-plays (a legacy item: no geometry); '
        'a tap still does', () async {
      await service.startTour([
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: null),
      ]);
      gps.simulatePosition(base, 2.35); // standing on the pin itself
      expect(audio.isPlaying, isFalse, reason: 'the phone invents no circle');
      service.skipToStop(0); // the tap
      expect(audio.isPlaying, isTrue);
    });

    test('the family day: the piece ARMS at the footprint edge and STARTS at '
        'the first standstill inside it (Nadia, §4.4.4); walking out before '
        'the standstill disarms it', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 60),
      ];
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180, party: 'family',
        placement: const PlacementPolicy(startAt: 'standstill', ownPlaceM: 60),
      ));
      gps.simulatePosition(base + 50 * degPerMeterLat, 2.35); // in, on the move
      expect(audio.isPlaying, isFalse, reason: 'armed, not started: a child crosses any circle');
      clock.tick(10);
      gps.simulatePosition(base + 35 * degPerMeterLat, 2.35); // still moving
      clock.tick(10);
      service.tick();
      expect(audio.isPlaying, isFalse, reason: 'not yet still for the settling period');
      clock.tick(TourPlaybackService.kSettleSeconds + 1); // the buggy stops
      service.tick();
      expect(audio.isPlaying, isTrue, reason: 'the first standstill inside starts it');

      // Walking out before the standstill disarms; nothing fires on the leg.
      final again = TourPlaybackService(
        locationService: gps, audioService: MockAudioService(), now: () => clock.now,
      );
      final stops2 = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 60),
      ];
      await again.startTour(stops2);
      again.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops2,
        retimeToleranceSeconds: 180, party: 'family',
        placement: const PlacementPolicy(startAt: 'standstill', ownPlaceM: 60),
      ));
      gps.simulatePosition(base + 50 * degPerMeterLat, 2.35); // in
      clock.tick(5);
      gps.simulatePosition(base + 120 * degPerMeterLat, 2.35); // out again
      clock.tick(TourPlaybackService.kSettleSeconds + 5);
      again.tick();
      expect((again.session != null), isTrue);
      expect(again.currentStopIndex, 0);
      expect(again.state, TourState.active);
      again.dispose();
    });

    test('told once is told: leaving and re-entering the footprint never '
        'replays a piece — a tap does', () async {
      await service.startTour([
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 40),
        _makeStop(sortOrder: 2, beatId: 'b2', lat: base + 500 * degPerMeterLat,
            lng: 2.35, audioUrl: url, radiusM: 40),
      ]);
      gps.simulatePosition(base + 30 * degPerMeterLat, 2.35); // enter: plays
      expect(audio.playCount, 1);
      audio.stop(); // the piece is cut short (a call, a tap) — not completed
      expect(service.currentStopIndex, 0);
      gps.simulatePosition(base + 200 * degPerMeterLat, 2.35); // out
      gps.simulatePosition(base + 20 * degPerMeterLat, 2.35); // back in
      expect(audio.playCount, 1, reason: 'told once is told');
      expect(audio.isPlaying, isFalse);
      service.skipToStop(0); // the tap replays
      expect(audio.playCount, 2);
    });

    // THE QUEUE PIECE. At a stop whose
    // arrival hour prices a line (the trigger's queue seconds), the piece does
    // not start at the footprint's edge: a solo walker's starts at the first
    // STANDSTILL inside the footprint — standing in the line is the place —
    // and a couple's or a family's waits for a TAP (a still phone among them is
    // a conversation or a sandpit, never "in the line"). An unqueued stop keeps
    // the arrival rule. The policy rides the session; the phone selects.
    test('a solo walker\'s piece at a queued stop waits for the first '
        'standstill inside the footprint, never the edge', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 30, queueSeconds: 28 * 60),
      ];
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180,
        placement: const PlacementPolicy(ownPlaceM: 60, queuePiece: 'auto'),
      ));
      gps.simulatePosition(base + 25 * degPerMeterLat, 2.35); // the edge, on the move
      expect(audio.isPlaying, isFalse, reason: 'the line is the place, not the edge');
      clock.tick(10);
      gps.simulatePosition(base + 15 * degPerMeterLat, 2.35); // shuffling in
      clock.tick(10);
      service.tick();
      expect(audio.isPlaying, isFalse, reason: 'not still for the settling period');
      clock.tick(TourPlaybackService.kSettleSeconds + 1); // standing in the line
      service.tick();
      expect(audio.isPlaying, isTrue, reason: 'the first standstill inside starts it');
      expect(service.armedOffer, isNull, reason: 'auto: nothing to tap');
    });

    test('a couple\'s piece at a queued stop waits for the TAP: the screen offers, '
        'a standstill alone starts nothing, startArmedPiece plays', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 30, queueSeconds: 28 * 60),
      ];
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180, party: 'couple',
        placement: const PlacementPolicy(ownPlaceM: 60, queuePiece: 'tap'),
      ));
      gps.simulatePosition(base + 10 * degPerMeterLat, 2.35); // in
      clock.tick(TourPlaybackService.kSettleSeconds + 5);
      service.tick();
      expect(audio.isPlaying, isFalse, reason: 'a still couple is a conversation');
      expect(service.armedOffer, 'Stop 1', reason: 'the offer names the stop');
      service.startArmedPiece(); // the tap
      expect(audio.isPlaying, isTrue);
      expect(audio.currentBeatId, 'b1');
      expect(service.armedOffer, isNull);
    });

    test('a stop with no priced line keeps the arrival rule under the same '
        'policy', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 30, queueSeconds: 0),
      ];
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180, party: 'couple',
        placement: const PlacementPolicy(ownPlaceM: 60, queuePiece: 'tap'),
      ));
      gps.simulatePosition(base + 25 * degPerMeterLat, 2.35);
      expect(audio.isPlaying, isTrue, reason: 'no line: the edge starts it (R1)');
      expect(service.armedOffer, isNull);
    });

    // THE DOOR. At a stop whose visit goes INSIDE, the piece ends at the end of its
    // current sentence when the placed OUTSIDE seconds have run (the phone's only
    // threshold under a roof), then the stop's CLOSE plays; inside, the screen
    // carries the transcript and a keep-listening tap that resumes from the cut
    // sentence's START; nothing resumes by itself. A stop that is not a door
    // plays on untouched.
    test('at a door stop the piece ends at its sentence when the outside seconds '
        'have run, the close plays, and keep-listening resumes from the cut '
        'sentence\'s start', () async {
      final player = _DoorPlayer();
      final doorService = TourPlaybackService(
        locationService: gps, audioService: player, now: () => clock.now,
      );
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 40, door: true, outsideSeconds: 120,
            narration: _fourSentences, audioDurationSec: 40,
            closeText: "That's the stop, the close that ends it all here."),
      ];
      await doorService.startTour(stops);
      gps.simulatePosition(base + 10 * degPerMeterLat, 2.35); // arrive: plays
      expect(player.isPlaying, isTrue);
      clock.tick(60);
      doorService.tick();
      expect(player.stops, 0, reason: 'the outside minutes are not up');
      expect(doorService.keepListeningOffer, isNull);
      // Two minutes outside: the door. 13 s into the piece, the sentence ends
      // at 20 s — the cut waits for it, never a word.
      player.pos = const Duration(seconds: 13);
      clock.tick(60);
      doorService.tick();
      expect(player.stops, 0, reason: 'the sentence finishes first');
      doorService.finishSentenceNow(); // the sentence end comes (the timer's door)
      expect(player.stops, 1, reason: 'the piece is cut at the sentence');
      expect(player.said, ["That's the stop, the close that ends it all here."],
          reason: 'the close plays at the door');
      expect(doorService.closesPlayed, ["That's the stop, the close that ends it all here."]);
      // Inside: the transcript and the keep-listening tap; nothing auto.
      expect(doorService.keepListeningOffer, 'Stop 1');
      expect(doorService.keepListeningTranscript, _fourSentences);
      clock.tick(300);
      doorService.tick();
      expect(player.playedFrom, isEmpty, reason: 'nothing resumes by itself');
      doorService.keepListening(); // the tap
      expect(player.playedFrom, [('b1', const Duration(seconds: 10))],
          reason: 'resumes from the START of the cut sentence (the 10 s boundary)');
      expect(doorService.keepListeningOffer, isNull);
      doorService.dispose();
    });

    test('S7.6: the start of the current sentence comes off the SAME boundary '
        'table as its end', () {
      final stop = _makeStop(sortOrder: 1, beatId: 'b', lat: 0, lng: 0,
          narration: _fourSentences, audioDurationSec: 40);
      expect(TourPlaybackService.sentenceStartSeconds(stop, const Duration(seconds: 13)), 10);
      expect(TourPlaybackService.sentenceStartSeconds(stop, const Duration(seconds: 3)), 0);
      expect(TourPlaybackService.sentenceStartSeconds(stop, const Duration(seconds: 35)), 30);
      expect(TourPlaybackService.sentenceStartSeconds(stop, const Duration(seconds: 45)), 30,
          reason: 'past the last boundary: the last sentence');
      final mute = _makeStop(sortOrder: 2, beatId: 'm', lat: 0, lng: 0);
      expect(TourPlaybackService.sentenceStartSeconds(mute, const Duration(seconds: 5)), 0);
    });

    test('a stop that is not a door plays on when its outside seconds have run; '
        'the door fires once', () async {
      final player = _DoorPlayer();
      final plain = TourPlaybackService(
        locationService: gps, audioService: player, now: () => clock.now,
      );
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 40, door: false, outsideSeconds: 30,
            narration: _fourSentences, audioDurationSec: 40,
            closeText: 'A close.'),
      ];
      await plain.startTour(stops);
      gps.simulatePosition(base + 10 * degPerMeterLat, 2.35);
      expect(player.isPlaying, isTrue);
      clock.tick(90);
      plain.tick();
      plain.finishSentenceNow(); // nothing is pending: a no-op
      expect(player.stops, 0);
      expect(player.isPlaying, isTrue, reason: 'no door: the piece plays on');
      expect(plain.keepListeningOffer, isNull);
      plain.dispose();
    });

    // THE LEG PIECE: audio overlaps the walking, so the stop's piece never opens
    // with the walking line the person has already walked. The stop's walking line is its
    // own file, played ON THE LEG: the first stop's at the first fix (the tap —
    // "Settle in, you're starting in…"), every later stop's once the walker has
    // left the previous stop's footprint. Told once; the story at the footprint
    // waits for the leg piece to end.
    test('the first stop\'s leg piece plays at the first fix; a later stop\'s '
        'once the walker leaves the previous footprint; told once; the story '
        'waits for it', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35, audioUrl: url,
            radiusM: 20, legAudioUrl: 'https://cdn.ondoway.com/leg1.mp3'),
        _makeStop(sortOrder: 2, beatId: 'b2', lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20, legAudioUrl: 'https://cdn.ondoway.com/leg2.mp3'),
      ];
      await service.startTour(stops);
      gps.simulatePosition(base - 300 * degPerMeterLat, 2.35); // the first fix, far off
      expect(audio.currentBeatId, 'b1-leg', reason: 'the opening walk is spoken at the start');
      expect(audio.playCount, 1);
      gps.simulatePosition(base - 250 * degPerMeterLat, 2.35);
      expect(audio.playCount, 1, reason: 'told once is told');
      audio.simulateComplete(); // the leg piece ends on the way
      expect(service.currentStopIndex, 0, reason: 'a leg piece never advances the tour');
      gps.simulatePosition(base, 2.35); // arrive: the story plays
      expect(audio.currentBeatId, 'b1');
      audio.simulateComplete();
      expect(service.currentStopIndex, 1);
      gps.simulatePosition(base + 10 * degPerMeterLat, 2.35); // still inside stop 0's footprint
      expect(audio.isPlaying, isFalse, reason: 'the next leg has not begun');
      gps.simulatePosition(base + 60 * degPerMeterLat, 2.35); // out of the footprint: the leg
      expect(audio.currentBeatId, 'b2-leg');
      gps.simulatePosition(base + 395 * degPerMeterLat, 2.35); // arrive while the leg still plays
      expect(audio.currentBeatId, 'b2-leg', reason: 'the story waits for the leg piece');
      audio.simulateComplete();
      service.tick();
      expect(audio.currentBeatId, 'b2', reason: 'then the story starts');
    });

    // Silence over wrongness, and the corrected words on the
    // SCREEN while the audio catches up. A replan rewrites a stale leg line and
    // clears its file; until the voicing pass lands the new one, the leg piece
    // is text with no url. The walk must not lose the direction to the gap: the
    // words ride the screen for the whole leg, and go away on arrival. A leg
    // whose file exists keeps today's behaviour: the file plays, no extra line.
    test("a leg with words and no file shows the words on the screen for the "
        "whole leg, and arrival clears them", () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35, audioUrl: url,
            radiusM: 20),
        _makeStop(sortOrder: 2, beatId: 'b2', lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20,
            legNarration: 'Leaving Stop 1 behind, head for Stop 2, '
                'about a 4-minute walk away.'),
      ];
      await service.startTour(stops);
      gps.simulatePosition(base, 2.35); // arrive at stop 1: its story plays
      audio.simulateComplete();
      expect(service.currentStopIndex, 1);
      expect(service.legTextLine, isNull,
          reason: 'still inside stop 1: the walk has not begun');
      gps.simulatePosition(base + 60 * degPerMeterLat, 2.35); // out: the leg
      expect(audio.isPlaying, isFalse,
          reason: 'no file: nothing plays, and nothing wrong plays');
      expect(service.legTextLine,
          'Leaving Stop 1 behind, head for Stop 2, about a 4-minute walk away.');
      gps.simulatePosition(base + 200 * degPerMeterLat, 2.35);
      expect(service.legTextLine, isNotNull,
          reason: 'the words ride the screen for the whole leg');
      gps.simulatePosition(base + 400 * degPerMeterLat, 2.35); // arrive
      expect(service.legTextLine, isNull, reason: 'arrival clears the line');
    });

    // The background voicing lands the re-voiced
    // file on the server DURING the walk. The page refetches when the service
    // says a text-only leg is under way (audioCatchUpDue) and hands the fresh
    // stops in; [adoptSessionAudio] takes AUDIO ONLY, for the same words, and
    // the leg piece then plays mid-walk — the screen line retires.
    test('caught-up leg audio adopted mid-leg plays, and the screen line '
        'retires', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35, audioUrl: url,
            radiusM: 20),
        _makeStop(sortOrder: 2, beatId: 'b2', stopId: 's2',
            lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20,
            legNarration: 'Leaving Stop 1 behind, head for Stop 2, '
                'about a 4-minute walk away.'),
      ];
      await service.startTour(stops);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete();
      gps.simulatePosition(base + 60 * degPerMeterLat, 2.35); // out: the text-only leg
      expect(service.legTextLine, isNotNull);
      expect(service.audioCatchUpDue, isTrue,
          reason: 'a text-only leg under way is the refetch signal');
      // The server voiced the line since the session was fetched.
      final adopted = service.adoptSessionAudio([
        _makeStop(sortOrder: 2, beatId: 'b2', stopId: 's2',
            lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20,
            legNarration: 'Leaving Stop 1 behind, head for Stop 2, '
                'about a 4-minute walk away.',
            legAudioUrl: 'https://cdn.ondoway.com/leg2-revoiced.mp3'),
      ]);
      expect(adopted, 1);
      gps.simulatePosition(base + 100 * degPerMeterLat, 2.35); // next fix: it plays
      expect(audio.currentBeatId, 's2-leg',
          reason: 'the caught-up file plays on the walk it was voiced for');
      expect(service.legTextLine, isNull,
          reason: 'the file says the words; the screen line is done');
      expect(service.audioCatchUpDue, isFalse);
    });

    // UNDO: remove the words-match guard (update.legNarration !=
    // stop.legNarration) from adoptSessionAudio -> the foreign file is adopted
    // -> RED. (A return-0 mutation cannot discriminate this negative test.)
    test('adoption refuses a file whose words are not the held words', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35, audioUrl: url,
            radiusM: 20),
        _makeStop(sortOrder: 2, beatId: 'b2', stopId: 's2',
            lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20, legNarration: 'Head for Stop 2.'),
      ];
      await service.startTour(stops);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete();
      gps.simulatePosition(base + 60 * degPerMeterLat, 2.35);
      final adopted = service.adoptSessionAudio([
        _makeStop(sortOrder: 2, beatId: 'b2', stopId: 's2',
            lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20,
            legNarration: 'A DIFFERENT line entirely.',
            legAudioUrl: 'https://cdn.ondoway.com/other.mp3'),
      ]);
      expect(adopted, 0,
          reason: 'audio for other words is another replan; it arrives '
              'through holdSession, never this door');
      expect(service.legTextLine, 'Head for Stop 2.');
    });

    test('a leg whose file exists plays it and shows no extra line', () async {
      final stops = [
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35, audioUrl: url,
            radiusM: 20),
        _makeStop(sortOrder: 2, beatId: 'b2', lat: base + 400 * degPerMeterLat, lng: 2.35,
            audioUrl: url, radiusM: 20,
            legAudioUrl: 'https://cdn.ondoway.com/leg2.mp3',
            legNarration: 'From Stop 1, make your way on to Stop 2.'),
      ];
      await service.startTour(stops);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete();
      gps.simulatePosition(base + 60 * degPerMeterLat, 2.35);
      expect(audio.currentBeatId, 'b2-leg');
      expect(service.legTextLine, isNull,
          reason: 'the file speaks for itself; the screen line is the fallback');
    });

    // THE CHAPTERS (the wire's "segments"): a
    // marquee's story is cut at its reviewed anchors; each chapter is its own
    // file at its own place inside the footprint. Outdoors it plays by itself
    // at the first standstill inside the anchor's radius; under a roof it is
    // offered on the screen and the tap plays it (GPS is useless inside); the
    // couple and the family tap for every chapter; told once is told.
    test('an outdoor chapter plays at the standstill inside its anchor; the '
        'indoor one is offered and tapped; told once', () async {
      const westLat = base + 50 * degPerMeterLat; // inside the 100 m footprint
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
          StopSegment(label: 'Inside', lat: base, lng: 2.35, radiusM: 60, indoor: true,
              narration: 'The nave.', audioUrl: 'https://cdn/inside.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35); // arrive: the story plays first
      expect(audio.currentBeatId, 'nd');
      audio.simulateComplete();
      expect(service.state, TourState.completed, reason: 'the one stop is told');
      // Walk to the west front: not yet standing — the chapter waits, offered.
      gps.simulatePosition(westLat, 2.35);
      expect(audio.isPlaying, isFalse);
      expect(service.segmentOffer, 'The west front');
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.currentBeatId, 'nd-seg-0', reason: 'outdoor: auto at the standstill');
      audio.simulateComplete();
      // Told once: standing here again plays nothing; the indoor chapter is only offered.
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.isPlaying, isFalse);
      expect(service.segmentOffer, 'Inside', reason: 'under a roof: the tap, never auto');
      service.startSegment();
      expect(audio.currentBeatId, 'nd-seg-1');
      audio.simulateComplete();
      // Told once is told, and a tap replays. This line used to assert NO offer
      // once every chapter was told, which left the thumb nothing to replay.
      // Standing at a told chapter's own anchor, the offer RETURNS
      // as a replay; only the AUTO-play is once-only.
      expect(service.segmentOffer, 'The west front',
          reason: 'standing at a told chapter: the tap can replay it');
    });

    // The sentence cap and the resume rule ride the WIRE's policy
    // block — the server decided them from the party and the hardness. The wire
    // outranks the phone's party stand-in: a SOLO session whose policy says
    // cap 5 and resume-by-tap behaves as the policy says, not as 'solo' would.
    test('the wire policy outranks the party for the cap and the resume rule',
        () async {
      final stop = _makeStop(
        sortOrder: 1, beatId: 'p1', lat: base, lng: 2.35, audioUrl: url,
        radiusM: 60, narration: _fourSentences, audioDurationSec: 40,
      );
      await service.startTour([stop]);
      service.holdSession(SessionPlan(
        tripId: 't', planVersion: 1, stops: [stop], retimeToleranceSeconds: 180,
        party: 'solo',
        placement: const PlacementPolicy(
            ownPlaceM: 60, sentenceCapS: 5, interruptionResume: 'tap'),
      ));
      expect(service.sentenceEndCapSeconds, 5.0,
          reason: 'the cap is the wire policy, not the solo default of 8');
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35); // arrive: the piece plays
      expect(audio.currentBeatId, 'p1');
      audio.simulateInterruption(AudioInterruptionKind.pauseBegin);
      audio.simulateInterruption(AudioInterruptionKind.ended);
      expect(service.resumeOffer, 'Stop 1',
          reason: 'the policy says resume by tap — even for a solo party');
      expect(audio.isPlaying, isFalse);
      service.resumeInterrupted();
      expect(audio.currentBeatId, 'p1');
    });

    // At the door the screen carries the transcript and WHEN TO LEAVE the
    // interior to keep the plan: the cut
    // stop's own planned departure (its arrival clock plus its dwell), on the
    // screen beside the keep-listening offer, never spoken.
    test('the door shows a leave-by clock beside the transcript (R3, Marcus)', () async {
      final stop = _makeStop(
        sortOrder: 1, beatId: 'd1', lat: base, lng: 2.35, audioUrl: url,
        radiusM: 30, door: true, outsideSeconds: 60,
        narration: _fourSentences, audioDurationSec: 40,
        closeText: 'That is the store.',
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 100 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35); // arrive: the piece plays
      expect(audio.currentBeatId, 'd1');
      clock.tick(61); // the placed outside minute has run
      service.tick(); // the door fires: sentence end pends
      service.finishSentenceNow();
      audio.simulateComplete(); // the close ends: the offer stands
      expect(service.keepListeningOffer, 'Stop 1');
      // startTime '09:01' + durationMin 5 -> leave by 09:06, on the screen.
      expect(service.doorLeaveByHhmm, '09:06',
          reason: 'the door screen says when to leave to keep the plan');
      service.keepListening();
      expect(service.doorLeaveByHhmm, isNull,
          reason: 'the tap spends the offer; the clock goes with it');
    });

    // Leaving and re-entering the footprint never replays; A TAP DOES. A
    // never-heard chapter stays on the screen, and a HEARD one must not vanish
    // from the thumb. The offer returns for a told chapter the walker is STANDING AT;
    // the tap replays it; the auto-play stays once-only.
    test('a told chapter can be replayed by tap where you stand — never by itself',
        () async {
      const westLat = base + 50 * degPerMeterLat;
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete(); // the story
      gps.simulatePosition(westLat, 2.35);
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.currentBeatId, 'nd-seg-0'); // told, once, by standing
      audio.simulateComplete(); // the chapter ends (and the goodbye, none here)
      // Still standing at the anchor: the offer RETURNS, and only the tap plays.
      final playsBefore =
          audio.playedIds.where((k) => k == 'nd-seg-0').length;
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.playedIds.where((k) => k == 'nd-seg-0').length, playsBefore,
          reason: 'auto-play is once-only — R1(c) first half');
      expect(service.segmentOffer, 'The west front',
          reason: 'the tap replays — R1(c) second half');
      service.startSegment();
      expect(audio.playedIds.where((k) => k == 'nd-seg-0').length, playsBefore + 1);
      // Away from the anchor (still at the stop): a told chapter is NOT offered —
      // the screen offers what the thumb can use where the feet are.
      audio.simulateComplete();
      gps.simulatePosition(base - 80 * degPerMeterLat, 2.35);
      expect(service.segmentOffer, isNull,
          reason: 'off its anchor a told chapter rests; only an unheard one follows you');
    });

    // The stop's GOODBYE used to end the story piece, so a chaptered marquee
    // said farewell on arrival and then spoke again at the chapter. A listener
    // who hears the farewell puts the phone away and never learns a chapter is
    // still owed. The server no longer puts the close in a chaptered story
    // (render_md); the phone plays it when the LAST chapter is told.
    test('the goodbye is said after the LAST chapter, never before them', () async {
      const westLat = base + 50 * degPerMeterLat;
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        closeText: "That's Notre-Dame, the symbolic heart of France.",
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35);
      expect(audio.currentBeatId, 'nd');
      audio.simulateComplete(); // the story ends on its last STORY sentence
      expect(service.closesPlayed, isEmpty,
          reason: 'the farewell must not be said while a chapter is still owed');
      gps.simulatePosition(westLat, 2.35);
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.currentBeatId, 'nd-seg-0');
      audio.simulateComplete(); // the last chapter is told: NOW the goodbye
      expect(service.closesPlayed,
          ["That's Notre-Dame, the symbolic heart of France."]);
    });

    test('an unreached chapter leaves the goodbye unsaid, never said early', () async {
      const westLat = base + 50 * degPerMeterLat;
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        closeText: "That's Notre-Dame.",
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete();
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      // She never walked to the front. The goodbye is the last thing said
      // at the last place, or it is not said.
      expect(service.closesPlayed, isEmpty);
    });

    // A visitor who will not stop moving in the open never stands still at the
    // west front, so a chapter that only starts itself there is lost silently.
    // Silence and 'there is nothing here' sound identical. Told-once
    // is about a piece ALREADY HEARD; a piece never heard is not that.
    // So: the chapter still STARTS ITSELF only where you stand at it, but the
    // OFFER stays on the screen anywhere at the stop, and the tap plays it.
    test('an untold outdoor chapter is offered anywhere at the stop, and the tap '
        'plays it — but it still starts itself only where you stand at it', () async {
      const westLat = base + 80 * degPerMeterLat; // inside the footprint, off the anchor
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35); // at the stop, 80 m from the anchor
      audio.simulateComplete(); // the story is told
      // She crosses the square without stopping at the front: no auto-play…
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.isPlaying, isFalse,
          reason: 'it must never start itself where she is not standing at it');
      // …but it is THERE, on the screen, and her tap plays it.
      expect(service.segmentOffer, 'The west front',
          reason: 'a chapter she has never heard must not be invisible');
      service.startSegment();
      expect(audio.currentBeatId, 'nd-seg-0');
    });

    test('the chapter you are standing in is the one offered, not merely the first',
        () async {
      const westLat = base + 80 * degPerMeterLat;
      const eastLat = base - 80 * degPerMeterLat;
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
          StopSegment(label: 'The east end', lat: eastLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The buttresses.', audioUrl: 'https://cdn/east.mp3'),
        ],
      );
      await service.startTour([stop]);
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete();
      gps.simulatePosition(eastLat, 2.35); // standing at the EAST end
      expect(service.segmentOffer, 'The east end',
          reason: 'standing at one chapter must not offer the other');
    });

    test('a couple taps for every chapter, outdoors too (R4)', () async {
      const westLat = base + 50 * degPerMeterLat;
      final stop = _makeStop(
        sortOrder: 1, beatId: 'nd', lat: base, lng: 2.35, audioUrl: url, radiusM: 100,
        segments: const [
          StopSegment(label: 'The west front', lat: westLat, lng: 2.35, radiusM: 30,
              indoor: false, narration: 'The facade.', audioUrl: 'https://cdn/west.mp3'),
        ],
      );
      await service.startTour([stop]);
      service.holdSession(SessionPlan(
        tripId: 't', planVersion: 1, stops: [stop], retimeToleranceSeconds: 180,
        party: 'couple', placement: const PlacementPolicy(ownPlaceM: 60, queuePiece: 'tap'),
      ));
      gps.simulatePosition(base - 200 * degPerMeterLat, 2.35);
      gps.simulatePosition(base, 2.35);
      audio.simulateComplete(); // the story
      gps.simulatePosition(westLat, 2.35);
      clock.tick(TourPlaybackService.kSettleSeconds + 1);
      service.tick();
      expect(audio.isPlaying, isFalse, reason: 'no chapter starts itself for a couple');
      expect(service.segmentOffer, 'The west front');
      service.startSegment();
      expect(audio.currentBeatId, 'nd-seg-0');
    });

    test('the approach nudge fires at the NEXT stop\'s footprint edge, not at '
        '10 m from its pin', () async {
      await service.startTour([
        _makeStop(sortOrder: 1, beatId: 'b1', lat: base, lng: 2.35,
            audioUrl: url, radiusM: 10),
        _makeStop(sortOrder: 2, beatId: 'b2', lat: base + 300 * degPerMeterLat,
            lng: 2.35, audioUrl: url, radiusM: 60),
      ]);
      audio.play('b1', url); // the first piece is playing
      gps.simulatePosition(base + 230 * degPerMeterLat, 2.35); // 70 m short of stop 2
      expect(service.state, TourState.active);
      gps.simulatePosition(base + 250 * degPerMeterLat, 2.35); // 50 m: inside 60
      expect(service.state, TourState.approaching);
      expect(service.pendingStopIndex, 1);
    });
  });
}
