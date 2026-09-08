import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

class AuthService extends ChangeNotifier {
  static const _apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );
  static const _baseUrl = '$_apiBaseUrl/auth';
  static const _accessTokenKey = 'access_token';
  static const _refreshTokenKey = 'refresh_token';

  final FlutterSecureStorage _storage;
  final http.Client _httpClient;

  String? _accessToken;
  String? _userId;
  String? _userEmail;
  bool _isLoading = false;
  String? _pendingDestination;

  AuthService({
    FlutterSecureStorage? storage,
    http.Client? httpClient,
  })  : _storage = storage ?? const FlutterSecureStorage(),
        _httpClient = httpClient ?? http.Client();

  bool get isAuthenticated => _accessToken != null;
  String? get accessToken => _accessToken;
  String? get userId => _userId;
  String? get userEmail => _userEmail;
  bool get isLoading => _isLoading;

  /// Remembers where a signed-out deep link was headed (a family-invite tap)
  /// so the sign-in landing can resume it. Plain fields, deliberately silent:
  /// the router listens to this service, and a notify here would re-run the
  /// redirect that is setting it.
  void stashPendingDestination(String location) {
    _pendingDestination = location;
  }

  /// A read-only peek at the stashed destination, for screens that SAY a
  /// resume is coming (the login panel's invite line) without spending it —
  /// only [consumePendingDestination] hands it over.
  String? get pendingDestination => _pendingDestination;

  /// The stashed destination, handed over exactly once — null after, and
  /// after logout.
  String? consumePendingDestination() {
    final destination = _pendingDestination;
    _pendingDestination = null;
    return destination;
  }

  Future<void> tryRestoreSession() async {
    final token = await _storage.read(key: _accessTokenKey);
    if (token == null) return;

    _accessToken = token;
    try {
      await _fetchMe();
    } on AuthException catch (e) {
      if (e.statusCode == 401 || e.statusCode == 403) {
        final refreshed = await _tryRefresh();
        if (!refreshed) {
          await _clearTokens();
        }
      }
    } catch (_) {
      // Network errors, timeouts: leave tokens intact so next launch can retry.
    }
    notifyListeners();
  }

  /// Public reactive refresh: exchange the stored refresh token for a fresh
  /// access token. Returns true on success (a new access token is now in
  /// [accessToken] and storage), false if there is no refresh token or the
  /// exchange fails. Callers use this to recover from a mid-session 401 on an
  /// ordinary authenticated request, then retry that request once.
  Future<bool> refreshSession() => _tryRefresh();

  Future<bool> _tryRefresh() async {
    final refreshToken = await _storage.read(key: _refreshTokenKey);
    if (refreshToken == null) return false;

    try {
      final resp = await _httpClient.post(
        Uri.parse('$_baseUrl/refresh'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'refresh_token': refreshToken}),
      );

      if (resp.statusCode != 200) return false;

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      await _storeTokens(data['access_token'], data['refresh_token']);
      await _fetchMe();
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _clearTokens() async {
    _accessToken = null;
    _userId = null;
    _userEmail = null;
    _pendingDestination = null;
    await _storage.delete(key: _accessTokenKey);
    await _storage.delete(key: _refreshTokenKey);
  }

  Future<void> requestMagicLink(String email) async {
    _isLoading = true;
    notifyListeners();

    try {
      final resp = await _httpClient.post(
        Uri.parse('$_baseUrl/magic-link/request'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email}),
      );

      if (resp.statusCode != 200) {
        throw AuthException('Failed to send magic link: ${resp.body}');
      }
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> verifyMagicLink(String token) async {
    _isLoading = true;
    notifyListeners();

    try {
      final resp = await _httpClient.post(
        Uri.parse('$_baseUrl/magic-link/verify'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'token': token}),
      );

      if (resp.statusCode != 200) {
        throw AuthException('Magic link verification failed: ${resp.body}');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      await _storeTokens(data['access_token'], data['refresh_token']);
      await _fetchMe();
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> loginWithGoogle(String idToken) async {
    _isLoading = true;
    notifyListeners();

    try {
      final resp = await _httpClient.post(
        Uri.parse('$_baseUrl/google'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'id_token': idToken}),
      );

      if (resp.statusCode != 200) {
        throw AuthException('Google login failed: ${resp.body}');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      await _storeTokens(data['access_token'], data['refresh_token']);
      await _fetchMe();
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> loginWithApple() async {
    _isLoading = true;
    notifyListeners();

    try {
      final credential = await SignInWithApple.getAppleIDCredential(
        scopes: [
          AppleIDAuthorizationScopes.email,
          AppleIDAuthorizationScopes.fullName,
        ],
      );

      final identityToken = credential.identityToken;
      if (identityToken == null) {
        throw AuthException('Failed to get Apple credentials');
      }

      await _postAppleToken(identityToken);
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> loginWithAppleWithToken(String identityToken) async {
    _isLoading = true;
    notifyListeners();

    try {
      await _postAppleToken(identityToken);
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> _postAppleToken(String identityToken) async {
    final resp = await _httpClient.post(
      Uri.parse('$_baseUrl/apple'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'identity_token': identityToken}),
    );

    if (resp.statusCode != 200) {
      throw AuthException('Apple login failed: ${resp.body}');
    }

    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    await _storeTokens(data['access_token'], data['refresh_token']);
    await _fetchMe();
  }

  Future<void> logout() async {
    await _clearTokens();
    notifyListeners();
  }

  Future<void> _fetchMe() async {
    if (_accessToken == null) return;

    final resp = await _httpClient.get(
      Uri.parse('$_baseUrl/me'),
      headers: {'Authorization': 'Bearer $_accessToken'},
    );

    if (resp.statusCode != 200) {
      throw AuthException('Failed to fetch user: ${resp.body}',
          statusCode: resp.statusCode);
    }

    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    _userId = data['id'] as String?;
    _userEmail = data['email'] as String?;
    notifyListeners();
  }

  Future<void> _storeTokens(String accessToken, String refreshToken) async {
    _accessToken = accessToken;
    await _storage.write(key: _accessTokenKey, value: accessToken);
    await _storage.write(key: _refreshTokenKey, value: refreshToken);
  }
}

class AuthException implements Exception {
  final String message;
  final int? statusCode;
  AuthException(this.message, {this.statusCode});

  @override
  String toString() => message;
}
