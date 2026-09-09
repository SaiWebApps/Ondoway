// Phase 5 S5.9 — the phone SELECTS from the contingency set; it never decides.
//
// Design §4.6: "The phone selects from that set" — it holds none of the
// server's planning machinery. The exhaustive list of what the phone may do
// (04-implementation-plan.md, PHASE 5, "THE STRUCTURAL RULE"): (1) arithmetic
// re-timing over numbers the server gave it; (2) MATCH its measured divergence
// against the triggers and select the entry — an equality/band lookup, taking
// the FIRST in server order when two match, never a tie-break of its own;
// (3) ask the ONE question the selected entry carries and apply the answer or
// the stated default; (4) REPORT a clock divergence, never correct it; (5) when
// nothing matches, carry on and re-time. Anything else is a defect.
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/providers.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_location_service.dart';

/// An audio double that records prefetch calls (the provider's default caches
/// nothing; the session must go through THIS door and no other).
class _RecordingAudio extends AudioProvider {
  final List<List<BeatAudioInfo>> prefetched = [];
  @override
  String? get currentBeatId => null;
  @override
  bool get isPlaying => false;
  @override
  bool get isDeeperDive => false;
  @override
  void play(String beatId, String audioUrl,
      {bool isDeeperDive = false, String? title}) {}
  @override
  void stop() {}
  @override
  Future<int> prefetchAudio(List<BeatAudioInfo> beats) async {
    prefetched.add(beats);
    return beats.length;
  }
}

ItineraryStop _stop(int n, {String? audioUrl}) => ItineraryStop(
  sortOrder: n,
  stopId: 'item-$n',
  poiId: 'poi-$n',
  poiName: 'Stop $n',
  lat: 48.85 + n * 0.001,
  lng: 2.35,
  beatId: 'beat-$n',
  lensName: 'history',
  lensDisplay: 'History',
  durationMin: 10,
  importanceTier: 3,
  startTime: '10:0$n',
  audioUrl: audioUrl,
);

SessionContingency _entry(
  String id,
  Map<String, dynamic> trigger,
  List<String> stopIds, {
  String screen = 'Next: Stop 2 · your finish about 12:00',
  String? question,
  String? defaultArm,
  List<String> alternate = const [],
}) => SessionContingency(
  contingencyId: id,
  trigger: trigger,
  planVersion: 1,
  stopIds: stopIds,
  screenText: screen,
  question: question,
  defaultArm: defaultArm,
  alternateStopIds: alternate,
);

void main() {
  final stops = [_stop(1), _stop(2), _stop(3), _stop(4)];

  SessionPlan plan(List<SessionContingency> entries) => SessionPlan(
    tripId: 'trip-1',
    planVersion: 1,
    stops: stops,
    retimeToleranceSeconds: 180,
    contingencies: entries,
  );

  group('the phone SELECTS from the held set (design §4.6)', () {
    test('a band lookup: the first entry in SERVER order whose band holds the '
        'measured minutes is selected — never a tie-break of its own', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      service.holdSession(
        plan([
          _entry(
            'a',
            {
              'kind': 'running_late',
              'stop_id': 'poi-1',
              'band_minutes': [10, 20],
            },
            ['poi-2', 'poi-3', 'poi-4'],
          ),
          // A second entry whose band ALSO holds 15 minutes: server order wins.
          _entry(
            'b',
            {
              'kind': 'running_late',
              'stop_id': 'poi-1',
              'band_minutes': [10, 20],
            },
            ['poi-2', 'poi-4'],
          ),
          _entry(
            'c',
            {
              'kind': 'running_late',
              'stop_id': 'poi-1',
              'band_minutes': [20, 30],
            },
            ['poi-4'],
          ),
        ]),
      );
      expect(
        service
            .matchContingency(const Divergence(minutesLate: 15))
            ?.contingencyId,
        'a',
      );
      expect(
        service
            .matchContingency(const Divergence(minutesLate: 25))
            ?.contingencyId,
        'c',
      );
      // Under 10 minutes nothing opens the set (W5.2 R1.3): carry on and re-time.
      expect(
        service.matchContingency(const Divergence(minutesLate: 7)),
        isNull,
      );
      // A stop skipped and a wrap-up match by id equality, nothing cleverer.
      service.holdSession(
        plan([
          _entry(
            's',
            {'kind': 'stop_skipped', 'stop_id': 'poi-2'},
            ['poi-3', 'poi-4'],
          ),
          _entry('w', {'kind': 'wrap_up_from', 'stop_id': 'poi-3'}, []),
        ]),
      );
      expect(
        service
            .matchContingency(const Divergence(skippedStopId: 'poi-2'))
            ?.contingencyId,
        's',
      );
      expect(
        service
            .matchContingency(const Divergence(wrapUpFromStopId: 'poi-3'))
            ?.contingencyId,
        'w',
      );
      expect(
        service.matchContingency(const Divergence(skippedStopId: 'poi-9')),
        isNull,
      );
    });

    test('exactly ONE method changes the current plan, and its only decision '
        'input is a server contingency id', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      await service.startTour(stops);
      service.holdSession(
        plan([
          _entry(
            'a',
            {'kind': 'stop_skipped', 'stop_id': 'poi-2'},
            ['poi-4', 'poi-3'],
            screen: 'Next: Stop 4 · your finish about 12:10',
          ),
        ]),
      );
      expect(
        service.applyContingency('nope'),
        isFalse,
        reason: 'an id the phone does not hold',
      );
      expect(service.nextStop?.poiId, 'poi-2', reason: 'the plan is unchanged');
      expect(service.applyContingency('a'), isTrue);
      // The remaining stops are the entry's ordered subset — the current stop stays.
      expect(service.currentStop?.poiId, 'poi-1');
      expect(service.nextStop?.poiId, 'poi-4');
      expect(service.screenText, 'Next: Stop 4 · your finish about 12:10');
      expect(
        service.pendingQuestion,
        isNull,
        reason: 'a fabric entry asks nothing (§4.2)',
      );
    });

    test('the ONE question: the entry\'s default is in force until answered, '
        'and the answer applies the arm the person chose (§4.2)', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      await service.startTour(stops);
      service.holdSession(
        plan([
          _entry(
            'q',
            {
              'kind': 'running_late',
              'stop_id': 'poi-1',
              'band_minutes': [20, 30],
            },
            ['poi-2', 'poi-3', 'poi-4'],
            screen:
                'Sit 7 minutes and be at your finish by 15:47, or keep your full rest '
                'and be at your finish about 15:50?',
            question:
                'Sit 7 minutes and be at your finish by 15:47, or keep your full rest '
                'and be at your finish about 15:50?',
            defaultArm: 'shorten',
            alternate: ['poi-3', 'poi-4'],
          ),
        ]),
      );
      expect(service.applyContingency('q'), isTrue);
      expect(service.pendingQuestion, contains('or keep your full rest'));
      // Safe default (a wall day): the shortened arm is in force until answered.
      expect(service.nextStop?.poiId, 'poi-3');
      service.answerQuestion('keep');
      expect(service.pendingQuestion, isNull);
      expect(
        service.nextStop?.poiId,
        'poi-2',
        reason: 'the kept arm restores the entry\'s stops',
      );
    });

    test('a band is the answer FROM a stop: with the position known, the entry '
        'for THAT stop is selected even when an earlier stop\'s band also holds '
        '(W5.2 R1.3; found at W5.12)', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      await service.startTour(stops);
      service.holdSession(
        plan([
          _entry(
            'late-from-1',
            {'kind': 'running_late', 'stop_id': 'poi-1', 'band_minutes': [10, 20]},
            ['poi-3', 'poi-4'],
          ),
          _entry(
            'late-from-2',
            {'kind': 'running_late', 'stop_id': 'poi-2', 'band_minutes': [10, 20]},
            ['poi-4'],
          ),
        ]),
      );
      final fromTwo = service.matchContingency(
        const Divergence(minutesLate: 12, atStopId: 'poi-2'),
      );
      expect(fromTwo?.contingencyId, 'late-from-2');
      // Position unknown: the band alone decides — the first in server order.
      final unknown = service.matchContingency(const Divergence(minutesLate: 12));
      expect(unknown?.contingencyId, 'late-from-1');
    });

    test('a promise-at-risk entry keeps the day through the stop BEFORE the '
        'promise — the server answered from there (R1.5) — and its stops '
        'start after it', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      await service.startTour(stops);
      service.holdSession(
        plan([
          _entry(
            'risk',
            {'kind': 'promise_at_risk', 'stop_id': 'poi-3'},
            ['poi-3', 'poi-4'],
            screen: 'Keep Stop 3 and be at your finish about 15:50, or go '
                'straight on and be there by 15:40?',
            question: 'Keep Stop 3 and be at your finish about 15:50, or go '
                'straight on and be there by 15:40?',
            defaultArm: 'keep',
            alternate: ['poi-4'],
          ),
        ]),
      );
      service.skipToStop(1); // standing at Stop 2, the stop before the promise
      expect(service.applyContingency('risk'), isTrue);
      expect(service.currentStop?.poiId, 'poi-2', reason: 'the stop before stays');
      expect(service.nextStop?.poiId, 'poi-3');
      service.answerQuestion('shorten');
      expect(service.currentStop?.poiId, 'poi-2');
      expect(service.nextStop?.poiId, 'poi-4');
    });

    test('a door reported SHUT selects its entry, and the door itself leaves '
        'the day — the walker is standing at it (Docs/adr/0006 rule 5)', () async {
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: _RecordingAudio(),
      );
      await service.startTour(stops);
      service.holdSession(
        plan([
          _entry(
            'door',
            {'kind': 'door_closed', 'stop_id': 'poi-2'},
            ['poi-3', 'poi-4'],
            screen: 'Keep the day going at Stop 4 and be at your finish about '
                '15:50, or carry on without it and be at your finish by 15:40?',
            question: 'Keep the day going at Stop 4 and be at your finish about '
                '15:50, or carry on without it and be at your finish by 15:40?',
            defaultArm: 'keep',
            alternate: ['poi-3'],
          ),
        ]),
      );
      // Nothing opens this entry until the walker actually says the door is shut.
      expect(service.matchContingency(service.measure()), isNull);
      service.reportDoorClosed('poi-2');
      expect(service.matchContingency(service.measure())?.contingencyId, 'door');

      service.skipToStop(1); // standing at Stop 2 — the shut door
      expect(service.applyContingency('door'), isTrue);
      // The KEEP arm is in force by default and it is the entry's own stops —
      // an empty list here would delete the rest of the walk.
      expect(
        service.plannedStops.map((s) => s.poiId),
        isNot(contains('poi-2')),
        reason: 'the door reported shut never stays on the day',
      );
      expect(service.currentStop?.poiId, 'poi-3');
      expect(service.plannedStops.length, greaterThan(1),
          reason: 'the keep arm must not truncate the walk');
    });

    test('a standby the session HOLDS beside the day resolves and is seated — '
        'the phone drops any id it does not hold (M6b)', () async {
      final audio = _RecordingAudio();
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: audio,
      );
      await service.startTour(stops);
      final standby = _stop(9, audioUrl: 'https://cdn/9.mp3');
      final question = 'Keep the day going at Stop 9 and be at your finish about '
          '15:50, or carry on without it and be at your finish by 15:40?';
      service.holdSession(
        SessionPlan(
          tripId: 'trip-1',
          planVersion: 1,
          stops: stops,
          standbys: [standby],
          retimeToleranceSeconds: 180,
          contingencies: [
            _entry(
              'door',
              {'kind': 'door_closed', 'stop_id': 'poi-2'},
              ['poi-9', 'poi-3', 'poi-4'],
              screen: question,
              question: question,
              defaultArm: 'keep',
              alternate: ['poi-3', 'poi-4'],
            ),
          ],
        ),
      );
      service.reportDoorClosed('poi-2');
      service.skipToStop(1);
      expect(service.applyContingency('door'), isTrue);
      final walked = service.plannedStops.map((s) => s.poiId).toList();
      expect(walked, contains('poi-9'),
          reason: 'the held standby is seated, not dropped');
      expect(walked, isNot(contains('poi-2')), reason: 'the shut door leaves');
      expect(service.currentStop?.poiId, 'poi-9');
      // Its audio is prefetched with the day's, through the one cache door.
      expect(await service.prefetchSessionAudio(), 5);
      expect(audio.prefetched.single.map((b) => b.beatId), contains('item-9'));
    });

    test('alternates prefetch through the EXISTING audio cache door, keyed as '
        'playback keys them (design §4.7)', () async {
      final audio = _RecordingAudio();
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: audio,
      );
      service.holdSession(
        SessionPlan(
          tripId: 'trip-1',
          planVersion: 1,
          stops: [
            _stop(1, audioUrl: 'https://cdn/1.mp3'),
            _stop(2, audioUrl: 'https://cdn/2.mp3'),
          ],
          retimeToleranceSeconds: 180,
        ),
      );
      expect(await service.prefetchSessionAudio(), 2);
      expect(audio.prefetched.single.map((b) => b.beatId), [
        'item-1',
        'item-2',
      ]);
    });
  });
}
