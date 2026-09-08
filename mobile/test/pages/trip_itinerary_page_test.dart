import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/trip_itinerary_page.dart';
import 'package:ondoway/theme/theme.dart';
import 'package:ondoway/services/audio_service.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/trip_service.dart';

// flutter_secure_storage doesn't work in tests — in-memory stand-in so an
// AuthService can hold a real access token (verifyMagicLink -> _storeTokens).
class _FakeSecureStorage extends FlutterSecureStorage {
  final Map<String, String> _store = {};
  _FakeSecureStorage() : super();

  @override
  Future<void> write({
    required String key,
    required String? value,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    if (value != null) _store[key] = value;
  }

  @override
  Future<String?> read({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      _store[key];

  @override
  Future<void> delete({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    _store.remove(key);
  }
}

/// Records play calls without booting a real just_audio engine (which never
/// completes on the headless-web runner and hangs finalize).
class _FakeAudioService extends AudioService {
  _FakeAudioService({required super.httpClient});

  String? lastPlayedBeatId;
  bool lastPlayedDeeperDive = false;

  @override
  Future<void> play(
    String beatId,
    String audioUrl, {
    bool isDeeperDive = false,
    String? title,
  }) async {
    lastPlayedBeatId = beatId;
    lastPlayedDeeperDive = isDeeperDive;
    notifyListeners();
  }
}

GeneratedTrip _sampleTrip({
  String id = 'trip-1',
  String name = 'Paris Day Trip',
  List<String> degradationNotices = const [],
  bool captained = true,
}) {
  return GeneratedTrip(
    tripId: id,
    tripName: name,
    profileId: 'profile-1',
    captained: captained,
    totalStops: 7,
    totalDurationMin: 90,
    anchorCount: 1,
    flavourCount: 6,
    degradationNotices: degradationNotices,
    stops: const [
      ItineraryStop(
        sortOrder: 1,
        poiId: 'poi-1',
        poiName: 'Eiffel Tower',
        lat: 48.8584,
        lng: 2.2945,
        beatId: 'beat-1',
        lensName: 'dark_history',
        lensDisplay: 'Dark History',
        durationMin: 5,
        importanceTier: 5,
        startTime: '09:00',
      ),
      ItineraryStop(
        sortOrder: 2,
        poiId: 'poi-2',
        poiName: 'Notre-Dame',
        lat: 48.8530,
        lng: 2.3499,
        beatId: 'beat-2',
        lensName: 'historic_arch',
        lensDisplay: 'Historic Architecture',
        durationMin: 4,
        importanceTier: 3,
        startTime: '09:30',
      ),
    ],
  );
}

Widget _buildTestWidget({
  required TripService tripService,
  required AudioService audioService,
  AuthService? authService,
  String tripId = 'trip-1',
}) {
  final router = GoRouter(
    initialLocation: '/trip/$tripId',
    routes: [
      GoRoute(
        path: '/trip/:tripId',
        builder: (context, state) => TripItineraryPage(
          tripId: state.pathParameters['tripId']!,
        ),
      ),
      GoRoute(
        path: '/saved-trips',
        builder: (context, state) => const Scaffold(
          body: Center(child: Text('Saved Trips')),
        ),
      ),
    ],
  );
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<TripService>.value(value: tripService),
      ChangeNotifierProvider<AudioService>.value(value: audioService),
      if (authService != null)
        ChangeNotifierProvider<AuthService>.value(value: authService),
    ],
    child: MaterialApp.router(
      routerConfig: router,
      theme: buildOndowayTheme(Brightness.dark),
    ),
  );
}

/// The server's answer to "GET /trips/{id}/session" BEFORE the day is composed
/// (Phase 5, design §4.6/§8.2): 404 with the reason the phone reads as "compose
/// first". Every confirm-flow double below answers the session probe with this so
/// the flow reaches compose the way it does against the real server.
http.Response noSessionYet() => http.Response(
      jsonEncode({
        'detail': {
          'reason': 'no_session_yet',
          'detail': 'compose the trip first'
        },
      }),
      404,
    );

void main() {
  group('TripItineraryPage', () {
    late TripService tripService;
    late AudioService audioService;

    setUp(() {
      final mockClient = MockClient((r) async => http.Response('', 200));
      tripService = TripService(httpClient: mockClient);
      audioService = AudioService(httpClient: mockClient);
    });

    testWidgets('renders trip summary card', (tester) async {
      final trip = _sampleTrip();
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      // Summary card shows total stops, duration, anchors, and flavour labels
      expect(find.text('Stops'), findsOneWidget);
      expect(find.text('Duration'), findsOneWidget);
      expect(find.text('Anchors'), findsOneWidget);
      expect(find.text('Flavour'), findsOneWidget);
      // Values: 7 stops, 1h 30m duration, 1 anchor, 6 flavour
      expect(find.text('7'), findsOneWidget);
      expect(find.text('1h 30m'), findsOneWidget);
      expect(find.text('6'), findsOneWidget);
    });

    testWidgets('renders stop cards with POI names and times', (tester) async {
      final trip = _sampleTrip();
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text('Eiffel Tower'), findsOneWidget);
      expect(find.text('Notre-Dame'), findsOneWidget);
      expect(find.text('09:00'), findsOneWidget);
      expect(find.text('09:30'), findsOneWidget);
    });

    testWidgets('shows lens chip on stop cards', (tester) async {
      final trip = _sampleTrip();
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text('Dark History'), findsOneWidget);
      expect(find.text('Historic Architecture'), findsOneWidget);
    });

    testWidgets('confirm and prepare FAB saves trip and triggers PER-STOP audio',
        (tester) async {
      // Step 1.4d: the page must trigger + poll the PER-STOP endpoints
      // (generate-trip-stops + stop-status), not the per-beat ones.
      var perStopTriggered = false;
      var perStopStatusPolled = false;
      // Mock the backend: generate-trip for setup, compose (the confirm tap
      // writes the one day first — design §8.1), then per-stop audio.
      final mockClient = MockClient((request) async {
        if (request.url.path.contains('/trips/generate')) {
          return http.Response(
            jsonEncode({
              'trip_id': 'trip-gen',
              'trip_name': 'Generated',
              'profile_id': 'p1',
              'total_stops': 1,
              'total_duration_min': 30,
              'anchor_count': 0,
              'flavour_count': 1,
              'stops': [
                {
                  'sort_order': 1,
                  'stop_id': 'stop-b1',
                  'poi_id': 'poi-1',
                  'poi_name': 'Test POI',
                  'lat': 48.85,
                  'lng': 2.34,
                  'beat_id': 'b1',
                  'lens_name': 'a',
                  'lens_display': 'A',
                  'duration_min': 5,
                  'importance_tier': 3,
                  'start_time': '09:00',
                },
              ],
            }),
            201,
          );
        }
        if (request.url.path.endsWith('/trips/trip-gen/session') &&
            request.method == 'GET') {
          return noSessionYet();
        }
        if (request.url.path.contains('/trips/trip-gen/compose')) {
          return http.Response(
            jsonEncode({
              'trip_id': 'trip-gen',
              'route_id': 'trip-gen-opt1',
              'attempts': 1,
              'stops': [
                {
                  'sort_order': 1,
                  'stop_id': 'stop-b1',
                  'poi_id': 'poi-1',
                  'poi_name': 'Test POI',
                  'lat': 48.85,
                  'lng': 2.34,
                  'beat_id': 'b1',
                  'lens_name': 'a',
                  'lens_display': 'A',
                  'duration_min': 5,
                  'importance_tier': 3,
                  'start_time': '09:00',
                  'audio_url': null,
                },
              ],
            }),
            200,
          );
        }
        if (request.url.path.contains('/audio/generate-trip-stops/')) {
          perStopTriggered = true;
          return http.Response(
            jsonEncode({
              'trip_id': 'trip-gen',
              'generated': 1,
              'skipped': 0,
              'failed': 0,
              'results': [],
            }),
            200,
          );
        }
        if (request.url.path.contains('/audio/stop-status/stop-b1')) {
          perStopStatusPolled = true;
          return http.Response(
            jsonEncode({
              'stop_id': 'stop-b1',
              'has_audio': true,
              'audio_url': 'https://cdn.example.com/stop-b1.mp3',
              'duration_sec': 120,
            }),
            200,
          );
        }
        return http.Response('', 200);
      });

      final service = TripService(httpClient: mockClient);
      final audio = AudioService(httpClient: mockClient);

      // Generate a trip so lastGenerated is set
      await service.generateTrip(
        profileId: 'p1',
        centerLat: 48.85,
        centerLng: 2.34,
        startDate: '2026-05-04',
        endDate: '2026-05-04',
        accessToken: 'tok',
      );

      expect(service.lastGenerated, isNotNull);
      expect(service.savedTrips, isEmpty);

      // Build the widget with an AUTHENTICATED AuthService (real access token),
      // so the confirm call actually reaches the network instead of throwing on
      // the null accessToken assertion.
      final authClient = MockClient((request) async {
        if (request.url.path.contains('verify')) {
          return http.Response(
            jsonEncode({
              'access_token': 'tok',
              'refresh_token': 'ref',
              'token_type': 'bearer',
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode({'id': 'user-1', 'email': 'test@example.com'}),
          200,
        );
      });
      final authService =
          AuthService(storage: _FakeSecureStorage(), httpClient: authClient);
      await authService.verifyMagicLink('tok');
      expect(authService.accessToken, isNotNull);

      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<TripService>.value(value: service),
            ChangeNotifierProvider<AudioService>.value(value: audio),
            ChangeNotifierProvider<AuthService>.value(value: authService),
          ],
          child: MaterialApp.router(
            routerConfig: GoRouter(
              initialLocation: '/trip/trip-gen',
              routes: [
                GoRoute(
                  path: '/trip/:tripId',
                  builder: (context, state) => TripItineraryPage(
                    tripId: state.pathParameters['tripId']!,
                  ),
                ),
                GoRoute(
                  path: '/saved-trips',
                  builder: (context, state) => const Scaffold(
                    body: Center(child: Text('Saved Trips')),
                  ),
                ),
              ],
            ),
            theme: buildOndowayTheme(Brightness.dark),
          ),
        ),
      );
      await tester.pump();
      await tester.pump();

      // Tap the "Confirm & Prepare" FAB
      expect(find.text('Confirm & Prepare'), findsOneWidget);
      await tester.tap(find.text('Confirm & Prepare'));
      await tester.pump(); // compose request/response (design §8.1)
      await tester.pump(); // post-compose setState + prepare flow start

      // saveTrip should have been called
      expect(service.savedTrips.length, 1);
      expect(service.savedTrips.first.tripId, 'trip-gen');
      // The per-stop generation endpoint was triggered (awaited before the timer).
      expect(perStopTriggered, isTrue,
          reason: 'must POST /audio/generate-trip-stops, not the per-beat endpoint');

      // Fire one poll cycle so the per-stop status path runs, then assert it
      // polled /audio/stop-status/{stopId} (deterministic MockClient).
      await tester.pump(const Duration(seconds: 3));
      await tester.pump();
      expect(perStopStatusPolled, isTrue,
          reason: 'must poll /audio/stop-status/{stopId}, not /audio/status/{beatId}');

      // The page schedules a periodic poll Timer; disposing the tree runs
      // State.dispose(), which cancels it. A leftover pending Timer keeps the
      // test isolate alive and intermittently hangs flutter_tools finalize.
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('the eyebrow says whose day it is', (tester) async {
      // A signed-out AuthService is enough: the shared-day probe needs the
      // provider present, and with no token it leaves the page alone.
      final auth = AuthService(
        storage: _FakeSecureStorage(),
        httpClient: MockClient((r) async => http.Response('', 200)),
      );

      // A day the viewer captains keeps YOUR TOUR…
      tripService.saveTrip(_sampleTrip());
      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        authService: auth,
        tripId: 'trip-1',
      ));
      await tester.pump();
      await tester.pump();
      expect(find.text('YOUR TOUR'), findsOneWidget);
      expect(find.text('SHARED TOUR'), findsNothing);

      // …and a day shared with the viewer never claims to be theirs.
      tripService.saveTrip(
        _sampleTrip(id: 'trip-shared', name: "Fiona's day", captained: false),
      );
      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        authService: auth,
        tripId: 'trip-shared',
      ));
      await tester.pump();
      await tester.pump();
      expect(find.text('SHARED TOUR'), findsOneWidget);
      expect(find.text('YOUR TOUR'), findsNothing);
    });

    testWidgets('shows anchor indicator for importance_tier 5', (tester) async {
      final trip = _sampleTrip();
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      // The summary card has an Icons.star (size 20) for the Anchors section
      // AND the stop card shows Icons.star (size 16) for anchor stops.
      final starIcons = find.byIcon(Icons.star);
      expect(starIcons, findsNWidgets(2)); // one in summary, one on stop card
    });

    testWidgets('shows trip not found for unknown tripId', (tester) async {
      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: 'nonexistent',
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text('Trip not found'), findsOneWidget);
    });

    // What quietly went worse while the tour was built has to REACH the
    // traveller. A field parsed but never rendered is the same silence the
    // dropped field was.
    const estimatedLegsNotice =
        'Walking times between stops are estimates, not measured routes, so '
        'the tour may run a little longer or shorter than it says.';

    testWidgets('shows the degradation notice the backend reported',
        (tester) async {
      final trip = _sampleTrip(
        degradationNotices: const [estimatedLegsNotice],
      );
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text(estimatedLegsNotice), findsOneWidget);
      expect(find.byIcon(Icons.info_outline), findsOneWidget);
    });

    testWidgets('shows every degradation notice, not only the first',
        (tester) async {
      final trip = _sampleTrip(
        degradationNotices: const [
          estimatedLegsNotice,
          'Some of the narration was written from a template.',
        ],
      );
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text(estimatedLegsNotice), findsOneWidget);
      expect(
        find.text('Some of the narration was written from a template.'),
        findsOneWidget,
      );
      // One card holding both sentences, not one card each.
      expect(find.byIcon(Icons.info_outline), findsOneWidget);
    });

    testWidgets('shows no degradation card when nothing degraded',
        (tester) async {
      final trip = _sampleTrip();
      tripService.saveTrip(trip);

      await tester.pumpWidget(_buildTestWidget(
        tripService: tripService,
        audioService: audioService,
        tripId: trip.tripId,
      ));
      await tester.pump();
      await tester.pump();

      expect(find.byIcon(Icons.info_outline), findsNothing);
    });

    group('one-day confirm (Phase 4, design §8.1)', () {
      // Generate response as the post-Phase-4 wire sends it: `options` is a
      // list of EXACTLY ONE (the field stays a list because stored
      // pre-Phase-4 trips carry multi-option lists). The phone ignores it —
      // the one day's route id is always {trip_id}-opt1. Stops carry the
      // ORIGINAL stop_id.
      Map<String, dynamic> generateResponse() => {
            'trip_id': 'trip-gen',
            'trip_name': 'Generated',
            'profile_id': 'p1',
            'total_stops': 1,
            'total_duration_min': 30,
            'anchor_count': 0,
            'flavour_count': 1,
            'stops': [
              {
                'sort_order': 1,
                'stop_id': 'stop-old-1',
                'poi_id': 'poi-1',
                'poi_name': 'Old POI',
                'lat': 48.85,
                'lng': 2.34,
                'beat_id': 'b1',
                'lens_name': 'a',
                'lens_display': 'A',
                'duration_min': 5,
                'importance_tier': 3,
                'start_time': '09:00',
              },
            ],
            'options': [
              {
                'route_id': 'trip-gen-opt1',
                'stops': [
                  {'name': 'Old POI', 'band': 'dwell', 'minutes': 10},
                  {'name': 'Walk-past', 'band': 'vignette', 'minutes': 2},
                ],
                'eta_seconds': 3600,
              },
            ],
          };

      // /compose re-persists the stops: FRESH stop_id, composed narration,
      // audio nulled (voiced afterwards by the per-stop flow).
      Map<String, dynamic> composeResponse() => {
            'trip_id': 'trip-gen',
            'route_id': 'trip-gen-opt1',
            'attempts': 1,
            'stops': [
              {
                'sort_order': 1,
                'stop_id': 'stop-new-1',
                'poi_id': 'poi-9',
                'poi_name': 'Composed POI',
                'lat': 48.86,
                'lng': 2.35,
                'beat_id': 'b9',
                'lens_name': 'a',
                'lens_display': 'A',
                'duration_min': 5,
                'importance_tier': 3,
                'start_time': '09:00',
                'narration': 'Composed narration.',
                'audio_url': null,
                'audio_duration_sec': null,
                'transit_polyline': null,
              },
            ],
          };

      Future<AuthService> authedAuthService() async {
        final authClient = MockClient((request) async {
          if (request.url.path.contains('verify')) {
            return http.Response(
              jsonEncode({
                'access_token': 'tok',
                'refresh_token': 'ref',
                'token_type': 'bearer',
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode({'id': 'user-1', 'email': 'test@example.com'}),
            200,
          );
        });
        final auth =
            AuthService(storage: _FakeSecureStorage(), httpClient: authClient);
        await auth.verifyMagicLink('tok');
        return auth;
      }

      Future<void> pumpTripPage(
        WidgetTester tester, {
        required TripService tripService,
        required AudioService audioService,
        required AuthService authService,
        required String tripId,
      }) async {
        await tester.pumpWidget(
          MultiProvider(
            providers: [
              ChangeNotifierProvider<TripService>.value(value: tripService),
              ChangeNotifierProvider<AudioService>.value(value: audioService),
              ChangeNotifierProvider<AuthService>.value(value: authService),
            ],
            child: MaterialApp.router(
              routerConfig: GoRouter(
                initialLocation: '/trip/$tripId',
                routes: [
                  GoRoute(
                    path: '/trip/:tripId',
                    builder: (context, state) => TripItineraryPage(
                      tripId: state.pathParameters['tripId']!,
                    ),
                  ),
                  GoRoute(
                    path: '/saved-trips',
                    builder: (context, state) => const Scaffold(
                      body: Center(child: Text('Saved Trips')),
                    ),
                  ),
                ],
              ),
              theme: buildOndowayTheme(Brightness.dark),
            ),
          ),
        );
        await tester.pump();
        await tester.pump();
      }

      testWidgets(
          'confirm composes {tripId}-opt1 directly — no sheet, no pick — '
          'rebuilds stops, audio flow hits the NEW stop ids (design §8.1)',
          (tester) async {
        var composeCalls = 0;
        String? composedRouteId;
        var perStopTriggered = false;
        final polledStopIds = <String>[];

        final mockClient = MockClient((request) async {
          if (request.url.path.contains('/trips/generate')) {
            return http.Response(jsonEncode(generateResponse()), 201);
          }
          if (request.url.path.endsWith('/trips/trip-gen/session') &&
              request.method == 'GET') {
            return noSessionYet();
          }
          if (request.url.path.contains('/trips/trip-gen/compose')) {
            composeCalls++;
            composedRouteId =
                (jsonDecode(request.body) as Map<String, dynamic>)['route_id']
                    as String?;
            return http.Response(jsonEncode(composeResponse()), 200);
          }
          if (request.url.path.contains('/audio/generate-trip-stops/')) {
            perStopTriggered = true;
            return http.Response(
              jsonEncode({
                'trip_id': 'trip-gen',
                'generated': 1,
                'skipped': 0,
                'failed': 0,
                'results': [],
              }),
              200,
            );
          }
          if (request.url.path.contains('/audio/stop-status/')) {
            polledStopIds.add(request.url.pathSegments.last);
            return http.Response(
              jsonEncode({
                'stop_id': request.url.pathSegments.last,
                'has_audio': true,
                'audio_url': 'https://cdn.example.com/stop.mp3',
                'duration_sec': 120,
              }),
              200,
            );
          }
          return http.Response('', 200);
        });

        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        await service.generateTrip(
          profileId: 'p1',
          centerLat: 48.85,
          centerLng: 2.34,
          startDate: '2026-07-02',
          endDate: '2026-07-02',
          accessToken: 'tok',
        );
        final auth = await authedAuthService();

        await pumpTripPage(
          tester,
          tripService: service,
          audioService: audio,
          authService: auth,
          tripId: 'trip-gen',
        );
        expect(find.text('Old POI'), findsOneWidget);

        await tester.tap(find.text('Confirm & Prepare'));
        // Discrete pumps, NOT pumpAndSettle: once the prepare flow starts,
        // the FAB shows an indeterminate spinner that never settles.
        await tester.pump(); // compose request + response
        await tester.pump(); // post-compose setState, prepare flow starts
        await tester.pump(); // post-setState frame

        // The one day composed exactly once, addressed by its fixed route id
        // {tripId}-opt1 — no sheet ever interposed, nothing to pick.
        expect(composeCalls, 1);
        expect(composedRouteId, 'trip-gen-opt1');
        expect(find.text('Choose your tour flavour'), findsNothing);
        // The page rebuilt its stop list from the compose response.
        expect(find.text('Composed POI'), findsOneWidget);
        expect(find.text('Old POI'), findsNothing);
        // Only then does the existing per-stop flow run…
        expect(perStopTriggered, isTrue);

        // …and its poll hits the FRESH stop_id, never the stale one.
        await tester.pump(const Duration(seconds: 3));
        await tester.pump();
        expect(polledStopIds, contains('stop-new-1'));
        expect(polledStopIds, isNot(contains('stop-old-1')));

        // Cancel the page's poll timer via State.dispose().
        await tester.pumpWidget(const SizedBox());
      });

      testWidgets(
          'a compose refusal shows the honest error card and stops the flow '
          '(design §8.1)', (tester) async {
        var composeCalls = 0;
        var perStopTriggered = false;

        final mockClient = MockClient((request) async {
          if (request.url.path.contains('/trips/generate')) {
            return http.Response(jsonEncode(generateResponse()), 201);
          }
          if (request.url.path.endsWith('/trips/trip-gen/session') &&
              request.method == 'GET') {
            return noSessionYet();
          }
          if (request.url.path.contains('/trips/trip-gen/compose')) {
            composeCalls++;
            return http.Response(
              jsonEncode({
                'detail': {
                  'reason': 'compose_verification_failed',
                  'attempts': 2,
                },
              }),
              422,
            );
          }
          if (request.url.path.contains('/audio/generate-trip-stops/')) {
            perStopTriggered = true;
            return http.Response('', 200);
          }
          return http.Response('', 200);
        });

        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        await service.generateTrip(
          profileId: 'p1',
          centerLat: 48.85,
          centerLng: 2.34,
          startDate: '2026-07-02',
          endDate: '2026-07-02',
          accessToken: 'tok',
        );
        final auth = await authedAuthService();

        await pumpTripPage(
          tester,
          tripService: service,
          audioService: audio,
          authService: auth,
          tripId: 'trip-gen',
        );

        await tester.tap(find.text('Confirm & Prepare'));
        await tester.pumpAndSettle();

        // Refused: the EXISTING error card carries the honest message — one
        // day per trip means there is no second flavour to offer, so it says
        // the way out is generating again. No sheet, no snackbar.
        expect(composeCalls, 1);
        expect(
          find.text("This day couldn't be written. Try generating again."),
          findsOneWidget,
        );
        expect(find.byIcon(Icons.error_outline), findsOneWidget);
        expect(find.text('Choose your tour flavour'), findsNothing);
        // The prepare flow never ran, and the FAB is back for a retry.
        expect(perStopTriggered, isFalse);
        expect(find.text('Confirm & Prepare'), findsOneWidget);

        await tester.pumpWidget(const SizedBox());
      });

      testWidgets(
          'an uncomposed shared day waits for the captain — no confirm '
          'button, no compose call', (tester) async {
        var composeCalls = 0;
        final mockClient = MockClient((request) async {
          if (request.url.path.endsWith('/trips/trip-shared/session') &&
              request.method == 'GET') {
            return noSessionYet();
          }
          if (request.url.path.contains('/compose')) {
            composeCalls++;
            return http.Response('{}', 403);
          }
          return http.Response('', 200);
        });

        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        service.saveTrip(
          _sampleTrip(
            id: 'trip-shared',
            name: "Fiona's day",
            captained: false,
          ),
        );
        final auth = await authedAuthService();

        await pumpTripPage(
          tester,
          tripService: service,
          audioService: audio,
          authService: auth,
          tripId: 'trip-shared',
        );
        // A third pump for the post-frame session probe's setState.
        await tester.pump();

        // The honest waiting state, keyed on the captained flag: crew cannot
        // write the day, so no action that can only 403 is offered.
        expect(
          find.text('Ask the captain to confirm this day.'),
          findsOneWidget,
        );
        expect(find.text('Confirm & Prepare'), findsNothing);
        expect(composeCalls, 0);
      });

      testWidgets(
          'a composed shared day keeps the prepare door — crew readies the '
          'audio it can read', (tester) async {
        final mockClient = MockClient((request) async {
          if (request.url.path.endsWith('/trips/trip-shared/session') &&
              request.method == 'GET') {
            return http.Response(
              jsonEncode({
                'trip_id': 'trip-shared',
                'plan_version': 1,
                'stops': [],
                'retime_tolerance_seconds': 120,
              }),
              200,
            );
          }
          return http.Response('', 200);
        });

        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        service.saveTrip(
          _sampleTrip(
            id: 'trip-shared',
            name: "Fiona's day",
            captained: false,
          ),
        );
        final auth = await authedAuthService();

        await pumpTripPage(
          tester,
          tripService: service,
          audioService: audio,
          authService: auth,
          tripId: 'trip-shared',
        );
        await tester.pump();

        expect(find.text('Confirm & Prepare'), findsOneWidget);
        expect(
          find.text('Ask the captain to confirm this day.'),
          findsNothing,
        );
      });

      testWidgets(
          "a crew member's confirm shows the captain-only sentence, "
          'never raw JSON', (tester) async {
        final mockClient = MockClient((request) async {
          if (request.url.path.contains('/trips/generate')) {
            return http.Response(jsonEncode(generateResponse()), 201);
          }
          if (request.url.path.endsWith('/trips/trip-gen/session') &&
              request.method == 'GET') {
            return noSessionYet();
          }
          if (request.url.path.contains('/trips/trip-gen/compose')) {
            return http.Response(
              jsonEncode({
                'detail': {
                  'reason': 'captain_only',
                  'detail': "Only the trip's captain can change the day",
                },
              }),
              403,
            );
          }
          return http.Response('', 200);
        });

        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        await service.generateTrip(
          profileId: 'p1',
          centerLat: 48.85,
          centerLng: 2.34,
          startDate: '2026-07-02',
          endDate: '2026-07-02',
          accessToken: 'tok',
        );
        final auth = await authedAuthService();

        await pumpTripPage(
          tester,
          tripService: service,
          audioService: audio,
          authService: auth,
          tripId: 'trip-gen',
        );

        await tester.tap(find.text('Confirm & Prepare'));
        await tester.pumpAndSettle();

        expect(
          find.text("Only the trip's captain can change the day"),
          findsOneWidget,
        );
        expect(find.textContaining('captain_only'), findsNothing);
        expect(find.textContaining('{'), findsNothing);

        await tester.pumpWidget(const SizedBox());
      });
    });

    group('keep exploring here (KE7)', () {
      GeneratedTrip tripWithExtras() => GeneratedTrip(
            tripId: 'trip-ke',
            tripName: 'KE Trip',
            profileId: 'profile-1',
            totalStops: 2,
            totalDurationMin: 20,
            anchorCount: 1,
            flavourCount: 1,
            stops: const [
              // Stop 0 HAS un-voiced extras -> button shown.
              ItineraryStop(
                sortOrder: 1,
                stopId: 'stop-ke-1',
                poiId: 'poi-1',
                poiName: 'Eiffel Tower',
                lat: 48.8584,
                lng: 2.2945,
                beatId: 'beat-1',
                lensName: 'dark_history',
                lensDisplay: 'Dark History',
                durationMin: 5,
                importanceTier: 5,
                startTime: '09:00',
                extraBeatIds: ['beat-x1', 'beat-x2'],
                extraNarration: 'More to discover.',
              ),
              // Stop 1 has NO extras -> button hidden.
              ItineraryStop(
                sortOrder: 2,
                stopId: 'stop-ke-2',
                poiId: 'poi-2',
                poiName: 'Notre-Dame',
                lat: 48.8530,
                lng: 2.3499,
                beatId: 'beat-2',
                lensName: 'historic_arch',
                lensDisplay: 'Historic Architecture',
                durationMin: 4,
                importanceTier: 3,
                startTime: '09:30',
              ),
            ],
          );

      testWidgets('button shows only for stops with non-empty extraBeatIds',
          (tester) async {
        tripService.saveTrip(tripWithExtras());

        await tester.pumpWidget(_buildTestWidget(
          tripService: tripService,
          audioService: audioService,
          tripId: 'trip-ke',
        ));
        await tester.pump();
        await tester.pump();

        // Exactly one button — only the stop WITH extras offers a deep dive.
        expect(find.text('Keep exploring here'), findsOneWidget);
      });

      testWidgets('no button when no stop has extras', (tester) async {
        // _sampleTrip's stops carry no extraBeatIds -> button hidden.
        tripService.saveTrip(_sampleTrip(id: 'trip-noke'));

        await tester.pumpWidget(_buildTestWidget(
          tripService: tripService,
          audioService: audioService,
          tripId: 'trip-noke',
        ));
        await tester.pump();
        await tester.pump();

        expect(find.text('Keep exploring here'), findsNothing);
      });

      testWidgets('tapping calls the keep-exploring endpoint with the stop id',
          (tester) async {
        String? hitPath;
        final mockClient = MockClient((request) async {
          hitPath = request.url.path;
          return http.Response(
            jsonEncode({
              'stop_id': 'stop-ke-1',
              'status': 'generated',
              'audio_url': 'https://cdn.example.com/stop-ke-1-ke.mp3',
              'duration_sec': 30,
            }),
            200,
          );
        });
        final service = TripService(httpClient: mockClient);
        // Fake player: a real AudioService.play awaits just_audio loading a URL
        // that never completes on headless web and hangs the suite.
        final audio = _FakeAudioService(httpClient: mockClient);
        service.saveTrip(tripWithExtras());

        await tester.pumpWidget(_buildTestWidget(
          tripService: service,
          audioService: audio,
          tripId: 'trip-ke',
        ));
        await tester.pump();
        await tester.pump();

        await tester.tap(find.text('Keep exploring here'));
        await tester.pump();
        await tester.pump();

        expect(hitPath, isNotNull);
        expect(hitPath, contains('/audio/stops/stop-ke-1/keep-exploring'));
        // Played as a DEEPER-DIVE source (KE6), so tour auto-advance won't fire.
        expect(audio.lastPlayedDeeperDive, isTrue);
        expect(audio.lastPlayedBeatId, 'stop-ke-1-keep-exploring');
      });

      testWidgets('shows an error when the endpoint returns status==failed',
          (tester) async {
        final mockClient = MockClient((request) async {
          if (request.url.path.contains('keep-exploring')) {
            return http.Response(
              jsonEncode({
                'stop_id': 'stop-ke-1',
                'status': 'failed',
                'audio_url': null,
                'duration_sec': null,
                'error': 'provider timeout',
              }),
              200,
            );
          }
          return http.Response('', 200);
        });
        final service = TripService(httpClient: mockClient);
        final audio = AudioService(httpClient: mockClient);
        service.saveTrip(tripWithExtras());

        await tester.pumpWidget(_buildTestWidget(
          tripService: service,
          audioService: audio,
          tripId: 'trip-ke',
        ));
        await tester.pump();
        await tester.pump();

        await tester.tap(find.text('Keep exploring here'));
        await tester.pump();
        await tester.pump();

        // The soft failure surfaces as an inline error, not a crash.
        expect(find.textContaining('provider timeout'), findsOneWidget);
      });
    });

    testWidgets('shows error card when audio preparation fails',
        (tester) async {
      // When accessToken is null, the null assertion throws.
      // The error should be caught and displayed in the error card.
      final mockClient = MockClient((request) async {
        return http.Response('', 200);
      });

      final service = TripService(httpClient: mockClient);
      final audio = AudioService(httpClient: mockClient);
      final trip = _sampleTrip();
      service.saveTrip(trip);

      // AuthService with no authentication (accessToken is null)
      final authClient = MockClient((r) async => http.Response('', 200));
      final authService = AuthService(httpClient: authClient);

      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<TripService>.value(value: service),
            ChangeNotifierProvider<AudioService>.value(value: audio),
            ChangeNotifierProvider<AuthService>.value(value: authService),
          ],
          child: MaterialApp.router(
            routerConfig: GoRouter(
              initialLocation: '/trip/trip-1',
              routes: [
                GoRoute(
                  path: '/trip/:tripId',
                  builder: (context, state) => TripItineraryPage(
                    tripId: state.pathParameters['tripId']!,
                  ),
                ),
                GoRoute(
                  path: '/saved-trips',
                  builder: (context, state) => const Scaffold(
                    body: Center(child: Text('Saved Trips')),
                  ),
                ),
              ],
            ),
            theme: buildOndowayTheme(Brightness.dark),
          ),
        ),
      );
      await tester.pump();
      await tester.pump();

      // Tap confirm button — the compose call (design §8.1) fails first on
      // the null accessToken, is caught, and lands on the error card.
      await tester.tap(find.text('Confirm & Prepare'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      // The failure never reached the prepare flow; the trip saved during
      // setup is untouched.
      expect(service.savedTrips.length, 1);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });
  });
}
