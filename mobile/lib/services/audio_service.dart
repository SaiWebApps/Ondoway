import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:audio_session/audio_session.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:path_provider/path_provider.dart';
import 'package:ondoway/services/native_audio_backend.dart';
import 'package:ondoway/services/providers.dart';
import 'package:ondoway/services/trip_service.dart';

export 'package:ondoway/services/providers.dart' show BeatAudioInfo;

class AudioService extends ChangeNotifier implements AudioProvider {
  /// The foreground-only session door. Separate from the `audio_session`
  /// package configured in [_ensureSession]: that one CONFIGURES the category
  /// and hands us interruptions; this one ACTIVATES the session natively, which
  /// is what the AVAudioPlayer below needs in order to be audible once the
  /// screen locks.
  static const MethodChannel _sessionChannel = MethodChannel(
    'com.ondoway/audio_session',
  );

  final http.Client _httpClient;
  final NativeAudioBackend? _injectedNative;
  NativeAudioBackend? _nativeInstance;
  AudioPlayer? _playerInstance;

  /// True while the NATIVE AVAudioPlayer — not just_audio — owns playback. Set
  /// whenever a cached file is played on iOS, the only path that stays audible
  /// through a locked screen. Decides which backend pause, resume, stop, seek
  /// and the position poll talk to.
  bool _nativeActive = false;
  Timer? _nativePosTimer;

  // ---- Phase 7 S7.9 (W7.2 R5): the audio SESSION and its interruptions -----
  // The session is configured for SPOKEN playback (background audio — the
  // walk keeps talking pocketed; iOS ducks us for a navigation prompt on its
  // own and interrupts us for a call or another app's music). The platform's
  // events are TRANSLATED here into the provider's one door; the policy is the
  // playback service's. Ducking is mechanics and stays here.
  bool _sessionReady = false;
  bool _ducked = false;
  bool _interrupted = false;
  final StreamController<AudioInterruptionKind> _interruptions =
      StreamController<AudioInterruptionKind>.broadcast();

  @override
  Stream<AudioInterruptionKind> get interruptions => _interruptions.stream;

  Future<void> _ensureSession() async {
    if (_sessionReady) return;
    _sessionReady = true;
    try {
      final session = await AudioSession.instance;
      await session.configure(const AudioSessionConfiguration.speech());
      session.interruptionEventStream.listen((event) {
        if (event.begin) {
          if (event.type == AudioInterruptionType.duck) {
            _ducked = true;
            _player.setVolume(0.25);
            _interruptions.add(AudioInterruptionKind.duckBegin);
          } else {
            // S8.7: the platform is TAKING the audio, so the pause that follows
            // is not a button anyone pressed. Held while it lasts so the remote
            // door below stays quiet — an interruption is never their pause.
            _interrupted = true;
            _interruptions.add(AudioInterruptionKind.pauseBegin);
          }
        } else {
          if (_ducked) {
            _ducked = false;
            _player.setVolume(1.0);
          }
          _interrupted = false;
          _interruptions.add(AudioInterruptionKind.ended);
        }
      });
    } catch (_) {
      // No session on this platform (a plain unit test, the web): the piece
      // still plays; there is simply nothing to translate.
    }
  }

  // ---- Phase 8 S8.7: THE LOCK SCREEN, translated (persona 09, Fiona & Dev) --
  // just_audio_background owns the platform's command centre: a press on the
  // lock screen, the earbud or the car moves the PLAYER and tells the app
  // afterwards. So the app's own INTENT is remembered here, and a reported
  // state that disagrees with it is the platform's doing — that disagreement IS
  // the command, and it goes out of the one door for the playback service to
  // answer. Nothing here decides anything: the policy (the tour's clock
  // suspended beside the audio) is TourPlaybackService's, exactly as with the
  // interruptions above.
  bool _appWantsPlaying = false;
  final StreamController<AudioRemoteCommand> _remoteCommands =
      StreamController<AudioRemoteCommand>.broadcast();

  @override
  Stream<AudioRemoteCommand> get remoteCommands => _remoteCommands.stream;

  void _translateRemoteCommand(PlayerState state) {
    // An interruption's pause is the platform TAKING the audio (S7.9), not a
    // button; loading and finishing are the app's own business either way.
    if (_interrupted ||
        state.processingState == ProcessingState.idle ||
        state.processingState == ProcessingState.completed) {
      return;
    }
    if (state.playing == _appWantsPlaying) return;
    _appWantsPlaying = state.playing;
    _remoteCommands.add(
      state.playing ? AudioRemoteCommand.play : AudioRemoteCommand.pause,
    );
  }

  /// What the lock screen SHOWS while [beatId] plays (S8.7). The title is the
  /// caller's real name for the piece — the place, the chapter, the line being
  /// said; with none, the piece's own id stands in and nothing is invented.
  static MediaItem _mediaItem(String beatId, String? title) =>
      MediaItem(id: beatId, title: title ?? beatId);

  String? _currentBeatId;
  bool _isPlaying = false;
  bool _isBuffering = false;
  bool _isDeeperDive = false;
  bool _isCompleted = false;
  Duration _position = Duration.zero;
  Duration _duration = Duration.zero;

  /// Test seam: resolves a beat's cached file path without touching the real
  /// filesystem or path_provider, so the native-routing decision is exercisable
  /// off-device. Null in production, where [_getCachedPath] hits the real cache
  /// directory.
  final Future<String?> Function(String beatId)? _cachedPathResolver;

  AudioService({
    http.Client? httpClient,
    NativeAudioBackend? nativeBackend,
    @visibleForTesting
    Future<String?> Function(String beatId)? cachedPathResolver,
  }) : _httpClient = httpClient ?? http.Client(),
       _injectedNative = nativeBackend,
       _cachedPathResolver = cachedPathResolver;

  /// The native bridge, created lazily on first native playback — the same
  /// rule as [_player] and for the same reason. Constructing it installs a
  /// method-call handler, which asserts unless a Flutter binding is up, and the
  /// HTTP-only calls on this service are meant to run in plain unit tests that
  /// have no binding.
  NativeAudioBackend get _native {
    final existing = _nativeInstance;
    if (existing != null) return existing;
    final backend = _injectedNative ?? NativeAudioBackend();
    backend.onComplete = _onNativeComplete;
    _nativeInstance = backend;
    return backend;
  }

  /// Where a stop's audio file is fetched from. A server that stores audio on
  /// its own disk answers with a path on itself (`/api/v1/audio/files/...`),
  /// which is read against the server the app talks to; a full address is used
  /// as given.
  static Uri audioUri(
    String audioUrl, {
    String apiBaseUrl = TripService.baseUrl,
  }) => Uri.parse(apiBaseUrl).resolve(audioUrl);

  /// iOS is the only platform carrying the native AVAudioPlayer bridge;
  /// everywhere else a cached file falls back to just_audio.
  bool get _useNativePlayer => defaultTargetPlatform == TargetPlatform.iOS;

  /// The just_audio player, created lazily on first playback. HTTP-only
  /// operations (checkStopAudioStatus, prefetch, cache queries) never touch it,
  /// so they don't boot the audio engine / its platform channels — which also
  /// lets them run in plain unit tests without a Flutter binding.
  AudioPlayer get _player {
    final existing = _playerInstance;
    if (existing != null) return existing;
    final player = AudioPlayer();
    player.playerStateStream.listen(_onPlayerStateChanged);
    player.positionStream.listen(_onPositionChanged);
    player.durationStream.listen(_onDurationChanged);
    _playerInstance = player;
    return player;
  }

  // Getters
  @override
  String? get currentBeatId => _currentBeatId;
  @override
  bool get isPlaying => _isPlaying;
  @override
  bool get isCompleted => _isCompleted;
  @override
  bool get isDeeperDive => _isDeeperDive;
  bool get isBuffering => _isBuffering;
  @override
  Duration get position => _position;

  /// S7.8: the player's decoded length, through the provider door (R6).
  @override
  Duration get duration => _duration;
  bool get isActive => _currentBeatId != null;

  /// Play audio for a beat. Uses cached file if available, otherwise streams from URL.
  ///
  /// Set [isDeeperDive] for on-demand "keep exploring here" audio (KE6); the flag
  /// is tracked so tour auto-advance can skip completion of a deep-dive clip.
  @override
  Future<void> play(
    String beatId,
    String audioUrl, {
    bool isDeeperDive = false,
    String? title,
  }) async {
    if (_currentBeatId == beatId && _isPlaying) return;
    await _start(
      beatId,
      audioUrl,
      isDeeperDive: isDeeperDive,
      from: null,
      title: title,
    );
  }

  /// Phase 7 S7.6 (W7.2 R3): the keep-listening tap at a door resumes the cut
  /// piece from the START of its cut sentence — the source is set, the player
  /// seeks, then plays; never a cut word. `void` because the provider's door is
  /// synchronous; the work is the same one start path [play] uses.
  @override
  void playFrom(
    String beatId,
    String audioUrl,
    Duration from, {
    String? title,
  }) {
    _start(beatId, audioUrl, isDeeperDive: false, from: from, title: title);
  }

  /// THE one start path: source (the cache, else the URL), an optional seek,
  /// play. [play] and [playFrom] are its two doors.
  Future<void> _start(
    String beatId,
    String audioUrl, {
    required bool isDeeperDive,
    required Duration? from,
    String? title,
  }) async {
    _currentBeatId = beatId;
    _isDeeperDive = isDeeperDive;
    _isBuffering = true;
    _isCompleted = false;
    notifyListeners();

    try {
      await _ensureSession(); // S7.9: spoken playback, interruptions heard
      final cachedPath = await _getCachedPath(beatId);

      if (cachedPath != null && _useNativePlayer) {
        // A cached file on iOS: play it through the native AVAudioPlayer, which
        // — unlike just_audio's AVPlayer — is still audible once the screen
        // locks. Measured on a device; see native_audio_backend.dart.
        await _playNative(cachedPath, from);
        return;
      }

      // Streaming a URL, or any non-iOS platform: just_audio. Make sure the
      // native player is not left running underneath it.
      await _stopNative();
      // S8.7: every just_audio source carries its MediaItem tag, or the lock
      // screen has nothing to show and just_audio_background refuses the
      // source outright.
      final tag = _mediaItem(beatId, title);
      if (cachedPath != null) {
        await _player.setAudioSource(AudioSource.file(cachedPath, tag: tag));
      } else {
        await _player.setAudioSource(
          AudioSource.uri(audioUri(audioUrl), tag: tag),
        );
      }
      if (from != null && from > Duration.zero) {
        await _player.seek(from);
      }
      _appWantsPlaying = true; // S8.7: the intent, before the player reports it
      await _player.play();
    } catch (e) {
      _isBuffering = false;
      notifyListeners();
      rethrow;
    }
  }

  /// Start the native player and bridge its state back. Duration comes from the
  /// `play` call, position is polled for the UI, and completion arrives via
  /// [_onNativeComplete]. just_audio, if it was playing, is stopped so the two
  /// never overlap — the nullable form, so a native-only session never boots
  /// the just_audio engine just to stop it.
  ///
  /// [from] is the keep-listening seek (S7.6, W7.2 R3): the native player has
  /// no start-at-position call, so it plays and then seeks. Without this the
  /// tap that resumes a cut piece would restart it from zero.
  Future<void> _playNative(String path, Duration? from) async {
    await _playerInstance?.stop();
    final duration = await _native.play(path);
    if (from != null && from > Duration.zero) {
      await _native.seek(from);
    }
    _nativeActive = true;
    _duration = duration;
    _position = from ?? Duration.zero;
    _isBuffering = false;
    _isPlaying = true;
    _appWantsPlaying = true;
    _startNativePolling();
    notifyListeners();
  }

  Future<void> _stopNative() async {
    if (!_nativeActive) return;
    _stopNativePolling();
    await _native.stop();
    _nativeActive = false;
  }

  void _startNativePolling() {
    _nativePosTimer?.cancel();
    _nativePosTimer = Timer.periodic(const Duration(milliseconds: 300), (
      _,
    ) async {
      if (!_nativeActive || !_isPlaying) return;
      _position = await _native.getPosition();
      notifyListeners();
    });
  }

  void _stopNativePolling() {
    _nativePosTimer?.cancel();
    _nativePosTimer = null;
  }

  /// The native player finished the clip on its own. Mirror just_audio's
  /// completed state — playing false, position pinned to duration, beat id
  /// retained — so the playback service's completion listener advances the tour.
  /// That auto-advance is the whole reason tour audio runs through this player.
  void _onNativeComplete() {
    _isPlaying = false;
    _isCompleted = true;
    _position = _duration;
    _appWantsPlaying = false;
    _stopNativePolling();
    notifyListeners();
  }

  @override
  Future<void> prepareSession() async {
    try {
      await _sessionChannel.invokeMethod<void>('prepare');
    } catch (e) {
      debugPrint('AudioService.prepareSession failed: $e');
    }
  }

  @override
  Future<void> releaseSession() async {
    try {
      await _sessionChannel.invokeMethod<void>('deactivate');
    } catch (e) {
      debugPrint('AudioService.releaseSession failed: $e');
    }
  }

  @override
  Future<void> pause() async {
    _appWantsPlaying = false; // S8.7: ours, so it is not read back as a command
    if (_nativeActive) {
      await _native.pause();
      _isPlaying = false;
      _stopNativePolling();
      notifyListeners();
      return;
    }
    await _player.pause();
  }

  @override
  Future<void> resume() async {
    _appWantsPlaying = true;
    if (_nativeActive) {
      await _native.resume();
      _isPlaying = true;
      _startNativePolling();
      notifyListeners();
      return;
    }
    await _player.play();
  }

  /// The session's own voice (design §4.4). The phone carries no text-to-speech
  /// plugin today, so this door is SILENT: every line reaches it only after it
  /// is on the screen (§4.4.2), and "mute beats graceful" (§4.4.4). Wiring a
  /// voice is one implementation here; the etiquette (when a line may be said —
  /// W5.2 R3/R4) lives in TourPlaybackService and does not change with it.
  @override
  Future<void> speak(String sentence) async {}

  @override
  Future<void> stop() async {
    _appWantsPlaying = false; // S8.7
    await _stopNative();
    // Only stop just_audio if it was actually booted — reading `_player` here
    // would create the engine for a session that was native-only.
    await _playerInstance?.stop();
    _currentBeatId = null;
    _isDeeperDive = false;
    _position = Duration.zero;
    _isPlaying = false;
    _isCompleted = false;
    notifyListeners();
  }

  @override
  Future<void> seek(Duration position) async {
    if (_nativeActive) {
      await _native.seek(position);
      _position = position;
      notifyListeners();
      return;
    }
    await _player.seek(position);
  }

  /// Pre-fetch audio files for a list of beats to device cache.
  /// Returns the number of files successfully cached.
  @override
  Future<int> prefetchAudio(List<BeatAudioInfo> beats) async {
    final cacheDir = await _cacheDirectory();
    int cached = 0;

    for (final beat in beats) {
      if (beat.audioUrl == null) continue;
      final file = File('${cacheDir.path}/${beat.beatId}.mp3');
      if (await file.exists()) {
        cached++;
        continue;
      }
      try {
        final response = await _httpClient.get(audioUri(beat.audioUrl!));
        if (response.statusCode == 200) {
          await file.writeAsBytes(response.bodyBytes);
          cached++;
        }
      } catch (_) {
        // Skip failed downloads — audio will stream on demand
      }
    }
    return cached;
  }

  /// Check if a beat's audio is cached locally.
  Future<bool> isCached(String beatId) async {
    final path = await _getCachedPath(beatId);
    return path != null;
  }

  /// Check PER-STOP audio status by ItineraryItem id (Phase 1, Step 1.4d) —
  /// THE status poll (the per-beat one was deleted at Phase 7 S7.10).
  ///
  /// GET /audio/stop-status/{stopId} reads the per-stop narration audio
  /// persisted by /audio/generate-trip-stops, so the itinerary flow polls/plays
  /// per stop. Returns the parsed body ({has_audio, audio_url, duration_sec})
  /// on 200, null otherwise.
  Future<Map<String, dynamic>?> checkStopAudioStatus(
    String baseUrl,
    String stopId,
  ) async {
    try {
      final resp = await _httpClient.get(
        Uri.parse('$baseUrl/audio/stop-status/$stopId'),
      );
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return null;
  }

  /// Clear all cached audio files.
  Future<void> clearCache() async {
    final dir = await _cacheDirectory();
    if (await dir.exists()) {
      await for (final entity in dir.list()) {
        if (entity is File && entity.path.endsWith('.mp3')) {
          await entity.delete();
        }
      }
    }
  }

  /// Total size of cached audio in bytes.
  Future<int> cacheSize() async {
    final dir = await _cacheDirectory();
    if (!await dir.exists()) return 0;
    int total = 0;
    await for (final entity in dir.list()) {
      if (entity is File && entity.path.endsWith('.mp3')) {
        total += await entity.length();
      }
    }
    return total;
  }

  @override
  void dispose() {
    _stopNativePolling();
    _interruptions.close();
    _remoteCommands.close();
    _playerInstance?.dispose();
    super.dispose();
  }

  // Private helpers

  Future<String?> _getCachedPath(String beatId) async {
    final resolver = _cachedPathResolver;
    if (resolver != null) return resolver(beatId);
    final dir = await _cacheDirectory();
    final file = File('${dir.path}/$beatId.mp3');
    if (await file.exists()) return file.path;
    return null;
  }

  Future<Directory> _cacheDirectory() async {
    final tempDir = await getTemporaryDirectory();
    final cacheDir = Directory('${tempDir.path}/ondoway_audio');
    if (!await cacheDir.exists()) {
      await cacheDir.create(recursive: true);
    }
    return cacheDir;
  }

  void _onPlayerStateChanged(PlayerState state) {
    _isPlaying = state.playing;
    _isBuffering =
        state.processingState == ProcessingState.loading ||
        state.processingState == ProcessingState.buffering;

    _isCompleted = state.processingState == ProcessingState.completed;
    if (_isCompleted) {
      _isPlaying = false;
      _position = _duration;
    }
    _translateRemoteCommand(state); // S8.7: the lock screen, heard here
    notifyListeners();
  }

  void _onPositionChanged(Duration position) {
    _position = position;
    notifyListeners();
  }

  void _onDurationChanged(Duration? duration) {
    _duration = duration ?? Duration.zero;
    notifyListeners();
  }
}
