import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:ondoway/models/lens.dart';

class LensService extends ChangeNotifier {
  static const _baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  final http.Client _httpClient;

  List<Lens> _allLenses = [];
  Map<String, List<Lens>> _childrenByParent = {};
  bool _isLoaded = false;

  LensService({http.Client? httpClient})
      : _httpClient = httpClient ?? http.Client();

  List<Lens> get allLenses => _allLenses;
  Map<String, List<Lens>> get childrenByParent => _childrenByParent;
  bool get isLoaded => _isLoaded;

  List<Lens> get selectableLenses =>
      _allLenses.where((l) => !l.isParent).toList();

  /// Loads the lens taxonomy from the public `GET /api/v1/lenses` endpoint.
  ///
  /// The endpoint already returns the hierarchy nested (a list of parent
  /// lenses, each with its own `children`), so this is a single call — no
  /// client-side join. The `/nodes/Lens` + `/edges/IS_PARENT_OF` pair is
  /// behind the workbench gate and answers 404 in production, so the app never
  /// calls it.
  Future<void> fetchLenses() async {
    final resp = await _httpClient.get(Uri.parse('$_baseUrl/lenses'));
    if (resp.statusCode != 200) {
      throw LensServiceException('Failed to fetch lenses: ${resp.body}');
    }

    final parents = jsonDecode(resp.body) as List<dynamic>;
    final allLenses = <Lens>[];
    final childrenByParent = <String, List<Lens>>{};

    for (final entry in parents) {
      final parentJson = entry as Map<String, dynamic>;
      final parent = Lens.fromApiJson(parentJson);
      allLenses.add(parent);

      final children = (parentJson['children'] as List<dynamic>? ?? const [])
          .map((c) => Lens.fromApiJson(c as Map<String, dynamic>))
          .toList();
      childrenByParent[parent.displayLabel] = children;
      allLenses.addAll(children);
    }

    _allLenses = allLenses;
    _childrenByParent = childrenByParent;
    _isLoaded = true;
    notifyListeners();
  }
}

class LensServiceException implements Exception {
  final String message;
  LensServiceException(this.message);

  @override
  String toString() => message;
}
