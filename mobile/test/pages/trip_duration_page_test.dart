import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:geolocator/geolocator.dart';
import 'package:ondoway/pages/trip_duration_page.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/location_service.dart';
import '../services/auth_service_test.dart';

/// A fake LocationService that returns a fixed position (Paris city center)
/// without hitting real Geolocator APIs.
class FakeLocationService extends LocationService {
  final Position? _fakePosition;
  final Completer<void>? _gate;

  FakeLocationService({
    Position? fakePosition,
    Completer<void>? gate,
  })  : _fakePosition = fakePosition,
        _gate = gate;

  @override
  Future<Position?> getCurrentPosition() async {
    if (_gate != null) {
      await _gate.future;
    }
    return _fakePosition;
  }
}

/// A position representing Paris city center (within 50km of the city coords).
Position _parisPosition() => Position(
      latitude: 48.8566,
      longitude: 2.3522,
      timestamp: DateTime.now(),
      accuracy: 10.0,
      altitude: 0.0,
      altitudeAccuracy: 0.0,
      heading: 0.0,
      headingAccuracy: 0.0,
      speed: 0.0,
      speedAccuracy: 0.0,
    );

Widget _buildTestWidget({
  TimeOfDay? initialStartTime,
  TimeOfDay? initialEndTime,
  LocationService? locationService,
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
        ChangeNotifierProvider<TripService>(
          create: (_) => TripService(httpClient: mockClient),
        ),
        ChangeNotifierProvider<AuthService>(
          create: (_) => AuthService(
            storage: FakeSecureStorage(),
            httpClient: mockClient,
          ),
        ),
        ChangeNotifierProvider<ProfileService>(
          create: (_) => ProfileService(httpClient: mockClient),
        ),
        ChangeNotifierProvider<LocationService>(
          create: (_) =>
              locationService ??
              FakeLocationService(fakePosition: _parisPosition()),
        ),
      ],
      child: TripDurationPage(
        citySlug: 'paris',
        initialStartTime: initialStartTime,
        initialEndTime: initialEndTime,
      ),
    ),
  );
}

void main() {
  group('TripDurationPage', () {
    testWidgets('shows date and time pickers', (tester) async {
      await tester.pumpWidget(_buildTestWidget());
      await tester.pumpAndSettle();

      expect(find.text('Paris'), findsOneWidget);
      expect(find.text('When are you visiting?'), findsOneWidget);
      expect(find.text('From'), findsOneWidget);
      expect(find.text('To (inclusive)'), findsOneWidget);
      expect(find.text('Generate Trip Plan'), findsOneWidget);
      expect(find.byIcon(Icons.calendar_today), findsNWidgets(2));
      expect(find.byIcon(Icons.access_time), findsNWidgets(2));
    });

    testWidgets(
        'the one end-time question shows in plain words, defaults to '
        'a hard deadline, and a tap changes it', (tester) async {
      await tester.pumpWidget(_buildTestWidget(
        initialStartTime: const TimeOfDay(hour: 9, minute: 0),
        initialEndTime: const TimeOfDay(hour: 18, minute: 0),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Is the end time fixed?'), findsOneWidget);
      final seg = tester.widget<SegmentedButton<String>>(
          find.byKey(const Key('end-hardness')));
      expect(seg.selected, {'firm'},
          reason: 'skipping the question means a hard deadline (owner ruling)');
      await tester.tap(find.text('Just a guess'));
      await tester.pumpAndSettle();
      final after = tester.widget<SegmentedButton<String>>(
          find.byKey(const Key('end-hardness')));
      expect(after.selected, {'open'});
    });

    testWidgets('shows duration summary for valid range', (tester) async {
      await tester.pumpWidget(_buildTestWidget());
      await tester.pumpAndSettle();

      // Default: same day 09:00 to 18:00 = 9 hours
      expect(find.text('9 hours'), findsOneWidget);
    });

    testWidgets('generate button is enabled for valid range', (tester) async {
      await tester.pumpWidget(_buildTestWidget());
      await tester.pumpAndSettle();

      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNotNull);
    });

    testWidgets('shows error when end is before start', (tester) async {
      // Start at 19:00, end at 18:00 — end is before start
      await tester.pumpWidget(_buildTestWidget(
        initialStartTime: const TimeOfDay(hour: 19, minute: 0),
        initialEndTime: const TimeOfDay(hour: 18, minute: 0),
      ));
      await tester.pumpAndSettle();

      expect(find.text('End must be after start'), findsOneWidget);
    });

    testWidgets('generate button disabled for invalid range', (tester) async {
      // Start at 19:00, end at 18:00 — invalid range
      await tester.pumpWidget(_buildTestWidget(
        initialStartTime: const TimeOfDay(hour: 19, minute: 0),
        initialEndTime: const TimeOfDay(hour: 18, minute: 0),
      ));
      await tester.pumpAndSettle();

      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNull);
    });

    testWidgets('generate button disabled while location resolving', (tester) async {
      // Use a gated LocationService that never completes during this test
      final gate = Completer<void>();
      final slowLocationService = FakeLocationService(
        fakePosition: _parisPosition(),
        gate: gate,
      );
      await tester.pumpWidget(_buildTestWidget(
        locationService: slowLocationService,
      ));
      // Pump once to trigger initState + postFrameCallback, but don't settle
      await tester.pump();

      // Button should be disabled because location hasn't resolved
      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNull);

      // Complete the gate so the async future finishes and no pending timer remains
      gate.complete();
      await tester.pumpAndSettle();
    });

    testWidgets('generate button enabled after location resolves', (tester) async {
      await tester.pumpWidget(_buildTestWidget());
      await tester.pumpAndSettle();

      // After settling, location should be resolved and button enabled
      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNotNull);
    });
  });
}
