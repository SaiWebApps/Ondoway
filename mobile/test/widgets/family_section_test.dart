import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/theme/theme.dart';
import 'package:ondoway/widgets/family_section.dart';
import 'package:qr_flutter/qr_flutter.dart';

Widget _host(Widget child) => MaterialApp(
      theme: buildOndowayTheme(Brightness.light),
      home: Scaffold(body: SingleChildScrollView(child: child)),
    );

const _familyOfTwo = FamilyInfo(
  familyId: 'fam-1',
  name: 'The Fionas',
  members: [
    FamilyMember(profileId: 'p-dev', displayName: 'Dev'),
    FamilyMember(profileId: 'p-fiona', displayName: 'Fiona'),
  ],
);

void main() {
  testWidgets('renders every member of the family', (tester) async {
    await tester.pumpWidget(_host(FamilySection(
      families: const [_familyOfTwo],
      isLoaded: true,
      onCreate: () async {},
      onRetry: () async {},
      onInvite: (_) async =>
          const FamilyInvite(inviteUrl: 'http://x/auth/join-family?token=t', expiresAt: ''),
    )));

    expect(find.text('Your family'), findsOneWidget);
    expect(find.text('Fiona'), findsOneWidget);
    expect(find.text('Dev'), findsOneWidget);
  });

  testWidgets('no family yet shows the create button and calls onCreate',
      (tester) async {
    var created = 0;
    await tester.pumpWidget(_host(FamilySection(
      families: const [],
      isLoaded: true,
      onCreate: () async => created++,
      onRetry: () async {},
      onInvite: (_) async =>
          const FamilyInvite(inviteUrl: 'http://x', expiresAt: ''),
    )));

    final create = find.text('Create your family');
    expect(create, findsOneWidget);
    await tester.tap(create);
    await tester.pump();
    expect(created, 1);
  });

  testWidgets('invite opens a dialog with the link and its QR', (tester) async {
    // The dialog holds a 200px QR plus the link text; give the test surface
    // the height a phone has.
    await tester.binding.setSurfaceSize(const Size(800, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final invitedFamilies = <String>[];
    await tester.pumpWidget(_host(FamilySection(
      families: const [_familyOfTwo],
      isLoaded: true,
      onCreate: () async {},
      onRetry: () async {},
      onInvite: (familyId) async {
        invitedFamilies.add(familyId);
        return const FamilyInvite(
          inviteUrl: 'http://localhost:3000/auth/join-family?token=abc',
          expiresAt: '2026-09-14T00:00:00+00:00',
        );
      },
    )));

    await tester.tap(find.text('Invite'));
    await tester.pumpAndSettle();

    expect(invitedFamilies, ['fam-1']);
    expect(
      find.text('http://localhost:3000/auth/join-family?token=abc'),
      findsOneWidget,
    );
    expect(find.byType(QrImageView), findsOneWidget);
  });

  testWidgets('still loading (no error) shows the loading line', (tester) async {
    await tester.pumpWidget(_host(FamilySection(
      families: const [],
      isLoaded: false,
      onCreate: () async {},
      onRetry: () async {},
      onInvite: (_) async =>
          const FamilyInvite(inviteUrl: 'http://x', expiresAt: ''),
    )));

    expect(find.text('Loading…'), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
  });

  testWidgets('a failed load shows the error with a Retry that calls onRetry',
      (tester) async {
    var retried = 0;
    await tester.pumpWidget(_host(FamilySection(
      families: const [],
      isLoaded: false,
      loadError: 'Could not load your family.',
      onCreate: () async {},
      onRetry: () async => retried++,
      onInvite: (_) async =>
          const FamilyInvite(inviteUrl: 'http://x', expiresAt: ''),
    )));

    // Never a bare "Loading…" over a failure: the error and the way out.
    expect(find.text('Loading…'), findsNothing);
    expect(find.text('Could not load your family.'), findsOneWidget);

    await tester.tap(find.text('Retry'));
    await tester.pump();
    expect(retried, 1);
  });

  testWidgets('a loaded family stays on screen even when a refetch fails',
      (tester) async {
    await tester.pumpWidget(_host(FamilySection(
      families: const [_familyOfTwo],
      isLoaded: true,
      loadError: 'Could not load your family.',
      onCreate: () async {},
      onRetry: () async {},
      onInvite: (_) async =>
          const FamilyInvite(inviteUrl: 'http://x', expiresAt: ''),
    )));

    expect(find.text('Fiona'), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
  });
}
