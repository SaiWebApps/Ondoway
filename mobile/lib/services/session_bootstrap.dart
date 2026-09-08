import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';

/// Loads everything a signed-in session's screens read up front — the lens
/// taxonomy, the caller's profile, and the caller's family.
///
/// ONE landing, four doors: the cold start (main), the magic-link callback,
/// and the Google and Apple sign-in handlers all arrive here, so every door
/// delivers the same session — including the family section, which otherwise
/// only the next cold start would fill. The family fetch never throws (it
/// records a retryable [FamilyService.loadError] instead), so a family
/// hiccup cannot break sign-in.
Future<void> loadSignedInSession({
  required AuthService auth,
  required LensService lenses,
  required ProfileService profile,
  required FamilyService family,
}) async {
  await Future.wait([
    if (!lenses.isLoaded) lenses.fetchLenses(),
    profile.fetchProfile(auth.accessToken!),
    family.fetchFamilies(
      auth.accessToken!,
      refresh: () async =>
          (await auth.refreshSession()) ? auth.accessToken : null,
    ),
  ]);
}
