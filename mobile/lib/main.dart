import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/router.dart';
import 'package:ondoway/services/audio_service.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/feedback_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/location_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/session_bootstrap.dart';
import 'package:ondoway/services/tour_playback_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/theme/theme.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Phase 8 S8.7 (persona 09, Fiona & Dev step 4): the lock screen's transport,
  // wired before anything can play. Dev's phone stays in his pocket, so the
  // pause he reaches is this one — and it arrives at the tour's own pause door
  // (TourPlaybackService), never at the player alone.
  await JustAudioBackground.init(
    androidNotificationChannelId: 'com.ondoway.app.channel.audio',
    androidNotificationChannelName: 'Tour audio',
    androidNotificationOngoing: true,
  );
  final authService = AuthService();
  final audioService = AudioService();
  final lensService = LensService();
  final profileService = ProfileService();
  final familyService = FamilyService();
  final tripService = TripService();
  final feedbackService = FeedbackService();
  final locationService = LocationService();
  final tourPlaybackService = TourPlaybackService(
    locationService: locationService,
    audioService: audioService,
  );

  await authService.tryRestoreSession();

  if (authService.isAuthenticated) {
    try {
      await loadSignedInSession(
        auth: authService,
        lenses: lensService,
        profile: profileService,
        family: familyService,
      );
    } catch (_) {
      // Non-fatal: app still works, onboarding detection may default to first-time
    }
  }

  final router = createRouter(authService, profileService, lensService);

  void handleDeepLink(Uri uri) {
    final host = uri.host;
    final path = uri.path;
    final fullPath = host.isNotEmpty ? '/$host$path' : path;
    final query = uri.query.isNotEmpty ? '?${uri.query}' : '';
    debugPrint('DEEP LINK: $uri → routing to $fullPath$query');
    Future.delayed(Duration.zero, () {
      router.go('$fullPath$query');
    });
  }

  final appLinks = AppLinks();
  appLinks.uriLinkStream.listen(handleDeepLink);

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: authService),
        ChangeNotifierProvider.value(value: audioService),
        ChangeNotifierProvider.value(value: lensService),
        ChangeNotifierProvider.value(value: profileService),
        ChangeNotifierProvider.value(value: familyService),
        ChangeNotifierProvider.value(value: tripService),
        ChangeNotifierProvider.value(value: feedbackService),
        ChangeNotifierProvider.value(value: locationService),
        // Typed, not a second untyped copy of the line above: TourWalkPage
        // watches AudioProvider, and without this registration that lookup
        // throws ProviderNotFoundException at runtime.
        ChangeNotifierProvider<AudioProvider>.value(value: audioService),
        ChangeNotifierProvider.value(value: tourPlaybackService),
      ],
      child: OndowayApp(router: router),
    ),
  );
}

class OndowayApp extends StatelessWidget {
  final dynamic router;
  const OndowayApp({super.key, required this.router});

  @override
  Widget build(BuildContext context) {
    final profile = context.watch<ProfileService>();
    return MaterialApp.router(
      title: 'Ondoway',
      theme: buildOndowayTheme(Brightness.light),
      darkTheme: buildOndowayTheme(Brightness.dark),
      themeMode: _resolveThemeMode(profile.themePreference),
      routerConfig: router,
      debugShowCheckedModeBanner: false,
    );
  }

  static ThemeMode _resolveThemeMode(String? preference) {
    switch (preference) {
      case 'dark':
        return ThemeMode.dark;
      case 'light':
        return ThemeMode.light;
      default:
        return ThemeMode.system;
    }
  }
}
