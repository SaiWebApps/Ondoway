import 'dart:async';

import 'package:ondoway/services/providers.dart';

/// Mock AudioService for unit testing.
/// Tracks play calls without actual audio playback.
class MockAudioService extends AudioProvider {
  /// The platform's interruptions, simulated (a call, a nav prompt).
  final StreamController<AudioInterruptionKind> _interruptions =
      StreamController<AudioInterruptionKind>.broadcast(sync: true);

  @override
  Stream<AudioInterruptionKind> get interruptions => _interruptions.stream;

  /// Simulate what the audio session would report. A pause-kind interruption
  /// also pauses the player, as iOS does before telling the app.
  void simulateInterruption(AudioInterruptionKind kind) {
    if (kind == AudioInterruptionKind.pauseBegin && _isPlaying) {
      _isPlaying = false;
      notifyListeners();
    }
    _interruptions.add(kind);
  }

  /// The platform's transport buttons, simulated.
  final StreamController<AudioRemoteCommand> _remoteCommands =
      StreamController<AudioRemoteCommand>.broadcast(sync: true);

  @override
  Stream<AudioRemoteCommand> get remoteCommands => _remoteCommands.stream;

  /// Simulate a press on the LOCK SCREEN. The buttons there reach the player
  /// first — the platform's transport moves it — and the app hears about it
  /// afterwards, which is the whole reason the command needs a door of its own.
  void simulateRemoteCommand(AudioRemoteCommand command) {
    final wants = command == AudioRemoteCommand.play;
    if (_isPlaying != wants && _currentBeatId != null) {
      _isPlaying = wants;
      notifyListeners();
    }
    _remoteCommands.add(command);
  }

  String? _currentBeatId;
  bool _isPlaying = false;
  bool _isDeeperDive = false;
  bool _isCompleted = false;
  int _playCount = 0;

  /// Every id handed to [play], in order — so a test can count REPLAYS of one
  /// piece (a told chapter replays by tap, never by itself).
  final List<String> playedIds = [];

  /// The title handed with each played id — what the lock screen would
  /// show. A test asserts it is the place's own name, never a stand-in.
  final Map<String, String?> titles = {};

  @override
  String? get currentBeatId => _currentBeatId;
  @override
  bool get isPlaying => _isPlaying;
  @override
  bool get isDeeperDive => _isDeeperDive;
  @override
  bool get isCompleted => _isCompleted;
  int get playCount => _playCount;

  @override
  void play(
    String beatId,
    String audioUrl, {
    bool isDeeperDive = false,
    String? title,
  }) {
    if (_currentBeatId == beatId && _isPlaying) return;
    playedIds.add(beatId);
    titles[beatId] = title;
    _currentBeatId = beatId;
    _isDeeperDive = isDeeperDive;
    // The piece was accepted either way; whether it SOUNDS is what
    // [playSucceeds] decides.
    _isPlaying = playSucceeds;
    _isCompleted = false;
    _playCount++;
    notifyListeners();
  }

  @override
  Future<void> pause() async {
    if (!_isPlaying) return;
    _isPlaying = false;
    notifyListeners();
  }

  @override
  Future<void> resume() async {
    if (_isPlaying || _currentBeatId == null) return;
    _isPlaying = true;
    notifyListeners();
  }

  @override
  void stop() {
    _isPlaying = false;
    _isCompleted = false;
    _currentBeatId = null;
    _isDeeperDive = false;
    notifyListeners();
  }

  /// Where the last seek landed (the keep-listening tap), for tests.
  Duration? lastSeek;

  /// The length the PLAYER knows for the loaded file — a test
  /// sets it to stand in for the real player's decoded duration. Zero = unknown.
  @override
  Duration duration = Duration.zero;

  @override
  Future<void> seek(Duration position) async {
    lastSeek = position;
  }

  /// Every session door the tour opened or closed, in order: 'prepare' when the
  /// walk asked for the audio session, 'release' when it gave it back. The iOS
  /// session must be ACTIVATED from the foreground — iOS refuses it from a
  /// background callback — so which of these the tour calls, and WHEN, is the
  /// difference between narration a locked phone plays and silence.
  ///
  /// Reassign it to a list the location double also holds, and it becomes a log
  /// of BOTH doubles in one order — which is the only way to assert
  /// `['prepare', 'track']`, the requirement that the session is opened before
  /// background tracking starts. That is why there is one list and not two: a
  /// second per-double log recorded the same events and could not express the
  /// only question worth asking about them.
  List<String> callLog = [];

  /// When false, [play] records the piece but never reaches playing — the
  /// native player accepting the call and then failing to sound. The tour must
  /// hold its position through that: no phantom advance to the next stop.
  bool playSucceeds = true;

  int get prepareSessionCount =>
      callLog.where((call) => call == 'prepare').length;

  int get releaseSessionCount =>
      callLog.where((call) => call == 'release').length;

  @override
  Future<void> prepareSession() async => callLog.add('prepare');

  @override
  Future<void> releaseSession() async => callLog.add('release');

  /// Simulate audio completing playback — the piece reached its END on its own
  /// (a pause is NOT this: it leaves [isCompleted] false).
  void simulateComplete() {
    _isPlaying = false;
    _isCompleted = true;
    notifyListeners();
  }
}
