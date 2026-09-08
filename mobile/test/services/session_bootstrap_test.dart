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
}
