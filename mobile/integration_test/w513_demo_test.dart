// The demo "the walk that noticed", on the iOS
// Simulator. Both persona traces replayed as scripted position streams and a
// scripted clock through the app's own doors (LocationProvider / AudioProvider
// test doubles; the real TripService over the wire for the live replan), the
// walk screen rendering every moment, and a verbatim transcript printed as
// "W513 " lines (the host writes it to a file).
// Screenshots: the host captures the simulator every 2 s; each moment pauses
// six seconds after printing its marker so a frame lands on it.
//
// The days are the ones the planner GIVES these personas today (generated and
// composed on the app path by w512_setup.py): FD — Fiona & Dev, Place Dauphine,
// couple, 180 min from 15:00; RO — Rosemary, Orsay round trip, take-it-easy,
// art lens (ONE stop under her preset's 12-minute cap); RO13 — the same request
// at a 13-minute leg length (Orangerie, her bench, the Orsay). The
// audio URLs are stand-ins so the doubles can "play" a piece; narration is not
// the subject.

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
const _tripFd = String.fromEnvironment('W512_TRIP_FD');
const _tripRo = String.fromEnvironment('W512_TRIP_RO');
const _tripRo13 = String.fromEnvironment('W512_TRIP_RO13');

class _Voice extends MockAudioService {
  final List<String> said = [];
  @override
  Future<void> speak(String sentence) async => said.add(sentence);
}

class _Clock {
  DateTime now;
  _Clock(this.now);
  void setHhmm(int h, int m) => now = DateTime(2026, 8, 19, h, m);
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
    initialLocation: '/trip/$tripId/walk',
    routes: [
      GoRoute(
        path: '/trip/:tripId/walk',
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

class _Trace {
  final String name;
  final String tripId;
  final SessionPlan session;
  final List<ItineraryStop> stops;
  final TourPlaybackService service;
  final _Voice voice;
  final MockLocationService gps;
  final _Clock clock;
  final List<String> lines = [];
  _Trace(this.name, this.tripId, this.session, this.stops, this.service,
      this.voice, this.gps, this.clock);
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('D6 — the walk that noticed', (tester) async {
    expect(_token.isNotEmpty, isTrue, reason: 'run with the dart-defines');
    final tripService = TripService();

    Future<_Trace> open(String name, String tripId, int h, int m) async {
      final session = await tripService.fetchSession(tripId, _token);
      // Stand-in audio so the doubles can play and complete a piece.
      final stops = [
        for (final s in session.stops)
          s.beatId == null ? s : s.copyWith(audioUrl: 'demo://${s.stopId}'),
      ];
      final clock = _Clock(DateTime(2026, 8, 19, h, m));
      final gps = MockLocationService();
      final voice = _Voice();
      final service = TourPlaybackService(
        locationService: gps,
        audioService: voice,
        now: () => clock.now,
      );
      await service.startTour(stops);
      service.holdSession(session);
      await tester.pumpWidget(_app(service, tripId));
      await tester.pump();
      final t = _Trace(name, tripId, session, stops, service, voice, gps, clock);
      t.lines.add('W513 [$name] DAY v${session.planVersion}: '
          '${stops.map((s) => "${s.poiName} ${s.startTime} (${s.plannedVisitSeconds ~/ 60} min)").join(" -> ")}; '
          'day ${session.dayStartHhmm}-${session.plannedEndHhmm}; party ${session.party}; '
          'promises: ${session.promises.map((p) => "${p.kind} ${p.name} ${p.arrivesHhmm}${p.protected ? " (protected)" : ""}").join(", ")}; '
          '${session.contingencies.length} contingencies, '
          '${session.contingencies.where((c) => c.question != null).length} with a question');
      return t;
    }

    String hhmm(DateTime d) =>
        '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

    Future<void> moment(_Trace t, String label, {bool pause = true}) async {
      final s = t.service;
      s.tick();
      await tester.pump();
      final etas = s.retimeRemaining();
      final line = StringBuffer('W513 [${t.name}] ${hhmm(t.clock.now)} '
          '(tour ${(s.tourElapsedSeconds ~/ 60).toString().padLeft(3)} min, wall ${(s.wallElapsedSeconds ~/ 60).toString().padLeft(3)} min) '
          '$label | state=${s.state.name} at=${s.currentStop?.poiName ?? "-"} '
          '| SCREEN: "${TourWalkPage.nextLineFor(s) ?? ""}" / "${TourWalkPage.finishLineFor(s) ?? ""}"'
          '${s.screenOnly ? " / [caption: screen-only, tap the speaker]" : ""}'
          '${s.finishMovedLine != null ? ' / [line: ${s.finishMovedLine}]' : ""} '
          '| session line: "${s.screenText ?? ""}" '
          '| question: ${s.pendingQuestion == null ? "none" : '"${s.pendingQuestion}"'} '
          '| spoken so far: ${t.voice.said.length} '
          '| entry: ${s.selectedContingency == null ? "-" : "${s.selectedContingency!.contingencyId} ${s.selectedContingency!.kind}"} '
          '| notices: ${s.clockNotices.length} '
          '| screen-only: ${s.screenOnly} '
          '| eta: ${etas.map((e) => "${e.stop.poiName}@${s.dayFrameHhmm(e.secondsToArrival)}").join(", ")}');
      t.lines.add(line.toString());
      // The marker the host matches a screenshot to.
      debugPrint('W513 MOMENT ${t.name} ${label.replaceAll(' ', '_')} ${DateTime.now().toIso8601String()}');
      if (pause) await Future<void>.delayed(const Duration(seconds: 6));
    }

    Future<void> close(_Trace t) async {
      await tester.pumpWidget(const SizedBox());
      t.service.stopTour();
      for (final l in t.lines) {
        debugPrint(l);
      }
    }

    // ---------------- Fiona & Dev — the silence bar ---------------------------
    if (_tripFd.isNotEmpty) {
      final t = await open('FD', _tripFd, 15, 0);
      final s = t.service;
      final st = t.stops;
      t.gps.simulatePosition(48.856407, 2.342655); // Place Dauphine, 15:00
      await moment(t, 'start at Place Dauphine');
      // Walk to the first stop; arrive at its clock; the piece plays.
      t.clock.setHhmm(15, 15);
      t.gps.simulatePosition(st[0].lat, st[0].lng);
      await moment(t, 'arrive ${st[0].poiName}');
      // Its piece ends at its planned departure; walk on; arrive at the second.
      t.clock.setHhmm(15, 33);
      t.voice.simulateComplete();
      t.gps.simulatePosition(st[1].lat, st[1].lng);
      await moment(t, 'arrive ${st[1].poiName}, piece playing');
      // 15:44 — Dev pauses. The tour clock stops; the wall does not.
      t.clock.setHhmm(15, 44);
      s.pauseTour();
      await moment(t, 'Dev pauses (minute 44)');
      t.clock.setHhmm(16, 15);
      await moment(t, 'still paused, 31 minutes in — nothing announced');
      // 16:47 — minute 107 — they resume.
      t.clock.setHhmm(16, 47);
      s.resumeTour();
      await moment(t, 'resume at minute 107 — what did the phone select?');
      // The remaining piece of the second stop ends; on to the third and fourth.
      t.clock.setHhmm(17, 28);
      t.voice.simulateComplete();
      await moment(t, 'piece over, leaving ${st[1].poiName}');
      if (st.length > 2) {
        t.clock.setHhmm(17, 35);
        t.gps.simulatePosition(st[2].lat, st[2].lng);
        await moment(t, 'arrive ${st[2].poiName}');
        t.clock.setHhmm(17, 40);
        t.voice.simulateComplete();
      }
      if (st.length > 3) {
        t.clock.setHhmm(18, 7);
        t.gps.simulatePosition(st[3].lat, st[3].lng);
        await moment(t, 'arrive ${st[3].poiName} — the planned end is ${t.session.plannedEndHhmm}');
        t.clock.setHhmm(18, 37);
        t.voice.simulateComplete();
      }
      await moment(t, 'end of the day');
      t.lines.add('W513 [FD] SPOKEN LINES, verbatim: ${t.voice.said.isEmpty ? "(none)" : t.voice.said.join(" || ")}');
      await close(t);
    }

    // ---------------- Rosemary — the bar sentence, on both her days -----------
    for (final (name, tripId) in [('RO', _tripRo), ('RO13', _tripRo13)]) {
      if (tripId.isEmpty) continue;
      final t = await open(name, tripId, 14, 0);
      final s = t.service;
      final st = t.stops;
      t.gps.simulatePosition(48.859962, 2.326561); // the Orsay, 14:00
      await moment(t, 'start at the Orsay');
      // Walk her day at its own clocks: arrive each stop at its start_time,
      // the piece completes at its planned departure (a bench has no piece).
      for (var i = 0; i < st.length; i++) {
        final parts = st[i].startTime.split(':');
        t.clock.setHhmm(int.parse(parts[0]), int.parse(parts[1]));
        t.gps.simulatePosition(st[i].lat, st[i].lng);
        await moment(t, 'arrive ${st[i].poiName}');
        final dep = t.clock.now.add(Duration(seconds: st[i].plannedVisitSeconds));
        t.clock.now = dep;
        if (st[i].beatId == null) {
          // A rest: she sits the planned minutes and moves on.
          if (i + 1 < st.length) s.skipToStop(i + 1);
          s.noteTranscriptOpened(st[i]);
        } else {
          t.voice.simulateComplete();
        }
        await moment(t, 'leave ${st[i].poiName} at its planned departure', pause: false);
      }
      // Her trace: at 16:32 she sits, and by 16:45 she has lingered
      // thirteen minutes. Wherever the day has left her.
      t.clock.setHhmm(16, 32);
      s.pauseTour();
      await moment(t, 'she sits (16:32)');
      t.clock.setHhmm(16, 45);
      s.resumeTour();
      await moment(t, 'lingered thirteen minutes (16:45) — the ONE question, if anything she asked for is at risk');
      // What the screen offers her now: [Head back now].
      s.requestWrapUp();
      final wrap = s.matchContingency(s.measure());
      if (wrap != null) s.applyContingency(wrap.contingencyId);
      await moment(t, 'she taps [Head back now]');
      t.lines.add('W513 [$name] SPOKEN LINES, verbatim: ${t.voice.said.isEmpty ? "(none)" : t.voice.said.join(" || ")}');
      await close(t);
    }

    // ---------------- Rosemary, RO13 — the LIVE path, when the set has no answer
    if (_tripRo13.isNotEmpty) {
      final t = await open('RO13-live', _tripRo13, 14, 0);
      final s = t.service;
      final st = t.stops;
      t.gps.simulatePosition(48.859962, 2.326561);
      final parts = st[0].startTime.split(':');
      t.clock.setHhmm(int.parse(parts[0]), int.parse(parts[1]));
      t.gps.simulatePosition(st[0].lat, st[0].lng);
      t.voice.simulateComplete();
      await moment(t, 'at ${st[0].poiName}, piece over');
      // She stays 50 minutes — beyond the widest band the set holds.
      t.clock.now = t.clock.now.add(const Duration(minutes: 50));
      final d = s.measure();
      final entry = s.matchContingency(d);
      t.lines.add('W513 [RO13-live] measured: late ${d.minutesLate} min, at ${d.atStopId}, '
          'precomputed answer: ${entry?.contingencyId ?? "NONE — the live path"}');
      final sw = Stopwatch()..start();
      final next = await tripService.replanSession(
        t.tripId,
        _token,
        lat: st[0].lat,
        lng: st[0].lng,
        wallElapsedSeconds: s.wallElapsedSeconds,
        tourElapsedSeconds: s.tourElapsedSeconds,
        observedPace: s.observedPace,
        listeningRate: s.listeningRate,
        nextStopIndex: 1,
        phoneNextStopHhmm: s.phoneNextStopHhmm,
      );
      s.holdSession(next);
      sw.stop();
      t.lines.add('W513 [RO13-live] LIVE REPLAN in ${sw.elapsedMilliseconds} ms -> v${next.planVersion}: '
          '${next.stops.map((x) => "${x.poiName} ${x.startTime}").join(" -> ")}; '
          'promises: ${next.promises.map((p) => "${p.kind} ${p.name} ${p.arrivesHhmm}${p.protected ? " (protected)" : ""}").join(", ")}; '
          'degradations: ${next.degradationNotices.join(" | ")}');
      await moment(t, 'the day the server hands back after 50 minutes at the Orangerie');
      await close(t);
    }
  }, timeout: const Timeout(Duration(minutes: 20)));
}
