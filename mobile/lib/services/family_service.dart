import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

/// One member of a family, as GET /families/mine lists them.
class FamilyMember {
  final String profileId;
  final String displayName;
  const FamilyMember({required this.profileId, required this.displayName});

  factory FamilyMember.fromJson(Map<String, dynamic> json) => FamilyMember(
        profileId: json['profile_id'] as String,
        displayName: (json['display_name'] as String?) ?? '',
      );
}

/// One family the caller belongs to, with its members.
class FamilyInfo {
  final String familyId;
  final String? name;
  final List<FamilyMember> members;
  const FamilyInfo(
      {required this.familyId, required this.name, required this.members});

  factory FamilyInfo.fromJson(Map<String, dynamic> json) => FamilyInfo(
        familyId: json['family_id'] as String,
        name: json['name'] as String?,
        members: (json['members'] as List<dynamic>? ?? [])
            .map((m) => FamilyMember.fromJson(m as Map<String, dynamic>))
            .toList(),
      );
}

/// The minted invite: the shareable link (also rendered as a QR) and when it
/// stops working.
class FamilyInvite {
  final String inviteUrl;
  final String expiresAt;
  const FamilyInvite({required this.inviteUrl, required this.expiresAt});
}

/// The family surface's client — the ProfileService mould: dart-defined base
/// URL, Bearer on every call, and one refresh-and-retry on an expired token.
class FamilyService extends ChangeNotifier {
  static const _apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  final http.Client _httpClient;

  List<FamilyInfo> _families = [];
  bool _isLoaded = false;

  FamilyService({http.Client? httpClient})
      : _httpClient = httpClient ?? http.Client();

  List<FamilyInfo> get families => List.unmodifiable(_families);
  bool get isLoaded => _isLoaded;

  /// The family the screen shows: a person is usually in one. Null = none yet.
  FamilyInfo? get family => _families.isEmpty ? null : _families.first;

  /// The 60-minute access token can expire mid-session; [refresh] supplies a
  /// fresh one on a 401/403 and the request is retried exactly once — the
  /// ProfileService.completeOnboarding contract, shared by every call here.
  Future<http.Response> _withAuthRetry(
    Future<http.Response> Function(String token) send,
    String accessToken,
    Future<String?> Function()? refresh,
  ) async {
    var resp = await send(accessToken);
    if ((resp.statusCode == 401 || resp.statusCode == 403) && refresh != null) {
      final freshToken = await refresh();
      if (freshToken != null) {
        resp = await send(freshToken);
      }
    }
    return resp;
  }

  /// Loads the caller's families from GET /families/mine.
  ///
  /// 401/403 = auth failure — left unloaded so the auth layer can refresh and
  /// retry (the fetchProfile contract). Any other non-200 throws; an empty
  /// `families` list is a real, loaded answer ("no family yet").
  Future<void> fetchFamilies(
    String accessToken, {
    Future<String?> Function()? refresh,
  }) async {
    final resp = await _withAuthRetry(
      (token) => _httpClient.get(
        Uri.parse('$_apiBaseUrl/families/mine'),
        headers: {'Authorization': 'Bearer $token'},
      ),
      accessToken,
      refresh,
    );

    if (resp.statusCode == 401 || resp.statusCode == 403) {
      return;
    }
    if (resp.statusCode != 200) {
      throw FamilyServiceException(
        'Could not load your family: ${resp.body}',
        statusCode: resp.statusCode,
      );
    }

    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    _families = (data['families'] as List<dynamic>)
        .map((f) => FamilyInfo.fromJson(f as Map<String, dynamic>))
        .toList();
    _isLoaded = true;
    notifyListeners();
  }

  /// Creates a family with the caller's profile as its first member, then
  /// refetches so the screen shows the real membership. Returns the family id.
  Future<String> createFamily(
    String accessToken, {
    String? name,
    Future<String?> Function()? refresh,
  }) async {
    final resp = await _withAuthRetry(
      (token) => _httpClient.post(
        Uri.parse('$_apiBaseUrl/families'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $token',
        },
        body: jsonEncode({'name': name}),
      ),
      accessToken,
      refresh,
    );
    if (resp.statusCode != 201) {
      throw FamilyServiceException(
        'Could not create your family: ${resp.body}',
        statusCode: resp.statusCode,
      );
    }
    final familyId =
        (jsonDecode(resp.body) as Map<String, dynamic>)['family_id'] as String;
    await fetchFamilies(accessToken, refresh: refresh);
    return familyId;
  }

  /// Mints the shareable invite link for a family the caller is in.
  Future<FamilyInvite> createInvite(
    String familyId,
    String accessToken, {
    Future<String?> Function()? refresh,
  }) async {
    final resp = await _withAuthRetry(
      (token) => _httpClient.post(
        Uri.parse('$_apiBaseUrl/families/invite'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $token',
        },
        body: jsonEncode({'family_id': familyId}),
      ),
      accessToken,
      refresh,
    );
    if (resp.statusCode != 200) {
      throw FamilyServiceException(
        'Could not create an invite: ${resp.body}',
        statusCode: resp.statusCode,
      );
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    return FamilyInvite(
      inviteUrl: data['invite_url'] as String,
      expiresAt: data['expires_at'] as String,
    );
  }

  /// Redeems an invite token, then refetches the family so the joiner sees who
  /// they just joined. An expired/invalid invite is the server's typed 401 and
  /// surfaces as a [FamilyServiceException] with statusCode 401 — the page
  /// turns it into a plain "ask for a new link" message.
  Future<void> join(
    String inviteToken,
    String accessToken, {
    Future<String?> Function()? refresh,
  }) async {
    final resp = await _withAuthRetry(
      (token) => _httpClient.post(
        Uri.parse('$_apiBaseUrl/families/join'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $token',
        },
        body: jsonEncode({'token': inviteToken}),
      ),
      accessToken,
      refresh,
    );
    if (resp.statusCode != 200) {
      throw FamilyServiceException(
        'Could not join the family: ${resp.body}',
        statusCode: resp.statusCode,
      );
    }
    await fetchFamilies(accessToken, refresh: refresh);
  }

  void reset() {
    _families = [];
    _isLoaded = false;
    notifyListeners();
  }
}

class FamilyServiceException implements Exception {
  final String message;
  final int? statusCode;
  FamilyServiceException(this.message, {this.statusCode});

  @override
  String toString() => message;
}
