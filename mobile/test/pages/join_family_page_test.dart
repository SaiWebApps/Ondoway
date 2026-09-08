import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/pages/join_family_page.dart';
import 'package:ondoway/pages/login_page.dart';
import 'package:ondoway/pages/profile_page.dart';
import 'package:ondoway/router.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/theme/theme.dart';

import '../services/auth_service_test.dart';

/// One MockClient answering every endpoint the invite walk touches, recording
/// the token each POST /families/join carries.
MockClient _inviteWorld(List<String> joinedTokens) {
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
    if (request.url.path.endsWith('/lenses')) {
      return http.Response(jsonEncode([]), 200);
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
    if (request.url.path.endsWith('/families/join')) {
      // Deliberately INSTANT: a join that answers before the page's entrance
      // transition finishes is the racy case — leaving for the tab shell
      // mid-animation re-parents the shell's GlobalKey and crashes. The page
      // must absorb a fast server, not the mock a slow one.
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      joinedTokens.add(body['token'] as String);
      return http.Response(
          jsonEncode({'family_id': 'fam-1', 'profile_id': 'p-dev'}), 200);
    }
    if (request.url.path.endsWith('/families/mine')) {
      return http.Response(
        jsonEncode({
          'families': [
            {
              'family_id': 'fam-1',
              'name': null,
              'members': [
                {'profile_id': 'p-dev', 'display_name': 'Dev'},
              ],
            },
          ],
        }),
        200,
      );
    }
    if (request.url.path.endsWith('/trips') && request.method == 'GET') {
      return http.Response(jsonEncode([]), 200);
    }
    return http.Response('Not found', 404);
  });
}

/// The production router with every service the walked pages watch.
Widget _appWithRealRouter({
  required http.Client client,
  required AuthService auth,
  required ProfileService profile,
  required LensService lenses,
}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<AuthService>.value(value: auth),
      ChangeNotifierProvider<LensService>.value(value: lenses),
      ChangeNotifierProvider<ProfileService>.value(value: profile),
      ChangeNotifierProvider<FamilyService>(
          create: (_) => FamilyService(httpClient: client)),
      ChangeNotifierProvider<TripService>(
          create: (_) => TripService(httpClient: client)),
    ],
    child: MaterialApp.router(
      routerConfig: createRouter(auth, profile, lenses),
      theme: buildOndowayTheme(Brightness.light),
    ),
  );
}

void main() {
  setUp(() {
    // ProfilePage and LoginPage render at phone height, not the test default.
    TestWidgetsFlutterBinding.ensureInitialized();
  });

  testWidgets(
      'a signed-in tap parses the token through the real route and joins',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 1600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    final joinedTokens = <String>[];
    final client = _inviteWorld(joinedTokens);
    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    await auth.verifyMagicLink('tok');
    final profile = ProfileService(httpClient: client);
    await profile.fetchProfile('tok');
    final lenses = LensService(httpClient: client);

    await tester.pumpWidget(_appWithRealRouter(
        client: client, auth: auth, profile: profile, lenses: lenses));
    await tester.pumpAndSettle();

    createRouterOf(tester).go('/auth/join-family?token=abc');
    await tester.pumpAndSettle();

    expect(joinedTokens, ['abc']);
    expect(find.byType(ProfilePage), findsOneWidget);
  });

  testWidgets('a signed-out tap stashes the destination and lands on login',
      (tester) async {
    final joinedTokens = <String>[];
    final client = _inviteWorld(joinedTokens);
    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    final profile = ProfileService(httpClient: client);
    final lenses = LensService(httpClient: client);

    await tester.pumpWidget(_appWithRealRouter(
        client: client, auth: auth, profile: profile, lenses: lenses));
    await tester.pumpAndSettle();

    createRouterOf(tester).go('/auth/join-family?token=abc');
    await tester.pumpAndSettle();

    // Bounced to login — but the tap is not lost.
    expect(find.byType(LoginPage), findsOneWidget);
    expect(joinedTokens, isEmpty);
    expect(auth.consumePendingDestination(), '/auth/join-family?token=abc');
  });

  testWidgets('the invite tap survives magic-link sign-in end to end',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 1600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    final joinedTokens = <String>[];
    final client = _inviteWorld(joinedTokens);
    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    final profile = ProfileService(httpClient: client);
    final lenses = LensService(httpClient: client);

    await tester.pumpWidget(_appWithRealRouter(
        client: client, auth: auth, profile: profile, lenses: lenses));
    await tester.pumpAndSettle();

    final router = createRouterOf(tester);

    // 1. The signed-out invite tap: bounced to login, destination stashed.
    router.go('/auth/join-family?token=abc');
    await tester.pumpAndSettle();
    expect(find.byType(LoginPage), findsOneWidget);

    // 2. The magic link lands: sign-in completes and the join RESUMES —
    //    no re-tap, the same token reaches the server.
    router.go('/auth?token=tok');
    await tester.pumpAndSettle();

    expect(joinedTokens, ['abc']);
    expect(find.byType(ProfilePage), findsOneWidget);
  });

  testWidgets('an empty token is a plain error, not a crash', (tester) async {
    final client = _inviteWorld(<String>[]);
    final auth = AuthService(storage: FakeSecureStorage(), httpClient: client);
    await auth.verifyMagicLink('tok');

    await tester.pumpWidget(MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthService>.value(value: auth),
        ChangeNotifierProvider<FamilyService>(
            create: (_) => FamilyService(httpClient: client)),
      ],
      child: MaterialApp(
        theme: buildOndowayTheme(Brightness.light),
        home: const JoinFamilyPage(token: ''),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('This invite link is missing its code.'), findsOneWidget);
  });
}

/// The GoRouter the pumped MaterialApp.router runs on.
GoRouter createRouterOf(WidgetTester tester) {
  final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
  return app.routerConfig! as GoRouter;
}
