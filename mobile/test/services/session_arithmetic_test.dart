// The phone's ARITHMETIC and the phone half of THE SESSION CLOCK SEAM.
//
// Walking pace is learned within the first ~15 minutes and replaces the preset,
// so a walker slowed by a heavy bag is not behind all day. Listening rate is
// learned from replays and playback speed, and the remaining day's estimates
// scale to the observed rate. A pause suspends the clock and is treated as
// information, never lateness. So: pace from moving minutes on WALKING LEGS
// only; the rate applied to narration seconds; a pause suspends the TOUR clock
// but the WALL clock keeps spending; skip = unplayed AND unopened; wandering
// inside a stop is not a divergence. The seam: exactly one re-timing expression
// on the phone, COMPARED with the server's on every held version, a gap beyond
// `retime_tolerance_seconds` REPORTED and never corrected.

import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/tour_playback_service.dart';

import 'mocks/mock_audio_service.dart';
import 'mocks/mock_location_service.dart';

const double _degPerMeterLat = 1.0 / 111320.0;

ItineraryStop _stop(
  int n, {
  required double lat,
  int dwellSeconds = 600,
  double? audioSeconds,
  String startTime = '',
}) => ItineraryStop(
  sortOrder: n,
  stopId: 'item-$n',
  poiId: 'poi-$n',
  poiName: 'Stop $n',
  lat: lat,
  lng: 2.35,
  beatId: 'beat-$n',
  lensName: 'history',
  lensDisplay: 'History',
  durationMin: dwellSeconds ~/ 60,
  importanceTier: 3,
  startTime: startTime,
  audioUrl: 'https://cdn.example.com/$n.mp3',
  audioDurationSec: audioSeconds,
  dwellSeconds: dwellSeconds,
  // The footprint these fixtures assumed (the phone's old 40 m circle) is
  // now STATED on each stop, as the wire carries it; the phone draws no circle.
  trigger: const StopTrigger(radiusM: 40),
);

class _Clock {
  DateTime now;
  _Clock(this.now);
  void tick(int seconds) => now = now.add(Duration(seconds: seconds));
}

void main() {
  // Three stops 300 m apart, due north of a start 600 m south of the first.
  const base = 48.85;
  final stops = [
    _stop(0, lat: base, audioSeconds: 100, dwellSeconds: 60, startTime: '09:12'),
    _stop(1, lat: base + 300 * _degPerMeterLat, audioSeconds: 100,
        dwellSeconds: 60, startTime: '09:23'),
    _stop(2, lat: base + 600 * _degPerMeterLat, audioSeconds: 100,
        dwellSeconds: 60, startTime: '09:34'),
  ];
  SessionPlan session({
    List<ItineraryStop>? serverStops,
    List<SessionPromise> promises = const [],
  }) => SessionPlan(
    tripId: 'trip-1',
    planVersion: 1,
    stops: serverStops ?? stops,
    promises: promises,
    retimeToleranceSeconds: 180,
    walkingPaceKmh: 3.0,
    dayStartHhmm: '09:00',
    // The day's own-place radius — the start square's width — comes off
    // the wire (the server's one number), never a circle of the phone's.
    placement: const PlacementPolicy(ownPlaceM: 60),
  );

  group('S5.10 the phone learns (design §4.1)', () {
    test('the walking pace replaces the preset after five minutes of walking on '
        'a leg; low-accuracy fixes, standing still and wandering inside a stop '
        'train nothing', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: MockAudioService(),
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      expect(service.paceMps, closeTo(3.0 / 3.6, 0.001)); // the preset
      // Walking north toward Stop 0 from 600 m south at 1.5 m/s: a fix every
      // 30 s, 45 m apart. Eleven segments = 330 s of walking.
      var lat = base - 600 * _degPerMeterLat;
      location.simulatePosition(lat, 2.35);
      for (var i = 0; i < 6; i++) {
        clock.tick(30);
        lat += 45 * _degPerMeterLat;
        location.simulatePosition(lat, 2.35);
      }
      expect(service.learnedPaceMps, isNull,
          reason: '180 s of walking is not yet a learned pace');
      for (var i = 0; i < 5; i++) {
        clock.tick(30);
        lat += 45 * _degPerMeterLat;
        location.simulatePosition(lat, 2.35);
      }
      expect(service.learnedPaceMps, closeTo(1.5, 0.05));
      expect(service.paceMps, closeTo(1.5, 0.05));
      // 1.5 m/s is faster than the planner's 3 km/h base: the wire's multiplier
      // never goes under 1.0 (the planner does not plan faster than its base).
      expect(service.observedPace, 1.0);
      final learnedBefore = service.learnedPaceMps!;
      // A low-accuracy fix breaks the chain: neither it nor the good fix after
      // it trains (a 40 m fix could put the person anywhere on the pavement).
      clock.tick(30);
      lat += 45 * _degPerMeterLat;
      location.simulatePosition(lat, 2.35, accuracy: 40);
      clock.tick(30);
      lat += 200 * _degPerMeterLat; // an implausible jump after the bad fix
      location.simulatePosition(lat, 2.35);
      expect(service.learnedPaceMps, closeTo(learnedBefore, 0.001));
      // Standing still (a crossing, a conversation) is not walking.
      clock.tick(60);
      location.simulatePosition(lat, 2.35);
      expect(service.learnedPaceMps, closeTo(learnedBefore, 0.001));
      // Wandering INSIDE Stop 0's circle at walking speed is not a leg.
      location.simulatePosition(base - 30 * _degPerMeterLat, 2.35);
      clock.tick(30);
      location.simulatePosition(base + 5 * _degPerMeterLat, 2.35);
      clock.tick(30);
      location.simulatePosition(base + 30 * _degPerMeterLat, 2.35);
      expect(service.learnedPaceMps, closeTo(learnedBefore, 0.001));
    });

    test('the listening rate is wall time over stated length — a replay raises '
        'it — and the re-timing spends the longer of the visit and the '
        'narration at that rate', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final audio = MockAudioService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: audio,
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      expect(service.listeningRate, 1.0);
      // Arrive at Stop 0: the geofence plays its 100 s piece.
      location.simulatePosition(base, 2.35);
      expect(audio.isPlaying, isTrue);
      clock.tick(100);
      audio.simulateComplete(); // heard once, at its stated length
      expect(service.listeningRate, closeTo(1.0, 0.01));
      // The listener replays it: another 100 s of wall time against the same 100 s.
      audio.play('item-0', 'https://cdn.example.com/0.mp3');
      clock.tick(100);
      audio.simulateComplete();
      expect(service.listeningRate, closeTo(2.0, 0.01));
      // The remaining day's estimates scale: Stop 1 says 100 s and stands 60 s,
      // so at rate 2.0 the phone spends 200 s there, not 100.
      final etas = service.retimeRemaining();
      final stop1 = etas.firstWhere((e) => e.stop.poiId == 'poi-1');
      expect(stop1.secondsToDeparture - stop1.secondsToArrival, 200);
    });
  });

  group('S5.10 pause as information (design §4.3)', () {
    test('a pause suspends the TOUR clock and is never lateness; the WALL clock '
        'keeps spending and the number on screen moves with it', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final audio = MockAudioService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: audio,
        now: () => clock.now,
      );
      await service.startTour(stops);
      // Standing at Stop 0, done there, heading to Stop 1 (300 m: 486 s at the
      // preset). The plan says Stop 1 at 09:23 and the day ends at 10:00.
      service.holdSession(session(promises: const [
        SessionPromise(
            promiseId: 'finish', kind: 'finish', name: 'your finish',
            arrivesHhmm: '10:00', protected: true),
      ]));
      location.simulatePosition(base, 2.35);
      audio.simulateComplete(); // Stop 0 heard; the target is now Stop 1
      expect(service.currentStopIndex, 1);
      clock.tick(600); // 09:10
      final before = service.measure();
      expect(before.minutesLate, isNull);
      expect(before.minutesLeft, 50);
      // Ten minutes talking. The wall spends them; the tour clock does not.
      service.pauseTour();
      expect(service.isPaused, isTrue);
      expect(audio.isPlaying, isFalse);
      clock.tick(600); // 09:20
      service.resumeTour();
      expect(service.pauseCount, 1);
      expect(service.longestPauseSeconds, 600);
      expect(service.wallElapsedSeconds, 1200);
      expect(service.tourElapsedSeconds, 600);
      final after = service.measure();
      // Tour frame: 09:00 + 10 min + 8 min = 09:18 against 09:23 — NOT late.
      expect(after.minutesLate, isNull, reason: 'a pause is never lateness');
      // Wall frame: 50 minutes left became 40 — the platform clock moved.
      expect(after.minutesLeft, 40);
      // And the clock the phone would send is in the wall frame: 09:20 + 8 min.
      expect(service.phoneNextStopHhmm, '09:28');
    });
  });

  group('W5.14 the tour clock and the firm finish', () {
    test('the tour clock starts at the first step off the square or the first '
        'play, not at the tap (R1.3; Fiona & Dev)', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: MockAudioService(),
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      // Standing on the square, 600 m from Stop 0, talking for six minutes.
      final square = base - 600 * _degPerMeterLat;
      location.simulatePosition(square, 2.35);
      clock.tick(360);
      location.simulatePosition(square + 3 * _degPerMeterLat, 2.35);
      expect(service.wallElapsedSeconds, 360);
      expect(service.tourElapsedSeconds, 0, reason: 'not the tour yet');
      expect(service.tourClockRunning, isFalse);
      // The first step off the square starts it — the square's width is the
      // day's own-place radius from the wire (60 m), so 80 m is off it.
      location.simulatePosition(square + 80 * _degPerMeterLat, 2.35);
      expect(service.tourClockRunning, isTrue);
      clock.tick(600);
      expect(service.tourElapsedSeconds, 600);
      expect(service.wallElapsedSeconds, 960);
    });

    test('a FIRM finish that has moved past the tolerance earns ONE screen line; '
        'an OPEN finish moves in silence (W5.14 Q3)', () async {
      for (final hardness in ['firm', 'open']) {
        final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
        final location = MockLocationService();
        final audio = MockAudioService();
        final service = TourPlaybackService(
          locationService: location,
          audioService: audio,
          now: () => clock.now,
        );
        await service.startTour(stops);
        service.holdSession(SessionPlan(
          tripId: 'trip-1',
          planVersion: 1,
          stops: stops,
          retimeToleranceSeconds: 180,
          walkingPaceKmh: 3.0,
          dayStartHhmm: '09:00',
          plannedEndHhmm: '09:50',
          finishLat: base + 600 * _degPerMeterLat,
          finishLng: 2.35,
          finishName: 'your finish',
          endHardness: hardness,
        ));
        location.simulatePosition(base, 2.35); // at Stop 0: the piece plays
        clock.tick(60);
        service.tick();
        expect(service.finishMovedLine, isNull, reason: 'on plan so far');
        // A forty-minute pause: the wall spends it, the finish moves past 09:50.
        service.pauseTour();
        clock.tick(40 * 60);
        service.resumeTour();
        service.tick();
        if (hardness == 'firm') {
          expect(service.finishMovedLine, isNotNull);
          expect(service.finishMovedLine, contains('now looks like'));
          expect(service.finishMovedLine, contains('not 09:50'));
          final once = service.finishMovedLine;
          clock.tick(30);
          service.tick();
          expect(service.finishMovedLine, once, reason: 'said once, not re-issued');
          // Never spoken: it is a screen line.
          expect(service.spoken, isEmpty);
        } else {
          expect(service.finishMovedLine, isNull, reason: 'an open day has no finish to defend');
        }
      }
    });
  });

  group('W5.13 a pause is not an ending', () {
    test('pausing the piece keeps the tour at its stop; only a piece that '
        'COMPLETES advances it (D6 found the tour jumping a stop on Dev\'s '
        'pause)', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final audio = MockAudioService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: audio,
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      location.simulatePosition(base, 2.35); // Stop 0 plays
      expect(audio.isPlaying, isTrue);
      expect(service.currentStopIndex, 0);
      service.pauseTour(); // the piece pauses: not playing, NOT completed
      expect(audio.isPlaying, isFalse);
      expect(audio.isCompleted, isFalse);
      expect(service.currentStopIndex, 0, reason: 'a pause is not an ending');
      expect(service.isNaturalMoment, isFalse, reason: 'nor a seam');
      service.resumeTour();
      expect(audio.isPlaying, isTrue);
      audio.simulateComplete();
      expect(service.currentStopIndex, 1, reason: 'the ending advances');
    });
  });

  group('S5.10 skip and wandering', () {
    test('skip = unplayed AND unopened; a stop read on the screen is not a skip', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final service = TourPlaybackService(
        locationService: MockLocationService(),
        audioService: MockAudioService(),
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      expect(service.measure().skippedStopId, isNull);
      service.noteTranscriptOpened(stops[1]); // the person read Stop 1
      service.skipToStop(2);
      // Stop 0 was neither played nor opened: that is the skip. Stop 1 was read.
      expect(service.measure().skippedStopId, 'poi-0');
    });
  });

  group('S5.10 THE SESSION CLOCK SEAM, phone half (design §4.6)', () {
    test('every held version is COMPARED with the phone\'s own re-timing; a gap '
        'beyond the tolerance is REPORTED, never corrected', () async {
      final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
      final location = MockLocationService();
      final audio = MockAudioService();
      final service = TourPlaybackService(
        locationService: location,
        audioService: audio,
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session());
      location.simulatePosition(base, 2.35);
      audio.simulateComplete(); // done at Stop 0, heading to Stop 1
      clock.tick(600); // 09:10; Stop 1 is 300 m on: 486 s -> 09:18 by the phone
      final own = service.retimeRemaining().first.secondsToArrival;
      expect(service.phoneNextStopHhmm, '09:18');
      // A version whose clock agrees (within 180 s): no report.
      service.holdSession(session(serverStops: [
        stops[0],
        _stop(1, lat: base + 300 * _degPerMeterLat, audioSeconds: 100,
            dwellSeconds: 60, startTime: '09:19'),
        stops[2],
      ]));
      expect(service.clockNotices, isEmpty);
      // A version whose clock disagrees by 12 minutes: reported ...
      service.holdSession(session(serverStops: [
        stops[0],
        _stop(1, lat: base + 300 * _degPerMeterLat, audioSeconds: 100,
            dwellSeconds: 60, startTime: '09:30'),
        stops[2],
      ]));
      expect(service.clockNotices, hasLength(1));
      expect(service.clockNotices.single, contains('12 minutes earlier'));
      expect(service.clockNotices.single, contains('keeps its own clock'));
      // ... and NOT corrected: the phone's re-timing is what it measured.
      expect(service.retimeRemaining().first.secondsToArrival, own);
      expect(service.phoneNextStopHhmm, '09:18');
      expect(service.tourElapsedSeconds, 600);
    });
  });
}
