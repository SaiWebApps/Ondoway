import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/audio_service.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/location_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/theme/tokens.dart';

TextStyle _fraunces(Color color, double size, FontWeight weight) => TextStyle(
    fontFamily: 'Fraunces', color: color, fontSize: size, fontWeight: weight, height: 1.1);

/// Builds a single itinerary stop card in isolation — the exact `_StopCard`
/// the itinerary list renders. Exposed only so golden/screenshot tests can
/// render one card (with or without "keep exploring here" extras) without
/// pumping the whole page. Not part of the app's public surface.
@visibleForTesting
Widget buildStopCardForTest(ItineraryStop stop) => _StopCard(stop: stop);

class TripItineraryPage extends StatelessWidget {
  final String tripId;

  const TripItineraryPage({super.key, required this.tripId});

  @override
  Widget build(BuildContext context) {
    final tripService = context.watch<TripService>();
    final trip = tripService.lastGenerated;

    if (trip == null || trip.tripId != tripId) {
      // Try to find in saved trips
      final saved = tripService.savedTrips
          .where((t) => t.tripId == tripId)
          .toList();
      if (saved.isNotEmpty) {
        return _TripItineraryContent(trip: saved.first);
      }
      return Scaffold(
        appBar: AppBar(title: const Text('Trip')),
        body: const Center(child: Text('Trip not found')),
      );
    }

    return _TripItineraryContent(trip: trip);
  }
}

class _TripItineraryContent extends StatefulWidget {
  final GeneratedTrip trip;

  const _TripItineraryContent({required this.trip});

  @override
  State<_TripItineraryContent> createState() =>
      _TripItineraryContentState();
}

class _TripItineraryContentState extends State<_TripItineraryContent> {
  bool _isUpdating = false;
  bool _isPreparing = false;
  bool _preparationDone = false;
  int _audioReady = 0;
  String? _prepareError;
  Timer? _pollTimer;

  /// Phase 4 (design §8.1): true while the confirm tap's compose call is in
  /// flight, so the FAB cannot double-fire the write of the one day.
  bool _isComposing = false;

  /// Tracks which stops have had their audio URL resolved.
  late List<ItineraryStop> _stops;

  /// A shared (non-captained) day the captain has not composed yet: the crew
  /// cannot write it, so the confirm action becomes an honest waiting state
  /// instead of a button that can only 403. Keyed on the trip's captained
  /// flag; set by the session probe below.
  bool _awaitingCaptain = false;

  @override
  void initState() {
    super.initState();
    _stops = List.of(widget.trip.stops);
    if (!widget.trip.captained) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _probeSharedDay());
    }
  }

  /// Whether the shared day is composed yet — GET /trips/{id}/session, the
  /// read crew is allowed. `no_session_yet` means the captain has not
  /// confirmed; anything else (composed, offline) leaves the prepare door as
  /// it is — a composed day's prepare flow is a crew-readable path.
  Future<void> _probeSharedDay() async {
    final tripService = context.read<TripService>();
    final accessToken = context.read<AuthService>().accessToken;
    if (accessToken == null) return;
    try {
      await tripService.fetchSession(widget.trip.tripId, accessToken);
    } on NoSessionYetException {
      if (mounted) setState(() => _awaitingCaptain = true);
      return;
    } catch (_) {
      // Offline or a plain failure: nothing to key the waiting state on.
    }
    if (mounted && _awaitingCaptain) {
      setState(() => _awaitingCaptain = false);
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _updateFromHere() async {
    final locationService = context.read<LocationService>();
    final tripService = context.read<TripService>();
    final authService = context.read<AuthService>();
    final profileService = context.read<ProfileService>();

    setState(() => _isUpdating = true);

    final position = await locationService.getCurrentPosition();
    if (position == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              locationService.error ?? 'Could not get location',
            ),
          ),
        );
        setState(() => _isUpdating = false);
      }
      return;
    }

    try {
      final now = DateTime.now();
      final dateStr =
          '${now.year}-${now.month.toString().padLeft(2, '0')}'
          '-${now.day.toString().padLeft(2, '0')}';
      final timeStr =
          '${now.hour.toString().padLeft(2, '0')}'
          ':${now.minute.toString().padLeft(2, '0')}';

      final newTrip = await tripService.generateTrip(
        profileId: profileService.profileId!,
        centerLat: position.latitude,
        centerLng: position.longitude,
        startDate: dateStr,
        endDate: dateStr,
        accessToken: authService.accessToken!,
        startTime: timeStr,
      );
      if (mounted) {
        context.pushReplacement('/trip/${newTrip.tripId}');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to regenerate: $e')),
        );
        setState(() => _isUpdating = false);
      }
    }
  }

  /// Phase 4 (design §8.1): the server plans ONE day per trip, addressed by
  /// the fixed route id `{tripId}-opt1`. Confirming writes that day (compose)
  /// and then runs the pre-existing prepare flow — no sheet, no pick.
  /// Phase 5 (design §8.2): the frozen trip is deleted, so "already written"
  /// is no longer a 409 from compose — it is a session that EXISTS
  /// (GET /trips/{id}/session answers 200): then the stops on hand ARE the
  /// composed day and nothing is re-written; a trip with no session yet is
  /// composed once (version 1). A refusal — the backend could not write the
  /// day well enough to ship — surfaces on the existing error card; one day
  /// per trip means there is no second option to offer, so the message says
  /// the honest way out is generating again.
  Future<void> _confirmAndPrepareAudio() async {
    final tripService = context.read<TripService>();
    final authService = context.read<AuthService>();

    setState(() {
      _isComposing = true;
      _prepareError = null;
    });

    List<ItineraryStop>? composedStops;
    try {
      try {
        await tripService.fetchSession(
          widget.trip.tripId,
          authService.accessToken!,
        );
        // The day is already written — the stops on hand ARE the composed day.
      } on NoSessionYetException {
        composedStops = await tripService.composeTrip(
          widget.trip.tripId,
          '${widget.trip.tripId}-opt1',
          authService.accessToken!,
        );
      }
    } on ComposeVerificationException catch (e) {
      if (!mounted) return;
      setState(() {
        _isComposing = false;
        _prepareError = e.message;
      });
      return;
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isComposing = false;
        _prepareError = e.toString();
      });
      return;
    }

    if (!mounted) return;
    setState(() {
      _isComposing = false;
      // Non-null -> /compose re-persisted the stops: FRESH stop_ids, composed
      // narration, audio nulled. Rebuild the page's list so the audio flow
      // below runs against the NEW stop_ids.
      if (composedStops != null) _stops = composedStops;
    });
    await _runPrepareFlow();
  }

  /// The pre-existing Confirm & Prepare flow: save the trip, trigger per-stop
  /// audio generation, poll + prefetch until every stop has audio.
  Future<void> _runPrepareFlow() async {
    if (!mounted) return;
    final tripService = context.read<TripService>();
    final authService = context.read<AuthService>();
    final audioService = context.read<AudioService>();

    // Step 1: Save trip locally (only if not already saved)
    final alreadySaved = tripService.savedTrips.any(
      (t) => t.tripId == widget.trip.tripId,
    );
    if (!alreadySaved) {
      tripService.saveTrip(widget.trip);
    }

    // Step 2: Determine which stops need audio
    final stopsWithoutAudio =
        _stops.where((s) => s.audioUrl == null).toList();
    final totalStops = _stops.length;

    setState(() {
      _isPreparing = true;
      _prepareError = null;
      _audioReady = totalStops - stopsWithoutAudio.length;
    });

    if (stopsWithoutAudio.isEmpty) {
      // All stops already have audio — skip generation
      setState(() {
        _isPreparing = false;
        _preparationDone = true;
        _audioReady = totalStops;
      });
      return;
    }

    // Step 3: Trigger backend audio generation
    try {
      await tripService.confirmTripStopAudio(
        widget.trip.tripId,
        authService.accessToken!,
      );
    } catch (e) {
      setState(() {
        _prepareError = e.toString();
        _isPreparing = false;
      });
      return;
    }

    // Step 4: Poll for readiness (max 60 seconds = 20 attempts * 3s)
    int attempts = 0;
    const maxAttempts = 20;

    _pollTimer = Timer.periodic(
      const Duration(seconds: 3),
      (timer) async {
        attempts++;
        int ready = 0;

        for (int i = 0; i < _stops.length; i++) {
          final stop = _stops[i];
          if (stop.audioUrl != null) {
            ready++;
            continue;
          }

          // Per-stop narration (Step 1.4d): poll + cache by the ItineraryItem
          // id — the same key playback uses, so the cache hits. A stop with no
          // item id has nothing to poll (a rest, or data older than the per-stop
          // path: the per-beat poll was deleted at Phase 7 S7.10).
          final audioKey = stop.stopId;
          if (audioKey == null) {
            ready++;
            continue;
          }
          final status = await audioService.checkStopAudioStatus(
            TripService.baseUrl,
            audioKey,
          );

          if (status != null && status['has_audio'] == true) {
            final url = status['audio_url'] as String;
            // Prefetch the audio file to local cache, keyed to match playback.
            await audioService.prefetchAudio([
              BeatAudioInfo(beatId: audioKey, audioUrl: url),
            ]);
            // Update the stop's audio URL
            _stops[i] = stop.copyWith(audioUrl: url);
            ready++;
          }
        }

        if (!mounted) {
          timer.cancel();
          return;
        }

        setState(() {
          _audioReady = ready;
        });

        if (ready >= _stops.length || attempts >= maxAttempts) {
          timer.cancel();
          _pollTimer = null;
          setState(() {
            _isPreparing = false;
            _preparationDone = true;
          });
        }
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final c = Theme.of(context).extension<OndowayColors>()!;

    return Scaffold(
      backgroundColor: c.bg,
      appBar: AppBar(
        backgroundColor: c.bg,
        surfaceTintColor: c.bg,
        elevation: 0,
        centerTitle: false,
        iconTheme: IconThemeData(color: c.ink),
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // The eyebrow says whose day this is: the viewer's own, or one a
            // family co-member planned and shared (the captained flag).
            Text(widget.trip.captained ? 'YOUR TOUR' : 'SHARED TOUR',
                style: TextStyle(
                    fontFamily: 'Space Mono',
                    color: c.accent,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 2.0)),
            const SizedBox(height: 2),
            Text(widget.trip.tripName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: _fraunces(c.ink, 22, FontWeight.w600)),
          ],
        ),
        actions: [
          IconButton(
            icon: Icon(Icons.refresh, color: c.inkMute),
            tooltip: 'Regenerate',
            onPressed: () {
              context.pop();
            },
          ),
        ],
      ),
      body: Column(
        children: [
          _SummaryCard(trip: widget.trip),
          if (widget.trip.degradationNotices.isNotEmpty)
            _DegradationCard(notices: widget.trip.degradationNotices),
          if (_isPreparing || _preparationDone) _buildProgressCard(colorScheme),
          if (_prepareError != null) _buildErrorCard(colorScheme),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _isUpdating ? null : _updateFromHere,
                icon: _isUpdating
                    ? SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: colorScheme.primary,
                        ),
                      )
                    : const Icon(Icons.my_location),
                label: Text(
                  _isUpdating
                      ? 'Updating...'
                      : 'Update trip from here',
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(
                horizontal: 16,
                vertical: 8,
              ),
              itemCount: _stops.length,
              itemBuilder: (context, index) {
                final stop = _stops[index];
                return _StopCard(stop: stop);
              },
            ),
          ),
        ],
      ),
      floatingActionButton: _buildFab(colorScheme),
    );
  }

  Widget _buildProgressCard(ColorScheme colorScheme) {
    final totalStops = _stops.length;
    final progress = totalStops > 0 ? _audioReady / totalStops : 0.0;
    final allReady = _audioReady >= totalStops;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: allReady
          ? colorScheme.primaryContainer
          : colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  allReady ? Icons.check_circle : Icons.headphones,
                  color: allReady
                      ? colorScheme.onPrimaryContainer
                      : colorScheme.onSurfaceVariant,
                  size: 20,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    allReady
                        ? 'Audio ready! Start your tour'
                        : _isPreparing
                            ? 'Preparing audio... $_audioReady/$totalStops'
                            : 'Audio ready for $_audioReady/$totalStops stops. '
                                'Start anyway?',
                    style: TextStyle(
                      color: allReady
                          ? colorScheme.onPrimaryContainer
                          : colorScheme.onSurfaceVariant,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
              ],
            ),
            if (_isPreparing) ...[
              const SizedBox(height: 12),
              LinearProgressIndicator(
                value: progress,
                backgroundColor: colorScheme.surfaceContainerHighest,
                valueColor: AlwaysStoppedAnimation(colorScheme.primary),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildErrorCard(ColorScheme colorScheme) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: colorScheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Icon(Icons.error_outline, color: colorScheme.onErrorContainer),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                _prepareError!,
                style: TextStyle(color: colorScheme.onErrorContainer),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Start the walk (Phase 5 S5.11): the playback service takes the prepared
  /// stops and HOLDS the living session — the plan and its contingency set
  /// (design §4.6/§4.7) — then the session screen shows what it holds. A
  /// session that cannot be fetched (offline) still starts the walk with the
  /// stops on hand; the phone can only act from what it already holds.
  Future<void> _startWalk() async {
    final playback = context.read<TourPlaybackService>();
    final tripService = context.read<TripService>();
    final authService = context.read<AuthService>();
    // Phase 7 S7.7 (plan defect 9's class): the SESSION's stops are the one source
    // that carries every placed and voiced field — the trigger geometry, the leg
    // piece, the closes' files — overlaid live by the server at every GET. The
    // walk starts from them; the stops on hand are the offline fallback only.
    var stops = _stops;
    try {
      final session = await tripService.fetchSession(
        widget.trip.tripId,
        authService.accessToken!,
      );
      playback.holdSession(session);
      if (session.stops.isNotEmpty) stops = session.stops;
    } catch (_) {
      // Offline or not yet composed: the walk still starts from the stops.
    }
    final started = await playback.startTour(stops);
    if (!mounted) return;
    if (!started) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Location is off — turn it on to walk.')),
      );
      return;
    }
    // The walk itself is the mapped screen: the stop pins, the now-playing
    // card, the approaching nudge. The tour is already running on the session's
    // stops by this point, and TourWalkPage will not restart it.
    context.push('/trip/${widget.trip.tripId}/walk', extra: widget.trip);
  }

  Widget _buildFab(ColorScheme colorScheme) {
    if (_preparationDone) {
      return FloatingActionButton.extended(
        onPressed: _startWalk,
        icon: const Icon(Icons.play_arrow),
        label: const Text('Start Tour'),
        backgroundColor: colorScheme.primary,
        foregroundColor: colorScheme.onPrimary,
      );
    }

    // A shared day the captain has not composed: the honest waiting state —
    // the crew is offered no action that can only 403.
    if (_awaitingCaptain) {
      return FloatingActionButton.extended(
        onPressed: null,
        icon: const Icon(Icons.hourglass_empty),
        label: const Text('Ask the captain to confirm this day.'),
        backgroundColor: colorScheme.surfaceContainerHighest,
        foregroundColor: colorScheme.onSurfaceVariant,
      );
    }

    // Busy covers both halves of the confirm tap: writing the day (compose,
    // design §8.1) and preparing its audio.
    final busy = _isPreparing || _isComposing;
    return FloatingActionButton.extended(
      onPressed: busy ? null : _confirmAndPrepareAudio,
      icon: busy
          ? SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: colorScheme.onPrimary,
              ),
            )
          : const Icon(Icons.headphones),
      label: Text(busy ? 'Preparing...' : 'Confirm & Prepare'),
      backgroundColor:
          busy ? colorScheme.surfaceContainerHighest : colorScheme.primary,
      foregroundColor:
          busy ? colorScheme.onSurfaceVariant : colorScheme.onPrimary,
    );
  }
}

/// Shows, above the itinerary, whatever quietly went worse while this tour was
/// built. Each notice is the backend's plain-English sentence — no identifiers,
/// nothing for the traveller to decode. It renders only when there is at least
/// one notice, so a clean tour shows nothing at all.
class _DegradationCard extends StatelessWidget {
  final List<String> notices;

  const _DegradationCard({required this.notices});

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.info_outline, color: colorScheme.onTertiaryContainer),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final notice in notices)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text(
                        notice,
                        style: TextStyle(color: colorScheme.onTertiaryContainer),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  final GeneratedTrip trip;

  const _SummaryCard({required this.trip});

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).extension<OndowayColors>()!;

    return Container(
      margin: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 12),
      decoration: BoxDecoration(
        color: c.card,
        borderRadius: BorderRadius.circular(Dims.radiusCard),
        border: Border.all(color: c.line),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _SummaryItem(
            icon: Icons.pin_drop,
            label: 'Stops',
            value: '${trip.totalStops}',
            color: c.accent,
          ),
          _SummaryItem(
            icon: Icons.timer,
            label: 'Duration',
            value: _formatDuration(trip.totalDurationMin),
            color: c.accent,
          ),
          _SummaryItem(
            icon: Icons.star,
            label: 'Anchors',
            value: '${trip.anchorCount}',
            color: c.spark,
          ),
          _SummaryItem(
            icon: Icons.auto_awesome,
            label: 'Flavour',
            value: '${trip.flavourCount}',
            color: c.accent,
          ),
        ],
      ),
    );
  }

  String _formatDuration(int minutes) {
    final hours = minutes ~/ 60;
    final mins = minutes % 60;
    if (hours > 0 && mins > 0) return '${hours}h ${mins}m';
    if (hours > 0) return '${hours}h';
    return '${mins}m';
  }
}

class _SummaryItem extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color color;

  const _SummaryItem({
    required this.icon,
    required this.label,
    required this.value,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).extension<OndowayColors>()!;

    return Column(
      children: [
        Icon(icon, color: color, size: 20),
        const SizedBox(height: 6),
        Text(value, style: _fraunces(c.ink, 20, FontWeight.w600)),
        const SizedBox(height: 2),
        Text(
          label,
          style: TextStyle(
              fontFamily: 'Space Mono',
              color: c.inkMute,
              fontSize: 11,
              letterSpacing: 0.5),
        ),
      ],
    );
  }
}

class _StopCard extends StatefulWidget {
  final ItineraryStop stop;

  const _StopCard({required this.stop});

  @override
  State<_StopCard> createState() => _StopCardState();
}

class _StopCardState extends State<_StopCard> {
  bool _generatingDeeperDive = false;
  String? _deeperDiveError;

  ItineraryStop get stop => widget.stop;

  /// KE7: generate + play the stop's "keep exploring here" deep-dive audio.
  /// Plays as a DEEPER-DIVE source (KE6) so completion never auto-advances the
  /// tour. status=='failed' comes back as a caught [KeepExploringException].
  Future<void> _keepExploring() async {
    final tripService = context.read<TripService>();
    final audioService = context.read<AudioService>();

    setState(() {
      _generatingDeeperDive = true;
      _deeperDiveError = null;
    });

    try {
      final result = await tripService.generateDeeperDiveAudio(
        stop.stopId ?? stop.beatId ?? stop.poiId,
      );
      if (!mounted) return;
      setState(() => _generatingDeeperDive = false);
      final url = result.audioUrl;
      if (url == null) {
        setState(() => _deeperDiveError = 'No audio was returned');
        return;
      }
      // Key off the stop so this clip is distinct from the scheduled per-stop
      // tour audio; isDeeperDive keeps auto-advance from firing on completion.
      await audioService.play(
        '${stop.stopId ?? stop.beatId ?? stop.poiId}-keep-exploring',
        url,
        isDeeperDive: true,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _generatingDeeperDive = false;
        _deeperDiveError = e is TripServiceException ? e.message : e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;
    final c = Theme.of(context).extension<OndowayColors>()!;
    final isAnchor = stop.importanceTier == 5;
    final hasExtras = stop.extraBeatIds.isNotEmpty;

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 5),
      decoration: BoxDecoration(
        color: c.card,
        borderRadius: BorderRadius.circular(Dims.radiusCard),
        border: Border.all(color: isAnchor ? c.accent.withValues(alpha: 0.5) : c.line),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            contentPadding: const EdgeInsets.fromLTRB(12, 6, 16, 6),
            leading: Container(
              width: 40,
              height: 40,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: isAnchor ? c.accent : c.panel,
                shape: BoxShape.circle,
                border: isAnchor ? null : Border.all(color: c.line),
              ),
              child: Text(
                '${stop.sortOrder}',
                style: TextStyle(
                  fontFamily: 'Space Grotesk',
                  color: isAnchor ? c.onAccent : c.inkMute,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            title: Row(
              children: [
                Expanded(
                  child: Text(
                    stop.poiName,
                    style: _fraunces(c.ink, 16, FontWeight.w600),
                  ),
                ),
                if (stop.audioUrl != null)
                  Icon(Icons.volume_up, size: 16, color: c.accent),
                if (isAnchor) ...[
                  const SizedBox(width: 4),
                  Icon(Icons.star, size: 16, color: c.spark),
                ],
              ],
            ),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Row(
                children: [
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                    decoration: BoxDecoration(
                      color: c.accent.withValues(alpha: 0.10),
                      borderRadius: BorderRadius.circular(Dims.radiusPill),
                    ),
                    child: Text(
                      stop.lensDisplay,
                      style: TextStyle(
                        fontFamily: 'Space Grotesk',
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: c.accentDeep,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '${stop.durationMin} min',
                    style: TextStyle(
                        fontFamily: 'Space Grotesk',
                        fontSize: 13,
                        color: c.inkMute),
                  ),
                ],
              ),
            ),
            trailing: Text(
              stop.startTime,
              style: const TextStyle(
                fontFamily: 'Space Mono',
                fontWeight: FontWeight.w700,
              ).copyWith(color: c.ink),
            ),
          ),
          // KE7: only stops with un-voiced extras offer a deeper dive.
          if (hasExtras) _buildKeepExploring(colorScheme, textTheme),
        ],
      ),
    );
  }

  Widget _buildKeepExploring(ColorScheme colorScheme, TextTheme textTheme) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _generatingDeeperDive ? null : _keepExploring,
              icon: _generatingDeeperDive
                  ? SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: colorScheme.primary,
                      ),
                    )
                  : Icon(Icons.explore, size: 18, color: colorScheme.primary),
              label: Text(
                _generatingDeeperDive
                    ? 'Preparing...'
                    : 'Keep exploring here',
                style: TextStyle(color: colorScheme.primary),
              ),
            ),
          ),
          if (_deeperDiveError != null)
            Padding(
              padding: const EdgeInsets.only(left: 8, top: 4),
              child: Text(
                _deeperDiveError!,
                style: textTheme.bodySmall?.copyWith(color: colorScheme.error),
              ),
            ),
        ],
      ),
    );
  }
}
