// DEMO "stand here", on the iOS Simulator, on a day planned,
// composed and VOICED on the app path (w711_days.py).
//
// The moments:
//  1. THE WALKING LINE PLAYS ON THE WALK:
//     the stop's piece used to OPEN with "walk east toward the cathedral" —
//     said on arrival, to someone who had just walked it. The line is now its
//     own voiced piece and it plays on the leg; the story waits at the place.
//  2. THE PLACE IS THE PLACE, NOT A DOT: the piece arms and starts
//     where the walker first TOUCHES the place's own footprint — a hundred
//     metres out at a big place — where a 10 m circle round the pin used to
//     mean nothing played until you stood on the pin itself.
//  3. STAND HERE: the marquee's story is cut at HUMAN-PLACED anchors. The
//     west-front chapter plays itself, once, when the walker stands on the
//     parvis inside that anchor's own circle — and walking away and coming
//     back does NOT replay it.
//  4. THE DOOR: when the placed OUTSIDE minutes run out at a place
//     the visit goes into, the piece ends at the end of its SENTENCE, the
//     stop's close plays at the door, and the whole transcript goes on the
//     screen with a keep-listening tap. Nothing auto-plays inside.
//  5. THE CALL: an interruption pauses the piece; when it ends and
//     the walker is still inside the footprint, the piece resumes from the
//     START of the cut sentence, saying nothing.
//  6. THE LINE: at a stop whose arrival hour prices a queue, the telling
//     starts at the first standstill inside the footprint — the best listening
//     slot of the day, silent until now.
//
// Same harness discipline as the earlier simulator demos: scripted GPS and clock through the
// app's own doors, the real TripService over the wire, the session screen
// pumped so the host's 2-second screenshot loop lands a frame per moment,
// "W712 " transcript lines. The PLAYER is the test double here, as in every
// prior demo — the real just_audio player was driven separately, on real files,
// in its own interruption run.

import 'dart:math';

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

const _token = String.fromEnvironment('W712_TOKEN');
const _trip = String.fromEnvironment('W712_TRIP');

/// The player double, with the two things the placement rule reads off a real
/// player: where the piece is, and how long it really is.
class _Voice extends MockAudioService {
  final List<String> said = [];
  final List<String> playedKeys = [];
  Duration _position = Duration.zero;

  @override
  Duration get position => _position;
  void setPosition(Duration p) => _position = p;

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
  void advance(Duration d) => now = now.add(d);
}

/// A point [metres] from (lat,lng) on [bearingDeg] — so a moment can stand at a
/// stated distance from a pin and the transcript can say what that distance was.
({double lat, double lng}) _offset(
    double lat, double lng, double metres, double bearingDeg) {
  const perDegLat = 111320.0;
  final perDegLng = 111320.0 * cos(lat * pi / 180.0);
  final b = bearingDeg * pi / 180.0;
  return (
    lat: lat + (metres * cos(b)) / perDegLat,
    lng: lng + (metres * sin(b)) / perDegLng,
  );
}

double _distance(double aLat, double aLng, double bLat, double bLng) =>
    TourPlaybackService.haversineDistance(aLat, aLng, bLat, bLng);

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

  testWidgets('stand here', (tester) async {
    expect(_token.isNotEmpty, isTrue, reason: 'run with the dart-defines');
    expect(_trip.isNotEmpty, isTrue);
    final tripService = TripService();
    final lines = <String>[];

    Future<({TourPlaybackService s, _Voice voice, MockLocationService gps, _Clock clock, List<ItineraryStop> stops, SessionPlan session})>
        open() async {
      final session = await tripService.fetchSession(_trip, _token);
      final stops = session.stops;
      final clock = _Clock(DateTime(2026, 8, 23, 10, 0));
      final gps = MockLocationService();
      final voice = _Voice();
      final s = TourPlaybackService(
        locationService: gps, audioService: voice, now: () => clock.now,
      );
      await s.startTour(stops);
      s.holdSession(session);
      await tester.pumpWidget(_app(s, _trip));
      await tester.pump();
      return (s: s, voice: voice, gps: gps, clock: clock, stops: stops, session: session);
    }

    Future<void> moment(TourPlaybackService s, _Voice voice, String tag,
        String label, {bool pause = true}) async {
      s.tick();
      await tester.pump();
      lines.add('W712 [$tag] $label '
          '| at=${s.currentStop?.poiName ?? "-"} state=${s.state.name} '
          '| played: ${voice.playedKeys.join(",")} '
          '| chapter offer: "${s.segmentOffer ?? ""}" '
          '| keep-listening: "${s.keepListeningOffer ?? ""}" '
          '| close line: "${s.closeLine ?? ""}" '
          '| spoken: ${voice.said.length}');
      debugPrint('W712 MOMENT $tag ${label.replaceAll(' ', '_')}');
      if (pause) await Future<void>.delayed(const Duration(seconds: 6));
    }

    // Pick the stops by what the wire PLACED on them, never by name, so the
    // demo runs on whichever day carries the marquee.
    final probe = await tripService.fetchSession(_trip, _token);
    final all = probe.stops;
    ItineraryStop? firstWhere(bool Function(ItineraryStop) test) {
      for (final s in all) {
        if (test(s)) return s;
      }
      return null;
    }

    ItineraryStop? largestBy(
        bool Function(ItineraryStop) test, num Function(ItineraryStop) size) {
      ItineraryStop? best;
      for (final s in all) {
        if (!test(s)) continue;
        if (best == null || size(s) > size(best)) best = s;
      }
      return best;
    }

    // The longest leg line, the biggest priced line, the widest place: the demo
    // shows the case a watcher can actually judge, not merely the first one.
    final legStop = largestBy(
        (s) => s.legAudioUrl != null, (s) => s.legAudioDurationSec ?? 0);
    final chapterStop = firstWhere((s) => s.segments.any((c) => c.audioUrl != null));
    final doorStop = firstWhere(
        (s) => (s.trigger?.door ?? false) && (s.trigger?.outsideSeconds ?? 0) > 0);
    final queuedStop = largestBy(
        (s) => (s.trigger?.queueSeconds ?? 0) > 0, (s) => s.trigger!.queueSeconds);
    final bigStop = largestBy(
        (s) => (s.trigger?.queueSeconds ?? 0) == 0, (s) => s.trigger?.radiusM ?? 0);

    lines.add('W712 [DAY] ${all.map((x) => "${x.poiName} r=${x.trigger?.radiusM}").join(" -> ")}');
    lines.add('W712 [DAY] leg piece: ${legStop?.poiName ?? "none"} '
        '| chapters: ${chapterStop?.poiName ?? "none"} '
        '| door: ${doorStop?.poiName ?? "none"} '
        '| priced queue: ${queuedStop?.poiName ?? "none"} '
        '| a big place: ${bigStop?.poiName ?? "none"}');

    // ---- ACT 1 — the walking line plays ON THE WALK -------------------------
    if (legStop != null) {
      final t = await open();
      final idx = t.stops.indexWhere((s) => s.stopId == legStop.stopId);
      // Stand the walker on the LEG: outside every footprint, between the
      // previous stop and this one.
      final prev = idx > 0 ? t.stops[idx - 1] : t.stops.first;
      final away = _offset(prev.lat, prev.lng, 400, 90);
      // Walk the pointer up to this stop so the leg is the leg being walked.
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      t.voice.stop();
      t.gps.simulatePosition(away.lat, away.lng);
      await moment(t.s, t.voice, 'ACT1',
          'on the leg to ${legStop.poiName}: the WALKING LINE plays '
          '(${legStop.legAudioDurationSec}s), the story does not');
      final legKey = '${legStop.stopId}-leg';
      lines.add('W712 [ACT1] leg piece played: '
          '${t.voice.playedKeys.contains(legKey) ? "YES" : "NO"} '
          '| story played: ${t.voice.playedKeys.contains(legStop.stopId) ? "YES (defect!)" : "NOT YET (correct)"}');
      lines.add('W712 [ACT1] the line itself: "${legStop.legNarration}"');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 2 — the place is the place, not a dot --------------------------
    if (bigStop != null) {
      final t = await open();
      final idx = t.stops.indexWhere((s) => s.stopId == bigStop.stopId);
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      t.voice.stop();
      final r = bigStop.trigger!.radiusM;
      final outside = _offset(bigStop.lat, bigStop.lng, r + 30, 0);
      t.gps.simulatePosition(outside.lat, outside.lng);
      await moment(t.s, t.voice, 'ACT2',
          '${(r + 30).round()} m from the pin — OUTSIDE the ${r.round()} m footprint: nothing plays');
      final edge = _offset(bigStop.lat, bigStop.lng, r - 5, 0);
      t.gps.simulatePosition(edge.lat, edge.lng);
      final d = _distance(edge.lat, edge.lng, bigStop.lat, bigStop.lng);
      await moment(t.s, t.voice, 'ACT2',
          'stepped onto the edge — ${d.round()} m from the pin: the story STARTS');
      lines.add('W712 [ACT2] ${bigStop.poiName}: footprint ${r.round()} m; the piece '
          'started ${d.round()} m from the pin. Before footprints the phone drew one 10 m '
          'circle here, so nothing played until the walker stood on the pin itself.');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 3 — STAND HERE: the chapter at its own anchor ------------------
    if (chapterStop != null) {
      final t = await open();
      final idx = t.stops.indexWhere((s) => s.stopId == chapterStop.stopId);
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      // Arrive and let the stop's own story be told first.
      t.gps.simulatePosition(chapterStop.lat, chapterStop.lng);
      t.s.skipToStop(idx);
      t.voice.simulateComplete();
      await moment(t.s, t.voice, 'ACT3', 'the stop\'s story has been told');
      final chapterIdx = chapterStop.segments.indexWhere((c) => c.audioUrl != null);
      final chapter = chapterStop.segments[chapterIdx];
      // Walk to the anchor and STAND there.
      t.gps.simulatePosition(chapter.lat, chapter.lng);
      t.clock.advance(const Duration(seconds: 40));
      t.gps.simulatePosition(chapter.lat, chapter.lng);
      await moment(t.s, t.voice, 'ACT3',
          'standing at "${chapter.label}" (its own ${chapter.radiusM.round()} m circle): the CHAPTER plays');
      final segKey = '${chapterStop.stopId}-seg-$chapterIdx';
      lines.add('W712 [ACT3] chapter played: '
          '${t.voice.playedKeys.contains(segKey) ? "YES" : "NO"} '
          '| it says: "${chapter.narration.split(RegExp(r"(?<=[.!?])\s+")).first}"');
      // Walk off, come back — no restart, no comment.
      t.voice.simulateComplete();
      final off = _offset(chapter.lat, chapter.lng, 300, 180);
      t.gps.simulatePosition(off.lat, off.lng);
      t.clock.advance(const Duration(minutes: 4));
      t.gps.simulatePosition(chapter.lat, chapter.lng);
      t.clock.advance(const Duration(seconds: 40));
      t.gps.simulatePosition(chapter.lat, chapter.lng);
      final before = t.voice.playedKeys.where((k) => k == segKey).length;
      await moment(t.s, t.voice, 'ACT3',
          'walked off for four minutes and came back — told once is told');
      final after = t.voice.playedKeys.where((k) => k == segKey).length;
      lines.add('W712 [ACT3] chapter replays on the way back: '
          '${after > before ? "YES (defect!)" : "NO (correct)"}');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 4 — the door ---------------------------------------------------
    if (doorStop != null) {
      final t = await open();
      final idx = t.stops.indexWhere((s) => s.stopId == doorStop.stopId);
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      t.gps.simulatePosition(doorStop.lat, doorStop.lng);
      t.s.skipToStop(idx);
      t.voice.setPosition(const Duration(seconds: 20));
      await moment(t.s, t.voice, 'ACT4',
          'at ${doorStop.poiName}, the piece is playing outside; the plan places '
          '${(doorStop.trigger!.outsideSeconds / 60).round()} minutes out here');
      // The placed outside minutes run out.
      t.clock.advance(Duration(seconds: doorStop.trigger!.outsideSeconds + 5));
      await moment(t.s, t.voice, 'ACT4',
          'the outside minutes have run: the piece finishes its SENTENCE');
      t.s.finishSentenceNow();
      await moment(t.s, t.voice, 'ACT4',
          'the sentence ended: the stop\'s CLOSE plays at the door');
      lines.add('W712 [ACT4] close heard: "${t.s.closesPlayed.isEmpty ? "" : t.s.closesPlayed.last}"');
      // The close is a piece too: the keep-listening offer belongs to the SILENCE
      // after it, not on top of it. Let it end, as it does on a real player.
      t.voice.simulateComplete();
      await moment(t.s, t.voice, 'ACT4',
          'the close has ended — she goes in, and the whole telling is on the screen '
          'with a KEEP LISTENING tap');
      lines.add('W712 [ACT4] keep-listening offer: "${t.s.keepListeningOffer ?? "(none)"}" '
          '| transcript on screen: ${(t.s.keepListeningTranscript ?? "").isNotEmpty ? "YES" : "NO"} '
          '(${(t.s.keepListeningTranscript ?? "").split(RegExp(r"\s+")).length} words)');
      // Inside: nothing auto-plays.
      final playedBefore = t.voice.playedKeys.length;
      t.clock.advance(const Duration(minutes: 3));
      t.gps.simulatePosition(doorStop.lat, doorStop.lng);
      await moment(t.s, t.voice, 'ACT4', 'three minutes inside: nothing auto-plays');
      lines.add('W712 [ACT4] pieces started inside: '
          '${t.voice.playedKeys.length - playedBefore} '
          '(nothing auto-plays under the roof)');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 5 — the call ---------------------------------------------------
    {
      final t = await open();
      final stop = chapterStop ?? doorStop ?? t.stops.first;
      final idx = t.stops.indexWhere((s) => s.stopId == stop.stopId);
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      t.gps.simulatePosition(stop.lat, stop.lng);
      t.s.skipToStop(idx);
      t.voice.setPosition(const Duration(seconds: 30));
      t.voice.duration = Duration(
          milliseconds: (((stop.audioDurationSec ?? 60) * 1000).round()));
      await moment(t.s, t.voice, 'ACT5', 'at ${stop.poiName}: the piece is playing');
      final saidBefore = t.voice.said.length;
      t.voice.simulateInterruption(AudioInterruptionKind.pauseBegin);
      await moment(t.s, t.voice, 'ACT5', 'a call comes in: the piece PAUSES');
      t.voice.simulateInterruption(AudioInterruptionKind.ended);
      await moment(t.s, t.voice, 'ACT5',
          'the call ends and she is still on the footprint: it resumes at the '
          'START of the sentence it cut, saying nothing');
      lines.add('W712 [ACT5] resumed from ${t.voice.lastSeek?.inSeconds ?? -1}s '
          '(the piece was cut at 30s) | said anything: '
          '${t.voice.said.length > saidBefore ? "YES (defect!)" : "NO (correct)"}');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    // ---- ACT 6 — the line ---------------------------------------------------
    if (queuedStop != null) {
      final t = await open();
      final idx = t.stops.indexWhere((s) => s.stopId == queuedStop.stopId);
      for (var i = 0; i < idx; i++) {
        t.s.skipToStop(i);
        t.voice.simulateComplete();
      }
      t.voice.stop();
      final q = queuedStop.trigger!.queueSeconds;
      // Arrive: at a queued stop the telling waits for the person to settle.
      t.gps.simulatePosition(queuedStop.lat, queuedStop.lng);
      await moment(t.s, t.voice, 'ACT6',
          'arrived at ${queuedStop.poiName}, where the plan prices a '
          '${(q / 60).round()}-minute line — still walking in, nothing starts yet');
      t.clock.advance(const Duration(seconds: 40));
      t.gps.simulatePosition(queuedStop.lat, queuedStop.lng);
      await moment(t.s, t.voice, 'ACT6',
          'she stops moving — in the line — and the telling STARTS');
      lines.add('W712 [ACT6] queue policy: ${t.session.placement?.queuePiece ?? "-"} '
          '| the telling started: '
          '${t.voice.playedKeys.contains(queuedStop.stopId) ? "YES" : "NO"} '
          '| offered by tap instead: "${t.s.armedOffer ?? "(no — it played)"}"');
      await tester.pumpWidget(const SizedBox());
      t.s.stopTour();
    }

    for (final l in lines) {
      debugPrint(l);
    }
  }, timeout: const Timeout(Duration(minutes: 15)));
}
