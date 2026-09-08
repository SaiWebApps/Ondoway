import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/pages/callback_page.dart';
import 'package:ondoway/pages/explore_page.dart';
import 'package:ondoway/pages/join_family_page.dart';
import 'package:ondoway/pages/lens_selection_page.dart';
import 'package:ondoway/pages/login_page.dart';
import 'package:ondoway/pages/profile_page.dart';
import 'package:ondoway/pages/saved_trips_page.dart';
import 'package:ondoway/pages/style_gallery_page.dart';
import 'package:ondoway/pages/tour_now_page.dart';
import 'package:ondoway/pages/tour_walk_page.dart';
import 'package:ondoway/pages/trip_duration_page.dart';
import 'package:ondoway/pages/trip_itinerary_page.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/session_bootstrap.dart';
import 'package:ondoway/spike/tour_pin_proof_page.dart';
import 'package:ondoway/spike/tour_playback_proof_page.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/widgets/app_shell.dart';
import 'package:provider/provider.dart';

/// Builds the lens editor (edit mode) shown when a signed-in user edits their
/// preferences from Profile. Pushed as a top-level route (not a tab) so it has
/// a normal back stack and returns to Profile on save/back.
Widget _buildLensEditor(BuildContext context) {
  final ls = context.read<LensService>();
  final ps = context.read<ProfileService>();
  final as_ = context.read<AuthService>();
  return LensSelectionPage(
    isOnboarding: false,
    lensesByParent: ls.childrenByParent,
    initialSelection: ps.selectedLensIds.toSet(),
    onSave: (selectedIds) async {
      final current = ps.selectedLensIds.toSet();
      final toAdd = selectedIds.difference(current);
      final toRemove = current.difference(selectedIds);
      try {
        // For now, re-run onboarding endpoint to replace all preferences
        if (toAdd.isNotEmpty || toRemove.isNotEmpty) {
          await ps.completeOnboarding(
            selectedIds.toList(),
            as_.accessToken!,
            refresh: () async =>
                (await as_.refreshSession()) ? as_.accessToken : null,
          );
        }
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Lenses saved')),
        );
        // Return to Profile — saving is done, don't strand the user here.
        if (context.canPop()) context.pop();
      } on ProfileServiceException catch (e) {
        if (!context.mounted) return;
        if (e.statusCode == 401 || e.statusCode == 403) {
          // Session is unrecoverable (refresh failed) — back to login.
          await as_.logout();
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Could not save — please try again.'),
            ),
          );
        }
      }
    },
  );
}

/// Pure decision function for the router's auth redirect.
///
/// Reproduces the router's auth-gating logic outside of a go_router
/// [GoRouter.redirect] closure so it is unit-testable without constructing
/// real services. Auth routes (`/login`, `/auth`, `/auth/callback`) are
/// always exempt from the "must be authenticated" guard; debug routes
/// (`/debug/...`) are exempt only when [allowDebugRoutes] is true (debug and
/// profile builds, never release), so production behavior is unchanged.
String? computeAuthRedirect({
  required bool isAuthenticated,
  required bool profileLoaded,
  required bool profileIsFirstTime,
  required String path,
  required bool allowDebugRoutes,
}) {
  final isAuthRoute =
      path == '/login' || path == '/auth' || path == '/auth/callback';
  final isExemptDebugRoute = allowDebugRoutes && path.startsWith('/debug/');

  if (!isAuthenticated && !isAuthRoute && !isExemptDebugRoute) {
    return '/login';
  }

  if (isAuthenticated && path == '/login') {
    if (!profileLoaded) return null;
    return profileIsFirstTime ? '/onboarding' : '/explore';
  }

  return null;
}

/// The location a signed-out deep link should RESUME at after sign-in, or null
/// when the bounce loses nothing worth keeping.
///
/// Today that is exactly the family-invite tap: `/auth/join-family?token=…`
/// needs a signed-in profile, so the guard bounces it to /login — and without
/// the stash the token dies there, forcing every new member to dig the invite
/// link back up after signing in. Pure, so the decision is unit-testable
/// beside [computeAuthRedirect]; the router's redirect closure stashes the
/// result on AuthService when it bounces.
String? pendingDestinationFor(Uri uri) {
  if (uri.path != '/auth/join-family') return null;
  final token = uri.queryParameters['token'];
  if (token == null || token.isEmpty) return null;
  return uri.toString();
}

GoRouter createRouter(
  AuthService authService,
  ProfileService profileService,
  LensService lensService,
) {
  return GoRouter(
    initialLocation: '/login',
    refreshListenable: authService,
    redirect: (context, state) {
      final redirect = computeAuthRedirect(
        isAuthenticated: authService.isAuthenticated,
        profileLoaded: profileService.isLoaded,
        profileIsFirstTime: profileService.isFirstTime,
        path: state.matchedLocation,
        // Debug affordances are reachable in debug AND profile builds —
        // profile is what on-device iOS testing uses — but never in release.
        allowDebugRoutes: !kReleaseMode,
      );
      if (redirect == '/login') {
        // A bounced invite tap survives sign-in: the sign-in landing
        // (signedInLandingRoute) resumes the stashed location.
        final pending = pendingDestinationFor(state.uri);
        if (pending != null) authService.stashPendingDestination(pending);
      }
      return redirect;
    },
    routes: [
      // The design system rendered in one page, for on-device visual checking.
      // Never linked from product navigation; the /debug/ prefix above is what
      // keeps it out of a release build.
      GoRoute(
        path: '/debug/style-gallery',
        builder: (context, state) => const StyleGalleryPage(),
      ),
      // The two on-device proof harnesses, debug builds only. They exist because
      // a green suite is not evidence for background audio: the simulator and a
      // real iPhone disagreed about it once already, and the only proof that
      // counts is a locked phone in a pocket on a pavement. Both drive the
      // PRODUCTION path — the real TourPlaybackService, the real LocationService,
      // the real cache convention — so what they prove is the product, not a
      // rehearsal of it.
      //
      // The pin harness is the one to reach for: drop your own stops on a map,
      // spaced as far apart as you like, then walk to each. The linear one
      // places two stops along a chosen bearing when there is no map to tap.
      GoRoute(
        path: '/debug/tour-playback-proof',
        builder: (context, state) => const TourPlaybackProofPage(),
      ),
      GoRoute(
        path: '/debug/tour-pin-proof',
        builder: (context, state) => const TourPinProofPage(),
      ),
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginPage(),
      ),
      GoRoute(
        path: '/auth',
        builder: (context, state) {
          final token = state.uri.queryParameters['token'] ?? '';
          return CallbackPage(token: token);
        },
      ),
      GoRoute(
        path: '/auth/callback',
        builder: (context, state) {
          final token = state.uri.queryParameters['token'] ?? '';
          return CallbackPage(token: token);
        },
      ),
      // The family invite deep link (rides /auth/* — the app's registered
      // universal-link space). NOT exempt from the auth guard above: joining
      // needs a signed-in profile, so an unauthenticated tap lands on /login —
      // with the destination stashed (pendingDestinationFor), so the join
      // resumes after sign-in instead of dying with the bounce.
      GoRoute(
        path: '/auth/join-family',
        builder: (context, state) {
          final token = state.uri.queryParameters['token'] ?? '';
          return JoinFamilyPage(token: token);
        },
      ),
      GoRoute(
        path: '/onboarding',
        builder: (context, state) {
          final ls = context.read<LensService>();
          final ps = context.read<ProfileService>();
          final as_ = context.read<AuthService>();
          return LensSelectionPage(
            isOnboarding: true,
            userName: as_.userEmail?.split('@')[0],
            lensesByParent: ls.childrenByParent,
            onComplete: (selectedIds) async {
              try {
                await ps.completeOnboarding(
                  selectedIds.toList(),
                  as_.accessToken!,
                  refresh: () async =>
                      (await as_.refreshSession()) ? as_.accessToken : null,
                );
                // The one landing decision: a join stashed before onboarding
                // resumes here; otherwise explore.
                if (context.mounted) {
                  context.go(signedInLandingRoute(auth: as_, profile: ps));
                }
              } on ProfileServiceException catch (e) {
                if (!context.mounted) return;
                if (e.statusCode == 401 || e.statusCode == 403) {
                  // Session is unrecoverable (refresh failed) — back to login.
                  await as_.logout();
                } else {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('Could not save your lenses — please try again.'),
                    ),
                  );
                }
              }
            },
          );
        },
      ),
      // Full-screen trip planning routes (outside tab shell)
      // Immediate: "Take a tour now" — one question (how long), builds from GPS.
      GoRoute(
        path: '/tour-now/:citySlug',
        builder: (context, state) =>
            TourNowPage(citySlug: state.pathParameters['citySlug'] ?? 'paris'),
      ),
      // Plan for later: dates, start-point picker, multi-day.
      GoRoute(
        path: '/plan-trip/:citySlug',
        builder: (context, state) {
          final citySlug = state.pathParameters['citySlug'] ?? 'paris';
          return TripDurationPage(citySlug: citySlug);
        },
      ),
      GoRoute(
        path: '/trip/:tripId',
        builder: (context, state) {
          final tripId = state.pathParameters['tripId'] ?? '';
          return TripItineraryPage(tripId: tripId);
        },
      ),
      GoRoute(
        path: '/trip/:tripId/walk',
        builder: (context, state) {
          // Golden path: "Start walking" pushes here with the already-loaded
          // GeneratedTrip as `extra` — no service round-trip needed.
          final extra = state.extra;
          if (extra is GeneratedTrip) return TourWalkPage(trip: extra);
          // Cold/deep-link entry (no `extra`, e.g. a fresh app launch on this
          // URL): TripService has no fetch-by-id, so there is nothing to hydrate
          // from here. Send the user back to a place they CAN start a walk from.
          return const _TourWalkFallback();
        },
      ),
      // Lens editor (edit mode) — a pushed full-screen route, so Profile's
      // "Edit" gives a real back stack instead of stranding the user in a tab.
      GoRoute(
        path: '/lenses',
        builder: (context, state) => _buildLensEditor(context),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) {
          return AppShell(
            currentIndex: navigationShell.currentIndex,
            onTabChanged: (index) => navigationShell.goBranch(index),
            child: navigationShell,
          );
        },
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/explore',
                builder: (context, state) => const ExplorePage(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/saved-trips',
                builder: (context, state) => const SavedTripsPage(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/profile',
                builder: (context, state) => const ProfilePage(),
              ),
            ],
          ),
        ],
      ),
    ],
  );
}

/// Graceful landing for `/trip/:tripId/walk` reached without a trip in hand
/// (a cold start or deep link, not the in-app "Start walking" button). There
/// is no fetch-by-id to hydrate from — `TripService` only generates, composes,
/// and lists saved trips — so this points the user back to a screen that can
/// actually start a walk instead of crashing on a null trip.
class _TourWalkFallback extends StatelessWidget {
  const _TourWalkFallback();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Walking tour')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(Dims.spaceLg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Open this walk from your trip'),
              const SizedBox(height: Dims.spaceMd),
              FilledButton(
                onPressed: () => context.go('/saved-trips'),
                child: const Text('Go to your saved trips'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
