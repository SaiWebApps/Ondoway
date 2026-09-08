import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/session_bootstrap.dart';

import 'auth_service_test.dart';

/// One MockClient answering every endpoint the sign-in landing reads, and
/// recording which paths were asked for.
MockClient _signedInWorld(List<String> requestedPaths) {
  return MockClient((request) async {
    requestedPaths.add(request.url.path);
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
          jsonEncode({'id': 'user-1', 'email': 'fiona@example.test'}), 200);
    }
    if (request.url.path.endsWith('/lenses')) {
      return http.Response(jsonEncode([]), 200);
    }
    if (request.url.path.endsWith('/profile')) {
      return http.Response(
        jsonEncode({
          'profile_id': 'p-fiona',
          'display_name': 'Fiona',
          'selected_lens_ids': ['l1'],
          'theme_preference': null,
        }),
        200,
      );
    }
    if (request.url.path.endsWith('/families/mine')) {
      return http.Response(jsonEncode({'families': []}), 200);
    }
    return http.Response('Not found', 404);
  });
}

void main() {
  test('loadSignedInSession loads lenses, profile AND families', () async {
    final requestedPaths = <String>[];
    final client = _signedInWorld(requestedPaths);

    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    await auth.verifyMagicLink('tok');

    final lenses = LensService(httpClient: client);
    final profile = ProfileService(httpClient: client);
    final family = FamilyService(httpClient: client);

    await loadSignedInSession(
      auth: auth,
      lenses: lenses,
      profile: profile,
      family: family,
    );

    expect(lenses.isLoaded, true);
    expect(profile.isLoaded, true);
    expect(family.isLoaded, true);
    expect(requestedPaths, contains('/api/v1/families/mine'));
  });

  test('a family failure never breaks the sign-in landing', () async {
    final client = MockClient((request) async {
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
            jsonEncode({'id': 'user-1', 'email': 'fiona@example.test'}), 200);
      }
      if (request.url.path.endsWith('/lenses')) {
        return http.Response(jsonEncode([]), 200);
      }
      if (request.url.path.endsWith('/profile')) {
        return http.Response(
          jsonEncode({
            'profile_id': 'p-fiona',
            'display_name': 'Fiona',
            'selected_lens_ids': ['l1'],
            'theme_preference': null,
          }),
          200,
        );
      }
      // /families/mine and everything else: a broken backend.
      return http.Response('{"detail":"boom"}', 500);
    });

    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    await auth.verifyMagicLink('tok');

    final profile = ProfileService(httpClient: client);
    final family = FamilyService(httpClient: client);

    await loadSignedInSession(
      auth: auth,
      lenses: LensService(httpClient: client),
      profile: profile,
      family: family,
    );

    expect(profile.isLoaded, true);
    expect(family.isLoaded, false);
    expect(family.loadError, isNotNull);
  });

  group('signedInLandingRoute', () {
    Future<(AuthService, ProfileService)> signedIn(
        {required bool firstTime}) async {
      final client = MockClient((request) async {
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
              jsonEncode({'id': 'user-1', 'email': 'fiona@example.test'}),
              200);
        }
        if (request.url.path.endsWith('/profile')) {
          return http.Response(
            jsonEncode({
              'profile_id': 'p-fiona',
              'display_name': 'Fiona',
              'selected_lens_ids': firstTime ? [] : ['l1'],
              'theme_preference': null,
            }),
            200,
          );
        }
        return http.Response('Not found', 404);
      });
      final auth =
          AuthService(storage: FakeSecureStorage(), httpClient: client);
      await auth.verifyMagicLink('tok');
      final profile = ProfileService(httpClient: client);
      await profile.fetchProfile('tok');
      return (auth, profile);
    }

    test('a stashed join resumes after sign-in for a returning user', () async {
      final (auth, profile) = await signedIn(firstTime: false);
      auth.stashPendingDestination('/auth/join-family?token=abc');

      expect(
        signedInLandingRoute(auth: auth, profile: profile),
        '/auth/join-family?token=abc',
      );
      // Consumed: the next landing is the plain one.
      expect(signedInLandingRoute(auth: auth, profile: profile), '/explore');
    });

    test('nothing stashed lands on explore', () async {
      final (auth, profile) = await signedIn(firstTime: false);
      expect(signedInLandingRoute(auth: auth, profile: profile), '/explore');
    });

    test('a first-time user onboards first; the stashed join waits', () async {
      final (auth, profile) = await signedIn(firstTime: true);
      auth.stashPendingDestination('/auth/join-family?token=abc');

      expect(signedInLandingRoute(auth: auth, profile: profile), '/onboarding');
      // Still stashed for the post-onboarding landing.
      expect(auth.consumePendingDestination(), '/auth/join-family?token=abc');
    });

    test('logout drops a stashed destination', () async {
      final (auth, _) = await signedIn(firstTime: false);
      auth.stashPendingDestination('/auth/join-family?token=abc');
      await auth.logout();
      expect(auth.consumePendingDestination(), isNull);
    });
  });
}
