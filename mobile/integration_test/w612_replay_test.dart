// Replays EACH PERSONA DAY ON ITS OWN. The days are generated and composed on
// the app path by w612_days.py and passed in at build time; this test opens
// each over the wire on the simulator, walks its opening stretch, then taps
// [Head back now] — the same one divergence for every day — printing a
// "W612 [name]" transcript line per moment to judge beside the composed text.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/tour_walk_page.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:ondoway/services/trip_service.dart';

import '../test/services/mocks/mock_audio_service.dart';
import '../test/services/mocks/mock_location_service.dart';

const _token = String.fromEnvironment('W612_TOKEN');
// "name:tripId,name:tripId,..."
const _days = String.fromEnvironment('W612_DAYS');

class _Voice extends MockAudioService {
  final List<String> said = [];
  final List<String> playedKeys = [];
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
  @override
  void play(String beatId, String audioUrl,
      {bool isDeeperDive = false, String? title}) {
    playedKeys.add(beatId);
    super.play(beatId, audioUrl, isDeeperDive: isDeeperDive, title: title);
  }
}

class _Clock {
  DateTime now;
  _Clock(this.now);
}

const GeneratedTrip _walkSeed = GeneratedTrip(
  tripId: 'trace',
  tripName: 'trace',
  profileId: 'trace',
  totalStops: 0,
  totalDurationMin: 0,
  anchorCount: 0,
  flavourCount: 0,
  stops: [],
);

Widget _app(TourPlaybackService service, String tripId) {
  final router = GoRouter(
    initialLocation: '/session/$tripId',
    routes: [
      GoRoute(
        path: '/session/:tripId',
        builder: (context, state) =>
            const TourWalkPage(trip: _walkSeed),
      ),
      GoRoute(
        path: '/saved-trips',
        builder: (context, state) =>
            const Scaffold(body: Center(child: Text('Saved Trips'))),
      ),
    ],
  );
  return ChangeNotifierProvider<TourPlaybackService>.value(
    value: service,
    child: MaterialApp.router(routerConfig: router),
  );
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('each persona on their own day', (tester) async {
    expect(_token.isNotEmpty, isTrue);
    expect(_days.isNotEmpty, isTrue);
    final tripService = TripService();
    final lines = <String>[];

    for (final pair in _days.split(',')) {
      final name = pair.split(':')[0];
      final tripId = pair.split(':')[1];
      SessionPlan session;
      try {
        session = await tripService.fetchSession(tripId, _token);
      } catch (e) {
        lines.add('W612 [$name] NO SESSION: $e');
        continue;
      }
      final stops = [
        for (final s in session.stops)
          s.beatId == null ? s : s.copyWith(audioUrl: 'demo://${s.stopId}'),
      ];
      final parts = session.dayStartHhmm.split(':');
      final clock = _Clock(DateTime(2026, 8, 19,
          int.parse(parts[0]), int.parse(parts[1])));
      final gps = MockLocationService();
      final voice = _Voice();
      final s = TourPlaybackService(
        locationService: gps, audioService: voice, now: () => clock.now,
      );
      await s.startTour(stops);
      s.holdSession(session);
      await tester.pumpWidget(_app(s, tripId));
      await tester.pump();

      lines.add('W612 [$name] DAY v${session.planVersion} '
          '${session.dayStartHhmm}-${session.plannedEndHhmm} party=${session.party}: '
          '${stops.map((x) => "${x.poiName} ${x.startTime}").join(" -> ")} '
          '| closes ${stops.where((x) => (x.closeText ?? "").isNotEmpty).length}/${stops.length}'
          '${stops.every((x) => (x.beatId == null) || x.closeAudioUrl != null) ? " (all voiced)" : ""} '
          '| threads ${stops.where((x) => x.threadLines?.isNotEmpty ?? false).length} '
          '| fulls ${stops.where((x) => (x.fullNarration ?? "").isNotEmpty).length} '
          '| entries ${session.contingencies.length} '
          '(${session.contingencies.where((c) => c.question != null).length} with the question) '
          '| skip tails: ${session.contingencies.where((c) => c.kind == "stop_skipped").map((c) => c.stopIds.length).join("/")}');

      // Walk the opening stretch: arrive stop 0 at its clock, the piece plays
      // and ends on its own; settle.
      if (stops.isNotEmpty) {
        gps.simulatePosition(stops[0].lat, stops[0].lng);
        s.tick();
        await tester.pump();
        voice.simulateComplete();
        clock.now = clock.now.add(const Duration(seconds: 40));
        s.tick();
        await tester.pump();
        lines.add('W612 [$name] after the first stretch: at=${s.currentStop?.poiName} '
            'offer="${s.fullTellingOffer ?? ""}" spoken=${voice.said.length} '
            'played=${voice.playedKeys.join(",")}');
      }

      // The one divergence: the wrap-up tap, 12 minutes per stop into the day.
      final twoThirds = Duration(minutes: (session.stops.length * 12));
      clock.now = clock.now.add(twoThirds);
      s.requestWrapUp();
      final wrap = s.matchContingency(s.measure());
      if (wrap != null) {
        s.applyContingency(wrap.contingencyId);
        clock.now = clock.now.add(const Duration(seconds: 35));
        s.tick();
        await tester.pump();
        lines.add('W612 [$name] WRAP: close line "${s.closeLine ?? ""}" '
            '| session line "${s.screenText ?? ""}" '
            '| played=${voice.playedKeys.join(",")} | spoken=${voice.said.join(" || ")}');
      } else {
        lines.add('W612 [$name] WRAP: no entry matched (day state: ${s.state.name})');
      }
      debugPrint('W612 MOMENT $name done');
      await Future<void>.delayed(const Duration(seconds: 4));
      await tester.pumpWidget(const SizedBox());
      s.stopTour();
    }

    for (final l in lines) {
      debugPrint(l);
    }
  }, timeout: const Timeout(Duration(minutes: 25)));
}
