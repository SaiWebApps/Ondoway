@TestOn('vm')
// Tagged as well as TestOn'd. @TestOn keeps this OFF the chrome pass; the tag
// is what puts it ON the VM pass. Without it the file matched neither
// selector and ran in neither half of `make flutter-test` — green by never
// executing, which is the one way a golden can lie.
@Tags(['vm'])
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/trip_itinerary_page.dart';
import 'package:ondoway/theme/theme.dart';

// Visual proof of the "keep exploring here" button.
//
// PLATFORM: pixel goldens are captured on the flutter-tester (VM) engine, so
// this file cannot run under chrome. `make flutter-test` is TWO passes — chrome
// with `--exclude-tags vm`, then the native engine with `--tags vm` — and BOTH
// annotations above are load-bearing: @TestOn('vm') keeps this file off the
// chrome pass, @Tags(['vm']) puts it on the VM pass. Remove either and the file
// matches neither selector and runs in neither pass, which is how it sat green
// without executing until 2026-08-30.
//
// Renders the real _StopCard (via the @visibleForTesting buildStopCardForTest
// hook) and writes a PNG golden, so the rendered button is a committed artifact.
// Goldens are generated on the DEFAULT flutter-tester (VM) platform, which
// captures real pixels — the chrome platform's golden comparator does not write
// PNGs the same way. Run:
//   flutter test test/pages/keep_exploring_golden_test.dart --update-goldens
// to (re)generate, then without the flag to verify against the committed PNG.

ItineraryStop _stop({
  required int sortOrder,
  required String poiName,
  List<String> extraBeatIds = const [],
  String? extraNarration,
}) {
  return ItineraryStop(
    sortOrder: sortOrder,
    stopId: 'stop-$sortOrder',
    poiId: 'poi-$sortOrder',
    poiName: poiName,
    lat: 48.8584,
    lng: 2.2945,
    beatId: 'beat-$sortOrder',
    lensName: 'dark_history',
    lensDisplay: 'Dark History',
    durationMin: 5,
    importanceTier: 5,
    startTime: '09:00',
    extraBeatIds: extraBeatIds,
    extraNarration: extraNarration,
  );
}

// Stable card-width surface. 500pt matches the card's effective width in the
// itinerary list (a phone screen minus the 16pt horizontal padding) with room
// for the default test font, which renders wider than a device's system font.
// The app theme matches the one the widget tests use (Material 3, dark).
Widget _harness(ItineraryStop stop) {
  return MaterialApp(
    theme: buildOndowayTheme(Brightness.dark),
    home: Scaffold(
      body: Center(
        child: SizedBox(
          width: 500,
          child: RepaintBoundary(
            child: buildStopCardForTest(stop),
          ),
        ),
      ),
    ),
  );
}

void main() {
  group('keep-exploring-here golden', () {
    testWidgets('stop WITH extras renders the button (golden)', (tester) async {
      await tester.pumpWidget(
        _harness(
          _stop(
            sortOrder: 1,
            poiName: 'Eiffel Tower',
            extraBeatIds: const ['beat-x1', 'beat-x2'],
            extraNarration: 'More to discover here.',
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Sanity: the button really is on screen in this render.
      expect(find.text('Keep exploring here'), findsOneWidget);

      await expectLater(
        find.byType(RepaintBoundary).first,
        matchesGoldenFile('goldens/keep_exploring_button.png'),
      );
    });

    testWidgets('stop WITHOUT extras hides the button (golden)',
        (tester) async {
      await tester.pumpWidget(
        _harness(_stop(sortOrder: 2, poiName: 'Notre-Dame')),
      );
      await tester.pumpAndSettle();

      // Sanity: no button when there are no extras.
      expect(find.text('Keep exploring here'), findsNothing);

      await expectLater(
        find.byType(RepaintBoundary).first,
        matchesGoldenFile('goldens/stop_card_no_extras.png'),
      );
    });
  });
}
