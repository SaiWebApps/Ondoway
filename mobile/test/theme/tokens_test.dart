import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ondoway/theme/tokens.dart';

void main() {
  test('light tokens match their hex values', () {
    const c = OndowayColors.light;
    expect(c.accent, const Color(0xFF2C6CC0));
    expect(c.bg, const Color(0xFFE9E5DB));
    expect(c.card, const Color(0xFFFFFFFF));
    expect(c.ink, const Color(0xFF20242C));
    expect(c.inkMute, const Color(0xFF5B6069));
    expect(c.spark, const Color(0xFFE8934A));
    expect(c.onAccent, const Color(0xFFFFFFFF));
  });

  test('dark tokens match their hex values', () {
    const c = OndowayColors.dark;
    expect(c.accent, const Color(0xFF7BB2F5));
    expect(c.bg, const Color(0xFF101218));
    expect(c.ink, const Color(0xFFF6F4F0));
    expect(c.onAccent, const Color(0xFF101218));
  });

  test('lerp(t=0) returns the start instance colors', () {
    const a = OndowayColors.light;
    const b = OndowayColors.dark;
    expect(a.lerp(b, 0).accent, a.accent);
  });
}
