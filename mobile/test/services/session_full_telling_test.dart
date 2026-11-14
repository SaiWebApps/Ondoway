// THE LINGER RULE.
//
// A linger OFFERS the full telling on the screen, silently; a TAP plays it, at
// a seam — auto-play on stillness is NOT the default: a visitor who lingers is
// looking, and standing still is part of the day. Never while paused, never
// mid-piece. The offer names its cost ("Full telling · 7 min"). "Again" is a
// separate control from "more" — the re-listen is not the full telling.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

class _Player extends MockAudioService {
  final List<String> played = [];
  @override
  void play(String beatId, String audioUrl,
      {bool isDeeperDive = false, String? title}) {
    played.add(beatId);
    super.play(beatId, audioUrl, isDeeperDive: isDeeperDive, title: title);
  }
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

// Exactly 300 words (294 + the six-word close) -> "Full telling · 2 min".
final String _fullText =
    '${List.filled(294, 'word').join(' ')}. And that closes the second story.';

ItineraryStop _stop(int n, {required double lat, bool major = false}) => ItineraryStop(
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
  dwellSeconds: 600,
  closeText: "And that's Stop $n.",
  fullNarration: major ? _fullText : null,
  fullCloseText: major ? 'And that closes the second story.' : null,
  // The footprint these fixtures assumed (the phone's old 40 m circle) is
  // now STATED on each stop, as the wire carries it.
  trigger: const StopTrigger(radiusM: 40),
);

void main() {
  _s68Tests();
  const base = 48.85;

  Future<({TourPlaybackService service, _Player player, MockLocationService gps, _Clock clock})>
      atMajorStopZero({bool major = true}) async {
    final stops = [
      _stop(0, lat: base, major: major),
      _stop(1, lat: base + 400 * _degPerMeterLat),
    ];
    final clock = _Clock(DateTime(2026, 8, 23, 15, 0));
    final gps = MockLocationService();
    final player = _Player();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: player,
      now: () => clock.now,
    );
    await service.startTour(stops);
    gps.simulatePosition(base, 2.35); // arrive; the tight piece starts
    return (service: service, player: player, gps: gps, clock: clock);
  }

  group('the linger rule', () {
    test('a linger past the tight telling OFFERS, with the cost, and plays '
        'NOTHING by itself', () async {
      final t = await atMajorStopZero();
      expect(t.service.fullTellingOffer, isNull, reason: 'mid-piece: no offer');
      t.player.simulateComplete(); // the tight piece ends on its own
      t.clock.tick(31); // settled: a standing seam inside the circle
      t.service.tick();
      expect(t.service.fullTellingOffer, 'Full telling · 2 min');
      // Silently: the offer never starts audio on its own.
      expect(t.player.played, ['item-0'], reason: 'only the tight piece ever played');
    });

    test('the TAP plays it as its own piece; a minor stop never offers', () async {
      final t = await atMajorStopZero();
      t.player.simulateComplete();
      t.clock.tick(31);
      t.service.tick();
      t.service.playFullTelling('https://cdn.example.com/0-full.mp3');
      expect(t.player.played, ['item-0', 'item-0-full']);

      final minor = await atMajorStopZero(major: false);
      minor.player.simulateComplete();
      minor.clock.tick(31);
      minor.service.tick();
      expect(minor.service.fullTellingOffer, isNull, reason: 'no second story authored');
      minor.service.playFullTelling('https://cdn.example.com/x.mp3');
      expect(minor.player.played, ['item-0'], reason: 'the tap without an offer is inert');
    });

    test('never while paused; and AGAIN replays the tight piece, not the full',
        () async {
      final t = await atMajorStopZero();
      t.player.simulateComplete();
      t.clock.tick(31);
      t.service.tick();
      t.service.pauseTour();
      expect(t.service.fullTellingOffer, isNull, reason: 'paused: stillness is the day');
      t.service.resumeTour();
      t.player.simulateComplete();
      t.clock.tick(31);
      t.service.tick();
      t.service.playAgain();
      expect(t.player.played.last, 'item-0', reason: 'again = the tight telling again');
    });
  });
}

// ---------------------------------------------------------------------------
// The tour's own voice.
// ---------------------------------------------------------------------------

ItineraryStop _voicedStop(int n, {required double lat}) => ItineraryStop(
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
  dwellSeconds: 600,
  closeText: "And that's Stop $n.",
  closeAudioUrl: 'https://cdn.example.com/$n-close.mp3',
  fullNarration: _fullText,
  fullCloseText: 'And that closes the second story.',
  fullCloseAudioUrl: 'https://cdn.example.com/$n-full-close.mp3',
  trigger: const StopTrigger(radiusM: 40), // the footprint, stated
);

void _s68Tests() {
  const base = 48.85;

  group('the narrator finishes (the tour\'s own voice)', () {
    test('a voiced tight close plays as a FILE at the tap — the plain voice '
        'only when no file exists', () async {
      final stops = [
        _voicedStop(0, lat: base),
        _voicedStop(1, lat: base + 400 * _degPerMeterLat),
      ];
      final clock = _Clock(DateTime(2026, 8, 23, 15, 0));
      final gps = MockLocationService();
      final player = _Player();
      final service = TourPlaybackService(
        locationService: gps, audioService: player, now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180,
        contingencies: [
          for (final poi in const ['poi-0', 'poi-1'])
            SessionContingency(
              contingencyId: 'v1-wrap-$poi',
              trigger: {'kind': 'wrap_up_from', 'stop_id': poi},
              planVersion: 1, stopIds: const ['poi-1'],
              screenText: 'Straight on',
            ),
        ],
        dayStartHhmm: '15:00',
      ));
      gps.simulatePosition(base, 2.35); // the tight piece starts
      service.requestWrapUp();
      final entry = service.matchContingency(service.measure());
      service.applyContingency(entry!.contingencyId);
      service.finishSentenceNow();
      expect(player.played.last, 'item-0-close',
          reason: 'the narrator finishes: the close is a file, not the robot');
    });

    test('a tap mid-FULL-piece finishes the full\'s sentence and plays the '
        'FULL\'s close file — never the tight\'s', () async {
      final stops = [
        _voicedStop(0, lat: base),
        _voicedStop(1, lat: base + 400 * _degPerMeterLat),
      ];
      final clock = _Clock(DateTime(2026, 8, 23, 15, 0));
      final gps = MockLocationService();
      final player = _Player();
      final service = TourPlaybackService(
        locationService: gps, audioService: player, now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(SessionPlan(
        tripId: 'trip-1', planVersion: 1, stops: stops,
        retimeToleranceSeconds: 180,
        contingencies: [
          for (final poi in const ['poi-0', 'poi-1'])
            SessionContingency(
              contingencyId: 'v1-wrap-$poi',
              trigger: {'kind': 'wrap_up_from', 'stop_id': poi},
              planVersion: 1, stopIds: const ['poi-1'],
              screenText: 'Straight on',
            ),
        ],
        dayStartHhmm: '15:00',
      ));
      gps.simulatePosition(base, 2.35);
      player.simulateComplete(); // the tight ends on its own
      clock.tick(31);
      service.tick();
      // The linger offer is up; the tap plays the full telling (300 words / 120 s).
      service.playFullTelling('https://cdn.example.com/0-full.mp3', durationSec: 120);
      expect(player.played.last, 'item-0-full');
      // Mid-full, the person wraps up: the FULL's close plays as a file.
      service.requestWrapUp();
      final entry = service.matchContingency(service.measure());
      service.applyContingency(entry!.contingencyId);
      service.finishSentenceNow();
      expect(player.played.last, 'item-0-full-close',
          reason: 'the full telling ends on ITS OWN close, in the narrator\'s voice');
      expect(service.closeLine, 'And that closes the second story.');
    });
  });
}
