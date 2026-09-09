/// Phase 7 S7.3 (design §5.6; W7.2 R1): WHERE a stop's piece plays — the
/// place's own footprint, placed by the server's ONE rule
/// (src/tour/placement.py) and READ here. The phone draws no circle of its
/// own: until S7.3 it decided "at the stop" with two literals (10 m to start a
/// piece, 40 m to be "at" a place) for a 140 m square and a doorway alike.
/// `kind` is "circle" this phase; a line or polygon is a carried data row.
class StopTrigger {
  final String kind;
  final double radiusM;
  // S7.5 (design §5.6; W7.2 R2): the seconds of line the planner priced at
  // this stop's arrival hour. 0 = no line: the piece keeps the arrival rule;
  // > 0 = the line is the place: the piece waits for the first standstill
  // inside the footprint, or for the tap under the day's `tap` policy.
  final int queueSeconds;
  // S7.6 (design §5.6 threshold silence; W7.2 R3): this stop is a DOOR — the
  // plan's visit goes inside a place you enter — and the placed OUTSIDE seconds
  // before it. At a door the piece ends at its current sentence when those
  // seconds have run, then the close plays; inside, nothing auto-plays.
  final bool door;
  final int outsideSeconds;

  const StopTrigger({
    this.kind = 'circle',
    required this.radiusM,
    this.queueSeconds = 0,
    this.door = false,
    this.outsideSeconds = 0,
  });

  factory StopTrigger.fromJson(Map<String, dynamic> json) => StopTrigger(
        kind: (json['kind'] as String?) ?? 'circle',
        radiusM: (json['radius_m'] as num).toDouble(),
        queueSeconds: (json['queue_seconds'] as num?)?.toInt() ?? 0,
        door: (json['door'] as bool?) ?? false,
        outsideSeconds: (json['outside_seconds'] as num?)?.toInt() ?? 0,
      );

  Map<String, dynamic> toJson() => {
        'kind': kind,
        'radius_m': radiusM,
        'queue_seconds': queueSeconds,
        'door': door,
        'outside_seconds': outsideSeconds,
      };
}

/// The day's placement POLICY (S7.3): whether an armed piece starts as the
/// footprint is entered or at the first standstill inside it (the family day —
/// Nadia, design §4.4.4), and the radius of the person's OWN place — the start
/// square and the named finish. Decided on the server from the party; the phone
/// reads it and invents no branch of its own.
class PlacementPolicy {
  final String startAt; // 'arrival' | 'standstill'
  final double ownPlaceM;
  // S7.5 (W7.2 R2): how a QUEUED stop's piece starts — 'auto' at the first
  // standstill inside the footprint; 'tap' the screen offers and the person's
  // tap starts it (the couple, the family); 'none' a wall prices no line.
  final String queuePiece;
  // W7.13 (F&D): the R6 sentence cap and the R5 resume rule ride the policy —
  // decided on the server from the party and the hardness, never a phone
  // branch. Null = an older session: the phone's party fallback stands in.
  final double? sentenceCapS;
  final String? interruptionResume; // 'auto' | 'tap'

  const PlacementPolicy({
    this.startAt = 'arrival',
    required this.ownPlaceM,
    this.queuePiece = 'auto',
    this.sentenceCapS,
    this.interruptionResume,
  });

  bool get startsAtStandstill => startAt == 'standstill';
  bool get queuePieceByTap => queuePiece == 'tap';
  bool get interruptionResumeByTap => interruptionResume == 'tap';

  factory PlacementPolicy.fromJson(Map<String, dynamic> json) =>
      PlacementPolicy(
        startAt: (json['start_at'] as String?) ?? 'arrival',
        ownPlaceM: (json['own_place_m'] as num).toDouble(),
        queuePiece: (json['queue_piece'] as String?) ?? 'auto',
        sentenceCapS: (json['sentence_cap_s'] as num?)?.toDouble(),
        interruptionResume: json['interruption_resume'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'start_at': startAt,
        'own_place_m': ownPlaceM,
        'queue_piece': queuePiece,
        if (sentenceCapS != null) 'sentence_cap_s': sentenceCapS,
        if (interruptionResume != null) 'interruption_resume': interruptionResume,
      };
}

class ItineraryStop {
  final int sortOrder;
  // ItineraryItem id — addresses this stop for per-stop narration audio
  // (Phase 1). Nullable: legacy/seed trips may not carry it.
  final String? stopId;
  final String poiId;
  final String poiName;
  final double lat;
  final double lng;
  // Null for a stop with no story — a rest (a bench) carries no beat and no
  // audio of its own; the audio key then falls back through stopId (S5.13).
  final String? beatId;
  final String lensName;
  final String lensDisplay;
  final int durationMin;
  final int importanceTier;
  final String startTime;
  final String? scriptBody;
  final String? audioUrl;
  final double? audioDurationSec;
  // M2: encoded polyline of the walking leg INTO this stop; null when the
  // backend's routing fell back to haversine (Valhalla not running).
  final String? transitPolyline;
  // KE: ordered ids of this stop's beats the tour did NOT voice — the
  // "keep exploring here" extras (most-important first). Empty for stops
  // with no overflow and for old trips whose JSON lacks the key.
  final List<String> extraBeatIds;
  // KE: composed "keep exploring here" narration for [extraBeatIds], voiced
  // on demand off the tour's time budget; null until /compose has run.
  final String? extraNarration;
  // Phase 5 S5.10: the planner's own visit for this stop, in seconds — what
  // the phone's re-timing spends here (the longer of this and the narration
  // at the learned listening rate). Falls back to the rounded minutes.
  final int? dwellSeconds;
  // Phase 6 S6.4: the stop's composed NARRATION — what its audio says (design
  // §5.7: every word ever spoken exists as on-screen text). Until now the phone
  // dropped it and showed the primary beat's raw body as "the transcript".
  final String? narration;
  // Phase 6 S6.4 (design §5.3): the stop's one-line CLOSE — the last sentence of
  // its narration, the line [Head back now] shows and plays for this stretch —
  // and its pre-voiced file when the narrator has one (S6.8).
  final String? closeText;
  final String? closeAudioUrl;
  // Phase 6 S6.5 (design §5.4; W6.2 R5): THREADS — one sentence per stop that may
  // come right BEFORE this one when the day is replanned, keyed by that stop's
  // name. Spoken at the departure seam, on screen through the leg. Null when no
  // replanned pair binds this stop (none rather than glue).
  final Map<String, String>? threadLines;
  // Phase 6 S6.6 (design §5.5; W6.2 R3): a MAJOR stop's FULL TELLING — the second
  // composed piece from this stop's own second story, with its own close. Null on
  // minor stops; the on-demand extras route still answers there.
  final String? fullNarration;
  final String? fullCloseText;
  // Phase 6 S6.8 (owner ruling: the tour's own voice): the pre-recorded files
  // for the authored session lines. Null until voiced; the phone falls back to
  // the plain spoken line.
  final Map<String, String>? threadAudioUrls;
  final String? fullCloseAudioUrl;
  // Phase 7 S7.3 (design §5.6; W7.2 R1): the stop's placed trigger geometry —
  // the place's own footprint, from the server's one rule. Null on an item
  // written before the rule existed: no geometry, so nothing auto-plays there
  // (a tap still does).
  final StopTrigger? trigger;
  // Phase 7 S7.7 (design §5.6 C7; plan defect 7): the stop's LEG piece — its
  // walking line, voiced as its own file and played ON THE LEG (the first
  // stop's at the start; every later stop's once the walker leaves the
  // previous footprint). Null when the leg carries nothing / not yet voiced.
  final String? legNarration;
  final String? legAudioUrl;
  final double? legAudioDurationSec;
  // Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): a marquee's CHAPTERS
  // — its story cut at the reviewed anchors, each its own piece at its own
  // place inside the footprint. Empty for every stop without anchors.
  final List<StopSegment> segments;

  const ItineraryStop({
    required this.sortOrder,
    this.stopId,
    required this.poiId,
    required this.poiName,
    required this.lat,
    required this.lng,
    this.beatId,
    required this.lensName,
    required this.lensDisplay,
    required this.durationMin,
    required this.importanceTier,
    required this.startTime,
    this.scriptBody,
    this.audioUrl,
    this.audioDurationSec,
    this.transitPolyline,
    this.extraBeatIds = const [],
    this.extraNarration,
    this.dwellSeconds,
    this.narration,
    this.closeText,
    this.closeAudioUrl,
    this.threadLines,
    this.fullNarration,
    this.fullCloseText,
    this.threadAudioUrls,
    this.fullCloseAudioUrl,
    this.trigger,
    this.legNarration,
    this.legAudioUrl,
    this.legAudioDurationSec,
    this.segments = const [],
  });

  /// Seconds the plan spends AT this stop (S5.10): the planner's exact visit
  /// when the wire carried it, else the rounded minutes.
  int get plannedVisitSeconds => dwellSeconds ?? durationMin * 60;

  /// Minutes of the full telling at the engine's own reading rate (150 wpm),
  /// for the offer's cost label (W6.2 R3 — Marcus: "the tap shows the full's
  /// cost"). Zero when there is no full telling.
  int get fullTellingMinutes {
    final text = fullNarration;
    if (text == null || text.trim().isEmpty) return 0;
    final words = text.trim().split(RegExp(r'\s+')).length;
    return (words / 150).ceil();
  }

  factory ItineraryStop.fromJson(Map<String, dynamic> json) {
    return ItineraryStop(
      sortOrder: json['sort_order'] as int,
      stopId: json['stop_id'] as String?,
      poiId: json['poi_id'] as String,
      poiName: json['poi_name'] as String,
      lat: (json['lat'] as num).toDouble(),
      lng: (json['lng'] as num).toDouble(),
      beatId: json['beat_id'] as String?,
      // M0b: a stop with no lensed beat has null lens fields in the API
      // response; render as empty rather than crash.
      lensName: (json['lens_name'] as String?) ?? '',
      lensDisplay: (json['lens_display'] as String?) ?? '',
      durationMin: json['duration_min'] as int,
      importanceTier: json['importance_tier'] as int,
      startTime: json['start_time'] as String,
      scriptBody: json['script_body'] as String?,
      audioUrl: json['audio_url'] as String?,
      audioDurationSec: (json['audio_duration_sec'] as num?)?.toDouble(),
      transitPolyline: json['transit_polyline'] as String?,
      // KE: tolerate the key being absent (old trips) — default to empty.
      extraBeatIds: ((json['extra_beat_ids'] as List<dynamic>?) ?? const [])
          .map((e) => e as String)
          .toList(),
      extraNarration: json['extra_narration'] as String?,
      dwellSeconds: (json['dwell_seconds'] as num?)?.toInt(),
      narration: json['narration'] as String?,
      closeText: json['close_text'] as String?,
      closeAudioUrl: json['close_audio_url'] as String?,
      threadLines: (json['thread_lines'] as Map<String, dynamic>?)
          ?.map((k, v) => MapEntry(k, v as String)),
      fullNarration: json['full_narration'] as String?,
      fullCloseText: json['full_close_text'] as String?,
      threadAudioUrls: (json['thread_audio_urls'] as Map<String, dynamic>?)
          ?.map((k, v) => MapEntry(k, v as String)),
      fullCloseAudioUrl: json['full_close_audio_url'] as String?,
      trigger: json['trigger'] == null
          ? null
          : StopTrigger.fromJson(json['trigger'] as Map<String, dynamic>),
      legNarration: json['leg_narration'] as String?,
      legAudioUrl: json['leg_audio_url'] as String?,
      legAudioDurationSec: (json['leg_audio_duration_sec'] as num?)?.toDouble(),
      segments: [
        for (final s in (json['segments'] as List<dynamic>? ?? const []))
          StopSegment.fromJson(s as Map<String, dynamic>),
      ],
    );
  }

  Map<String, dynamic> toJson() => {
        'sort_order': sortOrder,
        'stop_id': stopId,
        'poi_id': poiId,
        'poi_name': poiName,
        'lat': lat,
        'lng': lng,
        'beat_id': beatId,
        'lens_name': lensName,
        'lens_display': lensDisplay,
        'duration_min': durationMin,
        'importance_tier': importanceTier,
        'start_time': startTime,
        'script_body': scriptBody,
        'audio_url': audioUrl,
        'audio_duration_sec': audioDurationSec,
        'transit_polyline': transitPolyline,
        'dwell_seconds': dwellSeconds,
        'extra_beat_ids': extraBeatIds,
        'extra_narration': extraNarration,
        'narration': narration,
        'close_text': closeText,
        'close_audio_url': closeAudioUrl,
        'thread_lines': threadLines,
        'full_narration': fullNarration,
        'full_close_text': fullCloseText,
        'thread_audio_urls': threadAudioUrls,
        'full_close_audio_url': fullCloseAudioUrl,
        'trigger': trigger?.toJson(),
        'leg_narration': legNarration,
        'leg_audio_url': legAudioUrl,
        'leg_audio_duration_sec': legAudioDurationSec,
        'segments': [for (final s in segments) s.toJson()],
      };

  ItineraryStop copyWith({
    String? audioUrl,
    double? audioDurationSec,
    String? scriptBody,
    String? closeAudioUrl,
    String? fullCloseAudioUrl,
    String? legAudioUrl,
    double? legAudioDurationSec,
  }) {
    // EVERY field rides through (Phase 6 S6.8 found the class: this copy used
    // to drop narration, the closes, the threads and the full telling — the
    // demo's stand-in audio then silently stripped the very lines under test).
    return ItineraryStop(
      sortOrder: sortOrder,
      stopId: stopId,
      poiId: poiId,
      poiName: poiName,
      lat: lat,
      lng: lng,
      beatId: beatId,
      lensName: lensName,
      lensDisplay: lensDisplay,
      durationMin: durationMin,
      importanceTier: importanceTier,
      startTime: startTime,
      scriptBody: scriptBody ?? this.scriptBody,
      audioUrl: audioUrl ?? this.audioUrl,
      audioDurationSec: audioDurationSec ?? this.audioDurationSec,
      transitPolyline: transitPolyline,
      extraBeatIds: extraBeatIds,
      extraNarration: extraNarration,
      dwellSeconds: dwellSeconds,
      narration: narration,
      closeText: closeText,
      closeAudioUrl: closeAudioUrl ?? this.closeAudioUrl,
      threadLines: threadLines,
      threadAudioUrls: threadAudioUrls,
      fullNarration: fullNarration,
      fullCloseText: fullCloseText,
      fullCloseAudioUrl: fullCloseAudioUrl ?? this.fullCloseAudioUrl,
      trigger: trigger,
      legNarration: legNarration,
      legAudioUrl: legAudioUrl ?? this.legAudioUrl,
      legAudioDurationSec: legAudioDurationSec ?? this.legAudioDurationSec,
      segments: segments,
    );
  }
}

/// Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): ONE CHAPTER of a
/// marquee stop — the reviewed anchor's place and radius (read by the phone's
/// one `_within`), whether it is under a roof (then a tap, never auto), its
/// text, and its own file once voiced. The server cuts the story; the phone
/// plays what it holds.
class StopSegment {
  final String label;
  final double lat;
  final double lng;
  final double radiusM;
  final bool indoor;
  final String narration;
  final String? audioUrl;
  final double? audioDurationSec;

  const StopSegment({
    required this.label,
    required this.lat,
    required this.lng,
    required this.radiusM,
    this.indoor = false,
    required this.narration,
    this.audioUrl,
    this.audioDurationSec,
  });

  factory StopSegment.fromJson(Map<String, dynamic> json) => StopSegment(
        label: json['label'] as String,
        lat: (json['lat'] as num).toDouble(),
        lng: (json['lng'] as num).toDouble(),
        radiusM: (json['radius_m'] as num).toDouble(),
        indoor: json['indoor'] as bool? ?? false,
        narration: json['narration'] as String? ?? '',
        audioUrl: json['audio_url'] as String?,
        audioDurationSec: (json['audio_duration_sec'] as num?)?.toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'label': label,
        'lat': lat,
        'lng': lng,
        'radius_m': radiusM,
        'indoor': indoor,
        'narration': narration,
        'audio_url': audioUrl,
        'audio_duration_sec': audioDurationSec,
      };
}

// Phase 4 (design §8.1): POST /trips/generate still carries `options` on the
// wire (a list of exactly one; the field stays a list because stored
// pre-Phase-4 trips carry multi-option lists), but the phone neither parses
// nor shows it. The one day's route id is always `{trip_id}-opt1`, so there
// is nothing to choose and nothing to read — fromJson simply ignores the key.
class GeneratedTrip {
  final String tripId;
  final String tripName;
  final String profileId;
  // Whether the viewer's profile CAPTAINS this trip. profileId above is the
  // caller's own (GET /trips stamps the requesting profile on every row), so
  // it cannot tell an own day from one shared by the family — this flag, from
  // the relationship the server matched, is what the screens label with.
  // Defaults true: a locally generated trip is always the viewer's own.
  final bool captained;
  final int totalStops;
  final int totalDurationMin;
  final int anchorCount;
  final int flavourCount;
  final List<ItineraryStop> stops;
  // Everything that quietly went worse while this tour was built — most often
  // that the walking times between stops were estimated rather than measured.
  // Each entry is the plain-English sentence the backend wrote for a human to
  // read (src/tour/degradations.py's `human` register); the machine-facing
  // fields are deliberately not parsed here. Absent or empty means nothing
  // degraded, which is a statement, not a silence.
  final List<String> degradationNotices;

  const GeneratedTrip({
    required this.tripId,
    required this.tripName,
    required this.profileId,
    this.captained = true,
    required this.totalStops,
    required this.totalDurationMin,
    required this.anchorCount,
    required this.flavourCount,
    required this.stops,
    this.degradationNotices = const [],
  });

  factory GeneratedTrip.fromJson(Map<String, dynamic> json) {
    final stopsList = (json['stops'] as List<dynamic>)
        .map((s) => ItineraryStop.fromJson(s as Map<String, dynamic>))
        .toList();
    return GeneratedTrip(
      tripId: json['trip_id'] as String,
      tripName: json['trip_name'] as String,
      profileId: json['profile_id'] as String,
      // An older server sends no flag; every trip was the viewer's own then.
      captained: json['captained'] as bool? ?? true,
      totalStops: json['total_stops'] as int,
      totalDurationMin: json['total_duration_min'] as int,
      anchorCount: json['anchor_count'] as int,
      flavourCount: json['flavour_count'] as int,
      stops: stopsList,
      // Defensive by design: a missing key, a JSON null, a non-list value, a
      // non-map row, a row with no `human`, and a blank `human` all yield no
      // entry and none of them throws. An older server that sends no
      // degradations at all parses to an empty list, not an error.
      degradationNotices: [
        for (final row in (json['degradations'] as List<dynamic>?) ?? const [])
          if (row is Map<String, dynamic> &&
              (row['human'] as String?)?.trim().isNotEmpty == true)
            (row['human'] as String).trim(),
      ],
    );
  }

  Map<String, dynamic> toJson() => {
        'trip_id': tripId,
        'trip_name': tripName,
        'profile_id': profileId,
        'captained': captained,
        'total_stops': totalStops,
        'total_duration_min': totalDurationMin,
        'anchor_count': anchorCount,
        'flavour_count': flavourCount,
        'stops': stops.map((s) => s.toJson()).toList(),
        'degradations': [
          for (final notice in degradationNotices) {'human': notice},
        ],
      };
}

// ---------------------------------------------------------------------------
// THE LIVING SESSION (Phase 5, design §4.6 / §8.2). The server plans and REPLANS;
// the phone holds the plan and its contingency set and SELECTS from it — it never
// decides. Everything below is what the wire hands the phone: a versioned plan,
// the day's promises with who is protected, and precomputed answers to the
// divergences that matter, each with the ONE question (only when a protected
// thing is touched) and its screen text (always).
// ---------------------------------------------------------------------------

/// One promise of the day (design §3.1) — a pin, a rest, the finish, or the
/// planner's anchor. `protected` is the person's own class (W5.2 R1.5): the only
/// kinds a session ever asks a question about.
class SessionPromise {
  final String promiseId;
  final String kind;
  final String name;
  final String arrivesHhmm;
  final String departsHhmm;
  final bool protected;

  const SessionPromise({
    required this.promiseId,
    required this.kind,
    required this.name,
    this.arrivesHhmm = '',
    this.departsHhmm = '',
    this.protected = false,
  });

  factory SessionPromise.fromJson(Map<String, dynamic> json) => SessionPromise(
    promiseId: json['promise_id'] as String,
    kind: json['kind'] as String,
    name: (json['name'] as String?) ?? '',
    arrivesHhmm: (json['arrives_hhmm'] as String?) ?? '',
    departsHhmm: (json['departs_hhmm'] as String?) ?? '',
    protected: (json['protected'] as bool?) ?? false,
  );
}

/// One precomputed answer (design §4.6). `trigger` is a MATCHER, never a policy —
/// `{"kind": "running_late", "stop_id": ..., "band_minutes": [10, 20]}`,
/// `running_early`, `minutes_left` (an open day), `stop_skipped`,
/// `promise_at_risk`, `wrap_up_from`. The phone matches its measured divergence
/// against these in SERVER order and takes the first that matches; it never
/// ranks them. `question` is non-null only when the entry touches a protected
/// thing (§4.2's two tiers) and `screenText` is never empty (§4.4.2).
class SessionContingency {
  final String contingencyId;
  final Map<String, dynamic> trigger;
  final int planVersion;
  final List<String> stopIds;
  final String screenText;
  final String? question;
  final String? defaultArm; // "keep" | "shorten"
  final List<String> alternateStopIds;
  final String? atRiskStopId;
  final String finishHhmm;

  const SessionContingency({
    required this.contingencyId,
    required this.trigger,
    required this.planVersion,
    required this.stopIds,
    required this.screenText,
    this.question,
    this.defaultArm,
    this.alternateStopIds = const [],
    this.atRiskStopId,
    this.finishHhmm = '',
  });

  String get kind => (trigger['kind'] as String?) ?? '';
  String? get triggerStopId => trigger['stop_id'] as String?;

  /// The band `[lo, hi)` in minutes, or null when the trigger carries none.
  List<int>? get bandMinutes {
    final raw = trigger['band_minutes'];
    if (raw is! List || raw.length != 2) return null;
    return [(raw[0] as num).toInt(), (raw[1] as num).toInt()];
  }

  factory SessionContingency.fromJson(Map<String, dynamic> json) =>
      SessionContingency(
        contingencyId: json['contingency_id'] as String,
        trigger: (json['trigger'] as Map<String, dynamic>?) ?? const {},
        planVersion: (json['plan_version'] as num?)?.toInt() ?? 0,
        stopIds: ((json['stop_ids'] as List<dynamic>?) ?? const [])
            .map((e) => e as String)
            .toList(),
        screenText: (json['screen_text'] as String?) ?? '',
        question: json['question'] as String?,
        defaultArm: json['default_arm'] as String?,
        alternateStopIds:
            ((json['alternate_stop_ids'] as List<dynamic>?) ?? const [])
                .map((e) => e as String)
                .toList(),
        atRiskStopId: json['at_risk_stop_id'] as String?,
        finishHhmm: (json['finish_hhmm'] as String?) ?? '',
      );
}

/// GET /trips/{id}/session and every replan reply — the day the person is
/// standing in, versioned (design §8.2), with the contingency set beside it.
class SessionPlan {
  final String tripId;
  final int planVersion;
  final List<ItineraryStop> stops;

  /// Places a `door_closed` entry can carry the day to — HELD, not walked. The
  /// phone drops any id it does not already hold, so a standby has to arrive as
  /// a stop; it rides beside the day rather than in it, because these are not
  /// places the walk visits unless a door turns out to be shut.
  final List<ItineraryStop> standbys;
  final List<SessionPromise> promises;
  final int retimeToleranceSeconds;
  final List<SessionContingency> contingencies;
  final List<String> degradationNotices;

  /// The walking speed this day was planned at — the preset the phone re-times
  /// with until it has learned the person's own pace (design §4.1; S5.10).
  final double walkingPaceKmh;

  /// When the day's clock starts ("HH:MM"): the frame every server stop clock
  /// and the phone's own re-timing share (S5.10's seam compares in it).
  final String dayStartHhmm;

  /// When the day is planned to END ("HH:MM"): the phone's minutes-left on an
  /// open day (W5.2 R1.3 bands by minutes left) count down to this.
  final String plannedEndHhmm;

  /// The party the day was planned for (solo, couple, family, take_it_easy,
  /// with_luggage) or null: the screen-only switch is per PARTY (W5.2 R4).
  final String? party;

  /// Where the day ends and what to call it (null coords on an open walk with
  /// no end: the day ends at its last stop). The phone re-times the finish
  /// itself (W5.13): a precomputed line's clock is stale by the time it fires.
  final double? finishLat;
  final double? finishLng;
  final String finishName;

  /// wall | firm | open: a firm or wall finish that has moved past the tolerance
  /// earns one screen line (W5.14 Q3); an open day's finish moves in silence.
  final String endHardness;

  /// Phase 7 S7.3: the day's placement policy (who starts a piece at the
  /// footprint's edge, who at the first standstill; the own-place radius).
  /// Null from an older server: the phone starts at arrival and has no own
  /// place to measure the first step off the square against.
  final PlacementPolicy? placement;

  const SessionPlan({
    required this.tripId,
    required this.planVersion,
    required this.stops,
    this.standbys = const [],
    this.promises = const [],
    required this.retimeToleranceSeconds,
    this.contingencies = const [],
    this.degradationNotices = const [],
    this.walkingPaceKmh = 3.0,
    this.dayStartHhmm = '',
    this.plannedEndHhmm = '',
    this.party,
    this.finishLat,
    this.finishLng,
    this.finishName = 'your finish',
    this.endHardness = 'firm',
    this.placement,
  });

  factory SessionPlan.fromJson(Map<String, dynamic> json) => SessionPlan(
    tripId: json['trip_id'] as String,
    planVersion: (json['plan_version'] as num).toInt(),
    stops: ((json['stops'] as List<dynamic>?) ?? const [])
        .map((s) => ItineraryStop.fromJson(s as Map<String, dynamic>))
        .toList(),
    standbys: ((json['standbys'] as List<dynamic>?) ?? const [])
        .map((s) => ItineraryStop.fromJson(s as Map<String, dynamic>))
        .toList(),
    promises: ((json['promises'] as List<dynamic>?) ?? const [])
        .map((p) => SessionPromise.fromJson(p as Map<String, dynamic>))
        .toList(),
    retimeToleranceSeconds:
        (json['retime_tolerance_seconds'] as num?)?.toInt() ?? 180,
    contingencies: ((json['contingencies'] as List<dynamic>?) ?? const [])
        .map((c) => SessionContingency.fromJson(c as Map<String, dynamic>))
        .toList(),
    degradationNotices: [
      for (final row in (json['degradations'] as List<dynamic>?) ?? const [])
        if (row is Map<String, dynamic> &&
            (row['human'] as String?)?.trim().isNotEmpty == true)
          (row['human'] as String).trim(),
    ],
    walkingPaceKmh: (json['walking_pace_kmh'] as num?)?.toDouble() ?? 3.0,
    dayStartHhmm: (json['day_start_hhmm'] as String?) ?? '',
    plannedEndHhmm: (json['planned_end_hhmm'] as String?) ?? '',
    party: json['party'] as String?,
    finishLat: (json['finish_lat'] as num?)?.toDouble(),
    finishLng: (json['finish_lng'] as num?)?.toDouble(),
    finishName: (json['finish_name'] as String?) ?? 'your finish',
    endHardness: (json['end_hardness'] as String?) ?? 'firm',
    placement: json['placement'] == null
        ? null
        : PlacementPolicy.fromJson(json['placement'] as Map<String, dynamic>),
  );
}
