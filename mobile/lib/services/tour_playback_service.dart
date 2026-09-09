import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/providers.dart';

export 'package:ondoway/services/providers.dart'
    show
        LocationProvider,
        AudioProvider,
        AudioInterruptionKind,
        AudioRemoteCommand;

enum TourState { idle, active, approaching, completed }

/// The phone's MEASURED divergence, as facts (design §4.6). Every field is a
/// number the phone observed or an id it stands at; none is a decision. The
/// service matches these against the session's contingency triggers, in server
/// order, and takes the first that fits — it never weighs one entry against another.
class Divergence {
  final int? minutesLate;
  final int? minutesEarly;
  final int? minutesLeft;
  final String? skippedStopId;
  final String? wrapUpFromStopId;
  final String? atRiskStopId;

  /// The stop whose door the walker found SHUT and said so (Docs/adr/0006 rule
  /// 5). An observation like every other field here: the server already decided
  /// what to offer instead, and the entry it precomputed is matched on this id.
  final String? closedStopId;

  /// The stop the person is at or has just left (W5.12): a late/early/minutes-
  /// left band is the server's answer FROM that stop, so an entry names it and
  /// the phone matches it. Null = the position is unknown; the band alone
  /// decides (the S5.9 lookup).
  final String? atStopId;

  const Divergence({
    this.minutesLate,
    this.minutesEarly,
    this.minutesLeft,
    this.skippedStopId,
    this.wrapUpFromStopId,
    this.atRiskStopId,
    this.atStopId,
    this.closedStopId,
  });
}

/// One remaining stop's re-timed clocks, in seconds from NOW (Phase 5 S5.10) —
/// the output of the phone's one re-timing expression, [TourPlaybackService.retimeRemaining].
class StopEta {
  final ItineraryStop stop;
  final int secondsToArrival;
  final int secondsToDeparture;
  const StopEta(this.stop, this.secondsToArrival, this.secondsToDeparture);
}

class _Fix {
  final double lat;
  final double lng;
  final DateTime at;
  const _Fix(this.lat, this.lng, this.at);
}

class TourPlaybackService extends ChangeNotifier {
  final LocationProvider _locationService;
  final AudioProvider _audioService;

  // ---- Phase 5 S5.10: the phone's ARITHMETIC (design §4.1/§4.3) ----------
  // Learned pace, learned listening rate, pause as information, and the ONE
  // re-timing expression. Numbers the phone measured; never a decision.

  // ---- Phase 7 S7.3: THE audio placement rule, the phone's half ------------
  // WHERE a piece plays is decided on the server (src/tour/placement.py — the
  // one rule) and rides each stop as its trigger geometry; the day's policy
  // rides the session. The phone asks ONE question of it — "am I at this
  // place?" — and draws no circle of its own: until S7.3 two literals here
  // (10 m to start a piece, 40 m to be "at" a place) decided for a 140 m
  // square and a doorway alike (W7.2 R1, 11/11: both die). Inside the
  // footprint the person is AT the stop: no leg, no pace training, no
  // lateness (W5.2: "wandering inside a stop is not a divergence"). A stop
  // with no trigger has no geometry: nothing auto-plays there; a tap still does.

  /// THE one spelling of "within a radius of a point" on the phone.
  static bool _within(
    double lat,
    double lng,
    double centerLat,
    double centerLng,
    double radiusM,
  ) =>
      haversineDistance(lat, lng, centerLat, centerLng) <= radiusM;

  /// THE one predicate: is (lat, lng) inside [stop]'s placed footprint?
  ///
  /// The radius is the stop's OWN, placed by the server's one rule — which is
  /// why a 140 m courtyard and a doorway do not share a circle. A service-wide
  /// radius briefly lived here as an override and was deleted on 2026-08-31:
  /// it could not express that difference, and it was dead code besides, since
  /// this method returns on a null trigger before ever reading it. A caller
  /// that wants a wider circle gives its stop a wider [StopTrigger].
  static bool _atPlace(ItineraryStop stop, double lat, double lng) {
    final trigger = stop.trigger;
    if (trigger == null) return false;
    return _within(lat, lng, stop.lat, stop.lng, trigger.radiusM);
  }

  /// The stop whose footprint has been touched and whose piece has not yet
  /// started (the family day waits here for the first standstill — R1; a
  /// queued stop for the standstill or the tap — S7.5, R2).
  String? _armedKey;

  /// S7.5 (W7.2 R2, Fiona & Dev / Nadia): the name of the stop whose piece is
  /// armed and waiting for the PERSON'S TAP — a queued stop under the day's
  /// `tap` policy. The screen offers it; [startArmedPiece] is the tap. Null when
  /// nothing waits on a tap.
  String? get armedOffer {
    final key = _armedKey;
    final stop = currentStop;
    if (key == null || stop == null || _audioKeyOf(stop) != key) return null;
    if (_audioService.isPlaying) return null;
    final queued = (stop.trigger?.queueSeconds ?? 0) > 0;
    final byTap = _session?.placement?.queuePieceByTap ?? false;
    return queued && byTap ? stop.poiName : null;
  }

  /// The tap on the offer (S7.5): play the armed piece now.
  void startArmedPiece() {
    if (armedOffer == null) return;
    _armedKey = null;
    _playCurrentStop();
  }

  // ---- Phase 7 S7.6: THE DOOR (design §5.6 threshold silence; W7.2 R3) ------
  // At a stop whose visit goes INSIDE a place you enter, the piece ends at the
  // end of its current sentence when the placed OUTSIDE seconds have run since
  // it started — the only threshold a phone can see under a roof — then the
  // stop's CLOSE plays (through S6.4's one cut: never a second sentence end).
  // Inside, nothing auto-plays: the transcript is on the screen with a
  // keep-listening tap that resumes from the START of the cut sentence. Nothing
  // resumes by itself on exit; the door fires once per stop.

  /// When the current stop's piece started, on the injected clock, and which.
  DateTime? _pieceStartedAt;
  String? _pieceStartedKey;

  /// Stops whose door has fired this walk (once each).
  final Set<String> _doorFired = {};

  /// The stop cut at its door most recently, and the second its cut sentence
  /// began at — the keep-listening tap resumes there. Cleared by the tap or by
  /// the next piece starting.
  ItineraryStop? _doorCutStop;
  int _doorCutFrom = 0;
  bool _doorAdvance = false; // the pending cut is a DOOR cut: advance after it

  /// The stop whose piece was cut at its door and may be resumed by a tap —
  /// its name for the screen's offer; null when no door cut is on record or a
  /// piece is playing.
  String? get keepListeningOffer {
    final stop = _doorCutStop;
    if (stop == null || _audioService.isPlaying) return null;
    return stop.poiName;
  }

  /// The whole transcript of the cut piece (design §5.7: every word spoken is
  /// on the screen) while the offer stands; null otherwise.
  String? get keepListeningTranscript =>
      keepListeningOffer == null ? null : _doorCutStop?.narration;

  /// W7.13, Marcus — R3 quoted him ("transcript and leave-by on screen") and
  /// S7.6 built the transcript half only. While the door's offer stands, the
  /// screen also says WHEN TO LEAVE the interior to keep the plan: the cut
  /// stop's own planned departure — its arrival clock plus its dwell — shown,
  /// never spoken. Null when no door cut is on record or the clocks are absent.
  String? get doorLeaveByHhmm {
    final stop = _doorCutStop;
    if (stop == null || keepListeningOffer == null) return null;
    final t = stop.startTime;
    if (t.length != 5) return null;
    final h = int.tryParse(t.substring(0, 2));
    final m = int.tryParse(t.substring(3, 5));
    if (h == null || m == null) return null;
    final total = (h * 60 + m + stop.durationMin) % (24 * 60);
    final hh = (total ~/ 60).toString().padLeft(2, '0');
    final mm = (total % 60).toString().padLeft(2, '0');
    return '$hh:$mm';
  }

  /// The keep-listening tap (R3): the cut piece again, from the start of the
  /// sentence the door cut — through the one door to a position.
  void keepListening() {
    final stop = _doorCutStop;
    final key = _audioKeyOf(stop);
    if (stop == null || key == null || stop.audioUrl == null || keepListeningOffer == null) {
      return;
    }
    _doorCutStop = null;
    _pieceEndedNaturally = false;
    _audioService.playFrom(key, stop.audioUrl!, Duration(seconds: _doorCutFrom),
        title: stop.poiName);
    notifyListeners();
  }

  /// THE door moment: the piece is this stop's, the stop is a door, its outside
  /// seconds have run — cut at the sentence end (S6.4), then the close.
  void _maybeReachTheDoor() {
    final stop = currentStop;
    final key = _audioKeyOf(stop);
    final started = _pieceStartedAt;
    if (stop == null || key == null || started == null || _pieceStartedKey != key) return;
    final trigger = stop.trigger;
    if (trigger == null || !trigger.door || trigger.outsideSeconds <= 0) return;
    if (_doorFired.contains(key) || _closePending != null) return;
    if (!_audioService.isPlaying || _audioService.currentBeatId != key) return;
    if (_now().difference(started).inSeconds < trigger.outsideSeconds) return;
    _doorFired.add(key);
    final position = _audioService.position;
    _doorCutStop = stop;
    _doorCutFrom = sentenceStartSeconds(stop, position, lengthSec: _playerLengthFor(key));
    _doorAdvance = true;
    _closeLine = stop.closeText;
    _closePending = stop;
    _closePendingIsFull = false;
    final wait = sentenceWaitFor(stop); // S7.8: the player's length, the party's cap
    _sentenceEndTimer?.cancel();
    if (wait <= 0) {
      finishSentenceNow();
    } else {
      _sentenceEndTimer = Timer(
        Duration(milliseconds: (wait * 1000).round()),
        finishSentenceNow,
      );
    }
    notifyListeners();
  }

  /// The piece's sentence boundaries, in seconds from its start — THE one
  /// table behind [secondsToSentenceEnd] and [sentenceStartSeconds] (W6.2 R8;
  /// S7.6): the stop's own narration, word share against the file's length.
  /// Empty when the stop carries no text or no length.
  static List<double> _sentenceBoundaries(ItineraryStop stop, [double? lengthSec]) {
    final text = stop.narration;
    // S7.8 (R6): the player's real length when the caller has it; else the wire's.
    final length = lengthSec ?? stop.audioDurationSec;
    if (text == null || text.trim().isEmpty || length == null || length <= 0) {
      return const [];
    }
    final sentences = text
        .split(RegExp(r'(?<=[.!?])\s+'))
        .where((s) => s.trim().isNotEmpty)
        .toList();
    final words = sentences.map((s) => s.trim().split(RegExp(r'\s+')).length).toList();
    final total = words.fold<int>(0, (a, b) => a + b);
    if (total == 0) return const [];
    final out = <double>[];
    var cumulative = 0;
    for (final w in words) {
      cumulative += w;
      out.add(length * cumulative / total);
    }
    return out;
  }

  /// Seconds into the piece where the sentence playing at [position] BEGAN —
  /// the previous boundary, 0 in the first sentence, the last sentence's start
  /// past the final boundary. The keep-listening tap resumes here (S7.6).
  @visibleForTesting
  static int sentenceStartSeconds(ItineraryStop stop, Duration position, {double? lengthSec}) {
    final boundaries = _sentenceBoundaries(stop, lengthSec);
    if (boundaries.isEmpty) return 0;
    final at = position.inMilliseconds / 1000.0;
    var start = 0.0;
    for (final boundary in boundaries.take(boundaries.length - 1)) {
      if (boundary <= at) start = boundary;
    }
    return start.round();
  }

  /// Moving seconds needed before the learned pace replaces the preset
  /// (§4.1 "within the first ~15 minutes": five minutes of actual walking).
  static const int kPaceLearnAfterSeconds = 300;

  /// A segment slower than this is standing (a crossing, a conversation);
  /// faster is not walking (a bus). Neither trains the pace.
  static const double kMinWalkingMps = 0.4;
  static const double kMaxWalkingMps = 3.0;

  /// The same straight-line correction the server's estimate uses
  /// (src/tour/routing.py HAVERSINE_CORRECTION) — one number, both sides.
  static const double kHaversineCorrection = 1.35;

  /// The base speed the wire's pace multiplier is on (routing.py PACE_KMH).
  static const double kServerPaceKmh = 3.0;

  final DateTime Function() _now;
  DateTime? _startedAt;
  // The TOUR clock starts at the first play or the first step off the square
  // (W5.2 R1.3; W5.14 Fiona & Dev: "our tour clock ran from 15:00 while we
  // stood on the square"); the WALL clock starts at the tap.
  DateTime? _tourClockStartedAt;
  _Fix? _firstFix;
  // The TWO clocks (§4.3): the wall keeps spending through a pause; the tour
  // clock is suspended by it. A pause is information, never lateness.
  int _pausedSeconds = 0;
  DateTime? _pausedAt;
  int _pauseCount = 0;
  int _longestPauseSeconds = 0;
  // Learned pace: moving metres over moving seconds, on walking legs only.
  double _movingMeters = 0;
  double _movingSeconds = 0;
  _Fix? _lastFix;
  // Learned listening rate: wall seconds spent listening over stated seconds
  // (a replay adds wall time against the same stated length; a faster
  // playback speed spends less wall time on it).
  double _statedListened = 0;
  double _wallListened = 0;
  final Set<String> _statedKeys = {};
  DateTime? _playingSince;
  String? _playingKey;
  // Skip = unplayed AND unopened (a piece read on the screen was not skipped).
  final Set<String> _played = {};
  final Set<String> _opened = {};
  String? _lastSkippedPoiId;
  bool _wrapUpRequested = false;
  // THE SEAM's phone half: what the phone REPORTED about a clock gap. Lines
  // for the screen; nothing is ever corrected from them.
  final List<String> _clockNotices = [];

  // ---- Phase 5 S5.11: announcement etiquette (design §4.4; W5.2 R3/R4) ----
  /// Standing still this long at a stop before a moment can be "natural" — the
  /// settling period the panel named (shaking out rain, looking up: R3).
  static const int kSettleSeconds = 30;

  /// A fix this far from the last one is movement.
  static const double kStillRadiusM = 8.0;

  /// R4 — the day-long screen-only switch, per party: after the second pause
  /// of the day, or one pause longer than this (couple: 10 min; family: 5).
  static const int kLongPauseCoupleSeconds = 600;
  static const int kLongPauseFamilySeconds = 300;

  /// The one plain sentence said once when the switch flips (Aiko, Camille).
  static const String kScreenOnlyLine =
      "I'll keep this on the screen from here — tap the speaker to hear it again.";

  // The speech QUEUE (§4.4.1): at most one line waiting to be said, and it is
  // already on the screen. A newer line replaces an older one unsaid (a stale
  // sentence EXPIRES undelivered — R3); the question is the one line that
  // survives to the next moment, said once (R2.5, R4).
  String? _queuedLine;
  String? _queuedLineAudioUrl; // S6.8: the line's pre-voiced file, when it has one
  bool _queuedIsQuestion = false;
  bool _questionSpoken = false;
  final List<String> _spoken = [];
  DateTime? _stillSince;
  bool _pieceEndedNaturally = false;
  bool _transcriptOpen = false;
  // R4: pauses per stop (solo: two pauses inside ONE stop silence that stop).
  final Map<String, int> _pausesAtStop = {};
  bool _voiceRestored = false;
  bool _screenOnlyAnnounced = false;
  bool _pausedMidPiece = false;

  List<ItineraryStop> _stops = [];
  // The day as planned — the list handed to [startTour]. Every entry the phone
  // applies is an ordered subset of THIS (§4.7: it cannot add what it does not
  // hold), so ids are always mapped over it, never over the working list.
  List<ItineraryStop> _planned = [];
  int _currentStopIndex = -1;
  int? _pendingStopIndex;
  TourState _state = TourState.idle;
  double? _distanceToNext;

  // THE LIVING SESSION the phone holds (Phase 5, design §4.6/§4.7): the plan
  // the server sent and its contingency set. The phone SELECTS from it — the
  // one method that changes the current plan is [applyContingency], and its
  // only decision input is a server contingency id.
  SessionPlan? _session;
  SessionContingency? _selected;
  String? _screenText;
  String? _pendingQuestion;

  // ---- Phase 6 S6.4: the CLOSE (design §5.3; W6.2 R8) ----------------------
  // [Head back now] is the seam the person made: the current SENTENCE finishes,
  // the piece does not; then the stretch's close — one line — then the way home
  // on the screen; then nothing. The close is narrator CONTENT: it plays as its
  // own pre-voiced file when the wire carries one (S6.8), else it is handed to
  // the one door and is on the screen either way (§4.4.2).
  String? _closeLine;
  String? _threadLine;
  final List<String> _closesPlayed = [];
  Timer? _sentenceEndTimer;
  ItineraryStop? _closePending;
  bool _closePendingIsFull = false; // S6.6/S6.8: the pending cut is a FULL piece

  /// Seconds a tapped piece may keep playing to reach its sentence end before
  /// it is cut anyway — a person who tapped is not made to wait out a
  /// forty-second sentence (Fiona: "five seconds is the whole budget"). W7.2
  /// R6 (S7.8): eight, not twelve; a `wall` day five; the family nothing.
  static const int kSentenceEndCapSeconds = 8;
  static const int kSentenceEndCapWallSeconds = 5;

  /// THE cap in force for this day (S7.8; W7.2 R6): the family cuts at once
  /// (Nadia — a child is already walking), a day with a hard end waits at most
  /// five seconds (Marcus), everyone else eight. W7.13 (F&D): the number rides
  /// the WIRE's policy block — the server decided it from the party and the
  /// hardness; the party branch below is only the stand-in for an older
  /// session whose policy predates the field.
  double get sentenceEndCapSeconds {
    final session = _session;
    final wired = session?.placement?.sentenceCapS;
    if (wired != null) return wired;
    if (session?.party == 'family') return 0;
    if (session?.endHardness == 'wall') return kSentenceEndCapWallSeconds.toDouble();
    return kSentenceEndCapSeconds.toDouble();
  }

  /// The player's REAL length for the piece under [key], in seconds, when the
  /// player is on it and knows it; null otherwise (the wire's length then
  /// stands in). S7.8: the wire's number is the estimate, the player's the fact.
  double? _playerLengthFor(String? key) {
    if (key == null || _audioService.currentBeatId != key) return null;
    final length = _audioService.duration;
    if (length <= Duration.zero) return null;
    return length.inMilliseconds / 1000.0;
  }

  /// THE ONE EXPRESSION of "how long until this sentence ends" the door, the
  /// wrap-up and the full telling all read (S6.4's arithmetic over the stop's
  /// own text; S7.8: over the player's real length when it is loaded, the
  /// wire's when not; capped by the party's cap). [playingKey] names the piece
  /// the player is on when it is not the stop's own (the full telling's key).
  @visibleForTesting
  double sentenceWaitFor(ItineraryStop stop, {String? playingKey}) =>
      secondsToSentenceEnd(
        stop,
        _audioService.position,
        lengthSec: _playerLengthFor(playingKey ?? _audioKeyOf(stop)),
        cap: sentenceEndCapSeconds,
      );

  VoidCallback? _locationListener;
  VoidCallback? _audioListener;

  // ---- Phase 7 S7.9: INTERRUPTIONS (design §5.6 C7; W7.2 R5, 11/11) --------
  // A call, Siri, another app's voice or music PAUSE the piece — not the
  // person's pause, so nothing is counted against them. The START of the cut
  // sentence is remembered (S6.4's arithmetic over the player's real length —
  // S7.8). When the interruption ends: still inside the stop's footprint, the
  // piece resumes from that start by itself, saying nothing — the COUPLE by
  // their tap (F&D: "we restart when the conversation pauses"); off the
  // footprint, nothing resumes: the stop is over, the missed CLOSE goes on the
  // screen and into the one queue for the next standing seam (never on a leg).
  // A navigation prompt only DUCKS the piece (the player's mechanics): the
  // policy does nothing. A photo never reaches here.
  StreamSubscription<AudioInterruptionKind>? _interruptionSub;
  ItineraryStop? _interruptedStop;
  String? _interruptedKey;
  int _interruptedFrom = 0;
  bool _resumeOffered = false;

  /// The couple's resume offer (R5): the interrupted stop's name while its
  /// piece waits for their tap; null otherwise.
  String? get resumeOffer {
    final stop = _interruptedStop;
    if (stop == null || !_resumeOffered || _audioService.isPlaying) return null;
    return stop.poiName;
  }

  /// The tap on the offer: resume from the cut sentence's start.
  void resumeInterrupted() {
    if (resumeOffer == null) return;
    _resumeInterruptedNow();
  }

  /// THE policy, one site: what the walk does with an interruption.
  void _onInterruption(AudioInterruptionKind kind) {
    switch (kind) {
      case AudioInterruptionKind.duckBegin:
        return; // ducked, not cut: the piece carries on (R5)
      case AudioInterruptionKind.pauseBegin:
        final stop = currentStop;
        final key = _audioKeyOf(stop);
        // Only a stop's OWN piece is remembered — not a leg line, a chapter, a
        // close, and not a piece already over (the player keeps its last id).
        if (stop == null ||
            key == null ||
            _audioService.currentBeatId != key ||
            _audioService.isCompleted) {
          return;
        }
        _interruptedStop = stop;
        _interruptedKey = key;
        _interruptedFrom = sentenceStartSeconds(
          stop,
          _audioService.position,
          lengthSec: _playerLengthFor(key),
        );
        _resumeOffered = false;
        _audioService.pause();
        _pieceEndedNaturally = false; // a cut is not a seam (R3)
        notifyListeners();
      case AudioInterruptionKind.ended:
        final stop = _interruptedStop;
        if (stop == null) return;
        final fix = _lastFix;
        final inside = fix != null && _atPlace(stop, fix.lat, fix.lng);
        if (!inside) {
          // Off the footprint: nothing resumes; the stop is over; the missed
          // close on screen and queued for the next standing seam.
          _interruptedStop = null;
          _interruptedKey = null;
          _missedClose(stop);
          _advancePast();
          return;
        }
        // W7.13 (F&D): the resume rule rides the wire's policy; the party
        // branch is the stand-in for an older session without the field.
        final byTap = _session?.placement?.interruptionResume != null
            ? (_session?.placement?.interruptionResumeByTap ?? false)
            : _session?.party == 'couple';
        if (byTap) {
          _resumeOffered = true;
          notifyListeners();
          return;
        }
        _resumeInterruptedNow();
    }
  }

  void _resumeInterruptedNow() {
    final stop = _interruptedStop;
    final key = _interruptedKey;
    final url = stop?.audioUrl;
    _interruptedStop = null;
    _interruptedKey = null;
    _resumeOffered = false;
    if (stop == null || key == null || url == null) return;
    _pieceEndedNaturally = false;
    _audioService.playFrom(key, url, Duration(seconds: _interruptedFrom),
        title: stop.poiName);
    notifyListeners();
  }

  /// The close a cut-and-abandoned stop never got to say: on the screen now,
  /// through the one queue door for the next standing seam (R5: "the missed
  /// close is played" — Rosemary; "once and done" — Greta).
  void _missedClose(ItineraryStop stop) {
    final text = stop.closeText;
    if (text == null || text.isEmpty) return;
    _closeLine = text;
    _closesPlayed.add(text);
    _queue(text, isQuestion: false, audioUrl: stop.closeAudioUrl);
    notifyListeners();
  }

  // ---- Phase 8 S8.7: THE LOCK SCREEN (persona 09, Fiona & Dev step 4) -------
  // "Dev pauses the tour without mentioning it, mid-sentence, because a voice in
  // his ear is now in the way. The pause is not an interruption of the product.
  // It is the product being used correctly." They do it five times in three
  // hours and the phone never leaves the pocket, so the button they actually
  // reach is the platform's, on the lock screen.
  //
  // It arrives HERE, at the door the in-app pause already uses. F&D's pause
  // suspends the TOUR — the clock stops, the pause is counted as theirs, the
  // wall keeps spending (§4.3) — and a press that stopped only the player would
  // leave the session clock running and the plan drifting, which is their own
  // step-4 complaint ("from here the app's finish time is fiction") made worse.
  // There is no second pause path: the command IS [pauseTour] / [resumeTour].
  //
  // This is NOT S7.9. An interruption is the platform TAKING the audio and is
  // never counted against the person; a transport button is the person ASKING.
  StreamSubscription<AudioRemoteCommand>? _remoteSub;

  void _onRemoteCommand(AudioRemoteCommand command) {
    switch (command) {
      case AudioRemoteCommand.pause:
        pauseTour();
      case AudioRemoteCommand.play:
        resumeTour();
    }
  }

  /// Is a piece of the CURRENT stop loaded and unfinished — something a resume
  /// should bring back? The lock screen's button moves the PLAYER first and
  /// reaches the pause door a beat later, so "was a piece running" cannot be
  /// asked of the instant. A piece the walk has already left behind is not one
  /// of these: an abandoned stop's (S7.9) and a finished telling's keys no
  /// longer belong to the stop underfoot, and an INTERRUPTED piece is S7.9's
  /// policy to bring back — by itself or by the couple's tap — never this one's.
  bool get _pieceOfThisStopIsSuspended {
    final key = _audioKeyOf(currentStop);
    final loaded = _audioService.currentBeatId;
    if (key == null || loaded == null) return false;
    if (_audioService.isCompleted || _interruptedStop != null) return false;
    return loaded == key || loaded.startsWith('$key-');
  }

  // Getters
  TourState get state => _state;
  int get currentStopIndex => _currentStopIndex;
  int? get pendingStopIndex => _pendingStopIndex;
  double? get distanceToNext => _distanceToNext;
  ItineraryStop? get currentStop =>
      _currentStopIndex >= 0 && _currentStopIndex < _stops.length
          ? _stops[_currentStopIndex]
          : null;
  /// The stops this walk is actually made of, in order — THE screen's source
  /// for what to draw.
  ///
  /// A page is handed a trip in order to START a walk; once one is running this
  /// list is the truth. A session composed on the server carries placed trigger
  /// geometry and voiced files the pushed trip does not, and a contingency can
  /// reorder or drop stops mid-walk. Drawing from the page's copy would leave a
  /// pin on the map for a stop the day no longer visits.
  List<ItineraryStop> get plannedStops => List.unmodifiable(_stops);

  ItineraryStop? get nextStop => _currentStopIndex + 1 < _stops.length
      ? _stops[_currentStopIndex + 1]
      : null;
  bool get isActive =>
      _state != TourState.idle && _state != TourState.completed;
  bool get hasPendingStop => _pendingStopIndex != null;
  SessionPlan? get session => _session;
  SessionContingency? get selectedContingency => _selected;
  /// The one line the screen shows for the current state (§4.4.2 — everything
  /// spoken is also on screen; a fabric change is on screen only).
  String? get screenText => _screenText;
  /// The ONE question, when a selected entry carries one (§4.2); null otherwise.
  String? get pendingQuestion => _pendingQuestion;
  /// The close the last wrap-up put on the screen (S6.4) — the stretch's own
  /// line, or the day's when no piece was playing. Null until a wrap-up.
  String? get closeLine => _closeLine;

  /// S6.5 (W6.2 R5): the THREAD of the pair the session just made — one
  /// sentence, on screen through the leg, spoken once at a standing seam.
  String? get threadLine => _threadLine;

  /// The walking line of a leg whose FILE is absent, on the SCREEN for the
  /// whole leg (ADR: a replan drops a stale line and rewrites its words; the
  /// audio catches up in the background, and until it does the leg is silent
  /// rather than wrong — but the person still gets the direction, in text).
  /// Null while at a stop, and the moment the target's footprint is reached.
  String? get legTextLine => _legTextLine;
  String? _legTextLine;

  /// A text-only leg is UNDER WAY: the re-voiced file may have landed on the
  /// server since this session was fetched (the ADR's background voicing, with
  /// this walker's arrival as its deadline). The page watches this and
  /// refetches the session; [adoptSessionAudio] takes the fresh audio in.
  bool get audioCatchUpDue => _legTextLine != null;

  /// Adopt AUDIO the server has voiced since the held session was fetched —
  /// never a plan change: a fresh stop's file is taken only for the stop the
  /// walk already holds, matched by id, and only when the fresh WORDS are the
  /// held words (a mismatch means another replan happened; that arrives
  /// through [holdSession], never through this door). Returns how many stops
  /// gained a file.
  int adoptSessionAudio(List<ItineraryStop> fresh) {
    final byId = {
      for (final stop in fresh)
        if (stop.stopId != null) stop.stopId!: stop,
    };
    // The same stop rides both the working list and the planned one; one
    // adoption is one stop, counted once by id.
    final adoptedIds = <String>{};
    List<ItineraryStop> take(List<ItineraryStop> held) => [
          for (final stop in held)
            () {
              final update = stop.stopId == null ? null : byId[stop.stopId];
              if (update == null ||
                  stop.legAudioUrl != null ||
                  update.legAudioUrl == null ||
                  update.legNarration != stop.legNarration) {
                return stop;
              }
              adoptedIds.add(stop.stopId!);
              return stop.copyWith(
                legAudioUrl: update.legAudioUrl,
                legAudioDurationSec: update.legAudioDurationSec,
              );
            }(),
        ];
    final stops = take(_stops);
    final planned = take(_planned);
    if (adoptedIds.isEmpty) return 0;
    _stops = List.unmodifiable(stops);
    _planned = List.unmodifiable(planned);
    notifyListeners();
    return adoptedIds.length;
  }

  /// S6.6 (design §5.5; W6.2 R3, 9/11 by name): THE LINGER RULE — a linger
  /// OFFERS the full telling on the screen, silently; a TAP plays it. Never
  /// auto-play on stillness. The offer shows only at a standing seam INSIDE the
  /// stop's circle, after the stop's own tight piece ended on its own — never
  /// while paused, never mid-piece, never at an unplanned place (only planned
  /// stops carry a full telling), and it names its cost (Marcus: "Full telling
  /// · 7 min"). Null when the stop has no authored full telling: the offer
  /// simply never appears there.
  String? get fullTellingOffer {
    final stop = _offeredFullStop();
    if (stop == null) return null;
    return 'Full telling · ${stop.fullTellingMinutes} min';
  }

  ItineraryStop? _offeredFullStop() {
    if (!isNaturalMoment) return null;
    final fix = _lastFix;
    if (fix == null) return null;
    final stop = _stopUnderfoot(fix);
    if (stop == null || stop.fullNarration == null) return null;
    final key = _audioKeyOf(stop);
    // The tight telling comes first: no offer before its piece has ENDED on its
    // own (isNaturalMoment's not-begun arm must not offer).
    if (key == null || !_played.contains(key) || !_pieceEndedNaturally) return null;
    return stop;
  }

  /// The TAP on the offer: play the voiced full telling (the on-demand door's
  /// artifact — S6.6 makes it the authored full telling, never the raw dump).
  /// A second piece at this stop, through the player's own door.
  void playFullTelling(String audioUrl, {num? durationSec}) {
    final stop = _offeredFullStop();
    if (stop == null) return;
    _pieceEndedNaturally = false;
    _fullPieceDurationSec = durationSec;
    _audioService.play('${_audioKeyOf(stop)!}-full', audioUrl,
        isDeeperDive: true, title: stop.poiName);
    notifyListeners();
  }

  num? _fullPieceDurationSec; // S6.8: the playing full telling's length

  /// "AGAIN" is a separate control from "more" (W6.2 R3, 8 personas by name:
  /// the re-listen is not the full telling): replay THIS stop's tight piece.
  void playAgain() {
    final fix = _lastFix;
    final stop = fix == null ? null : _stopUnderfoot(fix);
    if (stop == null || stop.audioUrl == null) return;
    _pieceEndedNaturally = false;
    _audioService.play(_audioKeyOf(stop)!, stop.audioUrl!, title: stop.poiName);
    notifyListeners();
  }
  /// Every close this session played or said, in order (for the screen's
  /// record and for tests).
  List<String> get closesPlayed => List.unmodifiable(_closesPlayed);

  TourPlaybackService({
    required LocationProvider locationService,
    required AudioProvider audioService,
    DateTime Function()? now,
  })  : _locationService = locationService,
        _audioService = audioService,
        _now = now ?? DateTime.now;

  // ---- S5.10 getters: the measured numbers -------------------------------

  /// Real seconds since the tour started (a pause does NOT stop this one).
  int get wallElapsedSeconds =>
      _startedAt == null ? 0 : _now().difference(_startedAt!).inSeconds;
  int get _openPauseSeconds =>
      _pausedAt == null ? 0 : _now().difference(_pausedAt!).inSeconds;

  /// The tour clock: seconds since the first play or the first step off the
  /// square, minus every pause since (§4.3). Zero while the person still
  /// stands where they started.
  int get tourElapsedSeconds {
    final from = _tourClockStartedAt;
    if (from == null) return 0;
    return max(
      0,
      _now().difference(from).inSeconds - _pausedSeconds - _openPauseSeconds,
    );
  }

  /// The tour clock has started (first play or first step off the square).
  bool get tourClockRunning => _tourClockStartedAt != null;

  void _startTourClockIfNeeded() {
    _tourClockStartedAt ??= _now();
  }

  // ---- W5.14 Q3: a firm or wall finish that has moved ----------------------
  String? _finishMovedLine;
  int? _finishMovedNoticedAt;

  /// One screen line when a FIRM or WALL finish has moved past the tolerance
  /// (W5.14 Q3, the panel's majority: "one line on the screen at the next
  /// moment, never spoken, never a question"; Fiona & Dev's 18:00 became 18:53
  /// in silence). An OPEN day's finish moves in silence — nobody asked for that
  /// clock. Re-issued only when the finish moves again by more than the
  /// tolerance.
  String? get finishMovedLine => _finishMovedLine;

  void _checkFinishMoved() {
    if (!isActive) {
      _finishMovedLine = null; // a walk that is over has no finish to defend
      return;
    }
    final session = _session;
    final eta = finishEtaSeconds;
    if (session == null ||
        eta == null ||
        _startedAt == null ||
        session.plannedEndHhmm.length != 5 ||
        session.endHardness == 'open') {
      return;
    }
    final nowClock = dayFrameHhmm(eta);
    if (nowClock.isEmpty) return;
    // Only a finish that moved LATER is news (R1.4: early days lengthen what is
    // there in silence).
    final gap = _hhmmGapSeconds(nowClock, session.plannedEndHhmm);
    if (gap <= session.retimeToleranceSeconds) {
      _finishMovedLine = null;
      _finishMovedNoticedAt = null;
      return;
    }
    final last = _finishMovedNoticedAt;
    if (last != null && (gap - last).abs() <= session.retimeToleranceSeconds) {
      return; // said once; the clock line on screen keeps moving
    }
    _finishMovedNoticedAt = gap;
    _finishMovedLine = '${_cap(session.finishName)} now looks like $nowClock, '
        'not ${session.plannedEndHhmm}.';
  }

  static String _cap(String s) =>
      s.isEmpty ? s : '${s[0].toUpperCase()}${s.substring(1)}';
  bool get isPaused => _pausedAt != null;
  int get pauseCount => _pauseCount;
  int get longestPauseSeconds => max(_longestPauseSeconds, _openPauseSeconds);

  /// The preset: the speed this day was planned at (from the session).
  double get presetPaceMps =>
      (_session?.walkingPaceKmh ?? kServerPaceKmh) / 3.6;

  /// The person's own pace, once enough walking has been measured; else null.
  double? get learnedPaceMps => _movingSeconds >= kPaceLearnAfterSeconds
      ? _movingMeters / _movingSeconds
      : null;
  double get paceMps => learnedPaceMps ?? presetPaceMps;

  /// The learned pace as the wire's multiplier on the server's base speed
  /// (>= 1.0 slows; the planner does not plan faster than its base).
  double? get observedPace {
    final learned = learnedPaceMps;
    if (learned == null || learned <= 0) return null;
    return max(1.0, (kServerPaceKmh / 3.6) / learned);
  }

  /// Wall seconds spent listening per stated second, once a whole piece has
  /// been heard (>= 60 s stated); 1.0 until then. Clamped to the wire's range.
  double get listeningRate => _statedListened >= 60
      ? (_wallListened / _statedListened).clamp(0.5, 3.0)
      : 1.0;

  /// What the phone reported about a clock gap (S5.10's seam), for the screen.
  List<String> get clockNotices => List.unmodifiable(_clockNotices);

  /// Start a tour with the given stops. Begins GPS tracking and geofence
  /// monitoring.
  Future<bool> startTour(List<ItineraryStop> stops) async {
    if (stops.isEmpty) return false;

    _stops = List.unmodifiable(stops);
    _planned = _stops;
    _currentStopIndex = 0;
    _pendingStopIndex = null;
    _armedKey = null;
    _pieceStartedAt = null;
    _pieceStartedKey = null;
    _doorFired.clear();
    _doorCutStop = null;
    _doorAdvance = false;
    _chapterClosesSaid.clear();
    _state = TourState.active;
    _startedAt = _now();
    _tourClockStartedAt = null;
    _firstFix = null;
    _finishMovedLine = null;
    _finishMovedNoticedAt = null;
    _pausedSeconds = 0;
    _pausedAt = null;
    _pauseCount = 0;
    _longestPauseSeconds = 0;
    _movingMeters = 0;
    _movingSeconds = 0;
    _lastFix = null;
    _statedListened = 0;
    _wallListened = 0;
    _statedKeys.clear();
    _playingSince = null;
    _playingKey = null;
    _played.clear();
    _opened.clear();
    _lastSkippedPoiId = null;
    _wrapUpRequested = false;
    _closedStopId = null;
    _clockNotices.clear();
    _closeLine = null;
    _threadLine = null;
    _legTextLine = null;
    _closesPlayed.clear();
    _sentenceEndTimer?.cancel();
    _sentenceEndTimer = null;
    _closePending = null;
    _queuedLine = null;
    _queuedLineAudioUrl = null;
    _queuedIsQuestion = false;
    _questionSpoken = false;
    _spoken.clear();
    _stillSince = null;
    _pieceEndedNaturally = false;
    _transcriptOpen = false;
    _pausesAtStop.clear();
    _voiceRestored = false;
    _screenOnlyAnnounced = false;

    // THE AUDIO SESSION IS OPENED FIRST, BEFORE TRACKING. iOS grants the
    // .playback session only to a frontmost app and refuses activation from a
    // background callback (AVAudioSessionErrorCodeCannotInterruptOthers). The
    // tour's first piece is usually triggered by a geofence with the phone
    // already locked in a pocket, so if the door is not opened here — at the
    // start, on screen — the native player has no active session and the walk
    // is silent.
    //
    // The ORDER is the fix, and it is load-bearing rather than cosmetic. This
    // ran the other way round until 2026-08-31: tracking started, and only then
    // was the session prepared. The moment background tracking begins the app
    // may be backgrounded, so the activation that followed it was racing the
    // lock screen for the one window in which iOS would have allowed it.
    await _audioService.prepareSession();

    // Start GPS tracking that SURVIVES the screen locking. The phone spends the
    // walk in a pocket; foreground-only tracking stops the moment it locks, the
    // next stop's footprint is never reached, and no piece ever fires — the walk
    // just goes quiet. Asked for explicitly here because the default is false
    // for the planning screens, which only need a fix while they are on screen.
    final started = await _locationService.startTracking(background: true);
    if (!started) {
      _state = TourState.idle;
      notifyListeners();
      return false;
    }

    // Listen to position updates
    _locationListener = () => _onPositionUpdate();
    _locationService.addListener(_locationListener!);

    // Listen to audio completion
    _audioListener = () => _onAudioStateChanged();
    _audioService.addListener(_audioListener!);
    // S7.9: the platform's interruptions, through the one door.
    _interruptedStop = null;
    _interruptedKey = null;
    _resumeOffered = false;
    _interruptionSub?.cancel();
    _interruptionSub = _audioService.interruptions.listen(_onInterruption);
    // S8.7: the lock screen's transport, through the ONE pause door.
    _remoteSub?.cancel();
    _remoteSub = _audioService.remoteCommands.listen(_onRemoteCommand);

    notifyListeners();
    return true;
  }

  /// Stop the tour, cancel tracking.
  void stopTour() {
    if (_locationListener != null) {
      _locationService.removeListener(_locationListener!);
      _locationListener = null;
    }
    if (_audioListener != null) {
      _audioService.removeListener(_audioListener!);
      _audioListener = null;
    }
    _locationService.stopTracking();
    _audioService.stop();
    // Give the audio session back. Without this the `.duckOthers` category
    // keeps the tourist's own music or podcast quiet for the rest of the app's
    // life, long after the walk is over.
    _audioService.releaseSession();
    _sentenceEndTimer?.cancel();
    _sentenceEndTimer = null;
    _closePending = null;
    _interruptionSub?.cancel();
    _interruptionSub = null;
    _remoteSub?.cancel(); // S8.7
    _remoteSub = null;
    _interruptedStop = null;
    _interruptedKey = null;
    _resumeOffered = false;
    _stops = [];
    _planned = [];
    _currentStopIndex = -1;
    _pendingStopIndex = null;
    _armedKey = null;
    _pieceStartedAt = null;
    _pieceStartedKey = null;
    _doorCutStop = null;
    _doorAdvance = false;
    _legTextLine = null;
    _state = TourState.idle;
    _distanceToNext = null;
    _startedAt = null;
    _tourClockStartedAt = null;
    _pausedAt = null;
    notifyListeners();
  }

  // ---- S5.10: pause as information (design §4.3) --------------------------

  /// Pause the tour: the audio through the provider door, and the TOUR clock
  /// suspended — the wall keeps spending. Repeated pauses are counted for the
  /// screen-only switch (S5.11, W5.2 R4); they are never lateness.
  void pauseTour() {
    if (_startedAt == null || _pausedAt != null) return;
    _pausedAt = _now();
    _pauseCount++;
    final key = _audioKeyOf(currentStop);
    if (key != null) _pausesAtStop[key] = (_pausesAtStop[key] ?? 0) + 1;
    // A piece cut off mid-way is not a seam (R3); a conversation after a
    // piece ended on its own leaves that seam where it was. S8.7: the lock
    // screen pauses the player a beat BEFORE its command reaches this door, so
    // the second arm asks the piece, not the instant — without it a tour paused
    // from the pocket and resumed in the app comes back in silence.
    _pausedMidPiece = _audioService.isPlaying || _pieceOfThisStopIsSuspended;
    if (_pausedMidPiece) _pieceEndedNaturally = false;
    _audioService.pause();
    _maybeAnnounceScreenOnly();
    notifyListeners();
  }

  void resumeTour() {
    final at = _pausedAt;
    if (at == null) return;
    final length = _now().difference(at).inSeconds;
    _pausedSeconds += length;
    if (length > _longestPauseSeconds) _longestPauseSeconds = length;
    _pausedAt = null;
    if (_pausedMidPiece) _audioService.resume(); // the piece comes back untouched
    _pausedMidPiece = false;
    _maybeAnnounceScreenOnly();
    _checkFinishMoved();
    // Unpause, reconciled (R3): a natural moment ONLY when play resumes at a
    // stop with the piece not yet started — one sentence BEFORE the piece.
    _drainSpeech();
    notifyListeners();
  }

  /// The transcript is open on the screen (Paulo): no voice while reading.
  void setTranscriptOpen(bool open) {
    _transcriptOpen = open;
    if (!open) _drainSpeech();
    notifyListeners();
  }

  /// A clock tick from the screen (or a timer): standing still is measured by
  /// time passing without a fix moving, and the geolocator's distance filter
  /// sends no fix while nobody moves — so the moment must be re-checked here.
  void tick() {
    if (_startedAt == null) return;
    _checkFinishMoved();
    _drainSpeech();
    // S7.3: the family's standstill is measured by time passing, like the
    // natural moment — re-checked here for the same reason.
    _maybeStartArmed();
    _maybeReachTheDoor(); // S7.6: the outside seconds run on the clock
    _maybeStartSegment(); // S7.7 (B): the standstill at an anchor, by the clock
  }

  // ---- Phase 7 S7.7 (B): THE CHAPTERS (design §5.6 "segments"; W7.2 R4) ---
  // A marquee's story is cut on the server at its reviewed anchors; each
  // chapter rides the stop with its own place, radius and file. After the
  // stop's story, a chapter plays ITSELF outdoors at the first standstill
  // inside its anchor's radius (solo days only — the couple and the family tap
  // for every chapter); under a roof it is offered on the screen and the tap
  // plays it (GPS is useless inside); told once is told; a chapter completing
  // advances nothing (its key is not the stop's).

  static String _segmentKey(String stopKey, int index) => '$stopKey-seg-$index';

  /// Stops whose GOODBYE has been said after their last chapter (once each).
  final Set<String> _chapterClosesSaid = {};

  /// W7.11 defect 15 (the blind listening panel, ALL ELEVEN of them): a chaptered
  /// stop's GOODBYE is the LAST thing said at that place. Until this the close ended
  /// the story piece, so a marquee said farewell on arrival and then spoke again at
  /// the chapter — "a farewell said at hello" (Julien); "I would take the earbud out
  /// and put the phone away" (Théo); "'That's the walk' is a statement about my clock.
  /// If I hear it and then hear more, I stop trusting everything else that voice tells
  /// me about time" (Marcus). The server now keeps the close OUT of a chaptered story
  /// (`render_md.stop_narration_text`); it is played here, once, when the stop's last
  /// VOICED chapter has been told. A chapter the walker never reaches leaves the
  /// goodbye unsaid — Marcus's own rule: "the last thing said at the last place, or it
  /// is not said" — while the door (S7.6) and the wrap-up (S6.4) keep their own claims
  /// on it.
  void _maybeCloseAfterLastChapter() {
    final key = _audioService.currentBeatId;
    if (key == null || !_audioService.isCompleted) return;
    for (final stop in _planned) {
      final stopKey = _audioKeyOf(stop);
      if (stopKey == null || stop.segments.isEmpty) continue;
      final voiced = <String>[
        for (var i = 0; i < stop.segments.length; i++)
          if (stop.segments[i].audioUrl != null) _segmentKey(stopKey, i),
      ];
      if (!voiced.contains(key)) continue; // not this stop's chapter
      if (voiced.any((k) => !_played.contains(k))) return; // a chapter is still owed
      if (!_chapterClosesSaid.add(stopKey)) return; // said once
      _playClose(stop);
      return;
    }
  }

  /// The stop the walker is at, its key, and the indices of its UNTOLD voiced
  /// chapters (possibly empty) — null when there is no such stop, its own story
  /// is not told yet, or something is playing.
  (ItineraryStop, String, List<int>)? _chaptersAtThisStop() {
    final fix = _lastFix;
    if (fix == null || _startedAt == null || _audioService.isPlaying) return null;
    final stop = _stopUnderfoot(fix);
    final key = _audioKeyOf(stop);
    if (stop == null || key == null || !_played.contains(key)) return null;
    final untold = <int>[
      for (var i = 0; i < stop.segments.length; i++)
        if (stop.segments[i].audioUrl != null && !_played.contains(_segmentKey(key, i))) i,
    ];
    return (stop, key, untold);
  }

  /// The chapter the walker is STANDING AT: inside its own reviewed circle, or an
  /// indoor one (GPS is useless under a roof, so the whole footprint counts). THIS
  /// is the only one that may start itself — R4's "where I stand, never sends me"
  /// (Rosemary), "never narrate where I'm not standing" (Sofia). ``untoldOnly``
  /// false widens it to a TOLD chapter underfoot — the replay's question, never
  /// the auto-play's.
  (ItineraryStop, int)? _chapterAtHand({bool untoldOnly = true}) {
    final atStop = _chaptersAtThisStop();
    final fix = _lastFix;
    if (atStop == null || fix == null) return null;
    final (stop, key, untold) = atStop;
    for (var i = 0; i < stop.segments.length; i++) {
      final seg = stop.segments[i];
      if (seg.audioUrl == null) continue;
      if (untoldOnly && !untold.contains(i)) continue;
      if (seg.indoor && !untold.contains(i)) continue; // an indoor replay is the list, below
      if (seg.indoor || _within(fix.lat, fix.lng, seg.lat, seg.lng, seg.radiusM)) {
        return (stop, i);
      }
    }
    return null;
  }

  /// The chapter OFFERED on the screen: the one underfoot (heard or not), else the
  /// first chapter of this stop the walker has never heard.
  ///
  /// W7.11, Aiko's dissent: a chapter gated on stopping still is lost to someone
  /// who never stops — an UNHEARD chapter stays on the screen anywhere at the stop
  /// ("I would lose it silently and never find out it existed"). W7.13, Camille:
  /// R1(c)'s second half — "told once is told … A TAP REPLAYS" — was locked at
  /// W7.2 and not built; a TOLD chapter is offered again where the walker STANDS
  /// AT it, and only the tap plays it. The AUTO-play rule is untouched: once, at
  /// the standstill, untold only.
  (ItineraryStop, int)? _chapterOffered() {
    // An UNHEARD chapter always outranks a replay: the one underfoot first, else
    // the first unheard anywhere at the stop (Aiko). Only when every chapter has
    // been told does the tap become a REPLAY of the one the walker stands at.
    final untoldAtHand = _chapterAtHand();
    if (untoldAtHand != null) return untoldAtHand;
    final atStop = _chaptersAtThisStop();
    if (atStop == null) return null;
    if (atStop.$3.isNotEmpty) return (atStop.$1, atStop.$3.first);
    return _chapterAtHand(untoldOnly: false);
  }

  /// The chapter offered on the screen right now — its label; null when none.
  String? get segmentOffer {
    final offered = _chapterOffered();
    return offered == null ? null : offered.$1.segments[offered.$2].label;
  }

  /// The tap on the offer: play the offered chapter now.
  void startSegment() {
    final offered = _chapterOffered();
    if (offered == null) return;
    _playSegment(offered.$1, offered.$2);
  }

  void _playSegment(ItineraryStop stop, int index) {
    final key = _audioKeyOf(stop);
    final url = stop.segments[index].audioUrl;
    if (key == null || url == null) return;
    _pieceEndedNaturally = false;
    _audioService.play(_segmentKey(key, index), url,
        title: stop.segments[index].label);
    notifyListeners();
  }

  /// Outdoors the due chapter starts itself at the first standstill inside its
  /// anchor — solo days only (R4: the couple and the family tap); under a roof
  /// never.
  void _maybeStartSegment() {
    final due = _chapterAtHand();
    if (due == null || due.$1.segments[due.$2].indoor) return;
    final party = _session?.party;
    if (party == 'couple' || party == 'family') return;
    final still = _stillSince;
    if (still == null || _now().difference(still).inSeconds < kSettleSeconds) return;
    _playSegment(due.$1, due.$2);
  }

  // ---- S5.11: THE QUEUE and the natural moment ---------------------------

  /// What the session has said aloud, in order (for the screen's own record
  /// and for tests). Every line here was on the screen first.
  List<String> get spoken => List.unmodifiable(_spoken);
  String? get queuedLine => _queuedLine;

  /// R4 — the session's own voice goes to the screen for the rest of the day
  /// (the NARRATION is never muted, and the question is still said once):
  /// couple / family / second-language after the SECOND pause of the day or one
  /// pause longer than ten minutes (family: five); solo, take-it-easy, no party:
  /// no day-long switch on a count. Undone by one touch ([restoreVoice]).
  bool get screenOnly {
    if (_voiceRestored) return false;
    final party = _session?.party;
    if (party == 'couple' || party == 'family') {
      final long = party == 'family'
          ? kLongPauseFamilySeconds
          : kLongPauseCoupleSeconds;
      return _pauseCount >= 2 || longestPauseSeconds > long;
    }
    return false;
  }

  /// Solo / take-it-easy: two pauses inside ONE stop silence THAT stop only
  /// (Rosemary, Greta).
  bool get currentStopSilenced {
    final key = _audioKeyOf(currentStop);
    return key != null && (_pausesAtStop[key] ?? 0) >= 2;
  }

  /// One touch on the speaker: the voice comes back for the day.
  void restoreVoice() {
    _voiceRestored = true;
    notifyListeners();
  }

  void _maybeAnnounceScreenOnly() {
    if (!screenOnly || _screenOnlyAnnounced) return;
    _screenOnlyAnnounced = true;
    // On the screen as its own caption while the switch is on (the page reads
    // [screenOnly]); it never replaces the plan's line. Said once, in one plain
    // sentence, at the next natural moment — and it
    // may displace a waiting fabric line, never the question.
    if (!_queuedIsQuestion) {
      _queuedLine = kScreenOnlyLine;
      _queuedIsQuestion = false;
    }
  }

  /// R3 — the observable proxy for "a natural moment": at a STOP (inside its
  /// circle, or the last stop's), a SEAM in the audio (nothing playing; the
  /// piece ended on its own OR the first piece has not begun), standing still
  /// for the settling period, not paused, transcript closed. NEVER on a walking
  /// leg, never mid-piece, never on stillness alone.
  bool get isNaturalMoment {
    if (_startedAt == null || _pausedAt != null || _transcriptOpen) return false;
    if (_audioService.isPlaying) return false;
    final fix = _lastFix;
    if (fix == null) return false;
    final still = _stillSince;
    if (still == null ||
        _now().difference(still).inSeconds < kSettleSeconds) {
      return false;
    }
    final at = _stopUnderfoot(fix);
    if (at == null) return false;
    final key = _audioKeyOf(at);
    final notBegun = key != null && !_played.contains(key);
    return _pieceEndedNaturally || notBegun;
  }

  ItineraryStop? _stopUnderfoot(_Fix fix) {
    if (_stops.isEmpty || _currentStopIndex < 0) return null;
    for (final i in [_currentStopIndex, _currentStopIndex - 1]) {
      if (i < 0 || i >= _stops.length) continue;
      final stop = _stops[i];
      if (_atPlace(stop, fix.lat, fix.lng)) return stop;
    }
    return null;
  }

  /// Say the waiting line if this is a natural moment. The QUESTION is said
  /// once even under screen-only (Théo, Camille: "a question I cannot hear is a
  /// decision made for me"); a fabric line under screen-only, or at a stop the
  /// person has silenced, stays on the screen.
  void _drainSpeech() {
    final line = _queuedLine;
    if (line == null || !isNaturalMoment) return;
    // Under screen-only only the question and the switch's own one sentence
    // are said (R4: "said once in one plain sentence").
    final isSwitchLine = line == kScreenOnlyLine;
    if (!_queuedIsQuestion &&
        !isSwitchLine &&
        (screenOnly || currentStopSilenced)) {
      return;
    }
    if (_queuedIsQuestion && _questionSpoken) {
      _queuedLine = null;
      return;
    }
    _queuedLine = null;
    if (_queuedIsQuestion) _questionSpoken = true;
    _queuedIsQuestion = false;
    // S6.8 (owner ruling: the tour's own voice): a line with a pre-voiced file
    // plays through the narrator's door; only file-less lines use the plain
    // voice. Same seam, same etiquette either way.
    final url = _queuedLineAudioUrl;
    _queuedLineAudioUrl = null;
    if (url != null) {
      _audioService.play('session-line', url, isDeeperDive: true, title: line);
    } else {
      _spoken.add(line);
      _audioService.speak(line);
    }
    notifyListeners();
  }

  void _queue(String line, {required bool isQuestion, String? audioUrl}) {
    if (_queuedIsQuestion && !isQuestion) return; // the question outranks
    _queuedLine = line;
    _queuedLineAudioUrl = audioUrl;
    _queuedIsQuestion = isQuestion;
    if (isQuestion) _questionSpoken = false;
    _drainSpeech();
  }

  /// The person read this stop's transcript on the screen (Paulo): a stop
  /// opened is not a stop skipped, even if its audio never played.
  void noteTranscriptOpened(ItineraryStop stop) {
    final key = _audioKeyOf(stop);
    if (key != null) _opened.add(key);
  }

  /// [Head back now] — the one control the person has (W5.2 R1.1/R4): the
  /// measured divergence names the current stop as the wrap-up point.
  void requestWrapUp() {
    _wrapUpRequested = true;
    notifyListeners();
  }

  /// The walker found this door SHUT and said so (Docs/adr/0006 rule 5). It
  /// records the observation and nothing else: the server has already decided
  /// what to offer instead, and [matchContingency] finds that entry from here.
  void reportDoorClosed(String poiId) {
    _closedStopId = poiId;
    notifyListeners();
  }

  /// The stop reported shut, until the walk ends.
  String? _closedStopId;

  /// Every stop the phone HOLDS: the walked day, plus the standbys the session
  /// carries beside it (M6b). An id is resolved against this — a standby is not
  /// on the walked day, and resolving against that list alone is exactly how a
  /// place the server offered gets dropped on sight (§4.7 cuts the other way:
  /// what the phone holds it may seat; what it does not hold it may not invent).
  List<ItineraryStop> get _held => [
        ..._planned,
        ...(_session?.standbys ?? const <ItineraryStop>[]),
      ];

  // ---- S5.10: THE ONE re-timing expression --------------------------------

  /// THE phone's ONE re-timing expression (S5.10's seam; design §4.1): seconds
  /// from NOW to each remaining stop's arrival and departure — from the last
  /// fix, at the learned pace with the server's own straight-line correction,
  /// spending at each stop the longer of its planned visit and its narration
  /// at the learned listening rate (the server's `stop_seconds` rule). Nothing
  /// else on the phone spells a clock; the server's `stop_clocks` is the other
  /// side of the seam, and [holdSession] compares the two.
  List<StopEta> retimeRemaining() {
    if (_stops.isEmpty || _currentStopIndex < 0) return const [];
    final from = _currentStopIndex.clamp(0, _stops.length - 1);
    final fix = _lastFix;
    double? lat = fix?.lat;
    double? lng = fix?.lng;
    if (fix == null && from > 0) {
      lat = _stops[from - 1].lat;
      lng = _stops[from - 1].lng;
    }
    var cursor = 0;
    final out = <StopEta>[];
    for (var i = from; i < _stops.length; i++) {
      final stop = _stops[i];
      var walk = 0;
      if (lat != null && lng != null) {
        final d = haversineDistance(lat, lng, stop.lat, stop.lng);
        final atTheStop = i == from && fix != null && _atPlace(stop, lat, lng);
        walk = atTheStop ? 0 : _walkSeconds(d);
      }
      final arrival = cursor + walk;
      final says = ((stop.audioDurationSec ?? 0) * listeningRate).round();
      final departure = arrival + max<int>(stop.plannedVisitSeconds, says);
      out.add(StopEta(stop, arrival, departure));
      cursor = departure;
      lat = stop.lat;
      lng = stop.lng;
    }
    return out;
  }

  /// Seconds from NOW to the day's finish — a VIEW of [retimeRemaining] (the
  /// last departure plus the walk to the finish at the learned pace), never a
  /// second expression. The finish is where the session says the day ends; with
  /// no finish held (an open walk) the day ends at its last stop's departure.
  /// Null before anything can be re-timed.
  int? get finishEtaSeconds {
    final session = _session;
    final etas = retimeRemaining();
    final double lat;
    final double lng;
    var cursor = 0;
    if (etas.isNotEmpty) {
      cursor = etas.last.secondsToDeparture;
      lat = etas.last.stop.lat;
      lng = etas.last.stop.lng;
    } else if (_lastFix != null) {
      lat = _lastFix!.lat;
      lng = _lastFix!.lng;
    } else {
      return null;
    }
    final fLat = session?.finishLat;
    final fLng = session?.finishLng;
    if (fLat == null || fLng == null) return cursor;
    return cursor + _walkSeconds(haversineDistance(lat, lng, fLat, fLng));
  }

  /// THE walk arithmetic, spelled once: straight-line metres with the server's
  /// own correction, at the pace in force.
  int _walkSeconds(double meters) =>
      (meters * kHaversineCorrection / paceMps).round();

  /// A phone clock in the DAY's frame — the frame the server's clocks live in:
  /// the day's start plus the WALL elapsed plus [secondsFromNow]. "" when the
  /// held session has no day start.
  String dayFrameHhmm(int secondsFromNow) =>
      _frameHhmm(wallElapsedSeconds + secondsFromNow);

  String _frameHhmm(int secondsFromDayStart) {
    final start = _session?.dayStartHhmm ?? '';
    if (start.length != 5) return '';
    final h = int.tryParse(start.substring(0, 2));
    final m = int.tryParse(start.substring(3, 5));
    if (h == null || m == null) return '';
    final total = ((h * 60 + m) * 60 + secondsFromDayStart) % 86400;
    final hh = (total ~/ 3600).toString().padLeft(2, '0');
    final mm = ((total % 3600) ~/ 60).toString().padLeft(2, '0');
    return '$hh:$mm';
  }

  /// The phone's own clock for the stop it is heading to, in the day's frame
  /// — the observation it sends with a replan (the server compares, reports,
  /// never adopts). Null when nothing can be re-timed yet.
  String? get phoneNextStopHhmm {
    final etas = retimeRemaining();
    if (etas.isEmpty) return null;
    final clock = dayFrameHhmm(etas.first.secondsToArrival);
    return clock.isEmpty ? null : clock;
  }

  static int _hhmmGapSeconds(String a, String b) {
    final am = int.parse(a.substring(0, 2)) * 60 + int.parse(a.substring(3, 5));
    final bm = int.parse(b.substring(0, 2)) * 60 + int.parse(b.substring(3, 5));
    var diff = am - bm;
    if (diff > 12 * 60) diff -= 24 * 60;
    if (diff < -12 * 60) diff += 24 * 60;
    return diff * 60;
  }

  String? _serverClockFor(String poiId) {
    final session = _session;
    if (session == null) return null;
    for (final stop in session.stops) {
      if (stop.poiId == poiId && stop.startTime.length == 5) {
        return stop.startTime;
      }
    }
    return null;
  }

  /// The phone's MEASURED divergence, as facts (design §4.6) — what
  /// [matchContingency] takes. Lateness and earliness on the TOUR clock (§4.3:
  /// a pause is never lateness); minutes left on the WALL clock (the platform
  /// clock keeps moving through a pause and the number on screen moves with it
  /// — Marcus, W5.2 R4); a skip only for a stop neither played nor opened; a
  /// protected promise at risk when its tour-frame arrival runs past the plan
  /// by more than the tolerance.
  Divergence measure() {
    // A walk that is over has nothing left to diverge from (W5.13: a finished
    // day still matched a promise entry when poked).
    if (!isActive) return const Divergence();
    final etas = retimeRemaining();
    final session = _session;
    int? late;
    int? early;
    int? left;
    String? atRisk;
    if (session != null && etas.isNotEmpty && session.dayStartHhmm.length == 5) {
      final next = etas.first;
      final planned = _serverClockFor(next.stop.poiId);
      if (planned != null) {
        final gap = _hhmmGapSeconds(
          _frameHhmm(tourElapsedSeconds + next.secondsToArrival),
          planned,
        );
        if (gap > 0) late = gap ~/ 60;
        if (gap < 0) early = (-gap) ~/ 60;
      }
      // Minutes left on the WALL clock: to the day's planned end (an open day's
      // bands count down to it — R1.3), else to the finish promise's clock.
      var end = session.plannedEndHhmm.length == 5 ? session.plannedEndHhmm : '';
      if (end.isEmpty) {
        for (final promise in session.promises) {
          if (promise.kind == 'finish' && promise.arrivesHhmm.length == 5) {
            end = promise.arrivesHhmm;
            break;
          }
        }
      }
      if (end.isNotEmpty) left = -_hhmmGapSeconds(dayFrameHhmm(0), end) ~/ 60;
      for (final eta in etas) {
        for (final promise in session.promises) {
          if (!promise.protected ||
              promise.promiseId != eta.stop.poiId ||
              promise.arrivesHhmm.length != 5) {
            continue;
          }
          final gap = _hhmmGapSeconds(
            _frameHhmm(tourElapsedSeconds + eta.secondsToArrival),
            promise.arrivesHhmm,
          );
          if (gap > session.retimeToleranceSeconds) atRisk = promise.promiseId;
        }
        if (atRisk != null) break;
      }
    }
    // The stop the person is at or has just left: underfoot if inside a circle,
    // else the last stop passed (a leg is "from" the stop behind it).
    final fix = _lastFix;
    final underfoot = fix == null ? null : _stopUnderfoot(fix);
    final behind = _currentStopIndex > 0 && _currentStopIndex - 1 < _stops.length
        ? _stops[_currentStopIndex - 1]
        : null;
    return Divergence(
      minutesLate: late,
      minutesEarly: early,
      minutesLeft: left,
      skippedStopId: _lastSkippedPoiId,
      wrapUpFromStopId:
          _wrapUpRequested && isActive ? currentStop?.poiId : null,
      atRiskStopId: atRisk,
      atStopId: (underfoot ?? behind ?? currentStop)?.poiId,
      closedStopId: _closedStopId,
    );
  }

  /// THE SESSION CLOCK SEAM, phone half (S5.10; design §4.6): on every version
  /// the server hands over — the reconnect path — the phone's own re-timing is
  /// COMPARED with the server's clock for the next stop, in the day's frame. A
  /// gap beyond the session's tolerance is REPORTED — a line for the screen —
  /// and never corrected: nothing here assigns into the phone's clocks, its
  /// learned rates, or the server's plan. The reader who is tempted to "fix"
  /// the phone from the server here is looking at the silent-divergence bug
  /// §4.6 exists to prevent.
  void _compareClockWithServer(SessionPlan session) {
    if (_startedAt == null || _lastFix == null) return; // nothing measured yet
    final etas = retimeRemaining();
    if (etas.isEmpty) return;
    final next = etas.first;
    final phone = dayFrameHhmm(next.secondsToArrival);
    final server = _serverClockFor(next.stop.poiId);
    if (phone.isEmpty || server == null) return;
    final gap = _hhmmGapSeconds(phone, server);
    if (gap.abs() <= session.retimeToleranceSeconds) return;
    final minutes = (gap.abs() / 60).round();
    final direction = gap > 0 ? 'later' : 'earlier';
    _clockNotices.add(
      'Your phone reckons ${next.stop.poiName} about $minutes minutes '
      '$direction than the plan says. The phone keeps its own clock; the gap '
      'is shown, not hidden.',
    );
  }

  /// Hold the session the server sent (design §4.7: the phone carries the
  /// current plan and its contingency set so it can act offline, only from
  /// what it already holds). Replaces the held session on every new version.
  void holdSession(SessionPlan session) {
    final prevVersion = _session?.planVersion;
    _session = session;
    _selected = null;
    _pendingQuestion = null;
    _screenText = null;
    _compareClockWithServer(session);
    if (prevVersion != null &&
        session.planVersion > prevVersion &&
        _stops.isNotEmpty) {
      final keepCount = max(0, _currentStopIndex);
      final remainingPois = {
        for (var i = keepCount; i < _stops.length; i++) _stops[i].poiId,
      };
      final sessionPois = session.stops.map((s) => s.poiId).toSet();
      if (!setEquals(remainingPois, sessionPois)) {
        final done = _stops.take(keepCount).toList();
        // The standbys count here too: once the walker has taken one, the next
        // reply lists it as an ordinary stop, and mapping only through the
        // walked day would drop the place they are standing in.
        final byPoi = {for (final s in _held) s.poiId: s};
        final newRemaining = <ItineraryStop>[];
        for (final stop in session.stops) {
          final held = byPoi[stop.poiId];
          if (held != null &&
              !done.contains(held) &&
              !newRemaining.contains(held)) {
            newRemaining.add(held);
          }
        }
        _stops = List.unmodifiable([...done, ...newRemaining]);
        if (_currentStopIndex >= _stops.length) {
          _currentStopIndex = max(0, _stops.length - 1);
        }
      }
    }
    // THE PROMISE TIER ON THE LIVE PATH (W5.14): a live replan that could not
    // keep everything the person asked for within the clock carries the ONE
    // question as an entry of kind "live" — applied at once (its default in
    // force, the two big buttons on the screen), never re-matched later.
    for (final entry in session.contingencies) {
      if (entry.kind == 'live') {
        applyContingency(entry.contingencyId);
        break;
      }
    }
    notifyListeners();
  }

  /// Match the phone's MEASURED divergence against the held contingency set and
  /// return the FIRST entry, in server order, whose trigger fits — a band
  /// lookup or an id equality, nothing else. Null when nothing matches: the
  /// phone then carries on and re-times (design §4.6's arithmetic fallback).
  SessionContingency? matchContingency(Divergence divergence) {
    final session = _session;
    if (session == null) return null;
    for (final entry in session.contingencies) {
      if (_triggerMatches(entry, divergence)) return entry;
    }
    return null;
  }

  static bool _inBand(List<int>? band, int? value) =>
      band != null && value != null && value >= band[0] && value < band[1];

  static bool _fromHere(SessionContingency entry, Divergence d) =>
      d.atStopId == null ||
      entry.triggerStopId == null ||
      entry.triggerStopId == d.atStopId;

  static bool _triggerMatches(SessionContingency entry, Divergence d) {
    switch (entry.kind) {
      case 'running_late':
        return _fromHere(entry, d) && _inBand(entry.bandMinutes, d.minutesLate);
      case 'running_early':
        return _fromHere(entry, d) && _inBand(entry.bandMinutes, d.minutesEarly);
      case 'minutes_left':
        return _fromHere(entry, d) && _inBand(entry.bandMinutes, d.minutesLeft);
      case 'stop_skipped':
        return d.skippedStopId != null && entry.triggerStopId == d.skippedStopId;
      case 'wrap_up_from':
        return d.wrapUpFromStopId != null &&
            entry.triggerStopId == d.wrapUpFromStopId;
      case 'promise_at_risk':
        return d.atRiskStopId != null && entry.triggerStopId == d.atRiskStopId;
      case 'door_closed':
        return d.closedStopId != null && entry.triggerStopId == d.closedStopId;
      default:
        return false;
    }
  }

  /// THE ONE METHOD THAT CHANGES THE CURRENT PLAN — and its only decision input
  /// is a server contingency id (design §4.6: the phone SELECTS, it never
  /// DECIDES). Re-orders the remaining stops to the entry's stops (a subset of
  /// the planned day, in the server's order), puts the entry's screen text on
  /// screen, and surfaces its question if it carries one; the answer is applied
  /// through [answerQuestion]. Returns false when the id is not in the held set.
  bool applyContingency(String contingencyId) {
    final session = _session;
    if (session == null) return false;
    SessionContingency? entry;
    for (final held in session.contingencies) {
      if (held.contingencyId == contingencyId) {
        entry = held;
        break;
      }
    }
    if (entry == null) return false;
    _selected = entry;
    _screenText = entry.screenText;
    _pendingQuestion = entry.question;
    // §4.2: a fabric change is absorbed SILENTLY — on the screen, never said.
    // The ONE question is the one line the session speaks (R2.5): queued to a
    // natural moment, on the screen already as two big buttons.
    final question = entry.question;
    if (question != null) {
      _queue(question, isQuestion: true);
    } else if (_queuedIsQuestion == false) {
      _queuedLine = null; // a superseded fabric line expires unsaid (R3)
    }
    if (entry.kind == 'wrap_up_from') {
      _wrapUp(entry);
    }
    if (entry.question == null) {
      _reorderRemaining(entry, entry.stopIds);
    } else if (entry.defaultArm == 'shorten') {
      // The safe default is already in force until answered (§4.2).
      _reorderRemaining(
          entry,
          entry.alternateStopIds.isNotEmpty
              ? entry.alternateStopIds
              : entry.stopIds);
    } else {
      _reorderRemaining(entry, entry.stopIds);
    }
    notifyListeners();
    return true;
  }

  /// Apply the person's answer to the ONE question of the selected entry:
  /// `keep` = the entry's stops (the protected thing kept), `shorten` = its
  /// alternate. Silence applies the entry's own default (§4.2).
  void answerQuestion(String arm) {
    final entry = _selected;
    if (entry == null || entry.question == null) return;
    final chosen = arm == 'shorten' && entry.alternateStopIds.isNotEmpty
        ? entry.alternateStopIds
        : entry.stopIds;
    _pendingQuestion = null;
    if (_queuedIsQuestion) {
      _queuedLine = null; // answered on the screen: nothing left to say
      _queuedIsQuestion = false;
    }
    _reorderRemaining(entry, chosen);
    notifyListeners();
  }

  // ---- S6.4: the tap is the seam -------------------------------------------

  /// Seconds from [position] to the END OF THE CURRENT SENTENCE of [stop]'s
  /// piece — arithmetic over the stop's own narration (its word count per
  /// sentence against the file's length), capped at [kSentenceEndCapSeconds].
  /// Zero when the stop carries no narration or no length, or the position is
  /// past the last boundary. THE phone's one way of finding a sentence end
  /// (W6.2 R8: "the current sentence finishes — never a cut word").
  @visibleForTesting
  static double secondsToSentenceEnd(
    ItineraryStop stop,
    Duration position, {
    double? lengthSec,
    double? cap,
  }) {
    final at = position.inMilliseconds / 1000.0;
    final limit = cap ?? kSentenceEndCapSeconds.toDouble();
    for (final boundary in _sentenceBoundaries(stop, lengthSec)) {
      if (boundary > at) {
        return min(boundary - at, limit);
      }
    }
    return 0;
  }

  /// [Head back now] applied (the matched wrap-up entry): if THIS stop's piece
  /// is playing, let its sentence end, then cut it and play the stop's close;
  /// if nothing is playing, the DAY's close (the last planned stop's) goes on
  /// the screen at once and is said at the next natural moment. The way home
  /// is the entry's screen line and is never spoken. Nothing else follows.
  void _wrapUp(SessionContingency entry) {
    final stop = currentStop;
    // S6.6/S6.8: the FULL TELLING is a stretch of its own (§7.4.5 — every
    // prefix ends decently): a tap mid-full finishes ITS sentence by ITS own
    // arithmetic and plays ITS close. The playing KEY names its stop — the
    // tour pointer may already sit a stop ahead (the tight completing advances
    // it while the person lingers where they stand).
    ItineraryStop? fullStop;
    if (_audioService.isPlaying) {
      final playingKey = _audioService.currentBeatId;
      for (final s in _planned) {
        final k = _audioKeyOf(s);
        if (k != null && playingKey == '$k-full') {
          fullStop = s;
          break;
        }
      }
    }
    if (fullStop != null && (fullStop.fullCloseText ?? '').isNotEmpty) {
      _closeLine = fullStop.fullCloseText;
      _closePending = fullStop;
      _closePendingIsFull = true;
      final wait = sentenceWaitFor(
        _fullArithmeticStop(fullStop),
        playingKey: '${_audioKeyOf(fullStop)}-full',
      );
      _sentenceEndTimer?.cancel();
      if (wait <= 0) {
        finishSentenceNow();
      } else {
        _sentenceEndTimer = Timer(
          Duration(milliseconds: (wait * 1000).round()),
          finishSentenceNow,
        );
      }
      return;
    }
    final playingThis = stop != null &&
        _audioService.isPlaying &&
        _audioService.currentBeatId == _audioKeyOf(stop) &&
        (stop.closeText ?? '').isNotEmpty;
    if (playingThis) {
      _closeLine = stop.closeText;
      _closePending = stop;
      final wait = sentenceWaitFor(stop); // S7.8
      _sentenceEndTimer?.cancel();
      if (wait <= 0) {
        finishSentenceNow();
      } else {
        _sentenceEndTimer = Timer(
          Duration(milliseconds: (wait * 1000).round()),
          finishSentenceNow,
        );
      }
      return;
    }
    // No piece playing: the day's close — the last planned stop's — on screen
    // now; said at the next natural moment (never on a leg, §4.4.1), through
    // the queue like every session line. A stale queued fabric line expires.
    final last = _planned.isNotEmpty ? _planned.last : stop;
    final dayClose = last?.closeText ?? stop?.closeText;
    if (dayClose == null || dayClose.isEmpty) return;
    _closeLine = dayClose;
    if (!_queuedIsQuestion) {
      _queuedLine = dayClose;
      // S6.8: the day's close in the narrator's own voice when the wire
      // carries its file.
      _queuedLineAudioUrl =
          (last?.closeText == dayClose ? last?.closeAudioUrl : stop?.closeAudioUrl);
      _queuedIsQuestion = false;
    }
    _closesPlayed.add(dayClose);
    _drainSpeech();
  }

  /// The sentence end has come (or the cap): cut the piece and play the close.
  /// Public so a test — and the timer — reach the same door.
  @visibleForTesting
  void finishSentenceNow() {
    _sentenceEndTimer?.cancel();
    _sentenceEndTimer = null;
    final stop = _closePending;
    final wasFull = _closePendingIsFull;
    _closePending = null;
    _closePendingIsFull = false;
    if (stop == null) return;
    final key = _audioKeyOf(stop);
    final playingKey = wasFull && key != null ? '$key-full' : key;
    if (_audioService.isPlaying && _audioService.currentBeatId == playingKey) {
      _audioService.stop();
    }
    _pieceEndedNaturally = false; // a cut is not a seam for anything else (R3)
    if (wasFull) {
      _playFullClose(stop);
    } else {
      _playClose(stop);
    }
    // S7.6: a DOOR cut ends the stretch by the plan's own rule (§7.4.5, a
    // decent prefix): the next stop becomes the target — the same advance a
    // completed piece makes — while the keep-listening offer stays on screen.
    if (_doorAdvance) {
      _doorAdvance = false;
      _advancePast();
    }
  }

  /// The current stop's piece is over — completed on its own, or ended at its
  /// door (S7.6): the next stop becomes the target; the last one completes
  /// the walk. THE one advance.
  void _advancePast() {
    if (_currentStopIndex + 1 < _stops.length) {
      _currentStopIndex++;
    } else {
      _state = TourState.completed;
      // The walk is over on its own, without anyone pressing stop. Release the
      // session here too, or a tour that simply finished leaves other audio
      // ducked until the app dies.
      _audioService.releaseSession();
    }
    notifyListeners();
  }

  /// S6.6/S6.8: the full telling's own close — its file through the narrator's
  /// door when voiced, else the one silent door; on the screen either way.
  void _playFullClose(ItineraryStop stop) {
    final text = stop.fullCloseText;
    if (text == null || text.isEmpty) return;
    _closeLine = text;
    _closesPlayed.add(text);
    final key = _audioKeyOf(stop);
    final url = stop.fullCloseAudioUrl;
    if (url != null && key != null) {
      _audioService.play('$key-full-close', url,
          isDeeperDive: true, title: stop.poiName);
    } else {
      _spoken.add(text);
      _audioService.speak(text);
    }
    notifyListeners();
  }

  /// The close of [stop]: its pre-voiced file through the narrator's own door
  /// when the wire carries one (S6.8 — keyed `<stop>-close`, played as a deeper
  /// dive so completion never auto-advances the tour), else said through the
  /// one silent door; on the screen already either way (§4.4.2).
  /// The full telling's sentence arithmetic rides the SAME static function as
  /// the tight's, over the full's own text and length (S6.6/S6.8).
  ItineraryStop _fullArithmeticStop(ItineraryStop stop) => ItineraryStop(
        sortOrder: stop.sortOrder,
        poiId: stop.poiId,
        poiName: stop.poiName,
        lat: stop.lat,
        lng: stop.lng,
        lensName: stop.lensName,
        lensDisplay: stop.lensDisplay,
        durationMin: stop.durationMin,
        importanceTier: stop.importanceTier,
        startTime: stop.startTime,
        narration: stop.fullNarration,
        audioDurationSec: _fullPieceDurationSec?.toDouble(),
      );

  void _playClose(ItineraryStop stop) {
    final text = stop.closeText;
    if (text == null || text.isEmpty) return;
    _closeLine = text;
    _closesPlayed.add(text);
    final key = _audioKeyOf(stop);
    final url = stop.closeAudioUrl;
    if (url != null && key != null) {
      _audioService.play('$key-close', url,
          isDeeperDive: true, title: stop.poiName);
    } else {
      _spoken.add(text);
      _audioService.speak(text);
    }
    notifyListeners();
  }

  /// The day from the entry's trigger onward becomes the entry's ordered
  /// subset of the planned day. An entry's stops are the day AFTER its trigger
  /// stop — a late/early band "from stop k" lists the stops after k (k stays);
  /// a skip "of k" lists the stops after k (k goes); a promise-at-risk lists
  /// the day from the stop before the promise. Everything before that point is
  /// kept as it stands. A stop id the phone does not hold is skipped, never
  /// invented (§4.7: it cannot add narration it does not hold).
  void _reorderRemaining(SessionContingency entry, List<String> stopIdsInOrder) {
    if (_stops.isEmpty) return;
    final triggerId = entry.triggerStopId;
    var keepCount = _currentStopIndex < 0 ? 0 : _currentStopIndex;
    if (triggerId != null) {
      final at = _stops.indexWhere(
          (s) => s.poiId == triggerId || s.stopId == triggerId);
      if (at >= 0) {
        // skip "of k": k goes (exclusive). promise-at-risk about j: the
        // server's answer is FROM the stop before j and its stops start after
        // that stop, so everything before j stays. A door reported SHUT goes
        // the way a skip does — the walker is standing at it and it is closed,
        // so keeping it would send them back to the door they just reported.
        // A band "from k": k stays.
        keepCount = entry.kind == 'stop_skipped' ||
                entry.kind == 'promise_at_risk' ||
                entry.kind == 'door_closed'
            ? at
            : at + 1;
      }
    }
    final done = _stops.take(keepCount).toList();
    final held = _held;
    final byPoi = {for (final s in held) s.poiId: s};
    final byStop = {
      for (final s in held)
        if (s.stopId != null) s.stopId!: s
    };
    final remaining = <ItineraryStop>[];
    for (final id in stopIdsInOrder) {
      final stop = byStop[id] ?? byPoi[id];
      if (stop != null && !done.contains(stop) && !remaining.contains(stop)) {
        remaining.add(stop);
      }
    }
    _stops = List.unmodifiable([...done, ...remaining]);
    // A standby the walker has actually TAKEN becomes part of the walked day.
    // The goodbye after a stop's last chapter, the head-back close and the
    // thread for a new pair all read the planned list directly rather than
    // resolving an id, so a seated place that never joins it is walked in
    // silence — offered, reached, and then unable to say goodbye.
    final walked = {for (final s in _planned) s.poiId};
    final joined = [
      for (final s in _stops)
        if (!walked.contains(s.poiId)) s,
    ];
    if (joined.isNotEmpty) {
      _planned = List.unmodifiable([..._planned, ...joined]);
    }
    if (_currentStopIndex >= _stops.length) {
      _currentStopIndex = _stops.length - 1;
    }
    _threadForNewPair(entry, keepCount);
  }

  /// S6.5 (design §5.4; W6.2 R5, 11/11): when a reorder makes two stops
  /// consecutive that the plan never had consecutive, the writer's THREAD for
  /// that pair — authored at compose time, riding the arriving stop as
  /// `threadLines[predecessor name]` — goes on the screen for the whole leg
  /// and into the speech queue for the next standing seam (departure; the one
  /// lost on the move is waiting at the standstill). The pair the plan already
  /// had keeps its thread inside the arriving stop's own narration. No line
  /// for the pair means silence — none rather than glue. A wrap-up threads
  /// nothing: the close owns that seam (R8), and the question outranks the
  /// thread in the queue (R2.5).
  void _threadForNewPair(SessionContingency entry, int keepCount) {
    _threadLine = null;
    if (entry.kind == 'wrap_up_from') return;
    if (keepCount <= 0 || keepCount >= _stops.length) return;
    final from = _stops[keepCount - 1];
    final next = _stops[keepCount];
    final plannedIdx = _planned.indexWhere((s) => s.poiId == from.poiId);
    final plannedNext = plannedIdx >= 0 && plannedIdx + 1 < _planned.length
        ? _planned[plannedIdx + 1]
        : null;
    if (plannedNext != null && plannedNext.poiId == next.poiId) return;
    final line = next.threadLines?[from.poiName];
    if (line == null || line.isEmpty) return;
    _threadLine = line;
    _queue(line, isQuestion: false, audioUrl: next.threadAudioUrls?[from.poiName]);
  }

  /// Prefetch the audio of every stop the held session may play (design §4.7:
  /// the alternates and their audio, prefetched during walks) through the
  /// EXISTING per-stop cache — the same key playback uses (`_audioKeyOf`).
  Future<int> prefetchSessionAudio() {
    final session = _session;
    if (session == null) return Future.value(0);
    return _audioService.prefetchAudio([
      for (final stop in [...session.stops, ...session.standbys])
        if (_audioKeyOf(stop) != null)
          BeatAudioInfo(beatId: _audioKeyOf(stop)!, audioUrl: stop.audioUrl),
    ]);
  }

  /// Skip to a specific stop index.
  void skipToStop(int index) {
    if (index < 0 || index >= _stops.length) return;
    // Skip = unplayed AND unopened (S5.10): a stop passed over with its piece
    // neither played nor read on the screen is the skip the set answers.
    for (var i = max(0, _currentStopIndex); i < index; i++) {
      final key = _audioKeyOf(_stops[i]);
      if (key != null && !_played.contains(key) && !_opened.contains(key)) {
        _lastSkippedPoiId = _stops[i].poiId;
      }
    }
    _currentStopIndex = index;
    _pendingStopIndex = null;
    _armedKey = null; // the tap IS the start (S7.3: a tap always plays)
    _pieceEndedNaturally = false; // a skip is not a seam (R3)
    _playCurrentStop();
  }

  /// Accept the pending stop (user tapped the "now approaching" nudge).
  void acceptPendingStop() {
    if (_pendingStopIndex != null) {
      _currentStopIndex = _pendingStopIndex!;
      _pendingStopIndex = null;
      _armedKey = null;
      _playCurrentStop();
    }
  }

  /// Dismiss the pending nudge (user wants to keep listening).
  void dismissPending() {
    _pendingStopIndex = null;
    _state = TourState.active;
    notifyListeners();
  }

  void _onPositionUpdate() {
    final position = _locationService.lastPosition;
    if (position == null || _stops.isEmpty) return;

    final targetIndex = _currentStopIndex;
    if (targetIndex < 0 || targetIndex >= _stops.length) return;

    final target = _stops[targetIndex];
    final lat = (position.latitude as num).toDouble();
    final lng = (position.longitude as num).toDouble();
    _distanceToNext = haversineDistance(lat, lng, target.lat, target.lng);
    final before = _lastFix;
    _firstFix ??= _Fix(lat, lng, _now());
    // The first step off the square starts the tour clock (R1.3); standing
    // where you started is not the tour yet. The square's width is the day's
    // OWN-PLACE radius off the wire (S7.3); with no policy held the clock
    // starts at the first play, or on arrival at the first stop's footprint.
    final ownPlaceM = _session?.placement?.ownPlaceM;
    final steppedOff = ownPlaceM != null &&
        !_within(lat, lng, _firstFix!.lat, _firstFix!.lng, ownPlaceM);
    if (_tourClockStartedAt == null &&
        (steppedOff || _atPlace(target, lat, lng))) {
      _startTourClockIfNeeded();
    }
    _learnPace(lat, lng);
    // Standing still is measured from the last fix that MOVED (R3).
    if (before == null ||
        haversineDistance(before.lat, before.lng, lat, lng) > kStillRadiusM) {
      _stillSince = _now();
    }
    // Walking away from a stop is not a seam: on a leg nothing is said.
    if (_lastFix != null && _stopUnderfoot(_lastFix!) == null) {
      _pieceEndedNaturally = false;
    }
    _checkFinishMoved();
    _drainSpeech();

    // THE ARRIVAL (S7.3; W7.2 R1): touching the target's footprint ARMS its
    // piece; it starts at arrival — or, on the family day, at the first
    // standstill inside the footprint. Told once is told.
    _armOrStart(target, lat, lng);
    _maybeReachTheDoor(); // S7.6
    _maybeStartLeg(target, lat, lng); // S7.7: the walking line, on the walk
    _maybeStartSegment(); // S7.7 (B): a chapter at its anchor

    // Approaching the NEXT stop's footprint while the current piece plays.
    if (_audioService.isPlaying && _currentStopIndex + 1 < _stops.length) {
      final nextTarget = _stops[_currentStopIndex + 1];
      if (_atPlace(nextTarget, lat, lng) && _pendingStopIndex == null) {
        _pendingStopIndex = _currentStopIndex + 1;
        _state = TourState.approaching;
        notifyListeners();
      }
    }

    notifyListeners();
  }

  /// Phase 7 S7.7 (design §5.6 C7 "audio overlaps the walking"; plan defect 7):
  /// the target's LEG piece — its walking line, voiced as its own file — plays ON
  /// THE LEG: the first stop's at the first fix (the start: "Settle in, you're
  /// starting in…"), every later stop's once the walker has left the previous
  /// stop's footprint; never inside the target's own footprint (there the STORY
  /// arms), never while anything plays, told once (a played key never replays).
  /// A leg piece completing advances nothing — its key is not the stop's.
  void _maybeStartLeg(ItineraryStop target, double lat, double lng) {
    final stopKey = _audioKeyOf(target);
    if (stopKey == null) return;
    final legKey = '$stopKey-leg';
    if (_atPlace(target, lat, lng)) {
      if (_legTextLine != null) {
        _legTextLine = null; // arrived: the walking line's moment is over
        notifyListeners();
      }
      return;
    }
    if (_currentStopIndex > 0 && _atPlace(_stops[_currentStopIndex - 1], lat, lng)) {
      return; // still at the previous stop: its story, not the next walk
    }
    final url = target.legAudioUrl;
    if (url == null) {
      // A leg with WORDS and no FILE — a replan rewrote the line and its audio
      // has not caught up (or failed): the words ride the screen for the whole
      // leg, and nothing plays. Silence over wrongness, direction over silence.
      final text = target.legNarration;
      if (text != null && text.isNotEmpty && _legTextLine != text) {
        _legTextLine = text;
        notifyListeners();
      }
      return;
    }
    if (_played.contains(legKey) || _audioService.isPlaying) return;
    _startTourClockIfNeeded(); // the first play starts the tour clock (R1.3)
    _legTextLine = null; // the caught-up file says the words; the screen line is done
    _audioService.play(legKey, url, title: target.poiName);
    notifyListeners();
  }

  /// The footprint touched: ARM the target's piece, then start it when its
  /// moment has come. A piece already played never re-arms (told once is told;
  /// a tap replays — [skipToStop]); leaving before the start disarms.
  void _armOrStart(ItineraryStop target, double lat, double lng) {
    final key = _audioKeyOf(target);
    if (key == null || _played.contains(key)) return;
    if (!_atPlace(target, lat, lng)) {
      if (_armedKey == key) _armedKey = null;
      return;
    }
    _armedKey = key;
    _maybeStartArmed();
  }

  /// Start the armed piece: at once, or — the family day, the session's
  /// policy — at the first standstill inside the footprint (the one stillness
  /// the etiquette already measures, never a second one; Nadia, §4.4.4). At a
  /// QUEUED stop (S7.5, R2: the line is the place) the piece waits for that
  /// standstill for everyone, and under the day's `tap` policy it waits for the
  /// person's tap instead — [armedOffer] / [startArmedPiece].
  void _maybeStartArmed() {
    final key = _armedKey;
    if (key == null || _audioService.isPlaying) return;
    final stop = currentStop;
    if (stop == null || _audioKeyOf(stop) != key) {
      _armedKey = null;
      return;
    }
    final policy = _session?.placement;
    final queued = (stop.trigger?.queueSeconds ?? 0) > 0;
    if (queued && (policy?.queuePieceByTap ?? false)) return; // the tap starts it
    if ((policy?.startsAtStandstill ?? false) || queued) {
      final still = _stillSince;
      final fix = _lastFix;
      if (still == null ||
          fix == null ||
          _now().difference(still).inSeconds < kSettleSeconds ||
          !_atPlace(stop, fix.lat, fix.lng)) {
        return;
      }
    }
    _armedKey = null;
    _playCurrentStop();
  }

  void _onAudioStateChanged() {
    // KE6: a completed "keep exploring here" deep-dive clip NEVER advances the
    // tour — it is served off the tour's time budget. Only scheduled per-stop
    // tour audio drives auto-advance, so bail before either advance path.
    if (_audioService.isDeeperDive) return;
    _observeListening();
    // W7.11 defect 15: the goodbye of a chaptered stop lands after its last chapter.
    _maybeCloseAfterLastChapter();
    // A piece that ended ON ITS OWN is a seam (R3) — the player says
    // completed, not merely "not playing": a pause is neither a seam nor an
    // ending (W5.13: it used to be read as one and the tour jumped a stop).
    if (_audioService.isPlaying) {
      _pieceEndedNaturally = false;
    } else if (_audioService.isCompleted) {
      _pieceEndedNaturally = true;
    }

    // When a piece COMPLETES, auto-advance if there's a pending stop
    if (_audioService.isCompleted &&
        _audioService.currentBeatId != null &&
        _state == TourState.approaching &&
        _pendingStopIndex != null) {
      _creditStated(currentStop);
      _currentStopIndex = _pendingStopIndex!;
      _pendingStopIndex = null;
      _state = TourState.active;
      _playCurrentStop();
    } else if (_audioService.isCompleted &&
        _audioService.currentBeatId == _audioKeyOf(currentStop)) {
      // Audio completed for current stop — advance index for next geofence
      _creditStated(currentStop);
      _advancePast();
    }
    _drainSpeech();
  }

  /// The audio cache/playback key for a stop: the per-stop ItineraryItem id
  /// (Step 1.4d) when present, falling back to the legacy per-beat id. Both the
  /// play call and the completion check below use this so they always agree.
  String? _audioKeyOf(ItineraryStop? stop) =>
      stop == null ? null : (stop.stopId ?? stop.beatId);

  void _playCurrentStop() {
    if (_currentStopIndex < 0 || _currentStopIndex >= _stops.length) return;
    final stop = _stops[_currentStopIndex];
    if (stop.audioUrl != null) {
      _startTourClockIfNeeded(); // the first play starts the tour clock (R1.3)
      _threadLine = null; // the leg is over: the next story begins (S6.5)
      _legTextLine = null;
      _doorCutStop = null; // a new piece: the old door's offer is spent (S7.6)
      _audioService.play(_audioKeyOf(stop)!, stop.audioUrl!, title: stop.poiName);
      _pieceStartedAt = _now(); // the outside seconds count from here (S7.6)
      _pieceStartedKey = _audioKeyOf(stop);
      _state = TourState.active;
      notifyListeners();
    }
  }

  // ---- S5.10: the two learners -------------------------------------------

  /// Train the pace on this fix: moving minutes on WALKING LEGS only. A
  /// low-accuracy fix (> 25 m, the provider's own flag) breaks the chain and
  /// trains nothing; inside the current stop's footprint or the last stop's
  /// (the wire's geometry — S7.3) the person is wandering, not walking;
  /// standing still and vehicle speeds are neither; a pause trains nothing.
  void _learnPace(double lat, double lng) {
    final now = _now();
    if (_locationService.lowAccuracy) {
      _lastFix = null;
      return;
    }
    final last = _lastFix;
    _lastFix = _Fix(lat, lng, now);
    if (last == null || _pausedAt != null) return;
    final dt = now.difference(last.at).inMilliseconds / 1000.0;
    if (dt <= 0 || dt > 120) return;
    final cur = currentStop;
    if (cur != null && _atPlace(cur, lat, lng)) return;
    if (_currentStopIndex > 0 &&
        _atPlace(_stops[_currentStopIndex - 1], lat, lng)) {
      return;
    }
    final meters = haversineDistance(last.lat, last.lng, lat, lng);
    final mps = meters / dt;
    if (mps < kMinWalkingMps || mps > kMaxWalkingMps) return;
    _movingMeters += meters;
    _movingSeconds += dt;
  }

  /// Wall time spent listening, per piece: playing starts a span, anything
  /// else (pause, completion, stop) closes it. A replay opens a new span
  /// against the same stated length.
  void _observeListening() {
    final now = _now();
    final key = _audioService.currentBeatId;
    if (_audioService.isPlaying) {
      if (_playingSince == null || _playingKey != key) {
        _closeListening(now);
        _playingSince = now;
        _playingKey = key;
        if (key != null) _played.add(key);
      }
    } else {
      _closeListening(now);
    }
  }

  void _closeListening(DateTime now) {
    final since = _playingSince;
    if (since == null) return;
    _wallListened += now.difference(since).inMilliseconds / 1000.0;
    _playingSince = null;
  }

  /// A piece completed for [stop]: its stated length counts ONCE (a replay is
  /// more wall time against the same stated seconds — that IS the rate).
  void _creditStated(ItineraryStop? stop) {
    if (stop == null) return;
    final key = _audioKeyOf(stop);
    final stated = stop.audioDurationSec;
    if (key == null || stated == null || stated <= 0) return;
    if (_statedKeys.add(key)) _statedListened += stated;
  }

  /// Haversine formula — returns distance in meters between two lat/lng points.
  @visibleForTesting
  static double haversineDistance(
    double lat1,
    double lon1,
    double lat2,
    double lon2,
  ) {
    const earthRadius = 6371000.0; // meters
    final dLat = _toRadians(lat2 - lat1);
    final dLon = _toRadians(lon2 - lon1);
    final a = sin(dLat / 2) * sin(dLat / 2) +
        cos(_toRadians(lat1)) *
            cos(_toRadians(lat2)) *
            sin(dLon / 2) *
            sin(dLon / 2);
    final c = 2 * atan2(sqrt(a), sqrt(1 - a));
    return earthRadius * c;
  }

  static double _toRadians(double degrees) => degrees * pi / 180.0;

  @override
  void dispose() {
    stopTour();
    super.dispose();
  }
}
