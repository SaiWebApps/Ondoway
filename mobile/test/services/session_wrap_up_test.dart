// [Head back now] plays the CLOSE. A close lands anywhere, every prefix of the
// day is decent, and the announcement etiquette holds.
//
// The TAP IS THE SEAM — the person made it.
// The current SENTENCE finishes (never a cut word); the PIECE does NOT finish.
// Then the current stretch's close — one line, the narrator. Then the way home
// ON SCREEN. Then nothing. With no piece playing (the piece long over, or a
// leg) the DAY's close is what the tap plays, on screen at once and said at the
// next natural moment. The close is the one sentence the tap buys; the way-home
// line and the day's line are never spoken on top of it.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

/// A player that can report a POSITION inside the piece and records every door.
class _Player extends MockAudioService {
  final List<String> said = [];
  final List<String> played = [];
  int stops = 0;
  Duration pos = Duration.zero;
  @override
  Duration get position => pos;
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
  @override
  void play(String beatId, String audioUrl,
      {bool isDeeperDive = false, String? title}) {
    played.add(beatId);
    super.play(beatId, audioUrl, isDeeperDive: isDeeperDive, title: title);
  }

  @override
  void stop() {
    stops++;
    super.stop();
  }
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

// A 40-second piece of four sentences of 10 words each: boundaries at 10, 20,
// 30 and 40 s (a tap at 13 s waits 7 s — inside the 8-second cap a tapped piece
// may keep playing; the cap was lowered from 12).
const _narration =
    'One two three four five six seven eight nine ten. '
    'Eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty. '
    'Twenty-one two three four five six seven eight nine thirty. '
    "That's Stop N, the close that ends it all here.";

ItineraryStop _stop(int n, {required double lat, String? close}) => ItineraryStop(
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
  narration: _narration.replaceAll('Stop N', 'Stop $n'),
  audioUrl: 'https://cdn.example.com/$n.mp3',
  audioDurationSec: 40,
  dwellSeconds: 300,
  closeText: close ?? "That's Stop $n, the close that ends it all here.",
  // The footprint these fixtures assumed (the phone's old 40 m circle) is
  // now STATED on each stop, as the wire carries it.
  trigger: const StopTrigger(radiusM: 40),
);

SessionContingency _wrapUp(String fromPoi, {String screen = 'Straight to Stop 2'}) =>
    SessionContingency(
      contingencyId: 'v1-wrap-$fromPoi',
      trigger: {'kind': 'wrap_up_from', 'stop_id': fromPoi},
      planVersion: 1,
      stopIds: const ['poi-2'],
      screenText: screen,
    );

void main() {
  const base = 48.85;
  final stops = [
    _stop(0, lat: base),
    _stop(1, lat: base + 300 * _degPerMeterLat),
    _stop(2, lat: base + 600 * _degPerMeterLat, close: "That's the walk — the square is yours."),
  ];

  Future<({TourPlaybackService service, _Player player, MockLocationService gps, _Clock clock})>
      atStopZero() async {
    final clock = _Clock(DateTime(2026, 8, 23, 15, 0));
    final gps = MockLocationService();
    final player = _Player();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: player,
      now: () => clock.now,
    );
    await service.startTour(stops);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: stops,
      retimeToleranceSeconds: 180,
      contingencies: [_wrapUp('poi-0'), _wrapUp('poi-1'), _wrapUp('poi-2')],
      dayStartHhmm: '15:00',
    ));
    // Arrive at Stop 0 and let the geofence start its piece.
    gps.simulatePosition(base, 2.35);
    return (service: service, player: player, gps: gps, clock: clock);
  }

  group('the tap is the seam', () {
    test('mid-piece: the sentence ends, the piece does not; then the stop\'s close; '
        'the way home on screen; nothing else', () async {
      final t = await atStopZero();
      expect(t.player.isPlaying, isTrue, reason: 'premise: the piece is playing');
      // 13 s into a 40 s piece of four equal sentences: the current sentence
      // ends at 20 s — the tap waits ~7 s, never a cut word, never the piece.
      t.player.pos = const Duration(seconds: 13);
      expect(
        TourPlaybackService.secondsToSentenceEnd(stops[0], const Duration(seconds: 13)),
        closeTo(7, 1),
      );
      t.service.requestWrapUp();
      final entry = t.service.matchContingency(t.service.measure());
      expect(entry, isNotNull);
      t.service.applyContingency(entry!.contingencyId);
      // The screen flips at once: the close AND the way home are on it.
      expect(t.service.closeLine, stops[0].closeText);
      expect(t.service.screenText, 'Straight to Stop 2');
      // The piece is still playing its sentence out …
      expect(t.player.stops, 0);
      // … and at the sentence end it is cut and the CLOSE plays — through the
      // narrator's door when it has a file, else said through the one
      // silent door, on screen either way.
      t.service.finishSentenceNow();
      expect(t.player.stops, 1, reason: 'the piece is stopped at the sentence end');
      expect(t.service.closesPlayed, [stops[0].closeText]);
      expect(t.player.said, [stops[0].closeText],
          reason: 'the close is the one sentence the tap buys');
      // Nothing else: no "Straight to", no thank-you, no thread.
      t.clock.tick(60);
      t.service.tick();
      expect(t.player.said, [stops[0].closeText]);
      expect(t.player.played.where((k) => k.endsWith('-close')), isEmpty,
          reason: 'this fixture is UNVOICED (closeAudioUrl null): the file-less '
              'fallback speaks — the file door is proven in '
              'session_full_telling_test.dart');
    });

    test('no piece playing: the DAY\'s close goes on screen at once and is said '
        'at the next natural moment — never on the leg', () async {
      final t = await atStopZero();
      t.player.simulateComplete(); // the piece ended on its own; at the seam
      t.service.requestWrapUp();
      final entry = t.service.matchContingency(t.service.measure());
      t.service.applyContingency(entry!.contingencyId);
      expect(t.service.closeLine, "That's the walk — the square is yours.");
      expect(t.player.stops, 0, reason: 'nothing to cut');
      // Standing at the stop, settled: a natural moment → the day's close is said.
      t.clock.tick(31);
      t.service.tick();
      expect(t.player.said, ["That's the walk — the square is yours."]);
    });

    test('the close is ONE spoken sentence; a close never plays on a leg', () async {
      final t = await atStopZero();
      t.player.simulateComplete();
      // Walk off the stop onto the leg before tapping.
      t.gps.simulatePosition(base + 120 * _degPerMeterLat, 2.35);
      t.clock.tick(40);
      t.service.requestWrapUp();
      final entry = t.service.matchContingency(t.service.measure());
      t.service.applyContingency(entry!.contingencyId);
      expect(t.service.closeLine, isNotNull, reason: 'on screen at once');
      t.clock.tick(60);
      t.service.tick();
      expect(t.player.said, isEmpty, reason: 'on a leg nothing is said');
    });
  });

  group('the sentence boundary is arithmetic over the stop\'s own text', () {
    test('a piece of four equal sentences has its ends at quarter marks; past the '
        'last boundary the wait is zero; no text or length means no wait', () {
      final stop = stops[0];
      // The first sentence ends at 10 s, past the 8 s cap — a tap at 0 s waits
      // the cap, not the sentence.
      expect(TourPlaybackService.secondsToSentenceEnd(stop, Duration.zero),
          TourPlaybackService.kSentenceEndCapSeconds);
      expect(TourPlaybackService.secondsToSentenceEnd(stop, const Duration(seconds: 3)),
          closeTo(7, 1));
      expect(TourPlaybackService.secondsToSentenceEnd(stop, const Duration(seconds: 29)),
          closeTo(1, 1));
      expect(TourPlaybackService.secondsToSentenceEnd(stop, const Duration(seconds: 39)),
          closeTo(1, 1));
      expect(TourPlaybackService.secondsToSentenceEnd(stop, const Duration(seconds: 41)), 0);
      final mute = ItineraryStop(
        sortOrder: 9, poiId: 'p', poiName: 'P', lat: 0, lng: 0, lensName: '',
        lensDisplay: '', durationMin: 1, importanceTier: 1, startTime: '',
      );
      expect(TourPlaybackService.secondsToSentenceEnd(mute, const Duration(seconds: 5)), 0);
      // Never more than the cap: a person who tapped is not made to wait for a
      // forty-second sentence.
      final long = ItineraryStop(
        sortOrder: 8, poiId: 'q', poiName: 'Q', lat: 0, lng: 0, lensName: '',
        lensDisplay: '', durationMin: 1, importanceTier: 1, startTime: '',
        narration: '${List.filled(200, 'word').join(' ')}.', audioDurationSec: 100,
      );
      expect(TourPlaybackService.secondsToSentenceEnd(long, Duration.zero),
          TourPlaybackService.kSentenceEndCapSeconds);
      expect(TourPlaybackService.kSentenceEndCapSeconds, 8,
          reason: 'eight seconds is the whole budget');
    });
  });

  // The duration is MEASURED, and the clocks trust it: the seam is reckoned
  // from the PLAYER's real length when the file is loaded — the wire's length is the estimate, the player's is the fact — and
  // the cap is the party's: 8 s, the family 0 (cut at once), a `wall` day 5 s.
  // An unknown length cuts at once and the close plays.
  group('the seam trusts the player\'s length and the party\'s cap', () {
    test('the player\'s length wins over the wire\'s; the wire\'s is the fallback', () async {
      final t = await atStopZero();
      // The wire says 40 s; the loaded file is really 80 s long: at 25 s the
      // wire's boundary would be 30 s (5 s away) but the real sentence ends at
      // 40 s — 15 s away, capped at 8.
      t.player.pos = const Duration(seconds: 25);
      t.player.duration = const Duration(seconds: 80);
      expect(t.service.sentenceWaitFor(stops[0]), 8);
      t.player.duration = Duration.zero; // the player does not know yet: the wire's
      expect(t.service.sentenceWaitFor(stops[0]), closeTo(5, 1));
      expect(
        TourPlaybackService.secondsToSentenceEnd(stops[0], const Duration(seconds: 25),
            lengthSec: 80),
        TourPlaybackService.kSentenceEndCapSeconds,
      );
    });

    test('the family cuts at once; a wall day waits at most 5 s; unknown length cuts '
        'at once', () async {
      final t = await atStopZero();
      t.player.pos = const Duration(seconds: 13); // 7 s to the sentence end
      expect(t.service.sentenceWaitFor(stops[0]), closeTo(7, 1));
      t.service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops, retimeToleranceSeconds: 180,
        dayStartHhmm: '15:00', party: 'family',
      ));
      expect(t.service.sentenceWaitFor(stops[0]), 0, reason: 'cut at once');
      t.service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops, retimeToleranceSeconds: 180,
        dayStartHhmm: '15:00', endHardness: 'wall',
      ));
      expect(t.service.sentenceWaitFor(stops[0]), 5, reason: 'five seconds');
      final mute = ItineraryStop(
        sortOrder: 9, poiId: 'p', poiName: 'P', lat: 0, lng: 0, lensName: '',
        lensDisplay: '', durationMin: 1, importanceTier: 1, startTime: '',
        narration: _narration,
      );
      expect(t.service.sentenceWaitFor(mute), 0, reason: 'no length: cut at once');
    });
  });
}
