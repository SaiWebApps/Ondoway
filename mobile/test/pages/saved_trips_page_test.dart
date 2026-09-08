import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/saved_trips_page.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/trip_service.dart';

import '../services/auth_service_test.dart';

Widget _buildTestWidget({
  TripService? tripService,
  ProfileService? profileService,
  AuthService? authService,
}) {
  final mockClient = MockClient((r) async => http.Response('', 200));
  return MaterialApp(
    theme: ThemeData(
      colorSchemeSeed: const Color(0xFF3D5AFE),
      useMaterial3: true,
      brightness: Brightness.dark,
    ),
    home: MultiProvider(
      providers: [
        ChangeNotifierProvider<TripService>.value(
          value: tripService ?? TripService(httpClient: mockClient),
        ),
        ChangeNotifierProvider<ProfileService>.value(
          value: profileService ?? ProfileService(httpClient: mockClient),
        ),
        ChangeNotifierProvider<AuthService>.value(
          value: authService ??
              AuthService(storage: FakeSecureStorage(), httpClient: mockClient),
        ),
      ],
      child: const Scaffold(body: SavedTripsPage()),
    ),
  );
}

/// A signed-in Dev with a loaded profile — the state the tab opens in.
Future<(AuthService, ProfileService)> _signedInDev(http.Client client) async {
  final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
  await auth.verifyMagicLink('tok');
  final profile = ProfileService(httpClient: client);
  await profile.fetchProfile('tok');
  return (auth, profile);
}

/// One MockClient serving auth + profile for Dev, plus whatever [onTrips]
/// answers for GET /trips.
MockClient _devWorld(Future<http.Response> Function(http.Request) onTrips) {
  return MockClient((request) async {
    if (request.url.path.endsWith('/auth/magic-link/verify')) {
      return http.Response(
        jsonEncode({
          'access_token': 'tok',
          'refresh_token': 'ref',
          'token_type': 'bearer',
        }),
        200,
      );
    }
    if (request.url.path.endsWith('/auth/me')) {
      return http.Response(
          jsonEncode({'id': 'user-dev', 'email': 'dev@example.test'}), 200);
    }
    if (request.url.path.endsWith('/profile')) {
      return http.Response(
        jsonEncode({
          'profile_id': 'p-dev',
          'display_name': 'Dev',
          'selected_lens_ids': ['l1'],
          'theme_preference': null,
        }),
        200,
      );
    }
    if (request.url.path.endsWith('/trips') && request.method == 'GET') {
      return onTrips(request);
    }
    return http.Response('Not found', 404);
  });
}

Map<String, dynamic> _serverTripJson({
  String id = 'trip-crewed',
  String name = "Fiona's shared day",
  bool captained = false,
}) =>
    {
      'trip_id': id,
      'trip_name': name,
      'profile_id': 'p-dev',
      'captained': captained,
      'total_stops': 1,
      'total_duration_min': 60,
      'anchor_count': 0,
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
      ],
    };

GeneratedTrip _sampleTrip({String id = 'trip-1', String name = 'Paris Day Trip'}) {
  return GeneratedTrip(
    tripId: id,
    tripName: name,
    profileId: 'profile-1',
    totalStops: 5,
    totalDurationMin: 120,
    anchorCount: 1,
    flavourCount: 4,
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
    ],
  );
}

void main() {
  group('SavedTripsPage', () {
    testWidgets('shows empty state when no trips', (tester) async {
      await tester.pumpWidget(_buildTestWidget());
      await tester.pumpAndSettle();

      expect(find.text('No saved trips yet'), findsOneWidget);
      expect(find.byIcon(Icons.luggage_outlined), findsOneWidget);
      expect(
        find.text('Generate a trip from the Explore tab to get started.'),
        findsOneWidget,
      );
    });

    testWidgets('shows trip cards when trips exist', (tester) async {
      final mockClient = MockClient((r) async => http.Response('', 200));
      final service = TripService(httpClient: mockClient);
      service.saveTrip(_sampleTrip());
      service.saveTrip(_sampleTrip(id: 'trip-2', name: 'Evening Walk'));

      await tester.pumpWidget(_buildTestWidget(tripService: service));
      await tester.pumpAndSettle();

      expect(find.text('Paris Day Trip'), findsOneWidget);
      expect(find.text('Evening Walk'), findsOneWidget);
      expect(find.text('No saved trips yet'), findsNothing);
    });

    testWidgets('shows stop count and duration in trip cards', (tester) async {
      final mockClient = MockClient((r) async => http.Response('', 200));
      final service = TripService(httpClient: mockClient);
      service.saveTrip(_sampleTrip());

      await tester.pumpWidget(_buildTestWidget(tripService: service));
      await tester.pumpAndSettle();

      expect(find.textContaining('5 stops'), findsOneWidget);
      expect(find.textContaining('120 min'), findsOneWidget);
    });

    testWidgets('swipe to delete shows confirmation dialog', (tester) async {
      final mockClient = MockClient((r) async => http.Response('', 200));
      final service = TripService(httpClient: mockClient);
      service.saveTrip(_sampleTrip());

      await tester.pumpWidget(_buildTestWidget(tripService: service));
      await tester.pumpAndSettle();

      // Swipe the trip card from right to left (endToStart)
      await tester.drag(find.text('Paris Day Trip'), const Offset(-500, 0));
      await tester.pumpAndSettle();

      // Confirmation dialog should appear
      expect(find.byType(AlertDialog), findsOneWidget);
      expect(find.text('Delete trip?'), findsOneWidget);
      expect(find.text('Cancel'), findsOneWidget);
      expect(find.text('Delete'), findsOneWidget);
    });

    testWidgets('confirming delete removes trip from list', (tester) async {
      final mockClient = MockClient((r) async => http.Response('', 200));
      final service = TripService(httpClient: mockClient);
      service.saveTrip(_sampleTrip());

      await tester.pumpWidget(_buildTestWidget(tripService: service));
      await tester.pumpAndSettle();

      expect(find.text('Paris Day Trip'), findsOneWidget);

      // Swipe to trigger delete confirmation
      await tester.drag(find.text('Paris Day Trip'), const Offset(-500, 0));
      await tester.pumpAndSettle();

      // Tap "Delete" to confirm
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();

      // Trip should be removed
      expect(find.text('Paris Day Trip'), findsNothing);
      expect(service.savedTrips, isEmpty);
    });

    testWidgets('canceling delete keeps trip', (tester) async {
      final mockClient = MockClient((r) async => http.Response('', 200));
      final service = TripService(httpClient: mockClient);
      service.saveTrip(_sampleTrip());

      await tester.pumpWidget(_buildTestWidget(tripService: service));
      await tester.pumpAndSettle();

      expect(find.text('Paris Day Trip'), findsOneWidget);

      // Swipe to trigger delete confirmation
      await tester.drag(find.text('Paris Day Trip'), const Offset(-500, 0));
      await tester.pumpAndSettle();

      // Tap "Cancel" to dismiss
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      // Trip should still be there
      expect(find.text('Paris Day Trip'), findsOneWidget);
      expect(service.savedTrips.length, 1);
    });

    testWidgets(
        'opening the tab fetches the server list — a crewed trip renders '
        'with no local save ever having happened', (tester) async {
      String? requestedProfileId;
      final client = _devWorld((request) async {
        requestedProfileId = request.url.queryParameters['profile_id'];
        return http.Response(jsonEncode([_serverTripJson()]), 200);
      });
      final (auth, profile) = await _signedInDev(client);
      final trips = TripService(httpClient: client);

      await tester.pumpWidget(_buildTestWidget(
        tripService: trips,
        profileService: profile,
        authService: auth,
      ));
      await tester.pumpAndSettle();

      expect(requestedProfileId, 'p-dev');
      expect(find.text("Fiona's shared day"), findsOneWidget);
      expect(find.text('No saved trips yet'), findsNothing);
    });

    testWidgets(
        'a shared day is labeled and never swipe-deletable; an own day keeps '
        'the swipe', (tester) async {
      final client = _devWorld((request) async {
        return http.Response(
          jsonEncode([
            _serverTripJson(),
            _serverTripJson(id: 'trip-own', name: 'My own day', captained: true),
          ]),
          200,
        );
      });
      final (auth, profile) = await _signedInDev(client);
      final trips = TripService(httpClient: client);

      await tester.pumpWidget(_buildTestWidget(
        tripService: trips,
        profileService: profile,
        authService: auth,
      ));
      await tester.pumpAndSettle();

      // The shared row says whose it is and offers no delete swipe; the
      // captained row keeps the Dismissible.
      expect(find.text('Shared with you'), findsOneWidget);
      expect(find.byType(Dismissible), findsOneWidget);
      final dismissible =
          tester.widget<Dismissible>(find.byType(Dismissible));
      expect(dismissible.key, const Key('trip-own'));

      // Swiping the shared row opens no delete dialog and removes nothing.
      await tester.drag(find.text("Fiona's shared day"), const Offset(-500, 0));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
      expect(find.text("Fiona's shared day"), findsOneWidget);
    });

    testWidgets(
        'pull-to-refresh re-fetches the server list — a day the captain '
        'planned after the first look appears without an app restart',
        (tester) async {
      var tripCalls = 0;
      final client = _devWorld((request) async {
        tripCalls++;
        if (tripCalls == 1) {
          return http.Response(jsonEncode([]), 200);
        }
        return http.Response(jsonEncode([_serverTripJson()]), 200);
      });
      final (auth, profile) = await _signedInDev(client);
      final trips = TripService(httpClient: client);

      await tester.pumpWidget(_buildTestWidget(
        tripService: trips,
        profileService: profile,
        authService: auth,
      ));
      await tester.pumpAndSettle();

      // First look: the captain has not planned yet — the tab is empty.
      expect(tripCalls, 1);
      expect(find.text('No saved trips yet'), findsOneWidget);

      // Pull down on the EMPTY state: the list re-fetches and the shared day
      // is there — no app restart.
      await tester.fling(
        find.text('No saved trips yet'),
        const Offset(0, 400),
        1000,
      );
      await tester.pumpAndSettle();

      expect(tripCalls, 2);
      expect(find.text("Fiona's shared day"), findsOneWidget);
    });

    testWidgets('a failed server fetch shows a plain retryable error line',
        (tester) async {
      var tripCalls = 0;
      final client = _devWorld((request) async {
        tripCalls++;
        if (tripCalls == 1) {
          return http.Response('{"detail":"boom"}', 500);
        }
        return http.Response(jsonEncode([_serverTripJson()]), 200);
      });
      final (auth, profile) = await _signedInDev(client);
      final trips = TripService(httpClient: client);

      await tester.pumpWidget(_buildTestWidget(
        tripService: trips,
        profileService: profile,
        authService: auth,
      ));
      await tester.pumpAndSettle();

      expect(find.text('Could not load your trips.'), findsOneWidget);

      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text("Fiona's shared day"), findsOneWidget);
    });
  });
}
