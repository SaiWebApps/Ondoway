// THE THREAD: narration that survives the session's changes to the day.
//
// One sentence under fifteen words, authored
// at compose time for every adjacency the contingency set can make, riding the
// arriving stop keyed by the predecessor's name. WHERE: a standing seam — never
// mid-leg; on screen for the whole leg so the one lost on the move is waiting
// at the standstill. None rather than glue where the pair shares no
// theme. The pair the plan already had keeps its thread inside the arriving
// stop's own narration — the phone speaks a thread only for a pair the session
// MADE.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

class _Player extends MockAudioService {
  final List<String> said = [];
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

const _thread = 'The same tribunal filled this courtyard.';

ItineraryStop _stop(int n, {required double lat, Map<String, String>? threads}) =>
    ItineraryStop(
      sortOrder: n,
      stopId: 'item-$n',
      poiId: 'poi-$n',
      poiName: 'Stop $n',
      lat: lat,
      lng: 2.35,
      beatId: 'beat-$n',
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 5,
      importanceTier: 3,
      startTime: '',
      narration: "The story of Stop $n. And that's Stop $n.",
      audioUrl: 'https://cdn.example.com/$n.mp3',
      audioDurationSec: 20,
      dwellSeconds: 300,
      closeText: "And that's Stop $n.",
      threadLines: threads,
      // The footprint these fixtures assumed (the phone's old 40 m
      // circle) is now STATED on each stop, as the wire carries it.
      trigger: const StopTrigger(radiusM: 40),
    );

SessionContingency _skipOf(String skippedPoi, List<String> rest) =>
    SessionContingency(
      contingencyId: 'v1-skip-$skippedPoi',
      trigger: {'kind': 'stop_skipped', 'stop_id': skippedPoi},
      planVersion: 1,
      stopIds: rest,
      screenText: 'On to Stop 2',
    );

void main() {
  const base = 48.85;
  final stops = [
    _stop(0, lat: base),
    _stop(1, lat: base + 300 * _degPerMeterLat),
    _stop(2, lat: base + 600 * _degPerMeterLat, threads: {'Stop 0': _thread}),
  ];

  Future<({TourPlaybackService service, _Player player, MockLocationService gps, _Clock clock})>
      atStopZero({List<ItineraryStop>? day}) async {
    final plan = day ?? stops;
    final clock = _Clock(DateTime(2026, 8, 23, 15, 0));
    final gps = MockLocationService();
    final player = _Player();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: player,
      now: () => clock.now,
    );
    await service.startTour(plan);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: plan,
      retimeToleranceSeconds: 180,
      contingencies: [_skipOf('poi-1', const ['poi-2'])],
      dayStartHhmm: '15:00',
    ));
    gps.simulatePosition(base, 2.35); // arrive at Stop 0; its piece starts
    return (service: service, player: player, gps: gps, clock: clock);
  }

  group('S6.5 the thread of the pair the session made (W6.2 R5)', () {
    test('a skip makes 0->2 consecutive: the thread goes on screen and is said '
        'at the standing seam, once', () async {
      final t = await atStopZero();
      t.player.simulateComplete(); // the piece ended: a seam
      t.service.applyContingency('v1-skip-poi-1');
      expect(t.service.threadLine, _thread, reason: 'on screen at once');
      // Standing at the stop, settled: the next natural moment says it.
      t.clock.tick(31);
      t.service.tick();
      expect(t.player.said, [_thread]);
      // Said once; the screen still carries it for the leg.
      t.clock.tick(30);
      t.service.tick();
      expect(t.player.said, [_thread]);
      expect(t.service.threadLine, _thread);
    });

    test('never mid-leg: applied on the move, the line waits on the screen '
        'and is not spoken while walking', () async {
      final t = await atStopZero();
      t.player.simulateComplete();
      // Walk off the stop onto the leg before the skip lands.
      t.gps.simulatePosition(base + 120 * _degPerMeterLat, 2.35);
      t.clock.tick(40);
      t.service.applyContingency('v1-skip-poi-1');
      expect(t.service.threadLine, _thread, reason: 'waiting at the standstill');
      t.clock.tick(60);
      t.service.tick();
      expect(t.player.said, isEmpty, reason: 'on a leg nothing is said');
    });

    test('the leg ends when the next story begins: arrival at the new stop '
        'takes the thread off the screen', () async {
      final t = await atStopZero();
      t.player.simulateComplete();
      t.service.applyContingency('v1-skip-poi-1');
      expect(t.service.threadLine, _thread);
      // Arrive at Stop 2: its piece starts; the thread has done its work.
      t.gps.simulatePosition(base + 600 * _degPerMeterLat, 2.35);
      expect(t.service.threadLine, isNull);
    });

    test('none rather than glue: a pair with no authored line stays silent, '
        'and the planned pair threads nothing (its line is in the narration)',
        () async {
      // Stop 2 without threads: the made pair 0->2 has no authored line.
      final bare = [
        _stop(0, lat: base),
        _stop(1, lat: base + 300 * _degPerMeterLat),
        _stop(2, lat: base + 600 * _degPerMeterLat),
      ];
      final t = await atStopZero(day: bare);
      t.player.simulateComplete();
      t.service.applyContingency('v1-skip-poi-1');
      expect(t.service.threadLine, isNull);
      t.clock.tick(31);
      t.service.tick();
      expect(t.player.said, isEmpty, reason: 'silence beats glue');
    });
  });
}
