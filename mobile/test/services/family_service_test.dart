import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ondoway/services/family_service.dart';

// GET /api/v1/families/mine -> the family-of-two the wire returns.
MockClient _familyOfTwoClient() {
  return MockClient((request) async {
    if (request.url.path.endsWith('/families/mine')) {
      return http.Response(
        jsonEncode({
          'families': [
            {
              'family_id': 'fam-1',
              'name': 'The Fionas',
              'members': [
                {'profile_id': 'p-dev', 'display_name': 'Dev'},
                {'profile_id': 'p-fiona', 'display_name': 'Fiona'},
              ],
            },
          ],
        }),
        200,
      );
    }
    return http.Response('Not found', 404);
  });
}

void main() {
  group('FamilyService', () {
    test('fetchFamilies reads /families/mine into typed members', () async {
      final service = FamilyService(httpClient: _familyOfTwoClient());
      await service.fetchFamilies('token');

      expect(service.isLoaded, true);
      expect(service.families, hasLength(1));
      expect(service.family!.familyId, 'fam-1');
      expect(service.family!.name, 'The Fionas');
      expect(service.family!.members.map((m) => m.displayName),
          containsAll(['Fiona', 'Dev']));
    });

    test('fetchFamilies leaves state unloaded on 401 so auth can refresh',
        () async {
      final service = FamilyService(
        httpClient: MockClient(
            (r) async => http.Response('{"detail":"Not authenticated"}', 401)),
      );
      await service.fetchFamilies('bad-token');
      expect(service.isLoaded, false);
      expect(service.families, isEmpty);
    });

    test('fetchFamilies with no family loads an empty list', () async {
      final service = FamilyService(
        httpClient: MockClient((request) async {
          if (request.url.path.endsWith('/families/mine')) {
            return http.Response(jsonEncode({'families': []}), 200);
          }
          return http.Response('Not found', 404);
        }),
      );
      await service.fetchFamilies('token');
      expect(service.isLoaded, true);
      expect(service.family, isNull);
    });

    test('createFamily posts /families and refetches the members', () async {
      final paths = <String>[];
      final service = FamilyService(
        httpClient: MockClient((request) async {
          paths.add('${request.method} ${request.url.path}');
          if (request.method == 'POST' &&
              request.url.path.endsWith('/families')) {
            return http.Response(
                jsonEncode({'family_id': 'fam-1', 'name': null}), 201);
          }
          if (request.url.path.endsWith('/families/mine')) {
            return http.Response(
              jsonEncode({
                'families': [
                  {
                    'family_id': 'fam-1',
                    'name': null,
                    'members': [
                      {'profile_id': 'p-fiona', 'display_name': 'Fiona'},
                    ],
                  },
                ],
              }),
              200,
            );
          }
          return http.Response('Not found', 404);
        }),
      );
      final familyId = await service.createFamily('token');
      expect(familyId, 'fam-1');
      expect(service.family!.members.single.displayName, 'Fiona');
      expect(paths.first, 'POST /api/v1/families');
    });

    test('createInvite returns the shareable link and its expiry', () async {
      final service = FamilyService(
        httpClient: MockClient((request) async {
          if (request.url.path.endsWith('/families/invite')) {
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            expect(body['family_id'], 'fam-1');
            return http.Response(
              jsonEncode({
                'invite_url': 'http://localhost:3000/auth/join-family?token=abc',
                'expires_at': '2026-09-14T00:00:00+00:00',
              }),
              200,
            );
          }
          return http.Response('Not found', 404);
        }),
      );
      final invite = await service.createInvite('fam-1', 'token');
      expect(invite.inviteUrl, contains('token=abc'));
      expect(invite.expiresAt, contains('2026-09-14'));
    });

    test('join posts the token then refetches the family', () async {
      final paths = <String>[];
      final service = FamilyService(
        httpClient: MockClient((request) async {
          paths.add('${request.method} ${request.url.path}');
          if (request.url.path.endsWith('/families/join')) {
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            expect(body['token'], 'invite-token');
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
                      {'profile_id': 'p-fiona', 'display_name': 'Fiona'},
                    ],
                  },
                ],
              }),
              200,
            );
          }
          return http.Response('Not found', 404);
        }),
      );
      await service.join('invite-token', 'token');
      expect(service.family!.familyId, 'fam-1');
      expect(paths.any((p) => p == 'POST /api/v1/families/join'), true);
    });

    test('an expired invite surfaces as a typed 401', () async {
      final service = FamilyService(
        httpClient: MockClient((request) async {
          if (request.url.path.endsWith('/families/join')) {
            return http.Response(
              jsonEncode({
                'detail': {'reason': 'invite_invalid', 'detail': 'Invite has expired'}
              }),
              401,
            );
          }
          return http.Response('Not found', 404);
        }),
      );
      await expectLater(
        () => service.join('stale', 'token'),
        throwsA(isA<FamilyServiceException>()
            .having((e) => e.statusCode, 'statusCode', 401)),
      );
    });

    test('createInvite refreshes and retries once on a 401 expired bearer',
        () async {
      final authHeadersSeen = <String>[];
      var refreshCalls = 0;
      final service = FamilyService(
        httpClient: MockClient((request) async {
          if (request.url.path.endsWith('/families/invite')) {
            authHeadersSeen.add(request.headers['Authorization'] ?? '');
            if (authHeadersSeen.length == 1) {
              return http.Response(
                  jsonEncode({'detail': 'Invalid or expired token'}), 401);
            }
            return http.Response(
              jsonEncode({
                'invite_url': 'http://localhost:3000/auth/join-family?token=abc',
                'expires_at': '2026-09-14T00:00:00+00:00',
              }),
              200,
            );
          }
          return http.Response('Not found', 404);
        }),
      );
      final invite = await service.createInvite(
        'fam-1',
        'stale-token',
        refresh: () async {
          refreshCalls++;
          return 'fresh-token';
        },
      );
      expect(refreshCalls, 1);
      expect(authHeadersSeen, ['Bearer stale-token', 'Bearer fresh-token']);
      expect(invite.inviteUrl, contains('token=abc'));
    });

    test('reset clears everything', () async {
      final service = FamilyService(httpClient: _familyOfTwoClient());
      await service.fetchFamilies('token');
      expect(service.isLoaded, true);

      service.reset();
      expect(service.isLoaded, false);
      expect(service.families, isEmpty);
    });
  });
}
