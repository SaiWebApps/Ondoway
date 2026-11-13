// THE KILL CRITERION, measured ON the iOS Simulator, on both of the phone's
// paths. Two bars: (a) SELECT <= 1 s — from a position update
// (or the person's own tap) that crosses a trigger to the question on screen /
// the silent re-time applied and RENDERED, ten samples per persona trace, the
// worst case reported; (b) LIVE REPLAN <= 8 s — from the phone firing the replan
// to the new day held and rendered, phone-to-local-API from the simulator.
//
// The two persona days are real: generated and composed over the wire by
// w512_setup.py (FD — Fiona & Dev, Place Dauphine, open 180 min; RO — Rosemary,
// Orsay round trip, take-it-easy). The traces are scripted position streams
// through the same LocationProvider door the app uses (a test double), the clock
// is a fake so a band can be crossed on demand, and the walk screen renders each
// result. Run:
//   cd mobile && flutter test integration_test/w512_kill_criterion_test.dart \
//     -d <simulator udid> $(python w512_setup.py)   # the dart-defines
// Results are printed as lines starting "W512 ".

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

const _token = String.fromEnvironment('W512_TOKEN');
const _profile = String.fromEnvironment('W512_PROFILE');
const _tripFd = String.fromEnvironment('W512_TRIP_FD');
const _tripRo = String.fromEnvironment('W512_TRIP_RO');

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

int _secondsOfDay(String hhmm) =>
    (int.parse(hhmm.substring(0, 2)) * 60 + int.parse(hhmm.substring(3, 5))) * 60;

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('W5.12 SELECT and LIVE REPLAN on the simulator', (tester) async {
    expect(_token.isNotEmpty && _tripFd.isNotEmpty && _tripRo.isNotEmpty, isTrue,
        reason: 'run with the dart-defines w512_setup.py prints');
    final tripService = TripService();
    final saved = await tripService.fetchSavedTrips(_profile, _token);
    final report = <String>[];

    for (final (name, tripId) in [('FD', _tripFd), ('RO', _tripRo)]) {
      final trip = saved.firstWhere((t) => t.tripId == tripId);
      final session = await tripService.fetchSession(tripId, _token);
      // The day the person walks is the session's current day (its stops carry
      // the server's clocks); the trip's items are the same places.
      final stops = session.stops;
      expect(stops, isNotEmpty, reason: '$name has a day');
      final dayStart = _secondsOfDay(session.dayStartHhmm);
      report.add('W512 $name day: ${stops.length} stops, '
          '${session.contingencies.length} contingencies, day start '
          '${session.dayStartHhmm}, planned end ${session.plannedEndHhmm}');

      // ---- (a) SELECT: ten samples ------------------------------------------
      final selectMs = <String>[];
      final entries = session.contingencies;
      SessionContingency? firstOf(String kind, {String? stopId}) {
        for (final e in entries) {
          if (e.kind == kind && (stopId == null || e.triggerStopId == stopId)) {
            return e;
          }
        }
        return null;
      }

      Future<void> sample(
        String label,
        Future<void> Function(TourPlaybackService s, MockLocationService gps,
                _Clock clock)
            stimulus,
      ) async {
        final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
        final gps = MockLocationService();
        final service = TourPlaybackService(
          locationService: gps,
          audioService: MockAudioService(),
          now: () => clock.now,
        );
        await service.startTour(stops);
        service.holdSession(session);
        await tester.pumpWidget(_app(service, tripId));
        await tester.pump();
        final sw = Stopwatch()..start();
        await stimulus(service, gps, clock);
        // The measured moment: the divergence, the selection, the plan applied,
        // the frame rendered.
        final d = service.measure();
        final entry = service.matchContingency(d);
        final applied =
            entry != null && service.applyContingency(entry.contingencyId);
        await tester.pump();
        sw.stop();
        selectMs.add('$label=${sw.elapsedMilliseconds}ms'
            '${applied ? "" : " (NO ENTRY MATCHED)"}');
        await tester.pumpWidget(const SizedBox());
        service.stopTour();
      }

      // Wrap-up from each stop (the person's tap), then skips, then bands via
      // the clock, until ten samples.
      var n = 0;
      for (var k = 0; k < stops.length && n < 4; k++, n++) {
        await sample('wrap-up@$k', (s, gps, clock) async {
          if (k > 0) s.skipToStop(k);
          gps.simulatePosition(stops[k].lat, stops[k].lng);
          s.requestWrapUp();
        });
      }
      for (var k = 0; k + 1 < stops.length && n < 7; k++, n++) {
        await sample('skip@$k', (s, gps, clock) async {
          if (k > 0) s.skipToStop(k);
          gps.simulatePosition(stops[k].lat, stops[k].lng);
          s.skipToStop(k + 1); // stop k passed unplayed and unopened
        });
      }
      // Bands: stand at stop k, next stop k+1, and turn the clock so the
      // tour-frame arrival at k+1 sits inside the band the set holds from k.
      final bandKinds = ['running_late', 'running_early', 'minutes_left'];
      for (var k = 0; k + 1 < stops.length && n < 10; k++) {
        for (final kind in bandKinds) {
          if (n >= 10) break;
          final e = firstOf(kind, stopId: stops[k].poiId);
          if (e == null || e.bandMinutes == null) continue;
          final lo = e.bandMinutes![0];
          await sample('$kind[$lo]@$k', (s, gps, clock) async {
            if (k > 0) s.skipToStop(k);
            gps.simulatePosition(stops[k].lat, stops[k].lng);
            final eta = s.retimeRemaining().firstWhere(
                (x) => x.stop.poiId == stops[k + 1].poiId).secondsToArrival;
            final planned = _secondsOfDay(stops[k + 1].startTime) - dayStart;
            int tourElapsed;
            if (kind == 'running_late') {
              tourElapsed = planned - eta + (lo + 1) * 60;
            } else if (kind == 'running_early') {
              tourElapsed = planned - eta - (lo + 1) * 60;
            } else {
              final end = _secondsOfDay(session.plannedEndHhmm) - dayStart;
              tourElapsed = end - (lo + 1) * 60;
            }
            clock.now = DateTime(2026, 8, 19, 9, 0)
                .add(Duration(seconds: tourElapsed < 0 ? 0 : tourElapsed));
          });
          n++;
        }
      }
      // A short day has fewer distinct stimuli than ten (the RO one-stop
      // day has one): repeat the person's own tap until ten timings exist.
      var again = 0;
      while (n < 10) {
        await sample('wrap-up@0#${++again}', (s, gps, clock) async {
          gps.simulatePosition(stops[0].lat, stops[0].lng);
          s.requestWrapUp();
        });
        n++;
      }
      report.add('W512 $name SELECT samples: ${selectMs.join(", ")}');
      final worst = selectMs
          .map((s) => int.parse(RegExp(r'=(\d+)ms').firstMatch(s)!.group(1)!))
          .fold<int>(0, (a, b) => a > b ? a : b);
      report.add('W512 $name SELECT worst: ${worst}ms (bar 1000ms) '
          '${worst <= 1000 ? "UNDER" : "OVER"}');

      // ---- (b) LIVE REPLAN: three samples, phone-fire to new-day-rendered ---
      final replanMs = <int>[];
      for (var i = 0; i < 3; i++) {
        final clock = _Clock(DateTime(2026, 8, 19, 9, 0));
        final gps = MockLocationService();
        final service = TourPlaybackService(
          locationService: gps,
          audioService: MockAudioService(),
          now: () => clock.now,
        );
        await service.startTour(stops);
        service.holdSession(session);
        gps.simulatePosition(stops[0].lat, stops[0].lng);
        await tester.pumpWidget(_app(service, tripId));
        await tester.pump();
        clock.now = clock.now.add(const Duration(minutes: 15));
        final sw = Stopwatch()..start();
        final next = await tripService.replanSession(
          tripId,
          _token,
          lat: stops[0].lat,
          lng: stops[0].lng,
          wallElapsedSeconds: service.wallElapsedSeconds,
          tourElapsedSeconds: service.tourElapsedSeconds,
          observedPace: service.observedPace,
          listeningRate: service.listeningRate,
          nextStopIndex: 0,
          phoneNextStopHhmm: service.phoneNextStopHhmm,
        );
        service.holdSession(next);
        await tester.pump();
        sw.stop();
        replanMs.add(sw.elapsedMilliseconds);
        report.add('W512 $name REPLAN sample ${i + 1}: ${sw.elapsedMilliseconds}ms '
            '-> v${next.planVersion}, ${next.stops.length} stops, '
            '${next.contingencies.length} carried entries, notices '
            '${service.clockNotices.length}');
        await tester.pumpWidget(const SizedBox());
        service.stopTour();
        // Let the server land the full set before the next fire (a phone fires
        // minutes apart, not back to back).
        await Future<void>.delayed(const Duration(seconds: 12));
      }
      final worstReplan = replanMs.fold<int>(0, (a, b) => a > b ? a : b);
      report.add('W512 $name REPLAN worst: ${worstReplan}ms (bar 8000ms) '
          '${worstReplan <= 8000 ? "UNDER" : "OVER"}');
      // Keep the analyzer honest about the trip we fetched.
      expect(trip.tripId, tripId);
    }
    for (final line in report) {
      debugPrint(line);
    }
  }, timeout: const Timeout(Duration(minutes: 20)));
}
