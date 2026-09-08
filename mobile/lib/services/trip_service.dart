import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:ondoway/models/trip.dart';

class TripService extends ChangeNotifier {
  static const baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  final http.Client _httpClient;
  List<GeneratedTrip> _savedTrips = [];
  GeneratedTrip? _lastGenerated;
  bool _isGenerating = false;
  String? _error;

  TripService({http.Client? httpClient})
      : _httpClient = httpClient ?? http.Client();

  List<GeneratedTrip> get savedTrips => List.unmodifiable(_savedTrips);
  GeneratedTrip? get lastGenerated => _lastGenerated;
  bool get isGenerating => _isGenerating;
  String? get error => _error;

  /// POST /trips/generate — generate an optimized trip itinerary.
  Future<GeneratedTrip> generateTrip({
    required String profileId,
    required double centerLat,
    required double centerLng,
    required String startDate,
    required String endDate,
    required String accessToken,
    int radiusM = 3000,
    int maxStops = 10,
    int? durationMin,
    String startTime = '09:00',
    bool kidFriendlyOnly = false,
    String? tripName,
    // Phase 6 S6.9: "is 6pm a hard deadline or a rough idea?" — the planner's one
    // question. 'firm' (the default, per the owner's ruling: skipping the question
    // means a hard deadline), 'open' (a rough idea), or 'wall' (booked — a train,
    // a table; the day must end early enough to make it).
    String endHardness = 'firm',
  }) async {
    _isGenerating = true;
    _error = null;
    notifyListeners();

    try {
      final body = <String, dynamic>{
        'profile_id': profileId,
        'center_lat': centerLat,
        'center_lng': centerLng,
        'radius_m': radiusM,
        'max_stops': maxStops,
        'start_date': startDate,
        'end_date': endDate,
        'start_time': startTime,
        'kid_friendly_only': kidFriendlyOnly,
      };
      if (durationMin != null) body['duration_min'] = durationMin;
      if (tripName != null) body['trip_name'] = tripName;
      body['end_hardness'] = endHardness;

      final response = await _httpClient.post(
        Uri.parse('$baseUrl/trips/generate'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $accessToken',
        },
        body: jsonEncode(body),
      );

      if (response.statusCode == 201) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        final trip = GeneratedTrip.fromJson(data);
        _lastGenerated = trip;
        _isGenerating = false;
        notifyListeners();
        return trip;
      } else if (response.statusCode == 404) {
        throw TripServiceException('Profile not found');
      } else if (response.statusCode == 422) {
        final detail = _extractDetail(response.body);
        throw TripServiceException(detail);
      } else {
        throw TripServiceException(
          'Trip generation failed (${response.statusCode}): ${response.body}',
        );
      }
    } catch (e) {
      _isGenerating = false;
      if (e is TripServiceException) {
        _error = e.message;
        notifyListeners();
        rethrow;
      }
      _error = e.toString();
      notifyListeners();
      rethrow;
    }
  }

  /// GET /trips?profile_id=... — fetch all saved trips for a profile.
  Future<List<GeneratedTrip>> fetchSavedTrips(
    String profileId,
    String accessToken,
  ) async {
    final response = await _httpClient.get(
      Uri.parse('$baseUrl/trips?profile_id=$profileId'),
      headers: {
        'Authorization': 'Bearer $accessToken',
      },
    );

    if (response.statusCode == 200) {
      final data = jsonDecode(response.body) as List<dynamic>;
      _savedTrips = data
          .map((t) => GeneratedTrip.fromJson(t as Map<String, dynamic>))
          .toList();
      notifyListeners();
      return _savedTrips;
    } else if (response.statusCode == 404) {
      throw TripServiceException('Profile not found');
    } else {
      throw TripServiceException(
        'Failed to fetch trips (${response.statusCode})',
      );
    }
  }

  /// Add a generated trip to the saved list (local state).
  void saveTrip(GeneratedTrip trip) {
    _savedTrips = [..._savedTrips, trip];
    notifyListeners();
  }

  /// Remove a trip from the saved list by ID.
  void deleteTrip(String tripId) {
    _savedTrips = _savedTrips.where((t) => t.tripId != tripId).toList();
    notifyListeners();
  }

  /// POST /audio/generate-trip-stops/{tripId} — trigger PER-STOP narration audio
  /// (Phase 1, Step 1.4d). Voices each stop's stitched narration (cold-open →
  /// beats → transit → closing), keyed by ItineraryItem id — the per-stop
  /// replacement for the retired per-primary-beat generation.
  ///
  /// Returns the generation response with counts of generated/skipped/failed.
  Future<Map<String, dynamic>> confirmTripStopAudio(
    String tripId,
    String accessToken,
  ) async {
    final response = await _httpClient.post(
      Uri.parse('$baseUrl/audio/generate-trip-stops/$tripId'),
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $accessToken',
      },
    );

    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    } else if (response.statusCode == 404) {
      throw TripServiceException('Trip not found');
    } else {
      throw TripServiceException(
        'Stop audio generation failed (${response.statusCode}): ${response.body}',
      );
    }
  }

  /// POST /trips/{tripId}/compose — write the trip's ONE day (Phase 4,
  /// design §8.1). The server plans a single day per trip, addressed by the
  /// fixed route id `{tripId}-opt1`; the phone confirms the day by composing
  /// it — there is no route choice. Body: {"route_id": routeId}. Returns the
  /// re-persisted stops with FRESH stop_ids (narration is the composed text,
  /// audio_url is null — audio is generated afterwards by the existing
  /// per-stop flow).
  ///
  /// Phase 5 (design §8.2): the frozen trip is deleted — a second compose is
  /// version N+1 of the living session, never a 409, so there is no
  /// "already composed" exception any more; a day already written is found
  /// through [fetchSession] (200) rather than by composing into a lock.
  /// Throws [ComposeVerificationException] when the backend REFUSES the day
  /// (422 compose_verification_failed); one day per trip means there is no
  /// alternative to offer, so the caller surfaces the refusal and suggests
  /// generating again.
  Future<List<ItineraryStop>> composeTrip(
    String tripId,
    String routeId,
    String accessToken,
  ) async {
    final response = await _httpClient.post(
      Uri.parse('$baseUrl/trips/$tripId/compose'),
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $accessToken',
      },
      body: jsonEncode({'route_id': routeId}),
    );

    if (response.statusCode == 200) {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      return (data['stops'] as List<dynamic>)
          .map((s) => ItineraryStop.fromJson(s as Map<String, dynamic>))
          .toList();
    } else if (response.statusCode == 403) {
      throw _refusalFor403(response.body);
    } else if (response.statusCode == 404) {
      throw TripServiceException('Trip or route not found');
    } else if (response.statusCode == 422) {
      final detail = _detailMap(response.body);
      if (detail?['reason'] == 'compose_verification_failed') {
        throw ComposeVerificationException(
          attempts: (detail?['attempts'] as num?)?.toInt(),
        );
      }
      throw TripServiceException('Compose failed (422): ${response.body}');
    } else {
      throw TripServiceException(
        'Compose failed (${response.statusCode}): ${response.body}',
      );
    }
  }

  /// GET /trips/{tripId}/session — the current version of the living session
  /// (Phase 5, design §4.6/§8.2): the day the person is standing in and the
  /// contingency set the phone SELECTS from. Throws [NoSessionYetException]
  /// on 404 `no_session_yet` (generated, never composed) and a plain
  /// [TripServiceException] on any other 404.
  Future<SessionPlan> fetchSession(String tripId, String accessToken) async {
    final response = await _httpClient.get(
      Uri.parse('$baseUrl/trips/$tripId/session'),
      headers: {'Authorization': 'Bearer $accessToken'},
    );
    if (response.statusCode == 200) {
      return SessionPlan.fromJson(
        jsonDecode(response.body) as Map<String, dynamic>,
      );
    } else if (response.statusCode == 404) {
      final detail = _detailMap(response.body);
      if (detail?['reason'] == 'no_session_yet') {
        throw NoSessionYetException();
      }
      throw TripServiceException('Trip not found');
    }
    throw TripServiceException(
      'Session fetch failed (${response.statusCode}): ${response.body}',
    );
  }

  /// POST /trips/{tripId}/session/replan — REPORT the phone's observations
  /// (where it is, its two clocks, its learned rates, the next planned stop)
  /// and receive version N+1 of the session, replanned on the server (design
  /// §4.6: the server is the only place a plan decision is made). The body
  /// carries facts, never a decision.
  Future<SessionPlan> replanSession(
    String tripId,
    String accessToken, {
    required double lat,
    required double lng,
    required int wallElapsedSeconds,
    required int tourElapsedSeconds,
    double? observedPace,
    double? listeningRate,
    int nextStopIndex = 0,
    String? phoneNextStopHhmm,
  }) async {
    final response = await _httpClient.post(
      Uri.parse('$baseUrl/trips/$tripId/session/replan'),
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $accessToken',
      },
      body: jsonEncode({
        'lat': lat,
        'lng': lng,
        'wall_elapsed_seconds': wallElapsedSeconds,
        'tour_elapsed_seconds': tourElapsedSeconds,
        'observed_pace': ?observedPace,
        'listening_rate': ?listeningRate,
        'next_stop_index': nextStopIndex,
        'phone_next_stop_hhmm': ?phoneNextStopHhmm,
      }),
    );
    if (response.statusCode == 200) {
      return SessionPlan.fromJson(
        jsonDecode(response.body) as Map<String, dynamic>,
      );
    } else if (response.statusCode == 403) {
      throw _refusalFor403(response.body);
    } else if (response.statusCode == 404) {
      throw TripServiceException('Trip or session not found');
    }
    throw TripServiceException(
      'Replan failed (${response.statusCode}): ${response.body}',
    );
  }

  /// The write endpoints' 403 as the screen should say it. The typed
  /// {"reason": "captain_only"} (writes keep one captain — ADR 0005) becomes
  /// [CaptainOnlyException], whose message is the plain sentence the
  /// itinerary page renders verbatim; any other 403 still reads as a
  /// sentence, never the response body's raw JSON.
  TripServiceException _refusalFor403(String body) {
    if (_detailMap(body)?['reason'] == 'captain_only') {
      return CaptainOnlyException();
    }
    return TripServiceException("You don't have access to change this trip.");
  }

  /// POST /audio/stops/{stopId}/keep-exploring — voice a stop's persisted
  /// "keep exploring here" extra narration on demand (KE5).
  ///
  /// Served OFF the tour's time budget: a deep dive, not a scheduled stop, so
  /// the caller must play the result as a DEEPER-DIVE source (never auto-advance
  /// — see [AudioService.play]'s isDeeperDive flag / KE6).
  ///
  /// Optional [provider] / [voiceId] mirror the per-beat GenerateRequest and are
  /// usually omitted (server default provider). TTS failure comes back as HTTP
  /// 200 with status=='failed'; that is surfaced as a [KeepExploringException]
  /// so a flaky provider is a caught, retryable error — never a crash.
  Future<DeeperDiveAudio> generateDeeperDiveAudio(
    String stopId, {
    String? provider,
    String? voiceId,
  }) async {
    final body = <String, dynamic>{};
    if (provider != null) body['provider'] = provider;
    if (voiceId != null) body['voice_id'] = voiceId;

    final response = await _httpClient.post(
      Uri.parse('$baseUrl/audio/stops/$stopId/keep-exploring'),
      headers: {'Content-Type': 'application/json'},
      body: body.isEmpty ? null : jsonEncode(body),
    );

    if (response.statusCode == 200) {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final status = data['status'] as String?;
      if (status == 'failed') {
        // Soft TTS failure (never a 500): surface as a caught error the UI can
        // show, carrying the backend's reason when present.
        throw KeepExploringException(
          (data['error'] as String?) ?? 'Audio generation failed',
        );
      }
      return DeeperDiveAudio(
        stopId: (data['stop_id'] as String?) ?? stopId,
        status: status ?? 'generated',
        audioUrl: data['audio_url'] as String?,
        durationSec: (data['duration_sec'] as num?)?.toDouble(),
      );
    } else if (response.statusCode == 404) {
      throw TripServiceException('Stop not found');
    } else if (response.statusCode == 409) {
      throw TripServiceException('Nothing more to explore here');
    } else {
      throw TripServiceException(
        'Keep-exploring audio failed (${response.statusCode}): ${response.body}',
      );
    }
  }

  /// Clear all local state.
  void reset() {
    _savedTrips = [];
    _lastGenerated = null;
    _isGenerating = false;
    _error = null;
    notifyListeners();
  }

  String _extractDetail(String body) {
    try {
      final data = jsonDecode(body) as Map<String, dynamic>;
      return data['detail'] as String? ?? body;
    } catch (_) {
      return body;
    }
  }

  /// Structured error detail ({"detail": {...}}) or null when detail is a
  /// plain string / body is not JSON.
  Map<String, dynamic>? _detailMap(String body) {
    try {
      final data = jsonDecode(body) as Map<String, dynamic>;
      final detail = data['detail'];
      return detail is Map<String, dynamic> ? detail : null;
    } catch (_) {
      return null;
    }
  }
}

class TripServiceException implements Exception {
  final String message;
  TripServiceException(this.message);

  @override
  String toString() => message;
}

/// Parsed result of POST /audio/stops/{id}/keep-exploring (KE5): the on-demand
/// "keep exploring here" deep-dive audio for a stop. A 200 with status=='failed'
/// never reaches here — it is thrown as a [KeepExploringException] instead.
class DeeperDiveAudio {
  final String stopId;
  final String status;
  final String? audioUrl;
  final double? durationSec;

  const DeeperDiveAudio({
    required this.stopId,
    required this.status,
    this.audioUrl,
    this.durationSec,
  });
}

/// The keep-exploring endpoint returned 200 with status=='failed' (soft TTS
/// failure — a flaky provider, never a 500). Surfaced so the UI can show a
/// retryable error instead of crashing.
class KeepExploringException extends TripServiceException {
  KeepExploringException(super.message);
}

/// The trip's write endpoints refused a non-captain: 403 with
/// detail.reason == "captain_only" (writes keep one captain — ADR 0005).
/// [message] is the sentence the phone shows verbatim.
class CaptainOnlyException extends TripServiceException {
  CaptainOnlyException() : super("Only the trip's captain can change the day");
}

/// The backend REFUSED to write the trip's day: /compose returned 422 with
/// detail.reason == "compose_verification_failed" (VERIFY failed after the
/// recompose attempt). One day per trip (Phase 4, design §8.1) means there is
/// no alternative route to offer, so [message] says the honest way out —
/// generating again — in plain language the UI shows verbatim.
class ComposeVerificationException extends TripServiceException {
  final String reason;
  final int? attempts;

  ComposeVerificationException({this.attempts})
      : reason = 'compose_verification_failed',
        super("This day couldn't be written. Try generating again.");
}

/// GET /trips/{id}/session answered 404 `no_session_yet`: the trip has been
/// generated but its day has not been written yet — compose it first (Phase 5,
/// design §8.2). Not a failure; the caller's cue to compose.
class NoSessionYetException extends TripServiceException {
  NoSessionYetException() : super('No session yet — compose the trip first');
}
