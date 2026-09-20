import 'dart:async';

import 'package:apple_maps_flutter/apple_maps_flutter.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/theme/theme.dart';
import 'package:ondoway/theme/tokens.dart';
import 'package:provider/provider.dart';

/// THE walking screen — the one the tourist is on, and the only one.
///
/// It is the wireframe-05 live guide: a dark, audio-led screen over a real map
/// with stop pins, a now-playing card, an approaching nudge and a completion
/// panel. It is also the SESSION screen: the day's next line and finish clock,
/// the close and the thread, every offer the walk can make, and — the reason
/// this screen exists at all — the ONE question, as two big buttons with the
/// default named in words (W5.2 R2.5, Rosemary: "two large buttons under a
/// thumb"), plus [Head back now] on every screen (Nadia, R1.1).
///
/// THOSE TWO SCREENS USED TO BE TWO SCREENS. `SessionPage` rendered the whole
/// live surface and **nothing navigated to it** — the itinerary pushed here, and
/// here had none of it. So the walk that ships could not ask the question, its
/// pause button stopped the audio while the tour clock kept running, and its
/// scrubber was a hardcoded 28%. A tourist running late was never asked what to
/// drop, because the screen that asks was unreachable. Absorbed 2026-08-31 and
/// the other screen deleted; the plan's own instruction was to rebuild remote's
/// screen against local's playback service, and this finishes it.
///
/// A thin VIEW over [TourPlaybackService], which decides everything (design
/// §4.6): geofence, auto-play, advance, the clock, which question is due. This
/// file renders what the service holds and never decides anything itself.
class TourWalkPage extends StatefulWidget {
  final GeneratedTrip trip;
  const TourWalkPage({super.key, required this.trip});

  @override
  State<TourWalkPage> createState() => _TourWalkPageState();

  static String _minutes(int seconds) => ((seconds + 30) ~/ 60).toString();

  /// The first line of the screen: where you are, or the next stop and its
  /// minutes by the phone's own re-timing; "That was the walk." when done.
  /// Public so the demo transcripts print exactly what the screen shows.
  static String? nextLineFor(TourPlaybackService service) {
    if (service.state == TourState.completed) return 'That was the walk.';
    final etas = service.retimeRemaining();
    if (etas.isEmpty) return null;
    final next = etas.first;
    return next.secondsToArrival == 0
        ? "You're at ${next.stop.poiName}"
        : 'Next: ${next.stop.poiName} · ${_minutes(next.secondsToArrival)} min';
  }

  /// The finish clock: the phone's OWN re-timing while the walk runs (W5.13 —
  /// a precomputed line's clock is stale by the time it fires; the phone's is
  /// not), else the day's finish promise as planned.
  static String? finishLineFor(TourPlaybackService service) {
    final session = service.session;
    final name = session?.finishName ?? 'your finish';
    final eta = service.finishEtaSeconds;
    if (eta != null && service.state != TourState.completed) {
      final clock = service.dayFrameHhmm(eta);
      if (clock.isNotEmpty) return '$name by $clock';
    }
    final promises = session?.promises ?? const <SessionPromise>[];
    for (final p in promises) {
      if (p.kind == 'finish' && p.arrivesHhmm.isNotEmpty) {
        return '${p.name} by ${p.arrivesHhmm}';
      }
    }
    return null;
  }
}

class _TourWalkPageState extends State<TourWalkPage> {
  // See prior note: cached in didChangeDependencies so dispose() doesn't do a
  // Provider ancestor lookup on a possibly-deactivated element.
  TourPlaybackService? _engine;

  /// The screen ticks the service once a second. The geolocator sends no fix
  /// while nobody moves, so STANDING STILL is measured by time passing — and a
  /// standstill is what starts a queued piece, ends a door's outside seconds
  /// and makes a moment natural enough to speak into. Without this the walk
  /// goes quiet the moment the walker stops, which is precisely when it should
  /// be talking.
  Timer? _ticker;

  /// Whether THIS page started the tour. The itinerary starts a walk itself —
  /// from the SESSION's stops, which carry the placed trigger geometry and the
  /// voiced files that `widget.trip.stops` does not — and then pushes here. So
  /// the page must not restart what is already running, and must not end a walk
  /// it did not begin: popping back to glance at the itinerary is not the end
  /// of the walk. On a cold entry the engine is idle, the page starts the tour
  /// and owns it, and disposal ends it — which is the fork's own behaviour.
  bool _startedHere = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _engine = context.read<TourPlaybackService>();
  }

  /// The last time the page went to the server for caught-up audio, and the
  /// in-flight guard — one fetch at a time, at most one every twenty seconds.
  DateTime? _audioCatchUpAt;
  bool _audioCatchUpInFlight = false;

  /// A text-only leg is under way: the re-voiced file may have landed since the
  /// session was fetched (the ADR's background voicing, the walker's arrival as
  /// its deadline). Refetch and adopt AUDIO ONLY — a plan change still arrives
  /// through the replan path, never through this door.
  Future<void> _maybeCatchUpAudio() async {
    final engine = _engine;
    if (engine == null || !engine.audioCatchUpDue || _audioCatchUpInFlight) {
      return;
    }
    final last = _audioCatchUpAt;
    if (last != null && DateTime.now().difference(last).inSeconds < 20) return;
    _audioCatchUpInFlight = true;
    _audioCatchUpAt = DateTime.now();
    try {
      final token = context.read<AuthService>().accessToken;
      if (token == null) return;
      final session = await context
          .read<TripService>()
          .fetchSession(widget.trip.tripId, token);
      engine.adoptSessionAudio(session.stops);
    } catch (_) {
      // Offline: the words stay on the screen — the ADR's floor holds.
    } finally {
      _audioCatchUpInFlight = false;
    }
  }

  @override
  void initState() {
    super.initState();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      // Tick and nothing else. The screen already watches the service, so a
      // tick that CHANGES something rebuilds through notifyListeners, and a
      // tick that changes nothing costs no frame. An unconditional setState
      // here would repaint the map every second whatever happened — and would
      // schedule a frame forever, which is a tree that never settles.
      context.read<TourPlaybackService>().tick();
      _maybeCatchUpAudio();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final engine = _engine;
      if (engine == null || engine.isActive) return;
      _startedHere = true;
      engine.startTour(widget.trip.stops);
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    if (_startedHere) _engine?.stopTour();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final engine = context.watch<TourPlaybackService>();
    final audio = context.watch<AudioProvider>();
    final stop = engine.currentStop;
    // A piece is LOADED, which is not the same as playing. The player card is
    // gated on this rather than on isPlaying, because gating on isPlaying makes
    // the card — and with it the only resume control — vanish the instant the
    // walker pauses. A paused walk still has a piece; the icon says which.
    final hasPiece = audio.currentBeatId != null || engine.isPaused;

    // The live guide is always dark, regardless of the app theme.
    return Theme(
      data: buildOndowayTheme(Brightness.dark),
      child: Builder(builder: (context) {
        final c = Theme.of(context).extension<OndowayColors>()!;
        return Scaffold(
          backgroundColor: c.bg,
          body: Stack(
            fit: StackFit.expand,
            children: [
              // Drawn from the ENGINE's stops, never the trip this page was
              // pushed with. Once a walk is running the service's list is the
              // truth: a composed session carries placed geometry and voiced
              // files the pushed trip does not, and a contingency can reorder or
              // drop stops mid-walk. The trip seeds a cold start and nothing else.
              _MapBackdrop(
                stops: engine.plannedStops,
                currentIndex: engine.currentStopIndex,
              ),
              SafeArea(
                child: engine.state == TourState.completed
                    // A FINISHED WALK CAN STILL HAVE SOMETHING TO HEAR. The
                    // completion panel used to be the whole screen, so a walker
                    // standing inside the last stop — Notre-Dame, with its
                    // indoor chapter unheard — was told "Tour complete" and
                    // offered nothing, while the service was holding the offer
                    // the whole time. The offers outlive the walk because they
                    // are about WHERE THE PERSON IS STANDING, not about whether
                    // the itinerary has any stops left.
                    ? Stack(
                        children: [
                          _CompletePanel(
                              c: c,
                              onDone: () => Navigator.of(context).maybePop()),
                          Align(
                            alignment: Alignment.bottomCenter,
                            child: _Offers(c: c, engine: engine),
                          ),
                        ],
                      )
                    : Stack(
                        children: [
                          // Always-available exit from the immersive walk.
                          Align(
                            alignment: Alignment.topLeft,
                            child: Padding(
                              padding: const EdgeInsets.all(8),
                              child: _WalkExitButton(
                                c: c,
                                onTap: () => Navigator.of(context).maybePop(),
                              ),
                            ),
                          ),
                          // R4: the day-long switch to a screen-only voice, and
                          // the one touch that restores it.
                          if (engine.screenOnly)
                            Align(
                              alignment: Alignment.topRight,
                              child: Padding(
                                padding: const EdgeInsets.all(8),
                                child: IconButton(
                                  key: const Key('session-speaker'),
                                  tooltip: 'Hear it again',
                                  icon: Icon(Icons.volume_up, color: c.ink),
                                  onPressed: engine.restoreVoice,
                                ),
                              ),
                            ),
                          // What the day is doing, in words: the next stop and
                          // its minutes, the finish clock, the close, the
                          // thread, and any notice the session put on screen.
                          Align(
                            alignment: Alignment.topCenter,
                            child: _SessionLines(c: c, engine: engine),
                          ),
                          if (stop == null && engine.pendingQuestion == null)
                            Center(
                              child: Text('Preparing your walk…',
                                  style: TextStyle(color: c.inkSoft)),
                            ),
                          Align(
                            alignment: Alignment.bottomCenter,
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                _Offers(c: c, engine: engine),
                                if (stop != null)
                                  hasPiece
                                      ? _NowPlayingPlayer(
                                          c: c,
                                          stop: stop,
                                          index: engine.currentStopIndex,
                                          total: engine.plannedStops.length,
                                          isPlaying: audio.isPlaying,
                                          position: audio.position,
                                          duration: audio.duration,
                                          onPrev: () => engine.skipToStop(
                                              engine.currentStopIndex),
                                          // THE TOUR'S pause, not the player's.
                                          // Stopping the audio alone left the
                                          // tour clock running, so a pocketed
                                          // phone kept "walking" while its owner
                                          // stood still. One door: the service
                                          // suspends its clock beside the audio.
                                          onPlayPause: () => engine.isPaused
                                              ? engine.resumeTour()
                                              : engine.pauseTour(),
                                          onNext: () => engine.skipToStop(
                                              engine.currentStopIndex + 1),
                                        )
                                      : _WalkingBar(
                                          c: c,
                                          noAudio: stop.audioUrl == null,
                                          onSkip: () => engine.skipToStop(
                                              engine.currentStopIndex + 1),
                                        ),
                                _WalkControls(c: c, engine: engine),
                              ],
                            ),
                          ),
                          // LAST, so nothing paints or hit-tests over them.
                          // The question is the only thing the walk ever asks of
                          // the person walking it, and the nudge is the one
                          // moment they can answer before a piece starts; a
                          // control panel drifting over either is the difference
                          // between a tap that lands and a tap that does not.
                          if (engine.pendingQuestion != null)
                            Align(
                              alignment: Alignment.center,
                              child: _QuestionPanel(
                                c: c,
                                engine: engine,
                                question: engine.pendingQuestion!,
                                entry: engine.selectedContingency,
                              ),
                            )
                          else if (engine.hasPendingStop &&
                              engine.nextStop != null)
                            Align(
                              alignment: Alignment.center,
                              child: _ApproachingNudge(
                                c: c,
                                stopName: engine.nextStop!.poiName,
                                onAccept: engine.acceptPendingStop,
                                onDismiss: engine.dismissPending,
                              ),
                            ),
                        ],
                      ),
              ),
            ],
          ),
        );
      }),
    );
  }
}

/// Cobalt to match the wireframe's route + play button (the dark theme's accent
/// is a lighter blue used for the softer glow).
Color get _cobalt => OndowayColors.light.accent;

/// A translucent slab over the map. Every panel below sits on one, so text laid
/// over streets and parks stays readable without dimming the map itself.
BoxDecoration _overMap(OndowayColors c) => BoxDecoration(
      color: c.card.withValues(alpha: 0.92),
      borderRadius: BorderRadius.circular(Dims.radiusCard),
      border: Border.all(color: c.line),
    );

/// What the day is doing, in words. The service holds every one of these; this
/// renders them and decides nothing.
class _SessionLines extends StatelessWidget {
  final OndowayColors c;
  final TourPlaybackService engine;
  const _SessionLines({required this.c, required this.engine});

  @override
  Widget build(BuildContext context) {
    final next = TourWalkPage.nextLineFor(engine);
    final finish = TourWalkPage.finishLineFor(engine);
    final question = engine.pendingQuestion;
    final lines = <Widget>[
      if (next != null)
        Text(next,
            key: const Key('session-next-line'),
            style: TextStyle(
                color: c.ink,
                fontFamily: 'Fraunces',
                fontSize: 20,
                fontWeight: FontWeight.w600)),
      if (finish != null)
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(finish,
              key: const Key('session-finish-line'),
              style: TextStyle(color: c.inkSoft, fontSize: 14)),
        ),
      // S6.4: the close the wrap-up put on screen — the one sentence the tap
      // buys, shown before the way home.
      if (engine.closeLine != null)
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(engine.closeLine!,
              key: const Key('session-close-line'),
              style: TextStyle(color: c.ink, fontSize: 15)),
        ),
      // S1.M7: a leg whose words have no file yet — a replan rewrote the line
      // and the audio is catching up — shows its walking line here instead of
      // playing anything. The direction is never lost to the gap.
      if (engine.legTextLine != null)
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(engine.legTextLine!,
              key: const Key('session-leg-text-line'),
              style: TextStyle(color: c.ink, fontSize: 15)),
        ),
      // S6.5: the thread of the pair the session just made — on screen for the
      // whole leg, so the one lost on the move is waiting at the standstill.
      if (engine.threadLine != null)
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(engine.threadLine!,
              key: const Key('session-thread-line'),
              style: TextStyle(color: c.ink, fontSize: 15)),
        ),
      if (engine.screenText != null && engine.screenText != question)
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(engine.screenText!,
              key: const Key('session-screen-text'),
              style: TextStyle(color: c.ink, fontSize: 15)),
        ),
      if (engine.screenOnly)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(TourPlaybackService.kScreenOnlyLine,
              key: const Key('session-screen-only'),
              style: TextStyle(color: c.inkMute, fontSize: 13)),
        ),
      if (engine.finishMovedLine != null)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(engine.finishMovedLine!,
              key: const Key('session-finish-moved'),
              style: TextStyle(color: c.inkSoft, fontSize: 13)),
        ),
      for (final notice in engine.clockNotices)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(notice, style: TextStyle(color: c.inkMute, fontSize: 13)),
        ),
    ];
    if (lines.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.fromLTRB(
          Dims.spaceMd, 56, Dims.spaceMd, Dims.spaceSm),
      padding: const EdgeInsets.all(Dims.spaceMd),
      decoration: _overMap(c),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: lines,
      ),
    );
  }
}

/// THE ONE QUESTION, as two big buttons with the default named in words.
///
/// The arms are the two clauses of the one sentence the session asks; the arm
/// that KEEPS the protected thing answers `keep`, the other `shorten`. The
/// default is spelled out — "this happens if you do nothing" — because a walker
/// with a phone in one hand is allowed to not answer.
class _QuestionPanel extends StatelessWidget {
  final OndowayColors c;
  final TourPlaybackService engine;
  final String question;
  final SessionContingency? entry;
  const _QuestionPanel({
    required this.c,
    required this.engine,
    required this.question,
    required this.entry,
  });

  static String _cap(String s) =>
      s.isEmpty ? s : '${s[0].toUpperCase()}${s.substring(1)}';

  /// Apply the arm on the phone, then — when the question was a held answer
  /// at a SHUT door — report the answer to the server: the guess is marked for
  /// the next walker, the shut door leaves the stored day, and the standby the
  /// walker chose is seated in it, so a reconnect brings back the day being
  /// walked and not the one with the locked door still on it. The report rides
  /// after the answer, never before it: nothing waits on a round trip.
  Future<void> _answer(BuildContext context, String arm) async {
    final held = entry;
    engine.answerQuestion(arm);
    if (held == null || held.kind != 'door_closed') return;
    final shut = held.triggerStopId;
    final tripId = engine.session?.tripId;
    final token = context.read<AuthService>().accessToken;
    if (shut == null || tripId == null || token == null) return;
    final chosen = arm == 'shorten' && held.alternateStopIds.isNotEmpty
        ? held.alternateStopIds
        : held.stopIds;
    final tripService = context.read<TripService>();
    final at = engine.currentStop;
    try {
      final session = await tripService.replanSession(
        tripId,
        token,
        lat: at?.lat ?? 0,
        lng: at?.lng ?? 0,
        wallElapsedSeconds: engine.wallElapsedSeconds,
        tourElapsedSeconds: engine.tourElapsedSeconds,
        observedPace: engine.observedPace,
        listeningRate: engine.listeningRate,
        nextStopIndex: engine.currentStopIndex,
        phoneNextStopHhmm: engine.phoneNextStopHhmm,
        closedStopId: shut,
        keptStopIds: chosen,
      );
      engine.holdSession(session);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not report — try again.')),
        );
      }
    }
  }

  List<Widget> _arms(BuildContext context) {
    final body = question.endsWith('?')
        ? question.substring(0, question.length - 1)
        : question;
    final parts = body.split(', or ');
    if (parts.length != 2) {
      return [
        FilledButton(
          key: const Key('session-arm-default'),
          onPressed: () => _answer(context, entry?.defaultArm ?? 'keep'),
          child: const Text('Carry on as planned'),
        ),
      ];
    }
    final keepFirst = parts[0].toLowerCase().startsWith('keep');
    final arms = [
      (parts[0], keepFirst ? 'keep' : 'shorten'),
      (parts[1], keepFirst ? 'shorten' : 'keep'),
    ];
    return [
      for (final (label, arm) in arms)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: SizedBox(
            width: double.infinity,
            height: 64,
            child: FilledButton(
              key: Key('session-arm-$arm'),
              // The default arm keeps the filled style; the other is tinted
              // down. The label already says which one happens if you do
              // nothing, and the weight says it a second way — a walker
              // reading this has a phone in one hand and a street in front of
              // them, and the two arms should not look like a coin toss.
              style: arm == entry?.defaultArm
                  ? null
                  : FilledButton.styleFrom(
                      backgroundColor:
                          Theme.of(context).colorScheme.secondaryContainer,
                      foregroundColor:
                          Theme.of(context).colorScheme.onSecondaryContainer,
                    ),
              onPressed: () => _answer(context, arm),
              child: Text(
                arm == entry?.defaultArm
                    ? '${_cap(label)} — this happens if you do nothing'
                    : _cap(label),
                textAlign: TextAlign.center,
              ),
            ),
          ),
        ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(Dims.spaceMd),
      padding: const EdgeInsets.all(Dims.spaceLg),
      decoration: _overMap(c),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(question,
              key: const Key('session-question'),
              textAlign: TextAlign.center,
              style: TextStyle(
                  color: c.ink,
                  fontFamily: 'Fraunces',
                  fontSize: 19,
                  height: 1.25,
                  fontWeight: FontWeight.w600)),
          const SizedBox(height: Dims.spaceMd),
          ..._arms(context),
        ],
      ),
    );
  }
}

/// Everything the walk OFFERS but never takes by itself: a queued piece waiting
/// for a tap, a cut piece waiting to be carried on, a chapter at the walker's
/// spot, the fuller telling. Each is on screen silently; only a tap plays it.
class _Offers extends StatelessWidget {
  final OndowayColors c;
  final TourPlaybackService engine;
  const _Offers({required this.c, required this.engine});

  Future<void> _playFullTelling(BuildContext context) async {
    final stop = engine.currentStop;
    if (stop == null) return;
    final tripService = context.read<TripService>();
    try {
      final result = await tripService.generateDeeperDiveAudio(
        stop.stopId ?? stop.beatId ?? stop.poiId,
      );
      final url = result.audioUrl;
      if (url == null) return;
      engine.playFullTelling(url, durationSec: result.durationSec);
    } catch (_) {
      // A provider that fails never crashes the walk; the offer stays on screen.
    }
  }

  @override
  Widget build(BuildContext context) {
    final offers = <Widget>[
      // S7.5: a queued stop under the day's `tap` policy — the offer names the
      // stop, the tap plays it.
      if (engine.armedOffer != null)
        FilledButton.tonalIcon(
          key: const Key('session-armed-offer'),
          onPressed: engine.startArmedPiece,
          icon: const Icon(Icons.play_arrow),
          label: Text('Hear the story · ${engine.armedOffer}'),
        ),
      // S7.6: a piece cut at its door resumes from the cut sentence's start.
      // Nothing resumes by itself.
      if (engine.keepListeningOffer != null) ...[
        FilledButton.tonalIcon(
          key: const Key('session-keep-listening'),
          onPressed: engine.keepListening,
          icon: const Icon(Icons.headphones),
          label: Text('Keep listening · ${engine.keepListeningOffer}'),
        ),
        if (engine.doorLeaveByHhmm != null)
          Text('Leave by ${engine.doorLeaveByHhmm}',
              key: const Key('session-door-leave-by'),
              style: TextStyle(color: c.inkSoft, fontSize: 13)),
        if (engine.keepListeningTranscript != null)
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 120),
            child: SingleChildScrollView(
              child: Text(engine.keepListeningTranscript!,
                  key: const Key('session-transcript'),
                  style: TextStyle(color: c.inkMute, fontSize: 12)),
            ),
          ),
      ],
      // S7.9: after a call, the couple's piece waits for THEIR tap.
      if (engine.resumeOffer != null)
        FilledButton.tonalIcon(
          key: const Key('session-resume-offer'),
          onPressed: engine.resumeInterrupted,
          icon: const Icon(Icons.play_arrow),
          label: Text('Carry on · ${engine.resumeOffer}'),
        ),
      // S7.7(B): a chapter due at the walker's spot.
      if (engine.segmentOffer != null)
        FilledButton.tonalIcon(
          key: const Key('session-chapter-offer'),
          onPressed: engine.startSegment,
          icon: const Icon(Icons.place_outlined),
          label: Text('Hear about ${engine.segmentOffer}'),
        ),
      // S6.6: the linger offer, with its cost. "Again" is a separate control —
      // a re-listen is not the fuller telling.
      if (engine.fullTellingOffer != null)
        Row(children: [
          Expanded(
            child: FilledButton.tonalIcon(
              key: const Key('session-full-offer'),
              onPressed: () => _playFullTelling(context),
              icon: const Icon(Icons.auto_stories_outlined),
              label: Text(engine.fullTellingOffer!),
            ),
          ),
          const SizedBox(width: Dims.spaceSm),
          IconButton(
            key: const Key('session-again'),
            onPressed: engine.playAgain,
            icon: Icon(Icons.replay, color: c.ink),
            tooltip: 'Again',
          ),
        ]),
    ];
    if (offers.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: Dims.spaceMd),
      padding: const EdgeInsets.all(Dims.spaceMd),
      decoration: _overMap(c),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final (i, offer) in offers.indexed) ...[
            if (i > 0) const SizedBox(height: Dims.spaceSm),
            offer,
          ],
        ],
      ),
    );
  }
}

/// Pause and the way home — the two controls that are on every screen of the
/// walk (Nadia, R1.1).
class _WalkControls extends StatelessWidget {
  final OndowayColors c;
  final TourPlaybackService engine;
  const _WalkControls({required this.c, required this.engine});

  /// The phone measures, MATCHES the wrap-up entry the server precomputed for
  /// this stop, and applies it — a selection, never a decision.
  void _headBackNow(BuildContext context) {
    if (engine.state == TourState.completed) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
            content: Text('The walk is over — nothing left to head back from.')),
      );
      return;
    }
    engine.requestWrapUp();
    final entry = engine.matchContingency(engine.measure());
    if (entry != null) {
      engine.applyContingency(entry.contingencyId);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Heading back — the day ends from here.')),
      );
      engine.stopTour();
      context.go('/saved-trips');
    }
  }

  /// The walker found this door shut and said so.
  ///
  /// The server has already worked out what to offer instead, so the phone
  /// records the observation and SELECTS the answer it is holding: the standby
  /// question comes up at once, offline, with no round trip to wait through at
  /// a locked door. Only when no such answer was precomputed does this fall
  /// back to asking the server to replan the day without the stop, which is
  /// what every shut door used to do.
  Future<void> _reportClosed(BuildContext context) async {
    final stop = engine.currentStop;
    if (stop == null) return;
    engine.reportDoorClosed(stop.poiId);
    final held = engine.matchContingency(engine.measure());
    if (held != null) {
      engine.applyContingency(held.contingencyId);
      return;
    }
    final token = context.read<AuthService>().accessToken;
    final tripService = context.read<TripService>();
    final tripId = engine.session?.tripId;
    if (token == null || tripId == null) return;
    try {
      final session = await tripService.replanSession(
        tripId,
        token,
        lat: stop.lat,
        lng: stop.lng,
        wallElapsedSeconds: engine.wallElapsedSeconds,
        tourElapsedSeconds: engine.tourElapsedSeconds,
        observedPace: engine.observedPace,
        listeningRate: engine.listeningRate,
        nextStopIndex: engine.currentStopIndex,
        phoneNextStopHhmm: engine.phoneNextStopHhmm,
        closedStopId: stop.poiId,
      );
      engine.holdSession(session);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not report — try again.')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final showClosed = engine.currentStop?.trigger?.door == true;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
          Dims.spaceMd, 0, Dims.spaceMd, Dims.spaceMd),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (showClosed)
            Padding(
              padding: const EdgeInsets.only(bottom: Dims.spaceSm),
              child: SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  key: const Key('session-closed-report'),
                  onPressed: () => _reportClosed(context),
                  icon: const Icon(Icons.door_front_door_outlined),
                  label: const Text("It's closed"),
                ),
              ),
            ),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  key: const Key('session-pause'),
                  onPressed:
                      engine.isPaused ? engine.resumeTour : engine.pauseTour,
                  icon: Icon(engine.isPaused ? Icons.play_arrow : Icons.pause),
                  label: Text(engine.isPaused ? 'Carry on' : 'Pause'),
                ),
              ),
              const SizedBox(width: Dims.spaceSm),
              Expanded(
                child: FilledButton.tonalIcon(
                  key: const Key('session-head-back'),
                  onPressed: () => _headBackNow(context),
                  icon: const Icon(Icons.home_outlined),
                  label: Text(engine.session?.finishLat == null
                      ? 'Wrap up'
                      : 'Head back now'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

String _clock(num? seconds) {
  final s = (seconds ?? 0).round();
  final m = s ~/ 60;
  final r = s % 60;
  return '$m:${r.toString().padLeft(2, '0')}';
}

/// Circular close button that exits the immersive walk. Sits over the dark map,
/// so it carries its own translucent scrim for contrast.
class _WalkExitButton extends StatelessWidget {
  final OndowayColors c;
  final VoidCallback onTap;
  const _WalkExitButton({required this.c, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black.withValues(alpha: 0.45),
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onTap,
        child: const Tooltip(
          message: 'End walk',
          child: Padding(
            padding: EdgeInsets.all(9),
            child: Icon(Icons.close, size: 22, color: Colors.white),
          ),
        ),
      ),
    );
  }
}

/// The real map behind the guide: a basic Apple Map centered on the current
/// stop, with a pin per stop. Dark map styling + the route polyline +
/// camera-follow are tracked in live-guide-backlog.md (the MapView seam).
class _MapBackdrop extends StatelessWidget {
  final List<ItineraryStop> stops;
  final int currentIndex;
  const _MapBackdrop({required this.stops, required this.currentIndex});

  @override
  Widget build(BuildContext context) {
    if (stops.isEmpty) return const ColoredBox(color: Colors.black);
    final idx = (currentIndex >= 0 && currentIndex < stops.length) ? currentIndex : 0;
    final center = stops[idx];
    return AppleMap(
      initialCameraPosition: CameraPosition(
        target: LatLng(center.lat, center.lng),
        zoom: 15,
      ),
      myLocationEnabled: true,
      annotations: {
        for (final s in stops)
          Annotation(
            // Local's ItineraryStop.beatId is nullable — a rest (a bench) has
            // no story and so no beat (S5.13) — where the fork's was not, so
            // this needs a third fallback the fork did not. poiId is always
            // present and unique within a trip, which is all a pin id needs.
            annotationId: AnnotationId(s.stopId ?? s.beatId ?? s.poiId),
            position: LatLng(s.lat, s.lng),
            infoWindow: InfoWindow(title: s.poiName),
          ),
      },
    );
  }
}

class _NowPlayingPlayer extends StatelessWidget {
  final OndowayColors c;
  final ItineraryStop stop;
  final int index;
  final int total;
  final bool isPlaying;
  final Duration position;
  final Duration duration;
  final VoidCallback onPrev;
  final VoidCallback onPlayPause;
  final VoidCallback onNext;
  const _NowPlayingPlayer({
    required this.c,
    required this.stop,
    required this.index,
    required this.total,
    required this.isPlaying,
    required this.position,
    required this.duration,
    required this.onPrev,
    required this.onPlayPause,
    required this.onNext,
  });

  @override
  Widget build(BuildContext context) {
    final n = index < 0 ? 1 : index + 1;
    // The player's OWN decoded length wins over the wire's stored one once a
    // file is loaded (S7.8): the wire's is an estimate, this is the file.
    final totalSec = duration > Duration.zero
        ? duration.inMilliseconds / 1000.0
        : stop.audioDurationSec;
    final elapsedSec = position.inMilliseconds / 1000.0;
    final remainingSec =
        totalSec == null ? null : (totalSec - elapsedSec).clamp(0, totalSec);
    // Null while nothing is loaded — an indeterminate bar, rather than a bar
    // claiming a position it does not have.
    final progress = duration > Duration.zero
        ? (position.inMilliseconds / duration.inMilliseconds).clamp(0.0, 1.0)
        : null;
    return Container(
      margin: const EdgeInsets.all(Dims.spaceMd),
      padding: const EdgeInsets.all(Dims.spaceLg),
      decoration: BoxDecoration(
        color: c.card,
        borderRadius: BorderRadius.circular(28),
        border: Border.all(color: c.line),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 60,
                height: 60,
                decoration: BoxDecoration(
                  color: c.spark.withValues(alpha: 0.85),
                  borderRadius: BorderRadius.circular(Dims.spaceMd),
                ),
                child: const Icon(Icons.headphones_rounded, color: Colors.white),
              ),
              const SizedBox(width: Dims.spaceMd),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (stop.lensDisplay.isNotEmpty)
                      Text(
                        stop.lensDisplay.toUpperCase(),
                        style: TextStyle(
                            color: c.spark,
                            fontFamily: 'Space Mono',
                            fontSize: 11,
                            letterSpacing: 1.2,
                            fontWeight: FontWeight.w700),
                      ),
                    const SizedBox(height: Dims.spaceXs),
                    Text(
                      stop.poiName,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          color: c.ink,
                          fontFamily: 'Fraunces',
                          fontSize: 22,
                          height: 1.1,
                          fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: Dims.spaceXs),
                    Text('Stop $n of $total',
                        style: TextStyle(color: c.inkMute, fontSize: 13)),
                  ],
                ),
              ),
              Icon(Icons.menu_rounded, color: c.inkMute),
            ],
          ),
          const SizedBox(height: Dims.spaceMd),
          // Where the piece actually is. This was a hardcoded 0.28 until
          // 2026-08-31 — a bar that looked alive and told you nothing.
          Row(
            children: [
              Text(_clock(elapsedSec),
                  style: TextStyle(color: c.inkMute, fontSize: 12)),
              Expanded(
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: Dims.spaceSm),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(Dims.radiusPill),
                    child: LinearProgressIndicator(
                      value: progress,
                      minHeight: 4,
                      backgroundColor: c.line,
                      valueColor: AlwaysStoppedAnimation<Color>(_cobalt),
                    ),
                  ),
                ),
              ),
              Text(remainingSec == null ? '' : '-${_clock(remainingSec)}',
                  style: TextStyle(color: c.inkMute, fontSize: 12)),
            ],
          ),
          const SizedBox(height: Dims.spaceMd),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                key: const Key('tour-replay'),
                onPressed: onPrev,
                icon: Icon(Icons.skip_previous_rounded, color: c.ink, size: 32),
              ),
              const SizedBox(width: Dims.spaceLg),
              GestureDetector(
                key: const Key('tour-playpause'),
                onTap: onPlayPause,
                child: Container(
                  width: 68,
                  height: 68,
                  decoration: BoxDecoration(
                    color: _cobalt,
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: _cobalt.withValues(alpha: 0.5),
                        blurRadius: 20,
                        spreadRadius: 1,
                      ),
                    ],
                  ),
                  child: Icon(
                    isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded,
                    color: Colors.white,
                    size: 34,
                  ),
                ),
              ),
              const SizedBox(width: Dims.spaceLg),
              IconButton(
                key: const Key('tour-skip'),
                onPressed: onNext,
                icon: Icon(Icons.skip_next_rounded, color: c.ink, size: 32),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _WalkingBar extends StatelessWidget {
  final OndowayColors c;
  final bool noAudio;
  final VoidCallback onSkip;
  const _WalkingBar(
      {required this.c, required this.onSkip, this.noAudio = false});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(Dims.spaceMd),
      padding: const EdgeInsets.symmetric(
          horizontal: Dims.spaceLg, vertical: Dims.spaceMd),
      decoration: BoxDecoration(
        color: c.card,
        borderRadius: BorderRadius.circular(Dims.radiusPill),
        border: Border.all(color: c.line),
      ),
      child: Row(
        children: [
          Icon(Icons.directions_walk_rounded, color: c.inkSoft),
          const SizedBox(width: Dims.spaceSm),
          Expanded(
            child: Text(
                noAudio
                    ? 'No audio here — walk to the next stop'
                    : 'Walk to the next stop — audio starts on arrival',
                style: TextStyle(color: c.inkSoft, fontSize: 13)),
          ),
          TextButton(
            key: const Key('tour-walking-skip'),
            onPressed: onSkip,
            child: const Text('Skip'),
          ),
        ],
      ),
    );
  }
}

class _ApproachingNudge extends StatelessWidget {
  final OndowayColors c;
  final String stopName;
  final VoidCallback onAccept;
  final VoidCallback onDismiss;
  const _ApproachingNudge({
    required this.c,
    required this.stopName,
    required this.onAccept,
    required this.onDismiss,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(Dims.spaceMd),
      padding: const EdgeInsets.all(Dims.spaceLg),
      decoration: BoxDecoration(
        color: c.card,
        borderRadius: BorderRadius.circular(Dims.radiusCard),
        border: Border.all(color: c.line),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('Approaching $stopName',
              style: TextStyle(
                  color: c.ink,
                  fontFamily: 'Fraunces',
                  fontSize: 18,
                  fontWeight: FontWeight.w600)),
          const SizedBox(height: Dims.spaceMd),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              TextButton(
                  onPressed: onDismiss, child: const Text('Keep listening')),
              FilledButton(
                key: const Key('tour-nudge-accept'),
                onPressed: onAccept,
                child: const Text('Play now'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _CompletePanel extends StatelessWidget {
  final OndowayColors c;
  final VoidCallback onDone;
  const _CompletePanel({required this.c, required this.onDone});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.check_circle_outline_rounded, color: c.accent, size: 56),
          const SizedBox(height: Dims.spaceMd),
          Text('Tour complete',
              style: TextStyle(
                  color: c.ink,
                  fontFamily: 'Fraunces',
                  fontSize: 26,
                  fontWeight: FontWeight.w600)),
          const SizedBox(height: Dims.spaceMd),
          FilledButton(onPressed: onDone, child: const Text('Done')),
        ],
      ),
    );
  }
}
