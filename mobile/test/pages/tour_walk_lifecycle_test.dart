@Tags(['vm'])
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/pages/tour_walk_page.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:provider/provider.dart';

import '../services/mocks/mock_audio_service.dart';
import '../services/mocks/mock_location_service.dart';
import '../support/trip_fixture.dart';

/// Who owns the tour.
///
/// The itinerary starts a walk from the SESSION's stops — the ones carrying the
/// placed trigger geometry and the voiced files — and then pushes this page. If
/// the page restarted the tour on mount it would throw that session away and
/// begin again from the plainer stops it was handed. And if it stopped the tour
/// on dispose it would end the walk the moment someone popped back to glance at
/// the itinerary. Neither is what a walk is.
///
/// A cold entry is the other half: no tour running, so the page starts one and
/// owns it.
Widget _harness({
  required MockLocationService loc,
  required MockAudioService audio,
  required TourPlaybackService engine,
  required Widget child,
}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<LocationProvider>.value(value: loc),
      ChangeNotifierProvider<AudioProvider>.value(value: audio),
      ChangeNotifierProvider<TourPlaybackService>.value(value: engine),
    ],
    child: MaterialApp(home: child),
  );
}

// The walk screen carries a one-second HEARTBEAT: standing still is measured by
// time passing, because the geolocator sends no fix while nobody moves. A
// repeating timer means this tree never "settles" — pumpAndSettle advances the
// fake clock and the ticker fires again, forever — so these tests pump explicit
// frames instead. That is a property of the screen, not a workaround.
void main() {
  testWidgets('a tour already running is not restarted, and survives leaving the page',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    // The itinerary's walk: started elsewhere, before this page exists.
    await engine.startTour(trip.stops);
    engine.skipToStop(1);
    expect(engine.isActive, true);
    expect(engine.currentStopIndex, 1);
    final playsBefore = audio.playCount;

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    // Mount did not reset the walk back to its first stop.
    expect(engine.currentStopIndex, 1);
    expect(audio.playCount, playsBefore);
    expect(engine.isActive, true);

    // Leaving the page is not ending the walk.
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    await tester.pump(const Duration(milliseconds: 350));
    expect(engine.isActive, true);
  });

  testWidgets('a cold entry starts the tour and ends it on the way out',
      (tester) async {
    final trip = loadParisFixtureTrip();
    final loc = MockLocationService();
    final audio = MockAudioService();
    final engine = TourPlaybackService(locationService: loc, audioService: audio);

    expect(engine.isActive, false);

    await tester.pumpWidget(_harness(
        loc: loc, audio: audio, engine: engine, child: TourWalkPage(trip: trip)));
    await tester.pump(const Duration(milliseconds: 350));

    expect(engine.isActive, true);

    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    await tester.pump(const Duration(milliseconds: 350));
    expect(engine.isActive, false);
  });
}
