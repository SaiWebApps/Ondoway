import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/trip_service.dart';

Map<String, dynamic> _sampleTripResponse() => {
      'trip_id': 'trip-123',
      'trip_name': 'Trip (2026-05-04)',
      'profile_id': 'profile-abc',
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

void main() {
  group('TripService', () {
    test('generateTrip sends correct POST body', () async {
      Map<String, dynamic>? capturedBody;

      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, contains('/trips/generate'));
        expect(request.headers['Content-Type'], 'application/json');
        expect(request.headers['Authorization'], 'Bearer test-token');
        capturedBody = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(jsonEncode(_sampleTripResponse()), 201);
      });

      final service = TripService(httpClient: client);
      await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'test-token',
        radiusM: 3000,
        maxStops: 10,
        durationMin: 480,
        startTime: '09:00',
      );

      expect(capturedBody, isNotNull);
      expect(capturedBody!['profile_id'], 'profile-abc');
      expect(capturedBody!['center_lat'], 48.8566);
      expect(capturedBody!['center_lng'], 2.3522);
      expect(capturedBody!['radius_m'], 3000);
      expect(capturedBody!['max_stops'], 10);
      expect(capturedBody!['duration_min'], 480);
      expect(capturedBody!['start_date'], '2026-05-04');
      expect(capturedBody!['end_date'], '2026-05-06');
      expect(capturedBody!['start_time'], '09:00');
      expect(capturedBody!['kid_friendly_only'], false);
      expect(capturedBody!['end_hardness'], 'firm',
          reason: 'unanswered means a hard deadline (owner ruling 2026-08-19)');
    });

    test('generateTrip sends the chosen end_hardness (S6.9)', () async {
      Map<String, dynamic>? capturedBody;
      final client = MockClient((request) async {
        capturedBody = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(jsonEncode(_sampleTripResponse()), 201);
      });
      final service = TripService(httpClient: client);
      await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'test-token',
        endHardness: 'open',
      );
      expect(capturedBody!['end_hardness'], 'open');
    });

    test('generateTrip returns parsed GeneratedTrip on 201', () async {
      final client = MockClient((request) async {
        return http.Response(jsonEncode(_sampleTripResponse()), 201);
      });

      final service = TripService(httpClient: client);
      final trip = await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );

      expect(trip.tripId, 'trip-123');
      expect(trip.tripName, 'Trip (2026-05-04)');
      expect(trip.totalStops, 2);
      expect(trip.stops.length, 2);
      expect(trip.stops[0].poiName, 'Eiffel Tower');
      expect(service.lastGenerated, isNotNull);
      expect(service.isGenerating, false);
    });

    test('generateTrip throws on 404 (profile not found)', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': "Profile 'bad-id' not found"}),
          404,
        );
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.generateTrip(
          profileId: 'bad-id',
          centerLat: 48.8566,
          centerLng: 2.3522,
          startDate: '2026-05-04',
          endDate: '2026-05-06',
          accessToken: 'token',
        ),
        throwsA(isA<TripServiceException>()),
      );
    });

    test('generateTrip throws on 422 (no POIs in radius)', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode(
            {'detail': 'No POIs found within the specified radius and filters'},
          ),
          422,
        );
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.generateTrip(
          profileId: 'profile-abc',
          centerLat: 0.0,
          centerLng: 0.0,
          startDate: '2026-05-04',
          endDate: '2026-05-06',
          accessToken: 'token',
        ),
        throwsA(isA<TripServiceException>()),
      );
    });

    test('generateTrip sets error message on failure', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': 'No POIs found within the specified radius and filters'}),
          422,
        );
      });

      final service = TripService(httpClient: client);

      try {
        await service.generateTrip(
          profileId: 'profile-abc',
          centerLat: 0.0,
          centerLng: 0.0,
          startDate: '2026-05-04',
          endDate: '2026-05-06',
          accessToken: 'token',
        );
      } catch (_) {}

      expect(service.error, contains('No POIs found'));
      expect(service.isGenerating, false);
    });

    test('generateTrip surfaces the degradation notices the backend reported',
        () async {
      // The backend still builds a tour when the walking-directions service is
      // unreachable, but its walking times are estimates rather than measured
      // routes, and it says so on the wire. Dropping that on parse would show
      // the traveller an estimate wearing a measurement's clothes.
      final client = MockClient((request) async {
        final body = _sampleTripResponse();
        body['degradations'] = [
          {
            'kind': 'walking_times_estimated',
            'human': 'Walking times between stops are estimates, not measured '
                'routes, so the tour may run a little longer or shorter than '
                'it says.',
            'component': 'premium_tour.plan_premium_options',
            'error_type': '',
            'error_message': '',
            'context': <String, String>{},
          },
        ];
        return http.Response(jsonEncode(body), 201);
      });

      final service = TripService(httpClient: client);
      final trip = await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );

      expect(trip.degradationNotices, hasLength(1));
      expect(trip.degradationNotices.single, contains('estimates'));
      // The plain-English sentence only — never the operator-facing fields.
      expect(trip.degradationNotices.single, isNot(contains('premium_tour')));
      expect(
        trip.degradationNotices.single,
        isNot(contains('walking_times_estimated')),
      );
    });

    test('generateTrip keeps every degradation, not just the first', () async {
      final client = MockClient((request) async {
        final body = _sampleTripResponse();
        body['degradations'] = [
          {'human': 'First thing that went worse.'},
          {'human': 'Second thing that went worse.'},
        ];
        return http.Response(jsonEncode(body), 201);
      });

      final service = TripService(httpClient: client);
      final trip = await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );

      expect(trip.degradationNotices, hasLength(2));
      expect(trip.degradationNotices.last, 'Second thing that went worse.');
    });

    test('a response with no degradations key parses to no notices', () async {
      final client = MockClient(
        (request) async => http.Response(jsonEncode(_sampleTripResponse()), 201),
      );
      final service = TripService(httpClient: client);
      final trip = await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );
      expect(trip.degradationNotices, isEmpty);
    });

    test('a malformed degradations payload parses to no notices, not a crash',
        () async {
      // An older or misbehaving server must never take the itinerary screen
      // down: a JSON null, a non-list, a non-map row, a row with no human
      // sentence and a blank one all yield nothing and none of them throws.
      final client = MockClient((request) async {
        final body = _sampleTripResponse();
        body['degradations'] = [
          'not a map',
          {'kind': 'walking_times_estimated'},
          {'human': '   '},
          42,
        ];
        return http.Response(jsonEncode(body), 201);
      });

      final service = TripService(httpClient: client);
      final trip = await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );
      expect(trip.degradationNotices, isEmpty);

      final nullClient = MockClient((request) async {
        final body = _sampleTripResponse();
        body['degradations'] = null;
        return http.Response(jsonEncode(body), 201);
      });
      final nullTrip = await TripService(httpClient: nullClient).generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );
      expect(nullTrip.degradationNotices, isEmpty);
    });

    test('fetchSavedTrips returns list of trips', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.queryParameters['profile_id'], 'profile-abc');
        return http.Response(
          jsonEncode([_sampleTripResponse()]),
          200,
        );
      });

      final service = TripService(httpClient: client);
      final trips = await service.fetchSavedTrips('profile-abc', 'token');

      expect(trips.length, 1);
      expect(trips[0].tripId, 'trip-123');
      expect(service.savedTrips.length, 1);
    });

    test('fetchSavedTrips returns empty list when no trips', () async {
      final client = MockClient((request) async {
        return http.Response(jsonEncode([]), 200);
      });

      final service = TripService(httpClient: client);
      final trips = await service.fetchSavedTrips('profile-abc', 'token');

      expect(trips, isEmpty);
      expect(service.savedTrips, isEmpty);
    });

    test('fetchSavedTrips throws on 404', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': 'Profile not found'}),
          404,
        );
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.fetchSavedTrips('bad-id', 'token'),
        throwsA(isA<TripServiceException>()),
      );
    });

    test('saveTrip adds trip to savedTrips list', () {
      final service = TripService(httpClient: MockClient((r) async => http.Response('', 200)));
      final trip = GeneratedTrip.fromJson(_sampleTripResponse());

      service.saveTrip(trip);

      expect(service.savedTrips.length, 1);
      expect(service.savedTrips[0].tripId, 'trip-123');
    });

    test('deleteTrip removes trip by ID', () {
      final service = TripService(httpClient: MockClient((r) async => http.Response('', 200)));
      final trip = GeneratedTrip.fromJson(_sampleTripResponse());

      service.saveTrip(trip);
      expect(service.savedTrips.length, 1);

      service.deleteTrip('trip-123');
      expect(service.savedTrips, isEmpty);
    });

    test('reset clears all state', () async {
      final client = MockClient((request) async {
        return http.Response(jsonEncode(_sampleTripResponse()), 201);
      });

      final service = TripService(httpClient: client);
      await service.generateTrip(
        profileId: 'profile-abc',
        centerLat: 48.8566,
        centerLng: 2.3522,
        startDate: '2026-05-04',
        endDate: '2026-05-06',
        accessToken: 'token',
      );
      service.saveTrip(service.lastGenerated!);
      expect(service.savedTrips, isNotEmpty);

      service.reset();

      expect(service.savedTrips, isEmpty);
      expect(service.lastGenerated, isNull);
      expect(service.isGenerating, false);
      expect(service.error, isNull);
    });

    test('confirmTripStopAudio POSTs to the per-STOP generate endpoint',
        () async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        // Per-stop endpoint, NOT the per-beat /audio/generate-trip/{id}.
        expect(request.url.path, contains('/audio/generate-trip-stops/trip-123'));
        expect(request.headers['Authorization'], 'Bearer test-token');
        return http.Response(
          jsonEncode({
            'trip_id': 'trip-123',
            'generated': 4,
            'skipped': 1,
            'failed': 0,
            'results': [],
          }),
          200,
        );
      });

      final service = TripService(httpClient: client);
      final result =
          await service.confirmTripStopAudio('trip-123', 'test-token');

      expect(result['trip_id'], 'trip-123');
      expect(result['generated'], 4);
      expect(result['skipped'], 1);
      expect(result['failed'], 0);
    });

    test('confirmTripStopAudio throws on 404', () async {
      final client = MockClient((request) async {
        return http.Response(jsonEncode({'detail': 'Trip not found'}), 404);
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.confirmTripStopAudio('bad-id', 'token'),
        throwsA(isA<TripServiceException>()),
      );
    });

    test('confirmTripStopAudio throws on 500', () async {
      final client = MockClient((request) async {
        return http.Response('Internal Server Error', 500);
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.confirmTripStopAudio('trip-123', 'token'),
        throwsA(isA<TripServiceException>()),
      );
    });

    // Phase 4 (design §8.1): the server plans ONE day per trip and its route
    // id is always {trip_id}-opt1 — compose is the phone's confirm, not a
    // choice among flavours.
    test('composeTrip POSTs route_id to the compose endpoint and parses stops',
        () async {
      Map<String, dynamic>? capturedBody;

      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, contains('/trips/trip-123/compose'));
        expect(request.headers['Content-Type'], 'application/json');
        expect(request.headers['Authorization'], 'Bearer test-token');
        capturedBody = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'trip_id': 'trip-123',
            'route_id': 'trip-123-opt1',
            'attempts': 1,
            'stops': [
              {
                'sort_order': 1,
                // FRESH stop_id — stops are re-persisted by /compose.
                'stop_id': 'item-new-1',
                'poi_id': 'poi-1',
                'poi_name': 'Eiffel Tower',
                'lat': 48.8584,
                'lng': 2.2945,
                'beat_id': 'beat-1',
                'beat_ids': ['beat-1'],
                'lens_name': 'dark_history',
                'lens_display': 'Dark History',
                'duration_min': 5,
                'importance_tier': 5,
                'start_time': '09:00',
                'narration': 'Composed narration.',
                'audio_url': null,
                'audio_duration_sec': null,
                'dwell_seconds': 300,
                'transit_polyline': null,
              },
            ],
          }),
          200,
        );
      });

      final service = TripService(httpClient: client);
      final stops = await service.composeTrip(
        'trip-123',
        'trip-123-opt1',
        'test-token',
      );

      expect(capturedBody, {'route_id': 'trip-123-opt1'});
      expect(stops.length, 1);
      expect(stops[0].stopId, 'item-new-1');
      expect(stops[0].poiName, 'Eiffel Tower');
      // Audio is generated AFTER compose by the per-stop flow.
      expect(stops[0].audioUrl, isNull);
    });

    test(
        'composeTrip throws ComposeVerificationException on 422 '
        'compose_verification_failed', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'detail': {
              'reason': 'compose_verification_failed',
              'attempts': 2,
            },
          }),
          422,
        );
      });

      final service = TripService(httpClient: client);

      expect(
        () => service.composeTrip('trip-123', 'trip-123-opt1', 'token'),
        throwsA(
          isA<ComposeVerificationException>()
              .having((e) => e.reason, 'reason', 'compose_verification_failed')
              .having((e) => e.attempts, 'attempts', 2)
              // design §8.1: one day per trip means no second option to
              // offer, so the message the UI shows says the honest way out.
              .having(
                (e) => e.message,
                'message',
                "This day couldn't be written. Try generating again.",
              ),
        ),
      );
    });

    test('composeTrip throws plain TripServiceException on 404', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': 'Trip not found'}),
          404,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.composeTrip('bad-id', 'bad-id-opt1', 'token'),
        throwsA(
          isA<TripServiceException>().having(
            (e) => e is ComposeVerificationException,
            'is not a verification refusal',
            isFalse,
          ),
        ),
      );
    });

    // The family surface's refusal: writes keep one captain, and the crew's
    // typed 403 must reach the screen as a sentence, never raw JSON.
    test('composeTrip turns the typed 403 captain_only into a plain sentence',
        () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'detail': {
              'reason': 'captain_only',
              'detail': "Only the trip's captain can change the day",
            },
          }),
          403,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.composeTrip('trip-123', 'trip-123-opt1', 'token'),
        throwsA(
          isA<CaptainOnlyException>().having(
            (e) => e.message,
            'message',
            "Only the trip's captain can change the day",
          ),
        ),
      );
    });

    test('replanSession turns the typed 403 captain_only into a plain sentence',
        () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'detail': {
              'reason': 'captain_only',
              'detail': "Only the trip's captain can change the day",
            },
          }),
          403,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.replanSession(
          'trip-123',
          'token',
          lat: 48.86,
          lng: 2.33,
          wallElapsedSeconds: 0,
          tourElapsedSeconds: 0,
        ),
        throwsA(
          isA<CaptainOnlyException>().having(
            (e) => e.message,
            'message',
            "Only the trip's captain can change the day",
          ),
        ),
      );
    });

    test('generateDeeperDiveAudio POSTs to the keep-exploring endpoint and '
        'parses the result (KE5)', () async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(
          request.url.path,
          contains('/audio/stops/stop-77/keep-exploring'),
        );
        return http.Response(
          jsonEncode({
            'stop_id': 'stop-77',
            'status': 'generated',
            'audio_url': 'https://cdn.example.com/stop-77-ke.mp3',
            'duration_sec': 42.5,
            'error': null,
          }),
          200,
        );
      });

      final service = TripService(httpClient: client);
      final result = await service.generateDeeperDiveAudio('stop-77');

      expect(result.stopId, 'stop-77');
      expect(result.status, 'generated');
      expect(result.audioUrl, 'https://cdn.example.com/stop-77-ke.mp3');
      expect(result.durationSec, 42.5);
    });

    test('generateDeeperDiveAudio surfaces status==failed as an exception (KE5)',
        () async {
      // Soft TTS failure: 200 with status='failed', never a 500.
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'stop_id': 'stop-77',
            'status': 'failed',
            'audio_url': null,
            'duration_sec': null,
            'error': 'provider timeout',
          }),
          200,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.generateDeeperDiveAudio('stop-77'),
        throwsA(
          isA<KeepExploringException>()
              .having((e) => e.message, 'message', contains('provider timeout')),
        ),
      );
    });

    test('generateDeeperDiveAudio throws on 404 (unknown stop) (KE5)', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': "Stop 'bad' not found"}),
          404,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.generateDeeperDiveAudio('bad'),
        throwsA(isA<TripServiceException>()),
      );
    });

    // Phase 5 (design §4.6/§8.2): the living session on the wire.
    test('fetchSession GETs the session and parses the plan, its promises and '
        'its contingency set', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, contains('/trips/trip-123/session'));
        expect(request.headers['Authorization'], 'Bearer test-token');
        return http.Response(
          jsonEncode({
            'trip_id': 'trip-123',
            'plan_version': 2,
            'stops': [
              {
                'sort_order': 1,
                'stop_id': 'item-1',
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
                'narration': 'Composed narration.',
                'audio_url': null,
                'audio_duration_sec': null,
                'dwell_seconds': 300,
                'transit_polyline': null,
              },
            ],
            'promises': [
              {
                'promise_id': 'poi-1',
                'kind': 'rest',
                'name': 'Bench',
                'arrives_hhmm': '14:20',
                'departs_hhmm': '14:30',
                'protected': true,
              },
            ],
            'retime_tolerance_seconds': 180,
            'contingencies': [
              {
                'contingency_id': 'v2-1',
                'trigger': {'kind': 'wrap_up_from', 'stop_id': 'poi-1'},
                'plan_version': 2,
                'stop_ids': [],
                'screen_text':
                    'Straight to your finish · your finish about 15:09',
                'question': null,
                'default_arm': null,
                'alternate_stop_ids': [],
                'at_risk_stop_id': null,
                'finish_hhmm': '15:09',
              },
            ],
            'degradations': [],
          }),
          200,
        );
      });
      final service = TripService(httpClient: client);
      final plan = await service.fetchSession('trip-123', 'test-token');
      expect(plan.planVersion, 2);
      expect(plan.stops.single.stopId, 'item-1');
      expect(plan.promises.single.protected, isTrue);
      expect(plan.retimeToleranceSeconds, 180);
      expect(plan.contingencies.single.kind, 'wrap_up_from');
      expect(plan.contingencies.single.triggerStopId, 'poi-1');
      expect(plan.contingencies.single.question, isNull);
      expect(plan.contingencies.single.screenText, isNotEmpty);
    });

    test('fetchSession surfaces "no session yet" (404) as its own exception — '
        'the cue to compose, not a failure', () async {
      final client = MockClient(
        (request) async => http.Response(
          jsonEncode({
            'detail': {
              'reason': 'no_session_yet',
              'detail': 'compose the trip first',
            },
          }),
          404,
        ),
      );
      final service = TripService(httpClient: client);
      await expectLater(
        () => service.fetchSession('trip-123', 'token'),
        throwsA(isA<NoSessionYetException>()),
      );
    });

    test('replanSession POSTs the phone\'s OBSERVATIONS — position, two clocks, '
        'learned rates, next stop — and parses the next version', () async {
      Map<String, dynamic>? sent;
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, contains('/trips/trip-123/session/replan'));
        sent = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'trip_id': 'trip-123',
            'plan_version': 3,
            'stops': [],
            'promises': [],
            'retime_tolerance_seconds': 180,
            'contingencies': [],
            'degradations': [
              {
                'human':
                    'Walking times between stops are estimates, not measured '
                    'routes, so the tour may run a little longer or shorter than it says.',
              },
            ],
          }),
          200,
        );
      });
      final service = TripService(httpClient: client);
      final plan = await service.replanSession(
        'trip-123',
        'token',
        lat: 48.86,
        lng: 2.33,
        wallElapsedSeconds: 2400,
        tourElapsedSeconds: 2100,
        observedPace: 1.1,
        listeningRate: 1.5,
        nextStopIndex: 2,
        phoneNextStopHhmm: '09:28',
      );
      expect(sent, {
        'lat': 48.86,
        'lng': 2.33,
        'wall_elapsed_seconds': 2400,
        'tour_elapsed_seconds': 2100,
        'observed_pace': 1.1,
        'listening_rate': 1.5,
        'next_stop_index': 2,
        // S5.10's seam: the phone's OWN clock for its next stop — an observation
        // the server compares with, reports on, never adopts.
        'phone_next_stop_hhmm': '09:28',
      });
      expect(plan.planVersion, 3);
      expect(
        plan.degradationNotices.single,
        startsWith('Walking times between stops'),
      );
    });

    test('generateDeeperDiveAudio throws on 409 (no extras) (KE5)', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({'detail': 'no keep-exploring extras'}),
          409,
        );
      });

      final service = TripService(httpClient: client);

      await expectLater(
        () => service.generateDeeperDiveAudio('stop-77'),
        throwsA(isA<TripServiceException>()),
      );
    });
  });
}

// Phase 6 S6.9 (design W5.14 — "is 18:00 a table or a guess?"; owner ruling
// 2026-08-19: skipping the question means a HARD DEADLINE): the answer rides
// the generate request as `end_hardness`, and an unanswered planner sends
// "firm" explicitly — the server's own default, stated rather than implied.
