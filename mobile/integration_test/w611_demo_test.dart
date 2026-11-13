// A DEMO, "wrap it up at minute 14", on the iOS Simulator, on the days the
// planner gives Fiona & Dev TODAY (generated and composed on the app path by
// w61_trips.py under the three-minute tight, closes, threads, registers and
// full tellings).
//
// The moments, per the rulings that re-planned this demo:
//  1. THE TAP IS THE SEAM: mid-piece at minute 14 they tap
//     [Head back now] — the close and the way home go ON SCREEN at once, the
//     sentence finishes, the stretch's AUTHORED close is heard, then nothing.
//  2. THE THREAD (replaces the dead plant-fallback act — fallbacks were
//     removed): a skip makes a new pair — the pair's authored bridge line
//     goes on screen for the leg and is heard once at a standing seam.
//  3. THE LINGER OFFER: past the tight telling's end the screen OFFERS
//     the full telling with its cost; nothing auto-plays.
//
// Same harness as the other demo tests: scripted GPS and clock through the
// app's own doors, the real TripService over the wire, tagged transcript
// lines, 6-second pauses so the host's 2-second screenshot loop lands a frame
// per moment.

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

  testWidgets('D7 — wrap it up at minute 14', (tester) async {
    expect(_token.isNotEmpty, isTrue, reason: 'run with the dart-defines');
    expect(_tripFd.isNotEmpty, isTrue);
    final tripService = TripService();
    final lines = <String>[];

    Future<({TourPlaybackService s, _Voice voice, MockLocationService gps, _Clock clock, List<ItineraryStop> stops, SessionPlan session})>
        open(int h, int m) async {
      final session = await tripService.fetchSession(_tripFd, _token);
      final stops = [
        for (final s in session.stops)
          s.beatId == null ? s : s.copyWith(audioUrl: 'demo://${s.stopId}'),
      ];
      final clock = _Clock(DateTime(2026, 8, 19, h, m));
      final gps = MockLocationService();
      final voice = _Voice();
      final s = TourPlaybackService(
        locationService: gps, audioService: voice, now: () => clock.now,
      );
      await s.startTour(stops);
      s.holdSession(session);
      await tester.pumpWidget(_app(s, _tripFd));
      await tester.pump();
      return (s: s, voice: voice, gps: gps, clock: clock, stops: stops, session: session);
    }

    String hhmm(DateTime d) =>
        '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

    Future<void> moment(TourPlaybackService s, _Voice voice, _Clock clock,
        String tag, String label, {bool pause = true}) async {
      s.tick();
      await tester.pump();
      lines.add('W611 [$tag] ${hhmm(clock.now)} $label '
          '| at=${s.currentStop?.poiName ?? "-"} state=${s.state.name} '
          '| close line: "${s.closeLine ?? ""}" '
          '| thread line: "${s.threadLine ?? ""}" '
          '| full offer: "${s.fullTellingOffer ?? ""}" '
          '| session line: "${s.screenText ?? ""}" '
          '| spoken: ${voice.said.length} played: ${voice.playedKeys.join(",")}');
      debugPrint('W611 MOMENT $tag ${label.replaceAll(' ', '_')}');
      if (pause) await Future<void>.delayed(const Duration(seconds: 6));
    }

    // ---- ACT 1 — the tap is the seam, at minute 14 --------------------------
    {
      final t = await open(15, 0);
      final st = t.stops;
      lines.add('W611 [ACT1] DAY v${t.session.planVersion}: '
          '${st.map((x) => "${x.poiName} ${x.startTime}").join(" -> ")} '
          '| closes on the wire: ${st.where((x) => (x.closeText ?? "").isNotEmpty).length}/${st.length} '
          '| threads on the wire: ${st.where((x) => x.threadLines != null && x.threadLines!.isNotEmpty).length} '
          '| full tellings: ${st.where((x) => (x.fullNarration ?? "").isNotEmpty).length}');
      t.gps.simulatePosition(st[0].lat, st[0].lng); // arrive stop 0; piece plays
      await moment(t.s, t.voice, t.clock, 'ACT1', 'arrive ${st[0].poiName}, piece playing');
      // Minute 14, mid-piece: the tap.
      t.clock.setHhmm(15, 14);
      t.s.requestWrapUp();
      final entry = t.s.matchContingency(t.s.measure());
      expect(entry, isNotNull, reason: 'the set holds a wrap-up from here');
      t.s.applyContingency(entry!.contingencyId);
      await moment(t.s, t.voice, t.clock, 'ACT1',
          'minute 14: tapped — close and way home ON SCREEN, sentence finishing');
      t.s.finishSentenceNow();
      await moment(t.s, t.voice, t.clock, 'ACT1',
          'the sentence ended: the stretch\'s AUTHORED close was heard');
      lines.add('W611 [ACT1] the close heard: '
          '"${t.s.closesPlayed.isEmpty ? "" : t.s.closesPlayed.last}" '
          '(spoken=${t.voice.said.length}, played keys=${t.voice.playedKeys.join(",")})');
      // Then nothing: a minute passes, no further speech.
      final spokenBefore = t.voice.said.length;
      t.clock.setHhmm(15, 16);
      await moment(t.s, t.voice, t.clock, 'ACT1', 'a minute later — then NOTHING', pause: false);
      expect(t.voice.said.length, spokenBefore, reason: 'R8: then nothing');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 2 — the thread of the pair the session made --------------------
    {
      final t = await open(15, 0);
      final st = t.stops;
      // Find a skip entry whose NEW pair carries an authored thread.
      SessionContingency? skip;
      String? threadText;
      for (final e in t.session.contingencies) {
        if (e.kind != 'stop_skipped') continue;
        final skippedIdx = st.indexWhere((x) => x.poiId == e.triggerStopId);
        if (skippedIdx <= 0 || skippedIdx + 1 >= st.length) continue;
        final from = st[skippedIdx - 1];
        final to = st[skippedIdx + 1];
        final line = to.threadLines?[from.poiName];
        if (line != null && line.isNotEmpty) {
          skip = e;
          threadText = line;
          break;
        }
      }
      if (skip == null) {
        lines.add('W611 [ACT2] no skip pair carries an authored thread on this day '
            '(none rather than glue — R5); the act shows the silent continue');
        skip = t.session.contingencies
            .where((e) => e.kind == 'stop_skipped')
            .firstOrNull;
      }
      if (skip != null) {
        // A real stretch first: arrive at the day's first stop, let its piece
        // play and END on its own — the standing seam every real skip has.
        t.gps.simulatePosition(st[0].lat, st[0].lng);
        await moment(t.s, t.voice, t.clock, 'ACT2', 'at ${st[0].poiName}, piece playing');
        t.voice.simulateComplete();
        t.clock.setHhmm(15, 9);
        t.s.applyContingency(skip.contingencyId);
        await moment(t.s, t.voice, t.clock, 'ACT2',
            'skip applied: the pair\'s bridge line ON SCREEN'
            '${threadText == null ? " (none authored)" : ""}');
        t.clock.setHhmm(15, 10);
        await moment(t.s, t.voice, t.clock, 'ACT2',
            'standing seam: the thread ${threadText == null ? "stays silent" : "is heard once"}');
        lines.add('W611 [ACT2] thread="${t.s.threadLine ?? ""}" '
            'spoken=${t.voice.said.join(" || ")} '
            'played=${t.voice.playedKeys.join(",")}');
      }
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 3 — the linger OFFERS; nothing auto-plays ----------------------
    {
      final t = await open(15, 0);
      final st = t.stops;
      final major = st.firstWhere(
        (x) => (x.fullNarration ?? '').isNotEmpty,
        orElse: () => st.first,
      );
      t.gps.simulatePosition(major.lat, major.lng);
      await moment(t.s, t.voice, t.clock, 'ACT3', 'arrive ${major.poiName} (a major stop)');
      t.voice.simulateComplete(); // the tight ends on its own
      t.clock.setHhmm(15, 6);
      await moment(t.s, t.voice, t.clock, 'ACT3',
          'the tight ended; she lingers — the OFFER appears, silently');
      lines.add('W611 [ACT3] offer="${t.s.fullTellingOffer ?? "(none — no full authored here)"}" '
          'auto-played: ${t.voice.playedKeys.where((k) => k.endsWith("-full")).isEmpty ? "NOTHING (correct)" : "YES (defect!)"}');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    for (final l in lines) {
      debugPrint(l);
    }
  }, timeout: const Timeout(Duration(minutes: 15)));
}
