// The LOCK SCREEN's pause. A couple who would rather talk pause the tour without a word,
// mid-sentence, because the voice in the ear is in the way: that pause is the product being
// used correctly, not an interruption of it. They do it five times in three hours and the
// phone stays pocketed.
//
// THE INVARIANT: the platform's pause button reaches the SAME door the in-app pause uses.
// Their pause suspends the TOUR — its clock stops, the pause is counted as theirs, the wall
// clock keeps spending — not merely the AudioPlayer. A lock-screen press that only paused
// the player would leave the session clock running and the plan drifting, so the app's
// finish time would turn to fiction.
//
// This is NOT an interruption. An interruption (a call, Siri) is the platform taking the
// audio away and is never counted against the person; a transport button is the person
// asking, and it is.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

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
      narration: 'One two three four five six seven eight nine ten.',
      audioUrl: 'https://cdn.example.com/$n.mp3',
      audioDurationSec: 40,
      dwellSeconds: 300,
      closeText: "That's Stop $n.",
      trigger: const StopTrigger(radiusM: 40),
    );

void main() {
  const base = 48.85;
  final stops = [
    _stop(0, lat: base),
    _stop(1, lat: base + 300 * _degPerMeterLat),
  ];

  Future<({TourPlaybackService service, MockAudioService player, MockLocationService gps, _Clock clock})>
      midPieceAtStopZero({String? party}) async {
    final clock = _Clock(DateTime(2026, 8, 24, 15, 12));
    final gps = MockLocationService();
    final player = MockAudioService();
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
      dayStartHhmm: '15:12',
      party: party,
    ));
    gps.simulatePosition(base, 2.35); // arrive: the piece plays, the tour clock starts
    expect(player.isPlaying, isTrue, reason: 'premise: the piece is playing');
    expect(service.tourClockRunning, isTrue, reason: 'premise: the tour clock is running');
    return (service: service, player: player, gps: gps, clock: clock);
  }

  group('the lock screen', () {
    test('a pause pressed on the LOCK SCREEN suspends the TOUR, not just the audio: '
        'the tour clock stops, the wall clock keeps spending, and the pause is '
        'counted as their own', () async {
      final t = await midPieceAtStopZero();
      t.clock.tick(60);
      expect(t.service.tourElapsedSeconds, 60, reason: 'premise: one minute of tour');

      t.player.simulateRemoteCommand(AudioRemoteCommand.pause);

      expect(t.player.isPlaying, isFalse, reason: 'the voice in his ear is out of the way');
      expect(t.service.isPaused, isTrue,
          reason: 'THE INVARIANT: the lock screen pauses the TOUR, through the one door');
      expect(t.service.pauseCount, 1,
          reason: 'a transport button is the person asking — unlike an interruption');
      expect(t.service.resumeOffer, isNull, reason: 'not an interruption: no resume offer');

      t.clock.tick(300); // five minutes of the conversation they came for
      expect(t.service.tourElapsedSeconds, 60,
          reason: 'the tour clock is suspended: a pause is information, never lateness');
      expect(t.service.wallElapsedSeconds, 360,
          reason: 'the wall keeps spending — the two clocks');
    });

    test('play pressed on the LOCK SCREEN resumes the TOUR: the clock runs again '
        'and the pause is spent', () async {
      final t = await midPieceAtStopZero();
      t.clock.tick(60);
      t.player.simulateRemoteCommand(AudioRemoteCommand.pause);
      t.clock.tick(300);

      t.player.simulateRemoteCommand(AudioRemoteCommand.play);

      expect(t.service.isPaused, isFalse, reason: 'the tour is running again');
      expect(t.player.isPlaying, isTrue, reason: 'the piece comes back');
      t.clock.tick(30);
      expect(t.service.tourElapsedSeconds, 90,
          reason: 'sixty before the pause and thirty after; the five minutes are not tour time');
      expect(t.service.wallElapsedSeconds, 390);
    });

    test('the two surfaces are ONE control: paused on the lock screen, resumed in the '
        'app, the piece comes back — and back again the other way round', () async {
      final t = await midPieceAtStopZero();
      t.player.simulateRemoteCommand(AudioRemoteCommand.pause);
      t.service.resumeTour(); // the in-app button
      expect(t.service.isPaused, isFalse);
      expect(t.player.isPlaying, isTrue,
          reason: 'a tour resumed in silence is a tour that lost its piece');

      t.service.pauseTour(); // the in-app button
      expect(t.player.isPlaying, isFalse);
      t.player.simulateRemoteCommand(AudioRemoteCommand.play);
      expect(t.service.isPaused, isFalse,
          reason: 'the lock screen resumes the TOUR clock too, not only the player');
    });

    test('it is the SAME policy, not a copy: the couple\'s second lock-screen pause '
        'flips the day to screen-only, exactly as an in-app pause does',
        () async {
      final t = await midPieceAtStopZero(party: 'couple');
      t.player.simulateRemoteCommand(AudioRemoteCommand.pause);
      t.player.simulateRemoteCommand(AudioRemoteCommand.play);
      expect(t.service.screenOnly, isFalse, reason: 'one pause is not the switch');

      t.player.simulateRemoteCommand(AudioRemoteCommand.pause);

      expect(t.service.pauseCount, 2);
      expect(t.service.screenOnly, isTrue,
          reason: 'after the second pause of the day the couple hear no session voice');
    });

    test('the lock screen has something real to show: the piece carries the place\'s '
        'own name, never a stand-in', () async {
      final t = await midPieceAtStopZero();
      expect(t.player.titles['item-0'], 'Stop 0');
      t.player.simulateComplete(); // Stop 0's piece ends; the walk moves on
      t.gps.simulatePosition(base + 300 * _degPerMeterLat, 2.35);
      expect(t.player.titles['item-1'], 'Stop 1',
          reason: 'every piece names its own place, not the last one');
    });
  });
}
