// Announcement etiquette, the phone's half. Speech queues to a natural
// moment — never into a conversation, never onto a walking leg. Everything
// spoken also appears on screen. One sentence, maximum; mute beats graceful.
// Fabric events are absorbed SILENTLY. The observable proxy for a natural
// moment — a seam in the audio at a stop, still ~20–30 s, not paused; never on
// a leg, never mid-piece, never on stillness alone; a stale line expires
// undelivered, only the question survives. Repeated pauses → screen-only per
// PARTY, the narration never muted, the question still said once, undone by
// one touch. "Audio overlaps the walking" holds for STORIES and NOT for
// announcements; this is that separate rule: an announcement never lands on a
// leg.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

class _Voice extends MockAudioService {
  final List<String> said = [];
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

ItineraryStop _stop(int n, {required double lat}) => ItineraryStop(
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
  audioUrl: 'https://cdn.example.com/$n.mp3',
  audioDurationSec: 100,
  dwellSeconds: 300,
  // The footprint these fixtures assumed (the phone's old 40 m circle) is
  // now STATED on each stop, as the wire carries it.
  trigger: const StopTrigger(radiusM: 40),
);

const _question =
    'Keep your bench and be at your finish about 15:20, or sit 4 minutes and be there by 15:12?';

SessionContingency _questionEntry() => const SessionContingency(
  contingencyId: 'v1-q',
  trigger: {'kind': 'promise_at_risk', 'stop_id': 'poi-1'},
  planVersion: 1,
  stopIds: ['poi-1', 'poi-2'],
  screenText: _question,
  question: _question,
  defaultArm: 'keep',
  alternateStopIds: ['poi-2'],
);

SessionContingency _fabricEntry() => const SessionContingency(
  contingencyId: 'v1-f',
  trigger: {'kind': 'running_late', 'stop_id': 'poi-0', 'band_minutes': [10, 20]},
  planVersion: 1,
  stopIds: ['poi-2'],
  screenText: 'Next: Stop 2 · your finish about 15:30',
);

void main() {
  const base = 48.85;
  final stops = [
    _stop(0, lat: base),
    _stop(1, lat: base + 300 * _degPerMeterLat),
    _stop(2, lat: base + 600 * _degPerMeterLat),
  ];
  SessionPlan plan({String? party}) => SessionPlan(
    tripId: 'trip-1',
    planVersion: 1,
    stops: stops,
    retimeToleranceSeconds: 180,
    contingencies: [_questionEntry(), _fabricEntry()],
    dayStartHhmm: '09:00',
    party: party,
    // The day's placement policy rides the session — the
    // family piece starts at the first standstill inside the footprint.
    placement: PlacementPolicy(
      startAt: party == 'family' ? 'standstill' : 'arrival',
      ownPlaceM: 60,
    ),
  );

  // A person walking north from 600 m south of Stop 0, 45 m every 30 s.
  Future<({TourPlaybackService service, _Voice voice, MockLocationService gps, _Clock clock})>
      walking({String? party}) async {
    final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
    final gps = MockLocationService();
    final voice = _Voice();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: voice,
      now: () => clock.now,
    );
    await service.startTour(stops);
    service.holdSession(plan(party: party));
    gps.simulatePosition(base - 600 * _degPerMeterLat, 2.35);
    return (service: service, voice: voice, gps: gps, clock: clock);
  }

  group('§4.4.1 speech queues to a natural moment (R3)', () {
    test('the question is on the screen at once and NEVER said on a walking '
        'leg — audit F :282\'s C7 ("audio overlaps the walking") holds for '
        'stories, not for announcements; it is said once at the seam after the '
        'settling period', () async {
      // A FAMILY day: the stop's piece ARMS at the footprint
      // edge and starts at the first standstill, so the seam at Stop 0 is a
      // standstill with the first piece not yet begun — the question goes
      // first, then the story. (Earlier the fixture stood 25 m out of a
      // 10 m auto-play circle to get the same seam; that circle is gone.)
      final w = await walking(party: 'family');
      var lat = base - 600 * _degPerMeterLat;
      expect(w.service.applyContingency('v1-q'), isTrue);
      expect(w.service.pendingQuestion, _question); // on screen at once
      expect(w.service.screenText, _question);
      // Five minutes of walking, ticking every 30 s: nothing said on the leg.
      for (var i = 0; i < 10; i++) {
        w.clock.tick(30);
        lat += 45 * _degPerMeterLat;
        w.gps.simulatePosition(lat, 2.35);
        w.service.tick();
      }
      expect(w.voice.said, isEmpty, reason: 'never onto a walking leg');
      // A red light on the leg: still for 45 s, 150 m from any stop.
      // Stillness alone is never a moment — never on a walking leg.
      w.clock.tick(45);
      w.service.tick();
      expect(w.voice.said, isEmpty, reason: 'still on a leg is not a stop');
      // Arrive inside Stop 0's footprint (25 m in, a 40 m place): the family
      // piece is armed, not begun. Still 20 s: too soon.
      w.gps.simulatePosition(base - 25 * _degPerMeterLat, 2.35);
      w.clock.tick(20);
      w.service.tick();
      expect(w.voice.said, isEmpty, reason: 'the settling period (30 s) first');
      w.clock.tick(15);
      w.service.tick();
      expect(w.voice.said, [_question]);
      // Said ONCE — later moments do not repeat it.
      w.clock.tick(60);
      w.service.tick();
      expect(w.voice.said, hasLength(1));
      expect(w.service.pendingQuestion, _question, reason: 'still on screen until answered');
    });

    test('never mid-piece: a piece that ended on its own is the seam', () async {
      final w = await walking();
      // At Stop 0: the geofence plays its piece.
      w.gps.simulatePosition(base, 2.35);
      expect(w.voice.isPlaying, isTrue);
      w.service.applyContingency('v1-q');
      w.clock.tick(60);
      w.service.tick();
      expect(w.voice.said, isEmpty, reason: 'never mid-piece');
      w.voice.simulateComplete(); // ended on its own: a seam
      w.clock.tick(30);
      w.service.tick();
      expect(w.voice.said, [_question]);
      // A replay is mid-piece again: the switch line (family, two pauses cut
      // into the replay) waits for its end.
      w.service.holdSession(plan(party: 'family'));
      w.voice.play('item-0', 'https://cdn.example.com/0.mp3'); // a visitor replays
      w.service.pauseTour();
      w.service.resumeTour(); // the piece comes back untouched
      w.service.pauseTour();
      w.service.resumeTour();
      expect(w.service.screenOnly, isTrue);
      expect(w.voice.isPlaying, isTrue);
      w.clock.tick(60);
      w.service.tick();
      expect(w.voice.said, hasLength(1), reason: 'never mid-piece, replay included');
      w.voice.simulateComplete();
      w.clock.tick(35);
      w.service.tick();
      expect(w.voice.said.last, TourPlaybackService.kScreenOnlyLine);
    });

    test('a fabric change is absorbed SILENTLY (§4.2): on the screen, never said; '
        'and a waiting fabric line expires unsaid when the plan moves on (R3)',
        () async {
      final w = await walking();
      w.gps.simulatePosition(base - 25 * _degPerMeterLat, 2.35);
      w.clock.tick(40);
      expect(w.service.applyContingency('v1-f'), isTrue);
      expect(w.service.screenText, 'Next: Stop 2 · your finish about 15:30');
      w.service.tick();
      expect(w.voice.said, isEmpty);
      expect(w.service.queuedLine, isNull);
    });

    test('a pause is not a seam and a skip is not a seam; the transcript open '
        'holds the voice', () async {
      final w = await walking();
      w.gps.simulatePosition(base, 2.35);
      w.voice.simulateComplete(); // seam
      w.service.applyContingency('v1-q');
      w.service.setTranscriptOpen(true);
      w.clock.tick(40);
      w.service.tick();
      expect(w.voice.said, isEmpty, reason: 'reading the transcript (Paulo)');
      w.service.setTranscriptOpen(false);
      expect(w.voice.said, [_question]);
    });
  });

  group('R4 repeated pauses → screen-only, per party', () {
    test('family: the second pause of the day switches the session\'s voice to '
        'the screen, said once in one plain sentence; the question is still '
        'said once; one touch restores the voice', () async {
      final w = await walking(party: 'family');
      w.gps.simulatePosition(base, 2.35);
      // On the family day the piece starts at the first
      // STANDSTILL inside the footprint, not at the edge — stand still for the
      // settling period so the piece plays, then let it end on its own.
      w.clock.tick(TourPlaybackService.kSettleSeconds + 1);
      w.service.tick();
      expect(w.voice.isPlaying, isTrue, reason: 'the standstill starts the family piece');
      w.voice.simulateComplete();
      w.service.pauseTour();
      w.clock.tick(60);
      w.service.resumeTour();
      expect(w.service.screenOnly, isFalse);
      w.service.pauseTour();
      w.clock.tick(60);
      w.service.resumeTour();
      expect(w.service.screenOnly, isTrue);
      // The switch is on the screen as its own caption (the page reads
      // [screenOnly]); it never replaces the plan's line.
      expect(w.service.screenText, isNot(TourPlaybackService.kScreenOnlyLine));
      // The switch line is said once at the next natural moment ...
      w.clock.tick(40);
      w.service.tick();
      expect(w.voice.said, [TourPlaybackService.kScreenOnlyLine]);
      // ... and the QUESTION is still said once under screen-only.
      w.service.applyContingency('v1-q');
      w.clock.tick(40);
      w.service.tick();
      expect(w.voice.said.last, _question);
      // NEVER a pause length, a count, "you paused" or "behind by N" on the
      // screen or aloud — the switch line and the question carry none.
      for (final line in [
        TourPlaybackService.kScreenOnlyLine,
        ...w.voice.said,
        w.service.screenText ?? '',
      ]) {
        expect(line.toLowerCase(), isNot(contains('paus')));
        expect(line.toLowerCase(), isNot(contains('behind by')));
        expect(line, isNot(matches(RegExp(r'\b\d+ (s|sec|seconds) '))));
      }
      w.service.restoreVoice();
      expect(w.service.screenOnly, isFalse);
    });

    test('solo: no day-long switch on a count; two pauses inside ONE stop '
        'silence that stop only', () async {
      final w = await walking(party: 'solo');
      w.gps.simulatePosition(base, 2.35);
      w.voice.simulateComplete();
      w.service.pauseTour();
      w.clock.tick(30);
      w.service.resumeTour();
      w.service.pauseTour();
      w.clock.tick(30);
      w.service.resumeTour();
      expect(w.service.pauseCount, 2);
      expect(w.service.screenOnly, isFalse);
      expect(w.service.currentStopSilenced, isTrue);
    });
  });
}
