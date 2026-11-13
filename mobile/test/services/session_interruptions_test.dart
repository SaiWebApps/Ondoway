// Interruptions: a call, a photo, another app ("audio overlaps the walking"
// holds only while the piece plays).
//
// A call, another app's voice, Siri → PAUSE. Resume at the START of the cut
// sentence ("where it was" hands the listener half a sentence; the piece start
// is a punishment), saying NOTHING. By itself when the interruption ends IF the
// walker is still inside the stop's footprint — the COUPLE by their tap (they
// restart when their conversation pauses). Off the footprint when it ends:
// nothing resumes; the missed CLOSE goes on the screen and is said once at the
// next standing seam — the queue door, never on a leg. A navigation prompt DUCKS
// the piece and it carries on; a photo changes nothing.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

/// A player that can report a POSITION inside the piece and records every door.
class _Player extends MockAudioService {
  final List<String> said = [];
  Duration pos = Duration.zero;
  @override
  Duration get position => pos;
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

// A 40-second piece of four sentences of 10 words each: boundaries at 10, 20,
// 30 and 40 s — a position of 25 s is inside the THIRD sentence, which began
// at 20 s.
const _narration =
    'One two three four five six seven eight nine ten. '
    'Eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty. '
    'Twenty-one two three four five six seven eight nine thirty. '
    "That's Stop N, the close that ends it all here.";

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
  narration: _narration.replaceAll('Stop N', 'Stop $n'),
  audioUrl: 'https://cdn.example.com/$n.mp3',
  audioDurationSec: 40,
  dwellSeconds: 300,
  closeText: "That's Stop $n, the close that ends it all here.",
  trigger: const StopTrigger(radiusM: 40),
);

void main() {
  const base = 48.85;
  final stops = [
    _stop(0, lat: base),
    _stop(1, lat: base + 300 * _degPerMeterLat),
  ];

  Future<({TourPlaybackService service, _Player player, MockLocationService gps, _Clock clock})>
      midPieceAtStopZero({String? party}) async {
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
      tripId: 'trip-1', planVersion: 1, stops: stops, retimeToleranceSeconds: 180,
      dayStartHhmm: '15:00', party: party,
    ));
    gps.simulatePosition(base, 2.35); // arrive: the piece plays
    expect(player.isPlaying, isTrue, reason: 'premise: the piece is playing');
    player.pos = const Duration(seconds: 25); // inside the third sentence
    return (service: service, player: player, gps: gps, clock: clock);
  }

  group('S7.9 interruptions (W7.2 R5)', () {
    test('a call pauses the piece — not the person\'s pause — and, still inside the '
        'footprint when it ends, the piece resumes from the START of the cut '
        'sentence, saying nothing', () async {
      final t = await midPieceAtStopZero();
      t.player.simulateInterruption(AudioInterruptionKind.pauseBegin);
      expect(t.player.isPlaying, isFalse);
      expect(t.service.isPaused, isFalse, reason: 'an interruption is not their pause');
      expect(t.service.pauseCount, 0);
      t.clock.tick(90); // a ninety-second call
      t.player.simulateInterruption(AudioInterruptionKind.ended);
      expect(t.player.isPlaying, isTrue, reason: 'inside the footprint: by itself');
      expect(t.player.currentBeatId, 'item-0');
      expect(t.player.lastSeek, const Duration(seconds: 20),
          reason: 'the cut sentence began at 20 s — never mid-word, never the piece start');
      expect(t.player.said, isEmpty, reason: 'saying nothing');
      expect(t.service.resumeOffer, isNull);
    });

    test('the couple resumes by their tap (F&D), never by itself', () async {
      final t = await midPieceAtStopZero(party: 'couple');
      t.player.simulateInterruption(AudioInterruptionKind.pauseBegin);
      t.player.simulateInterruption(AudioInterruptionKind.ended);
      expect(t.player.isPlaying, isFalse, reason: 'F&D: "we restart when the conversation pauses"');
      expect(t.service.resumeOffer, 'Stop 0');
      t.service.resumeInterrupted();
      expect(t.player.isPlaying, isTrue);
      expect(t.player.lastSeek, const Duration(seconds: 20));
      expect(t.service.resumeOffer, isNull);
    });

    test('off the footprint when it ends: nothing resumes; the missed close is on the '
        'screen and said once at the next standing seam — never on the leg', () async {
      final t = await midPieceAtStopZero();
      t.player.simulateInterruption(AudioInterruptionKind.pauseBegin);
      // The walker moves on during the call: 120 m north, off Stop 0's footprint.
      t.gps.simulatePosition(base + 120 * _degPerMeterLat, 2.35);
      t.player.simulateInterruption(AudioInterruptionKind.ended);
      expect(t.player.isPlaying, isFalse, reason: 'off the footprint nothing resumes');
      expect(t.player.lastSeek, isNull);
      expect(t.service.closeLine, stops[0].closeText, reason: 'the missed close, on screen');
      expect(t.service.resumeOffer, isNull);
      expect(t.service.currentStop?.poiId, 'poi-1', reason: 'Stop 0 is over; Stop 1 is next');
      // On the leg: standing still says nothing.
      t.clock.tick(40);
      t.service.tick();
      expect(t.player.said, isEmpty);
      // At Stop 1: its own piece first; then, at the standing seam, the missed close once.
      t.gps.simulatePosition(base + 300 * _degPerMeterLat, 2.35);
      expect(t.player.currentBeatId, 'item-1');
      t.player.simulateComplete();
      t.clock.tick(31);
      t.service.tick();
      expect(t.player.said, [stops[0].closeText]);
      t.clock.tick(31);
      t.service.tick();
      expect(t.player.said, [stops[0].closeText], reason: 'said once');
    });

    test('a navigation prompt ducks and the piece carries on; an interruption while '
        'nothing plays changes nothing', () async {
      final t = await midPieceAtStopZero();
      t.player.simulateInterruption(AudioInterruptionKind.duckBegin);
      expect(t.player.isPlaying, isTrue, reason: 'ducked, not paused');
      t.player.simulateInterruption(AudioInterruptionKind.ended);
      expect(t.player.lastSeek, isNull, reason: 'nothing was cut, nothing restarts');
      expect(t.service.resumeOffer, isNull);
      // Nothing playing: a call comes and goes.
      t.player.simulateComplete();
      t.player.simulateInterruption(AudioInterruptionKind.pauseBegin);
      t.player.simulateInterruption(AudioInterruptionKind.ended);
      expect(t.player.isPlaying, isFalse);
      expect(t.player.lastSeek, isNull);
      expect(t.service.resumeOffer, isNull);
    });
  });
}
