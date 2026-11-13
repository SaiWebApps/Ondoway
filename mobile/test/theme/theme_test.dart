import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/theme/theme.dart';
import 'package:ondoway/theme/tokens.dart';

void main() {
  test('light theme carries the OndowayColors extension with v10 accent', () {
    final t = buildOndowayTheme(Brightness.light);
    final c = t.extension<OndowayColors>();
    expect(c, isNotNull);
    expect(c!.accent, const Color(0xFF2C6CC0));
    expect(t.brightness, Brightness.light);
  });

  test('dark theme uses the dark token set', () {
    final t = buildOndowayTheme(Brightness.dark);
    expect(t.extension<OndowayColors>()!.bg, const Color(0xFF101218));
  });

  testWidgets(
      'type scale renders the Material 3 hierarchy at runtime (display > body)',
      (tester) async {
    // The size-less TextStyles do not collapse every role to ~14px:
    // M3 sizes live in the Typography *geometry*, which the Theme
    // widget merges into the text theme at runtime (null fontSize -> role size).
    // This asserts the real, rendered hierarchy via Theme.of(context).
    late TextTheme rt;
    await tester.pumpWidget(MaterialApp(
      theme: buildOndowayTheme(Brightness.light),
      home: Builder(builder: (ctx) {
        rt = Theme.of(ctx).textTheme;
        return const SizedBox();
      }),
    ));
    expect(rt.displayLarge!.fontSize, greaterThan(rt.bodyLarge!.fontSize!));
    expect(rt.displayLarge!.fontFamily, 'Fraunces');
    expect(rt.bodyLarge!.fontFamily, 'Space Grotesk');
  });

  test('buttons are pill-shaped (StadiumBorder)', () {
    final t = buildOndowayTheme(Brightness.light);
    final shape = t.filledButtonTheme.style?.shape?.resolve({});
    expect(shape, isA<StadiumBorder>());
  });
}
