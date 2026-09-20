@Tags(['vm'])
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/tour_walk_page.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:provider/provider.dart';

import '../services/mocks/mock_audio_service.dart';
import '../services/mocks/mock_location_service.dart';
import '../support/trip_fixture.dart';

Widget _harness({
  required MockLocationService loc,
  required MockAudioService audio,
  required TourPlaybackService engine,
  required Widget child,
}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<LocationProvider>.value(value: loc),
      ChangeNotifierProvider<AudioProvider>.value(value: audio),
      ChangeNotifierProvider<TourPlaybackService>.value(value: engine),
    ],
    child: MaterialApp(home: child),
  );
}

// The walk screen carries a one-second HEARTBEAT: standing still is measured by
// time passing, because the geolocator sends no fix while nobody moves. A
// repeating timer means this tree never "settles" — pumpAndSettle advances the
// fake clock and the ticker fires again, forever — so these tests pump explicit
// frames instead. That is a property of the screen, not a workaround.
// ---------------------------------------------------------------------------
// THE SESSION SURFACE — the question, the offers, the way home.
//
// These four came from mobile/test/pages/session_page_test.dart, which was
// deleted on 2026-08-31 along with the screen it tested. SessionPage rendered
// this whole surface and NOTHING NAVIGATED TO IT; TourWalkPage is the screen the
// itinerary actually pushes, so the surface moved here and its coverage had to
// come with it. Every `Key('session-…')` survived the move verbatim, which is
// what makes three of these a faithful port rather than a rewrite.
//
// The fourth is a rewrite, deliberately. SessionPage answered an unstarted walk
// with 'No walk is running.' and no controls; this screen answers with
// 'Preparing your walk…' and STILL carries the way home, because [Head back now]
// belongs on every screen of the walk (W6.2 R8 / Nadia, R1.1). The old
// assertion contradicts the new design, so it is not ported — the test derives
// from the design that shipped.

const double _degPerMeterLat = 1.0 / 111320.0;
const double _base = 48.85;

/// A seed for the page's constructor and nothing more. Each test starts the
/// service itself with the stops it needs, so the page finds a walk already
/// running and never uses this — see `_startedHere` in the screen.
const GeneratedTrip _walkSeed = GeneratedTrip(
  tripId: 'trip-1',
  tripName: 'Session surface',
  profileId: 'debug',
  totalStops: 0,
  totalDurationMin: 0,
  anchorCount: 0,
  flavourCount: 0,
  stops: [],
);

ItineraryStop _sessionStop(int n, {required double lat}) => ItineraryStop(
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
      // S7.3: the footprint is STATED on each stop, as the wire carries it.
      trigger: const StopTrigger(radiusM: 40),
    );

const _question =
    'Keep your bench and be at Gare du Nord about 15:20, or sit 4 minutes and be there by 15:12?';

/// A GoRouter harness, because [Head back now] navigates when no wrap-up entry
/// matches. The plain MaterialApp harness above is enough for the tests that
/// never reach that branch.
Widget _routerHarness({
  required MockLocationService loc,
  required MockAudioService audio,
  required TourPlaybackService engine,
  GeneratedTrip trip = _walkSeed,
}) {
  final router = GoRouter(
    initialLocation: '/walk',
    routes: [
      GoRoute(path: '/walk', builder: (_, _) => TourWalkPage(trip: trip)),
      GoRoute(
        path: '/saved-trips',
        builder: (_, _) => const Scaffold(body: Center(child: Text('Saved Trips'))),
      ),
    ],
  );
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<LocationProvider>.value(value: loc),
      ChangeNotifierProvider<AudioProvider>.value(value: audio),
      ChangeNotifierProvider<TourPlaybackService>.value(value: engine),
    ],
    child: MaterialApp.router(routerConfig: router),
  );
}

class _FixedTokenAuthService extends AuthService {
  final String? _fixedToken;
  _FixedTokenAuthService(this._fixedToken);
  @override
  String? get accessToken => _fixedToken;
}

class _MockTripService extends TripService {
  SessionPlan? replanResult;
  String? capturedClosedStopId;
  List<String>? capturedKeptStopIds;

  @override
  Future<SessionPlan> replanSession(
    String tripId,
    String accessToken, {
    required double lat,
    required double lng,
    required int wallElapsedSeconds,
    required int tourElapsedSeconds,
    double? observedPace,
    double? listeningRate,
    int nextStopIndex = 0,
    String? phoneNextStopHhmm,
    String? closedStopId,
    List<String>? keptStopIds,
  }) async {
    capturedClosedStopId = closedStopId;
    capturedKeptStopIds = keptStopIds;
    return replanResult!;
  }
}

Widget _closedDoorHarness({
  required MockLocationService loc,
  required MockAudioService audio,
  required TourPlaybackService engine,
  required AuthService auth,
  required TripService tripService,
}) {
  final router = GoRouter(
    initialLocation: '/walk',
    routes: [
      GoRoute(path: '/walk', builder: (_, _) => TourWalkPage(trip: _walkSeed)),
      GoRoute(
        path: '/saved-trips',
        builder: (_, _) =>
            const Scaffold(body: Center(child: Text('Saved Trips'))),
      ),
    ],
  );
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<LocationProvider>.value(value: loc),
      ChangeNotifierProvider<AudioProvider>.value(value: audio),
      ChangeNotifierProvider<TourPlaybackService>.value(value: engine),
      ChangeNotifierProvider<AuthService>.value(value: auth),
      ChangeNotifierProvider<TripService>.value(value: tripService),
    ],
    child: MaterialApp.router(routerConfig: router),
  );
}

void main() {
  testWidgets('walking state shows the next-stop banner; arriving plays audio and shows the story card',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
      loc: loc, audio: audio, engine: engine,
      child: TourWalkPage(trip: trip),
    ));
    await tester.pump(const Duration(milliseconds: 350)); // let startTour() settle

    // Walking: heading to the first stop, no audio yet -> the walking bar shows.
    expect(find.byKey(const Key('tour-walking-skip')), findsOneWidget);
    expect(audio.playCount, 0);

    // Walk into the first stop's geofence.
    loc.simulatePosition(48.8606, 2.3376);
    await tester.pump(const Duration(milliseconds: 350));

    expect(audio.playCount, 1); // engine auto-played the stop
    // Story: the now-playing player shows the stop title + transport controls.
    expect(find.textContaining('The Louvre'), findsWidgets);
    expect(find.byKey(const Key('tour-playpause')), findsOneWidget);
  });

  testWidgets('Skip advances to the next stop', (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
      loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    // Arrive at stop 0 so the audio card (with Skip) is showing.
    loc.simulatePosition(48.8606, 2.3376);
    await tester.pump(const Duration(milliseconds: 350));
    expect(engine.currentStopIndex, 0);

    await tester.tap(find.byKey(const Key('tour-skip')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(engine.currentStopIndex, 1);
  });

  testWidgets('approaching the next stop shows the nudge; Play now accepts it',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    loc.simulatePosition(48.8606, 2.3376); // arrive stop 0 -> audio plays
    await tester.pump(const Duration(milliseconds: 350));
    // Walk into stop 1's radius while stop 0 audio still "plays".
    loc.simulatePosition(48.8570, 2.3410);
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.hasPendingStop, true);
    expect(find.byKey(const Key('tour-nudge-accept')), findsOneWidget);

    await tester.tap(find.byKey(const Key('tour-nudge-accept')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(engine.currentStopIndex, 1);
  });

  testWidgets('completed tour shows the done panel', (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    // Jump to the final stop, play it, then complete -> engine goes `completed`.
    engine.skipToStop(2);
    await tester.pump(const Duration(milliseconds: 350));
    audio.simulateComplete();
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.state, TourState.completed);
    expect(find.textContaining('Tour complete'), findsOneWidget);
  });

  testWidgets('walking state has a manual Skip that advances without a geofence hit',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    // Still walking: no GPS fix yet, no audio playing.
    expect(engine.currentStopIndex, 0);
    expect(audio.isPlaying, false);

    await tester.tap(find.byKey(const Key('tour-walking-skip')));
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.currentStopIndex, 1);
  });

  testWidgets('a stop with no audio shows "No audio for this stop" alongside Skip',
      (tester) async {
    const noAudioStop = ItineraryStop(
      sortOrder: 0,
      poiId: 'poi-no-audio',
      poiName: 'Silent Courtyard',
      lat: 48.8606,
      lng: 2.3376,
      beatId: 'beat-no-audio',
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 5,
      importanceTier: 1,
      startTime: '10:00',
      audioUrl: null,
    );
    final trip = GeneratedTrip(
      tripId: 'trip-no-audio',
      tripName: 'No Audio Trip',
      profileId: 'profile-1',
      totalStops: 1,
      totalDurationMin: 5,
      anchorCount: 1,
      flavourCount: 1,
      stops: const [noAudioStop],
    );
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.textContaining('No audio here'), findsOneWidget);
    expect(find.byKey(const Key('tour-walking-skip')), findsOneWidget);
  });

  testWidgets('dismissing the nudge clears hasPendingStop and hides the nudge',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    loc.simulatePosition(48.8606, 2.3376); // arrive stop 0 -> audio plays
    await tester.pump(const Duration(milliseconds: 350));
    // Walk into stop 1's radius while stop 0 audio still "plays".
    loc.simulatePosition(48.8570, 2.3410);
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.hasPendingStop, true);

    await tester.tap(find.text('Keep listening'));
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.hasPendingStop, false);
    expect(find.byKey(const Key('tour-nudge-accept')), findsNothing);
  });

  // W5.2 R2.5: the question on screen ALWAYS as two big buttons with the default
  // marked. R4 "what the screen shows": one or two lines — next stop and minutes,
  // the finish clock — and the way home; NEVER a pause length, a count, "you
  // paused", "behind by N", a stopwatch.
  testWidgets(
      'the question is two big buttons with the default named in words; the way '
      'home is on the screen; the lines carry no pause count or stopwatch',
      (tester) async {
    var now = DateTime(2026, 8, 19, 9, 0);
    final gps = MockLocationService();
    final audio = MockAudioService();
    final stops = [
      _sessionStop(0, lat: _base),
      _sessionStop(1, lat: _base + 300 * _degPerMeterLat),
      _sessionStop(2, lat: _base + 600 * _degPerMeterLat),
    ];
    final service = TourPlaybackService(
      locationService: gps,
      audioService: audio,
      now: () => now,
    );
    await service.startTour(stops);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: stops,
      promises: const [
        SessionPromise(
            promiseId: 'finish',
            kind: 'finish',
            name: 'Gare du Nord',
            arrivesHhmm: '15:20',
            protected: true),
      ],
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
      // The finish is Stop 2 itself (a round trip back to it), 600 m north.
      finishLat: _base + 600 * _degPerMeterLat,
      finishLng: 2.35,
      finishName: 'Gare du Nord',
      contingencies: const [
        SessionContingency(
          contingencyId: 'v1-q',
          trigger: {'kind': 'promise_at_risk', 'stop_id': 'poi-1'},
          planVersion: 1,
          stopIds: ['poi-1', 'poi-2'],
          screenText: _question,
          question: _question,
          defaultArm: 'keep',
          alternateStopIds: ['poi-2'],
          finishHhmm: '15:20',
        ),
        SessionContingency(
          contingencyId: 'v1-w0',
          trigger: {'kind': 'wrap_up_from', 'stop_id': 'poi-0'},
          planVersion: 1,
          stopIds: [],
          screenText: 'Straight to Gare du Nord · Gare du Nord about 14:40',
          finishHhmm: '14:40',
        ),
      ],
    ));
    gps.simulatePosition(_base - 25 * _degPerMeterLat, 2.35);
    service.pauseTour();
    now = now.add(const Duration(minutes: 3));
    service.resumeTour();
    expect(service.applyContingency('v1-q'), isTrue);

    await tester.pumpWidget(
        _routerHarness(loc: gps, audio: audio, engine: service));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.byKey(const Key('session-next-line')), findsOneWidget);
    // The finish clock is the PHONE's re-timing (W5.13), not a stale server line.
    final finishLine =
        tester.widget<Text>(find.byKey(const Key('session-finish-line')));
    expect(finishLine.data, startsWith('Gare du Nord by '));
    expect(finishLine.data, isNot('Gare du Nord by 15:20'));
    expect(find.byKey(const Key('session-question')), findsOneWidget);
    expect(find.byKey(const Key('session-arm-keep')), findsOneWidget);
    expect(find.byKey(const Key('session-arm-shorten')), findsOneWidget);
    expect(
      find.textContaining('this happens if you do nothing'),
      findsOneWidget,
      reason: 'the default is named in words (R2.5)',
    );
    expect(find.byKey(const Key('session-head-back')), findsOneWidget);
    // Nothing on the screen names the pause, a count or a stopwatch (R4). The
    // player's own clock is m:ss, so it cannot trip the hh:mm:ss check.
    for (final t in find.byType(Text).evaluate()) {
      final data = (t.widget as Text).data ?? '';
      if (data == 'Pause' || data == 'Carry on') continue; // the control itself
      expect(data.toLowerCase(), isNot(contains('paus')));
      expect(data.toLowerCase(), isNot(contains('behind by')));
      expect(data, isNot(matches(RegExp(r'\d\d:\d\d:\d\d'))));
    }
    // Answering: the shorten arm applies the alternate — the screen decided nothing.
    await tester.tap(find.byKey(const Key('session-arm-shorten')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(service.pendingQuestion, isNull);
    expect(find.byKey(const Key('session-arm-keep')), findsNothing);
    expect(service.nextStop?.poiId, 'poi-2');
    // The way home: the wrap-up entry the server precomputed for this stop.
    await tester.tap(find.byKey(const Key('session-head-back')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(service.selectedContingency?.contingencyId, 'v1-w0');
    expect(find.text('Straight to Gare du Nord · Gare du Nord about 14:40'),
        findsOneWidget);
    // Straight home: the phone's finish clock moves earlier than with the stops.
    final after =
        tester.widget<Text>(find.byKey(const Key('session-finish-line')));
    expect(after.data, startsWith('Gare du Nord by '));
    expect(after.data!.compareTo(finishLine.data!) < 0, isTrue,
        reason: 'wrap-up: ${after.data} should be earlier than ${finishLine.data}');
    await tester.pumpWidget(const SizedBox());
  });

  // Phase 7 S7.5 (design §5.6; W7.2 R2 — Fiona & Dev, Nadia): at a stop whose
  // arrival hour prices a line, a couple's piece waits for a TAP; the screen
  // carries the offer and the tap plays it. The screen decides nothing.
  testWidgets('at a queued stop a couple sees the offer and the tap plays the piece',
      (tester) async {
    var now = DateTime(2026, 8, 19, 9, 0);
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: audio,
      now: () => now,
    );
    final queued = [
      ItineraryStop(
        sortOrder: 0,
        stopId: 'item-0',
        poiId: 'poi-0',
        poiName: 'Sainte-Chapelle',
        lat: _base,
        lng: 2.35,
        beatId: 'beat-0',
        lensName: 'history',
        lensDisplay: 'History',
        durationMin: 5,
        importanceTier: 5,
        startTime: '',
        audioUrl: 'https://cdn.example.com/0.mp3',
        audioDurationSec: 100,
        dwellSeconds: 300,
        trigger: const StopTrigger(radiusM: 30, queueSeconds: 28 * 60),
      ),
    ];
    await service.startTour(queued);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: queued,
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
      party: 'couple',
      placement: const PlacementPolicy(ownPlaceM: 60, queuePiece: 'tap'),
    ));
    gps.simulatePosition(_base + 10 * _degPerMeterLat, 2.35); // in the line
    now = now.add(const Duration(seconds: 40));
    service.tick();
    expect(audio.isPlaying, isFalse);

    await tester.pumpWidget(
        _routerHarness(loc: gps, audio: audio, engine: service));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.byKey(const Key('session-armed-offer')), findsOneWidget);
    expect(find.textContaining('Sainte-Chapelle'), findsWidgets);
    await tester.tap(find.byKey(const Key('session-armed-offer')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(audio.isPlaying, isTrue);
    expect(find.byKey(const Key('session-armed-offer')), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): under a roof a chapter is
  // offered on the screen — the tap plays it; the screen renders the offer the
  // service holds and decides nothing.
  testWidgets('an indoor chapter is offered on the screen and the tap plays it',
      (tester) async {
    var now = DateTime(2026, 8, 19, 9, 0);
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service = TourPlaybackService(
      locationService: gps,
      audioService: audio,
      now: () => now,
    );
    final marquee = [
      ItineraryStop(
        sortOrder: 0,
        stopId: 'item-0',
        poiId: 'nd',
        poiName: 'Notre-Dame Cathedral',
        lat: _base,
        lng: 2.35,
        beatId: 'beat-0',
        lensName: 'history',
        lensDisplay: 'History',
        durationMin: 25,
        importanceTier: 5,
        startTime: '',
        audioUrl: 'https://cdn.example.com/0.mp3',
        audioDurationSec: 100,
        dwellSeconds: 1500,
        trigger: const StopTrigger(radiusM: 100),
        segments: const [
          StopSegment(
              label: 'Inside',
              lat: _base,
              lng: 2.35,
              radiusM: 60,
              indoor: true,
              narration: 'The nave.',
              audioUrl: 'https://cdn.example.com/inside.mp3'),
        ],
      ),
    ];
    await service.startTour(marquee);
    gps.simulatePosition(_base - 200 * _degPerMeterLat, 2.35);
    gps.simulatePosition(_base, 2.35); // arrive: the story
    audio.simulateComplete();
    now = now.add(const Duration(seconds: 40));
    service.tick();
    expect(audio.isPlaying, isFalse,
        reason: 'under a roof nothing starts itself');
    // The single stop's piece ended, so the itinerary is finished — and the
    // chapter under the roof is STILL unheard. This is the case that used to
    // vanish behind the completion panel.
    expect(service.state, TourState.completed);

    await tester.pumpWidget(
        _routerHarness(loc: gps, audio: audio, engine: service));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.byKey(const Key('session-chapter-offer')), findsOneWidget);
    expect(find.textContaining('Inside'), findsWidgets);
    await tester.tap(find.byKey(const Key('session-chapter-offer')));
    await tester.pump(const Duration(milliseconds: 350));
    expect(audio.currentBeatId, 'item-0-seg-0');
    expect(find.byKey(const Key('session-chapter-offer')), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  // NOT a port. SessionPage said 'No walk is running.' and offered nothing; this
  // screen says it is getting ready AND still carries the way home, because that
  // control belongs on every screen of the walk (Nadia, R1.1).
  testWidgets('with no walk running the screen says so and still offers the way home',
      (tester) async {
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service =
        TourPlaybackService(locationService: gps, audioService: audio);

    await tester.pumpWidget(
        _routerHarness(loc: gps, audio: audio, engine: service));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.text('Preparing your walk…'), findsOneWidget);
    expect(find.byKey(const Key('session-head-back')), findsOneWidget);
    expect(find.byKey(const Key('session-question')), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('the closed-door button shows at a door stop and hides at a non-door stop',
      (tester) async {
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service =
        TourPlaybackService(locationService: gps, audioService: audio);
    final stops = [
      ItineraryStop(
        sortOrder: 0,
        stopId: 'item-0',
        poiId: 'poi-0',
        poiName: 'Musée Carnavalet',
        lat: _base,
        lng: 2.35,
        beatId: 'beat-0',
        lensName: 'history',
        lensDisplay: 'History',
        durationMin: 5,
        importanceTier: 3,
        startTime: '',
        audioUrl: 'https://cdn.example.com/0.mp3',
        audioDurationSec: 100,
        dwellSeconds: 300,
        trigger: const StopTrigger(radiusM: 40, door: true),
      ),
      _sessionStop(1, lat: _base + 300 * _degPerMeterLat),
    ];
    await service.startTour(stops);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: stops,
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
    ));

    await tester.pumpWidget(
        _routerHarness(loc: gps, audio: audio, engine: service));
    await tester.pump(const Duration(milliseconds: 350));

    expect(find.byKey(const Key('session-closed-report')), findsOneWidget,
        reason: 'a door stop shows the closed-report button');

    service.skipToStop(1);
    await tester.pump(const Duration(milliseconds: 350));
    expect(find.byKey(const Key('session-closed-report')), findsNothing,
        reason: 'a non-door stop hides the closed-report button');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('tapping the closed-door button sends the stop poiId to replanSession',
      (tester) async {
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service =
        TourPlaybackService(locationService: gps, audioService: audio);
    final doorStop = ItineraryStop(
      sortOrder: 0,
      stopId: 'item-0',
      poiId: 'musee-carnavalet',
      poiName: 'Musée Carnavalet',
      lat: _base,
      lng: 2.35,
      beatId: 'beat-0',
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 5,
      importanceTier: 3,
      startTime: '',
      audioUrl: 'https://cdn.example.com/0.mp3',
      audioDurationSec: 100,
      dwellSeconds: 300,
      trigger: const StopTrigger(radiusM: 40, door: true),
    );
    final nextStop = _sessionStop(1, lat: _base + 300 * _degPerMeterLat);
    await service.startTour([doorStop, nextStop]);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: [doorStop, nextStop],
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
    ));

    final auth = _FixedTokenAuthService('tok');
    final trips = _MockTripService()
      ..replanResult = SessionPlan(
        tripId: 'trip-1',
        planVersion: 2,
        stops: [nextStop],
        retimeToleranceSeconds: 180,
        dayStartHhmm: '09:00',
      );

    await tester.pumpWidget(_closedDoorHarness(
      loc: gps,
      audio: audio,
      engine: service,
      auth: auth,
      tripService: trips,
    ));
    await tester.pump(const Duration(milliseconds: 350));

    await tester.tap(find.byKey(const Key('session-closed-report')));
    await tester.pump(const Duration(milliseconds: 350));

    expect(trips.capturedClosedStopId, 'musee-carnavalet');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('after a closed-door replan the shut stop leaves plannedStops',
      (tester) async {
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service =
        TourPlaybackService(locationService: gps, audioService: audio);
    final doorStop = ItineraryStop(
      sortOrder: 0,
      stopId: 'item-0',
      poiId: 'musee-closed',
      poiName: 'Closed Museum',
      lat: _base,
      lng: 2.35,
      beatId: 'beat-0',
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 5,
      importanceTier: 3,
      startTime: '',
      audioUrl: 'https://cdn.example.com/0.mp3',
      audioDurationSec: 100,
      dwellSeconds: 300,
      trigger: const StopTrigger(radiusM: 40, door: true),
    );
    final nextStop = _sessionStop(1, lat: _base + 300 * _degPerMeterLat);
    await service.startTour([doorStop, nextStop]);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: [doorStop, nextStop],
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
    ));

    final auth = _FixedTokenAuthService('tok');
    final trips = _MockTripService()
      ..replanResult = SessionPlan(
        tripId: 'trip-1',
        planVersion: 2,
        stops: [nextStop],
        retimeToleranceSeconds: 180,
        dayStartHhmm: '09:00',
      );

    await tester.pumpWidget(_closedDoorHarness(
      loc: gps,
      audio: audio,
      engine: service,
      auth: auth,
      tripService: trips,
    ));
    await tester.pump(const Duration(milliseconds: 350));

    expect(service.plannedStops.length, 2);

    await tester.tap(find.byKey(const Key('session-closed-report')));
    await tester.pump(const Duration(milliseconds: 350));

    expect(service.plannedStops.length, 1);
    expect(service.plannedStops.first.poiId, 'poi-1');
    await tester.pumpWidget(const SizedBox());
  });

  // M6d (Docs/adr/0006 rule 4): the server has already worked out what to offer
  // at a guessed door, so the tap SELECTS the answer the phone is holding — two
  // arms, at once, offline — instead of making the walker wait on a round trip
  // while they stand at a locked door.
  testWidgets('a shut door with a standby held asks the two-arm question without '
      'going to the server, and the keep arm walks to the standby', (tester) async {
    final gps = MockLocationService();
    final audio = MockAudioService();
    final service =
        TourPlaybackService(locationService: gps, audioService: audio);
    final doorStop = ItineraryStop(
      sortOrder: 0,
      stopId: 'item-0',
      poiId: 'orangerie',
      poiName: 'the Orangerie',
      lat: _base,
      lng: 2.35,
      beatId: 'beat-0',
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 5,
      importanceTier: 3,
      startTime: '',
      audioUrl: 'https://cdn.example.com/0.mp3',
      audioDurationSec: 100,
      dwellSeconds: 300,
      trigger: const StopTrigger(radiusM: 40, door: true),
    );
    final nextStop = _sessionStop(1, lat: _base + 300 * _degPerMeterLat);
    const standby = ItineraryStop(
      sortOrder: 9,
      stopId: 'item-9',
      poiId: 'cluny',
      poiName: 'the Cluny',
      lat: _base + 120 * _degPerMeterLat,
      lng: 2.35,
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 10,
      importanceTier: 3,
      startTime: '',
      audioUrl: 'https://cdn.example.com/9.mp3',
      narration: 'The Cluny keeps its Roman baths.',
    );
    const doorQuestion =
        'Keep the day going at the Cluny and be at the Orsay about 15:38, '
        'or carry on without it and be at the Orsay by 15:28?';

    await service.startTour([doorStop, nextStop]);
    service.holdSession(SessionPlan(
      tripId: 'trip-1',
      planVersion: 1,
      stops: [doorStop, nextStop],
      standbys: const [standby],
      retimeToleranceSeconds: 180,
      dayStartHhmm: '09:00',
      contingencies: const [
        SessionContingency(
          contingencyId: 'v1-door',
          trigger: {'kind': 'door_closed', 'stop_id': 'orangerie'},
          planVersion: 1,
          stopIds: ['cluny', 'poi-1'],
          screenText: doorQuestion,
          question: doorQuestion,
          defaultArm: 'keep',
          alternateStopIds: ['poi-1'],
        ),
      ],
    ));

    final trips = _MockTripService()
      ..replanResult = SessionPlan(
        tripId: 'trip-1',
        planVersion: 2,
        stops: [standby, nextStop],
        retimeToleranceSeconds: 180,
        dayStartHhmm: '09:00',
      );
    await tester.pumpWidget(_closedDoorHarness(
      loc: gps,
      audio: audio,
      engine: service,
      auth: _FixedTokenAuthService('tok'),
      tripService: trips,
    ));
    await tester.pump(const Duration(milliseconds: 350));

    await tester.tap(find.byKey(const Key('session-closed-report')));
    await tester.pump(const Duration(milliseconds: 350));

    expect(trips.capturedClosedStopId, isNull,
        reason: 'the answer was already held — no round trip at a locked door');
    expect(find.byKey(const Key('session-question')), findsOneWidget);
    expect(find.byKey(const Key('session-arm-keep')), findsOneWidget);
    expect(find.byKey(const Key('session-arm-shorten')), findsOneWidget);
    expect(find.textContaining('the Cluny'), findsWidgets);

    // The keep arm walks to the standby; the shut door leaves either way.
    await tester.tap(find.byKey(const Key('session-arm-keep')));
    await tester.pump(const Duration(milliseconds: 350));
    final walked = service.plannedStops.map((s) => s.poiId).toList();
    expect(walked, contains('cluny'));
    expect(walked, isNot(contains('orangerie')));

    // The answer, once given, REACHES the server: the shut door is reported so
    // the guess is marked and the stored day drops it, and the standby the
    // walker chose is named so the server seats it in the day it stores.
    expect(trips.capturedClosedStopId, 'orangerie',
        reason: 'the report rides after the answer, never before it');
    expect(trips.capturedKeptStopIds, ['cluny', 'poi-1']);
    expect(service.session?.planVersion, 2,
        reason: 'the server\'s version of the walked day is held');
    await tester.pumpWidget(const SizedBox());
  });
}
