import 'package:flutter/foundation.dart';

/// Abstract interface for location tracking.
/// Allows mocking in tests without depending on geolocator.
abstract class LocationProvider extends ChangeNotifier {
  dynamic get lastPosition;
  bool get isTracking;
  bool get lowAccuracy;

  /// [background] asks for location updates that SURVIVE the screen locking.
  /// A walk is the whole reason this exists: the phone goes in a pocket, and a
  /// tour whose GPS stops there never reaches the next stop's footprint, so no
  /// piece ever fires. The default is false because the planning screens only
  /// want a fix while they are on screen; the tour asks for true.
  ///
  /// It is declared HERE, on the door, rather than only on the concrete
  /// service. It was on the implementation alone until 2026-08-31 — so the
  /// playback service, which holds this interface, could not ask for it and
  /// silently started a walk on foreground-only tracking.
  Future<bool> startTracking({bool background});
  void stopTracking();
}

/// What the platform's audio session tells the walk,
/// reduced to the three things the policy distinguishes — an interruption that
/// PAUSES the piece (a call, Siri, another app's music), one that only DUCKS
/// it (a navigation prompt: the piece carries on), and the end of either.
enum AudioInterruptionKind { pauseBegin, duckBegin, ended }

/// What the person asked of the
/// TRANSPORT CONTROLS the platform owns — the lock screen, the earbud, the car
/// — reduced to the two the walk answers. A pause pressed on a phone in a
/// pocket is the TOUR's pause, never the player's alone: it is the product
/// being used correctly, not an interruption of it.
enum AudioRemoteCommand { play, pause }

/// Abstract interface for audio playback.
/// Allows mocking in tests without depending on an audio plugin.
abstract class AudioProvider extends ChangeNotifier {
  /// The platform's interruptions, through ONE door. The
  /// policy — pause, resume at the cut sentence's start, the couple's tap,
  /// the missed close — lives in the playback service; the player only
  /// translates. The default never emits, so a double that has no session
  /// need not implement it.
  Stream<AudioInterruptionKind> get interruptions => const Stream.empty();

  /// The platform's TRANSPORT BUTTONS, through ONE door — the same shape
  /// as [interruptions] and for the same reason: the player only TRANSLATES
  /// what the lock screen did; the policy — the tour's clock suspended beside
  /// the audio, the pause counted as the person's — is the playback service's,
  /// and it is the very same policy an in-app pause runs. The default never
  /// emits, so a double with no remote surface need not implement it.
  Stream<AudioRemoteCommand> get remoteCommands => const Stream.empty();

  String? get currentBeatId;
  bool get isPlaying;

  /// True when the current piece has reached its END on its own. Not the same
  /// as "not playing": a paused piece is not playing and has not completed
  /// (read as completion, a pause would jump the tour a stop). The
  /// default is false so a double that never completes need not implement it.
  bool get isCompleted => false;

  /// True while the currently-playing source is a "keep exploring here"
  /// deep-dive clip. The tour auto-advance MUST NOT fire when this is set
  /// — a deep dive is served off the tour budget and never moves the itinerary.
  bool get isDeeperDive;

  /// Where the current piece is, from its start: the session
  /// uses it to stop a piece at the end of its CURRENT SENTENCE on [Head back
  /// now] — never a cut word, never the whole piece. The default is
  /// zero so a double that does not track position need not implement it.
  Duration get position => Duration.zero;

  /// The REAL length of the loaded piece, as the player decoded it:
  /// the sentence arithmetic trusts this over the wire's stored
  /// length once the file is loaded. Zero = not known (nothing loaded, or a
  /// double that never reports one).
  Duration get duration => Duration.zero;

  /// Play [audioUrl] under [beatId]. Set [isDeeperDive] for on-demand
  /// "keep exploring here" audio so completion does not auto-advance the tour.
  ///
  /// [title] is what the LOCK SCREEN shows while this piece plays — the
  /// place's own name, or the line being said. Null when the caller has no name
  /// for it; the piece's id then stands in, and nothing is invented.
  void play(
    String beatId,
    String audioUrl, {
    bool isDeeperDive = false,
    String? title,
  });
  void stop();

  /// Move the current piece to [position]. The default does
  /// nothing so a double that never seeks need not implement it.
  Future<void> seek(Duration position) => Future.value();

  /// Play [audioUrl] under [beatId] FROM [from] — the keep-listening tap at a
  /// door resumes a cut piece from the start of its cut sentence.
  /// ONE door to a position: the default plays, then seeks, so a double
  /// need not implement it; the real player seeks before it starts.
  void playFrom(String beatId, String audioUrl, Duration from, {String? title}) {
    play(beatId, audioUrl, title: title);
    seek(from);
  }

  /// Pause / resume the current piece. The tour's own
  /// pause goes through this door so the session can suspend its clock beside
  /// the audio; the defaults do nothing so a double that never pauses need not
  /// implement them.
  Future<void> pause() => Future.value();
  Future<void> resume() => Future.value();

  /// Say one sentence of the SESSION's own voice — never a story.
  /// The playback service hands a line here only at a natural moment
  /// and only after it is already on the screen. The default is silent:
  /// the phone has no voice plugin today, and "mute beats graceful" —
  /// wiring a voice is one implementation behind this door.
  Future<void> speak(String sentence) => Future.value();

  /// Activate the iOS audio session (.playback/.duckOthers) so playback is
  /// audible through a locked screen. MUST be called from the foreground — iOS
  /// refuses session activation from a background callback
  /// (AVAudioSessionErrorCodeCannotInterruptOthers), which is why this is its
  /// own door rather than something the first play() does lazily. No-op off
  /// iOS. The default does nothing, so a double with no session need not
  /// implement it.
  Future<void> prepareSession() => Future.value();

  /// Deactivate the iOS audio session so a ducked session is RELEASED — the
  /// tourist's music or podcast returns to full volume. Called when the tour
  /// ends, stopped or completed; otherwise `.duckOthers` keeps other audio
  /// ducked for the whole session, even after playback finishes.
  Future<void> releaseSession() => Future.value();

  /// Pre-fetch audio files to the device cache; returns how many are cached.
  /// The session prefetches its alternates' audio through
  /// this same door. The default caches nothing, so a test double that never
  /// caches need not implement it.
  Future<int> prefetchAudio(List<BeatAudioInfo> beats) => Future.value(0);
}

/// Minimal info needed for prefetching audio.
class BeatAudioInfo {
  final String beatId;
  final String? audioUrl;

  const BeatAudioInfo({required this.beatId, this.audioUrl});
}
