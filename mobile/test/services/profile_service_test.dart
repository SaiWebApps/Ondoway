import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ondoway/services/profile_service.dart';

// GET /api/v1/profile -> 404 (no profile yet -> first-time user).
MockClient _firstTimeUserClient() {
  return MockClient((request) async {
    if (request.url.path.endsWith('/profile')) {
      return http.Response(jsonEncode({'detail': 'No profile for this user'}), 404);
    }
    return http.Response('Not found', 404);
  });
}

// GET /api/v1/profile -> 200 with the public product shape (single call, no join).
MockClient _returningUserClient({
  String profileId = 'profile-1',
  List<String> selectedLensIds = const ['lens-a', 'lens-b', 'lens-c'],
  String? themePreference,
}) {
  return MockClient((request) async {
    if (request.url.path.endsWith('/profile')) {
      return http.Response(
        jsonEncode({
          'profile_id': profileId,
          'display_name': 'Test User',
          'selected_lens_ids': selectedLensIds,
          'theme_preference': themePreference,
        }),
        200,
      );
    }
    return http.Response('Not found', 404);
  });
}

MockClient _onboardingClient() {
  return MockClient((request) async {
    if (request.url.path.endsWith('/profile')) {
      return http.Response(jsonEncode({'detail': 'No profile'}), 404);
    }
    if (request.url.path.contains('/onboarding/complete')) {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      final lensIds = body['lens_ids'] as List<dynamic>;
      return http.Response(
        jsonEncode({
          'profile_id': 'new-profile-1',
          'display_name': 'Testuser',
          'lens_count': lensIds.length,
        }),
        200,
      );
    }
    return http.Response('Not found', 404);
  });
}

void main() {
  group('ProfileService', () {
    test('fetchProfile calls the public /profile endpoint, not the workbench node/edge routes',
        () async {
      final paths = <String>[];
      final service = ProfileService(
        httpClient: MockClient((request) async {
          paths.add(request.url.path);
          return http.Response(
            jsonEncode({
              'profile_id': 'p1',
              'display_name': 'x',
              'selected_lens_ids': <String>[],
              'theme_preference': null,
            }),
            200,
          );
        }),
      );
      await service.fetchProfile('token');
      expect(paths.any((p) => p.endsWith('/profile')), true,
          reason: 'must call the new public /profile endpoint');
      expect(paths.any((p) => p.contains('/nodes/') || p.contains('/edges/')), false,
          reason: 'must NOT call the workbench endpoints (they 404 in prod)');
    });

    test('fetchProfile does not set isFirstTime on 401 (auth failure)', () async {
      final service = ProfileService(
        httpClient: MockClient((request) async {
          return http.Response('{"detail":"Not authenticated"}', 401);
        }),
      );

      await service.fetchProfile('bad-token');

      // Should NOT mark as first-time or loaded — the auth layer needs to refresh.
      expect(service.isLoaded, false);
      expect(service.isFirstTime, true); // default; isLoaded=false means the router won't act
    });

    test('detects first-time user (404, no profile)', () async {
      final service = ProfileService(httpClient: _firstTimeUserClient());
      await service.fetchProfile('token');

      expect(service.isFirstTime, true);
      expect(service.isLoaded, true);
      expect(service.profileId, isNull);
      expect(service.selectedLensIds, isEmpty);
    });

    test('detects returning user with lenses', () async {
      final service = ProfileService(httpClient: _returningUserClient());
      await service.fetchProfile('token');

      expect(service.isFirstTime, false);
      expect(service.isLoaded, true);
      expect(service.profileId, 'profile-1');
      expect(service.selectedLensIds, ['lens-a', 'lens-b', 'lens-c']);
    });

    test('detects half-onboarded user (profile, no lenses) as first-time', () async {
      final service = ProfileService(
        httpClient: _returningUserClient(selectedLensIds: []),
      );
      await service.fetchProfile('token');

      expect(service.isFirstTime, true);
      expect(service.profileId, 'profile-1');
    });

    test('completeOnboarding stores profile and lenses', () async {
      final service = ProfileService(httpClient: _onboardingClient());
      await service.fetchProfile('token');
      expect(service.isFirstTime, true);

      await service.completeOnboarding(['lens-1', 'lens-2', 'lens-3'], 'token');

      expect(service.isFirstTime, false);
      expect(service.profileId, 'new-profile-1');
      expect(service.displayName, 'Testuser');
      expect(service.selectedLensIds.length, 3);
    });

    test('completeOnboarding throws on API failure', () async {
      final service = ProfileService(
        httpClient: MockClient((r) async => http.Response('error', 500)),
      );

      expect(
        () => service.completeOnboarding(['a', 'b', 'c'], 'token'),
        throwsA(isA<ProfileServiceException>()),
      );
    });

    test('reset clears all state', () async {
      final service = ProfileService(httpClient: _returningUserClient());
      await service.fetchProfile('token');
      expect(service.isFirstTime, false);

      service.reset();

      expect(service.isFirstTime, true);
      expect(service.isLoaded, false);
      expect(service.profileId, isNull);
      expect(service.selectedLensIds, isEmpty);
    });

    test('isLoaded is false before fetch', () {
      final service = ProfileService(httpClient: _firstTimeUserClient());
      expect(service.isLoaded, false);
    });

    test('fetchProfile reads theme_preference verbatim', () async {
      final service = ProfileService(
        httpClient: _returningUserClient(themePreference: 'dark'),
      );
      await service.fetchProfile('token');

      expect(service.themePreference, 'dark');
    });

    test('fetchProfile leaves themePreference null when absent', () async {
      final service = ProfileService(
        httpClient: _returningUserClient(themePreference: null),
      );
      await service.fetchProfile('token');

      expect(service.themePreference, isNull);
    });

    // --- Write path: updateThemePreference sends a PUT to /nodes/Profile/<id>,
    // while fetchProfile reads /profile. These verify the client-side
    // optimistic-update logic.

    test('updateThemePreference sends PUT and updates state', () async {
      String? capturedBody;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/profile')) {
          return http.Response(
            jsonEncode({
              'profile_id': 'profile-1',
              'display_name': 'Test',
              'selected_lens_ids': ['lens-a'],
              'theme_preference': null,
            }),
            200,
          );
        }
        if (request.url.path.contains('/nodes/Profile/') && request.method == 'PUT') {
          capturedBody = request.body;
          return http.Response(
            jsonEncode({
              'id': 'profile-1',
              'labels': ['Profile'],
              'properties': {'display_name': 'Test', 'theme_preference': 'light'},
            }),
            200,
          );
        }
        return http.Response('Not found', 404);
      });

      final service = ProfileService(httpClient: client);
      await service.fetchProfile('token');
      await service.updateThemePreference('light', 'token');

      expect(service.themePreference, 'light');
      expect(capturedBody, contains('theme_preference'));
      expect(capturedBody, contains('light'));
    });

    test('updateThemePreference reverts on API failure', () async {
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/profile')) {
          return http.Response(
            jsonEncode({
              'profile_id': 'profile-1',
              'display_name': 'Test',
              'selected_lens_ids': ['lens-a'],
              'theme_preference': 'dark',
            }),
            200,
          );
        }
        if (request.url.path.contains('/nodes/Profile/') && request.method == 'PUT') {
          return http.Response('Server error', 500);
        }
        return http.Response('Not found', 404);
      });

      final service = ProfileService(httpClient: client);
      await service.fetchProfile('token');
      expect(service.themePreference, 'dark');

      expect(
        () => service.updateThemePreference('light', 'token'),
        throwsA(isA<ProfileServiceException>()),
      );

      // Wait for the future to settle so the revert takes effect.
      await Future.delayed(Duration.zero);
      expect(service.themePreference, 'dark');
    });

    test('updateThemePreference is no-op when profileId is null', () async {
      final service = ProfileService(httpClient: _firstTimeUserClient());
      await service.fetchProfile('token');
      expect(service.profileId, isNull);

      // Should not throw, just return.
      await service.updateThemePreference('dark', 'token');
      expect(service.themePreference, isNull);
    });

    test('reset clears themePreference', () async {
      final service = ProfileService(
        httpClient: _returningUserClient(themePreference: 'dark'),
      );
      await service.fetchProfile('token');
      expect(service.themePreference, 'dark');

      service.reset();
      expect(service.themePreference, isNull);
    });

    test('completeOnboarding refreshes and retries once on 401 expired token', () async {
      final authHeadersSeen = <String>[];
      var refreshCalls = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/profile')) {
          return http.Response(jsonEncode({'detail': 'No profile'}), 404);
        }
        if (request.url.path.contains('/onboarding/complete')) {
          authHeadersSeen.add(request.headers['Authorization'] ?? '');
          if (authHeadersSeen.length == 1) {
            // First attempt uses the stale token -> server rejects it.
            return http.Response(
              jsonEncode({'detail': 'Invalid or expired token'}),
              401,
            );
          }
          return http.Response(
            jsonEncode({
              'profile_id': 'p1',
              'display_name': 'U',
              'lens_count': 3,
            }),
            200,
          );
        }
        return http.Response('Not found', 404);
      });

      final service = ProfileService(httpClient: client);
      await service.completeOnboarding(
        ['a', 'b', 'c'],
        'stale-token',
        refresh: () async {
          refreshCalls++;
          return 'fresh-token';
        },
      );

      // Refreshed exactly once, retried with the fresh token, and succeeded.
      expect(refreshCalls, 1);
      expect(authHeadersSeen, ['Bearer stale-token', 'Bearer fresh-token']);
      expect(service.isFirstTime, false);
      expect(service.selectedLensIds, ['a', 'b', 'c']);
    });

    test('completeOnboarding throws typed 401 when refresh also fails', () async {
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/profile')) {
          return http.Response(jsonEncode({'detail': 'No profile'}), 404);
        }
        if (request.url.path.contains('/onboarding/complete')) {
          return http.Response(
            jsonEncode({'detail': 'Invalid or expired token'}),
            401,
          );
        }
        return http.Response('Not found', 404);
      });

      final service = ProfileService(httpClient: client);

      await expectLater(
        () => service.completeOnboarding(
          ['a', 'b', 'c'],
          'stale-token',
          refresh: () async => null, // refresh token also dead
        ),
        throwsA(
          isA<ProfileServiceException>()
              .having((e) => e.statusCode, 'statusCode', 401),
        ),
      );
    });
  });
}
