import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';

void main() {
  group('ItineraryStop', () {
    test('fromJson parses all fields correctly', () {
      final json = {
        'sort_order': 1,
        'stop_id': 'item-789',
        'poi_id': 'poi-123',
        'poi_name': 'Eiffel Tower',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'dark_history',
        'lens_display': 'Dark History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
        'transit_polyline': 'encodedpolyline6',
      };

      final stop = ItineraryStop.fromJson(json);

      expect(stop.sortOrder, 1);
      expect(stop.stopId, 'item-789');
      expect(stop.poiId, 'poi-123');
      expect(stop.poiName, 'Eiffel Tower');
      expect(stop.lat, 48.8584);
      expect(stop.lng, 2.2945);
      expect(stop.beatId, 'beat-456');
      expect(stop.lensName, 'dark_history');
      expect(stop.lensDisplay, 'Dark History');
      expect(stop.durationMin, 5);
      expect(stop.importanceTier, 5);
      expect(stop.startTime, '09:00');
      expect(stop.transitPolyline, 'encodedpolyline6');
    });

    test('fromJson defaults transitPolyline to null when absent', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Eiffel Tower',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'dark_history',
        'lens_display': 'Dark History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
      };
      expect(ItineraryStop.fromJson(json).transitPolyline, isNull);
    });

    test('fromJson defaults stopId to null when absent', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Eiffel Tower',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'dark_history',
        'lens_display': 'Dark History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
      };
      expect(ItineraryStop.fromJson(json).stopId, isNull);
    });

    test('fromJson parses extra_beat_ids and extra_narration (KE5)', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Eiffel Tower',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'dark_history',
        'lens_display': 'Dark History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
        'extra_beat_ids': ['beat-extra-1', 'beat-extra-2'],
        'extra_narration': 'There is even more to discover here.',
      };

      final stop = ItineraryStop.fromJson(json);

      expect(stop.extraBeatIds, ['beat-extra-1', 'beat-extra-2']);
      expect(stop.extraNarration, 'There is even more to discover here.');
    });

    test('fromJson defaults extra fields when absent (old trips) (KE5)', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Eiffel Tower',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'dark_history',
        'lens_display': 'Dark History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
      };

      final stop = ItineraryStop.fromJson(json);

      expect(stop.extraBeatIds, isEmpty);
      expect(stop.extraNarration, isNull);
    });

    test('extra fields survive toJson round-trip (KE5)', () {
      const original = ItineraryStop(
        sortOrder: 1,
        poiId: 'poi-1',
        poiName: 'Test POI',
        lat: 48.8,
        lng: 2.3,
        beatId: 'beat-1',
        lensName: 'scandal',
        lensDisplay: 'Scandal',
        durationMin: 4,
        importanceTier: 3,
        startTime: '10:00',
        extraBeatIds: ['x1', 'x2'],
        extraNarration: 'More here.',
      );

      final rebuilt = ItineraryStop.fromJson(original.toJson());

      expect(rebuilt.extraBeatIds, ['x1', 'x2']);
      expect(rebuilt.extraNarration, 'More here.');
    });

    test('fromJson handles integer lat/lng values', () {
      final json = {
        'sort_order': 2,
        'poi_id': 'poi-789',
        'poi_name': 'Notre-Dame',
        'lat': 48,
        'lng': 2,
        'beat_id': 'beat-012',
        'lens_name': 'architecture',
        'lens_display': 'Architecture',
        'duration_min': 3,
        'importance_tier': 4,
        'start_time': '09:05',
      };

      final stop = ItineraryStop.fromJson(json);

      expect(stop.lat, 48.0);
      expect(stop.lng, 2.0);
    });

    test('toJson round-trips correctly', () {
      final original = ItineraryStop(
        sortOrder: 1,
        poiId: 'poi-1',
        poiName: 'Test POI',
        lat: 48.8,
        lng: 2.3,
        beatId: 'beat-1',
        lensName: 'scandal',
        lensDisplay: 'Scandal',
        durationMin: 4,
        importanceTier: 3,
        startTime: '10:00',
      );

      final json = original.toJson();
      final rebuilt = ItineraryStop.fromJson(json);

      expect(rebuilt.sortOrder, original.sortOrder);
      expect(rebuilt.poiId, original.poiId);
      expect(rebuilt.poiName, original.poiName);
      expect(rebuilt.lat, original.lat);
      expect(rebuilt.lng, original.lng);
    });

    // The stop's placed trigger geometry
    // rides the wire and the phone READS it — the footprint is the server's
    // number, never a circle of the phone's. A legacy item carries none.
    test('fromJson reads the placed trigger geometry (S7.3) and keeps it '
        'through toJson and copyWith', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Place des Vosges',
        'lat': 48.8556,
        'lng': 2.3655,
        'beat_id': 'beat-456',
        'lens_name': 'history',
        'lens_display': 'History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '10:30',
        'trigger': {'kind': 'circle', 'radius_m': 140},
      };
      final stop = ItineraryStop.fromJson(json);
      expect(stop.trigger, isNotNull);
      expect(stop.trigger!.kind, 'circle');
      expect(stop.trigger!.radiusM, 140.0);
      final rebuilt = ItineraryStop.fromJson(stop.toJson());
      expect(rebuilt.trigger!.radiusM, 140.0);
      expect(stop.copyWith(audioUrl: 'https://cdn/x.mp3').trigger!.radiusM, 140.0);
    });

    // The stop's LEG piece — its walking line,
    // voiced as its own file — rides the wire beside the story and survives the
    // round trip and copyWith.
    test('fromJson reads the leg piece (S7.7) and keeps it through toJson and '
        'copyWith', () {
      final json = {
        'sort_order': 2,
        'poi_id': 'poi-2',
        'poi_name': 'Notre-Dame',
        'lat': 48.853,
        'lng': 2.3499,
        'beat_id': 'beat-2',
        'lens_name': 'history',
        'lens_display': 'History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '10:30',
        'leg_narration': 'Walk southeast along the river for about ten minutes.',
        'leg_audio_url': 'https://cdn.example.com/stop-2-leg.mp3',
        'leg_audio_duration_sec': 9.5,
      };
      final stop = ItineraryStop.fromJson(json);
      expect(stop.legNarration, startsWith('Walk southeast'));
      expect(stop.legAudioUrl, 'https://cdn.example.com/stop-2-leg.mp3');
      expect(stop.legAudioDurationSec, 9.5);
      final rebuilt = ItineraryStop.fromJson(stop.toJson());
      expect(rebuilt.legAudioUrl, stop.legAudioUrl);
      expect(stop.copyWith(audioUrl: 'https://cdn/x.mp3').legAudioUrl, stop.legAudioUrl);
    });

    // A marquee's chapters ride the wire as a list, each with
    // its own place, radius, roof flag, text and (once voiced) file.
    test('fromJson reads the chapters (S7.7 B); a legacy item has none', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'nd',
        'poi_name': 'Notre-Dame Cathedral',
        'lat': 48.852966,
        'lng': 2.349902,
        'beat_id': 'b',
        'lens_name': 'history',
        'lens_display': 'History',
        'duration_min': 25,
        'importance_tier': 5,
        'start_time': '10:30',
        'segments': [
          {
            'label': 'The west front', 'lat': 48.85325, 'lng': 2.34875, 'radius_m': 45.0,
            'indoor': false, 'narration': 'The facade.', 'audio_url': 'https://cdn/w.mp3',
            'audio_duration_sec': 31.5,
          },
          {
            'label': 'Inside', 'lat': 48.853, 'lng': 2.34975, 'radius_m': 60.0,
            'indoor': true, 'narration': 'The nave.', 'audio_url': null,
            'audio_duration_sec': null,
          },
        ],
      };
      final stop = ItineraryStop.fromJson(json);
      expect(stop.segments.length, 2);
      expect(stop.segments[0].label, 'The west front');
      expect(stop.segments[0].radiusM, 45.0);
      expect(stop.segments[0].indoor, isFalse);
      expect(stop.segments[0].audioUrl, 'https://cdn/w.mp3');
      expect(stop.segments[0].audioDurationSec, 31.5);
      expect(stop.segments[1].indoor, isTrue);
      expect(stop.segments[1].audioUrl, isNull);
      final rebuilt = ItineraryStop.fromJson(stop.toJson());
      expect(rebuilt.segments.map((s) => s.label), ['The west front', 'Inside']);
      expect(stop.copyWith(audioUrl: 'x').segments.length, 2);
      json.remove('segments');
      expect(ItineraryStop.fromJson(json).segments, isEmpty);
    });

    test('a legacy item without a trigger reads null — no geometry (S7.3)', () {
      final json = {
        'sort_order': 1,
        'poi_id': 'poi-123',
        'poi_name': 'Old item',
        'lat': 48.8584,
        'lng': 2.2945,
        'beat_id': 'beat-456',
        'lens_name': 'history',
        'lens_display': 'History',
        'duration_min': 5,
        'importance_tier': 5,
        'start_time': '09:00',
      };
      expect(ItineraryStop.fromJson(json).trigger, isNull);
      expect(ItineraryStop.fromJson(json).toJson()['trigger'], isNull);
    });
  });

  group('SessionPlan placement (S7.3)', () {
    test('fromJson reads the day\'s placement policy; an older server sends none',
        () {
      final base = {
        'trip_id': 'trip-1',
        'plan_version': 1,
        'stops': <dynamic>[],
        'retime_tolerance_seconds': 180,
      };
      final family = SessionPlan.fromJson({
        ...base,
        'placement': {'start_at': 'standstill', 'own_place_m': 60},
      });
      expect(family.placement, isNotNull);
      expect(family.placement!.startsAtStandstill, isTrue);
      expect(family.placement!.ownPlaceM, 60.0);
      final solo = SessionPlan.fromJson({
        ...base,
        'placement': {'start_at': 'arrival', 'own_place_m': 60},
      });
      expect(solo.placement!.startsAtStandstill, isFalse);
      expect(SessionPlan.fromJson(base).placement, isNull);
    });
  });

  group('GeneratedTrip', () {
    test('fromJson parses full response with stops', () {
      final json = {
        'trip_id': 'trip-abc',
        'trip_name': 'Trip (2026-05-04)',
        'profile_id': 'profile-xyz',
        'total_stops': 2,
        'total_duration_min': 8,
        'anchor_count': 1,
        'flavour_count': 1,
        'stops': [
          {
            'sort_order': 1,
            'poi_id': 'poi-1',
            'poi_name': 'Eiffel Tower',
            'lat': 48.8584,
            'lng': 2.2945,
            'beat_id': 'beat-1',
            'lens_name': 'dark_history',
            'lens_display': 'Dark History',
            'duration_min': 5,
            'importance_tier': 5,
            'start_time': '09:00',
          },
          {
            'sort_order': 2,
            'poi_id': 'poi-2',
            'poi_name': 'Louvre',
            'lat': 48.8606,
            'lng': 2.3376,
            'beat_id': 'beat-2',
            'lens_name': 'scandal',
            'lens_display': 'Scandal & Intrigue',
            'duration_min': 3,
            'importance_tier': 3,
            'start_time': '09:05',
          },
        ],
      };

      final trip = GeneratedTrip.fromJson(json);

      expect(trip.tripId, 'trip-abc');
      expect(trip.tripName, 'Trip (2026-05-04)');
      expect(trip.profileId, 'profile-xyz');
      expect(trip.totalStops, 2);
      expect(trip.totalDurationMin, 8);
      expect(trip.anchorCount, 1);
      expect(trip.flavourCount, 1);
      expect(trip.stops.length, 2);
      expect(trip.stops[0].poiName, 'Eiffel Tower');
      expect(trip.stops[1].poiName, 'Louvre');
    });

    // The server plans ONE day, and generate keeps
    // `options` on the wire as a list of exactly one (stored older
    // trips carry multi-option lists, so the field stays a list forever).
    // The phone no longer parses or exposes it — the model has no options
    // surface at all, and an options key of ANY shape is ignored input.
    test('fromJson accepts the one-option generate payload without reading it '
        '(design §8.1)', () {
      final json = {
        'trip_id': 'trip-one',
        'trip_name': 'One Day Trip',
        'profile_id': 'profile-1',
        'total_stops': 0,
        'total_duration_min': 0,
        'anchor_count': 0,
        'flavour_count': 0,
        'stops': <dynamic>[],
        // The wire's single option — present, well-formed, and ignored.
        'options': [
          {
            'route_id': 'trip-one-opt1',
            'stops': [
              {'name': 'A', 'band': 'dwell', 'minutes': 5},
            ],
            'eta_seconds': 3600,
          },
        ],
      };

      final trip = GeneratedTrip.fromJson(json);

      expect(trip.tripId, 'trip-one');
      expect(trip.stops, isEmpty);
    });

    test('fromJson accepts a stored pre-Phase-4 multi-option payload '
        '(design §8.1)', () {
      // Old trips carry k options; compose must keep serving them, so the
      // parser must keep swallowing them — malformed rows included, because
      // the phone never looks inside.
      final json = {
        'trip_id': 'trip-old',
        'trip_name': 'Pre-Phase-4 Trip',
        'profile_id': 'profile-1',
        'total_stops': 0,
        'total_duration_min': 0,
        'anchor_count': 0,
        'flavour_count': 0,
        'stops': <dynamic>[],
        'options': [
          {'route_id': 'trip-old-opt1', 'eta_seconds': 3600},
          {'route_id': 'trip-old-opt2', 'eta_seconds': 4200},
          'not even a map',
        ],
      };

      expect(GeneratedTrip.fromJson(json).tripId, 'trip-old');
    });

    test('fromJson parses a GET /trips payload with no options key '
        '(design §8.1)', () {
      final json = {
        'trip_id': 'trip-saved',
        'trip_name': 'Saved Trip',
        'profile_id': 'profile-1',
        'total_stops': 0,
        'total_duration_min': 0,
        'anchor_count': 0,
        'flavour_count': 0,
        'stops': <dynamic>[],
      };

      expect(GeneratedTrip.fromJson(json).tripId, 'trip-saved');
    });

    test('fromJson still hard-requires flavour_count (wire contract, '
        'design §8.1)', () {
      // flavour_count predates flavours: it is a per-trip stop-kind stat
      // (stops minus anchors), merely mis-named. It stays REQUIRED on the
      // wire — a server that drops it breaks the phone, and this test is the
      // tripwire that says so before a tourist does.
      final json = {
        'trip_id': 'trip-nofc',
        'trip_name': 'No Flavour Count',
        'profile_id': 'profile-1',
        'total_stops': 0,
        'total_duration_min': 0,
        'anchor_count': 0,
        'stops': <dynamic>[],
      };

      expect(() => GeneratedTrip.fromJson(json), throwsA(isA<TypeError>()));
    });

    test('fromJson handles empty stops list', () {
      final json = {
        'trip_id': 'trip-empty',
        'trip_name': 'Empty Trip',
        'profile_id': 'profile-1',
        'total_stops': 0,
        'total_duration_min': 0,
        'anchor_count': 0,
        'flavour_count': 0,
        'stops': [],
      };

      final trip = GeneratedTrip.fromJson(json);

      expect(trip.tripId, 'trip-empty');
      expect(trip.totalStops, 0);
      expect(trip.stops, isEmpty);
    });

    test('toJson round-trips correctly', () {
      final original = GeneratedTrip(
        tripId: 'trip-rt',
        tripName: 'Round Trip',
        profileId: 'prof-1',
        totalStops: 1,
        totalDurationMin: 5,
        anchorCount: 1,
        flavourCount: 0,
        stops: const [
          ItineraryStop(
            sortOrder: 1,
            poiId: 'poi-rt',
            poiName: 'Test',
            lat: 48.0,
            lng: 2.0,
            beatId: 'beat-rt',
            lensName: 'test',
            lensDisplay: 'Test',
            durationMin: 5,
            importanceTier: 5,
            startTime: '09:00',
          ),
        ],
      );

      final json = original.toJson();
      final rebuilt = GeneratedTrip.fromJson(json);

      expect(rebuilt.tripId, original.tripId);
      expect(rebuilt.tripName, original.tripName);
      expect(rebuilt.stops.length, 1);
      expect(rebuilt.stops[0].poiId, 'poi-rt');
    });
  });
}
