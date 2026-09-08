import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/pages/callback_page.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/theme/theme.dart';

import '../services/auth_service_test.dart';

/// One MockClient serving the whole sign-in landing, recording the paths asked.
MockClient _landingWorld(List<String> requestedPaths) {
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
  testWidgets(
      'the magic-link landing fetches the family — the sign-up session sees '
      'its family section, not a forever-loading block', (tester) async {
    final requestedPaths = <String>[];
    final client = _landingWorld(requestedPaths);

    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    final lenses = LensService(httpClient: client);
    final profile = ProfileService(httpClient: client);
    final family = FamilyService(httpClient: client);

    final router = GoRouter(
      initialLocation: '/auth',
      routes: [
        GoRoute(
          path: '/auth',
          builder: (context, state) => const CallbackPage(token: 'tok'),
        ),
        GoRoute(
          path: '/explore',
          builder: (context, state) =>
              const Scaffold(body: Center(child: Text('explore-stub'))),
        ),
        GoRoute(
          path: '/onboarding',
          builder: (context, state) =>
              const Scaffold(body: Center(child: Text('onboarding-stub'))),
        ),
      ],
    );

    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<AuthService>.value(value: auth),
          ChangeNotifierProvider<LensService>.value(value: lenses),
          ChangeNotifierProvider<ProfileService>.value(value: profile),
          ChangeNotifierProvider<FamilyService>.value(value: family),
        ],
        child: MaterialApp.router(
          routerConfig: router,
          theme: buildOndowayTheme(Brightness.light),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(requestedPaths, contains('/api/v1/families/mine'));
    expect(family.isLoaded, true);
    // Lenses selected -> not first-time -> lands on explore.
    expect(find.text('explore-stub'), findsOneWidget);
  });
}
