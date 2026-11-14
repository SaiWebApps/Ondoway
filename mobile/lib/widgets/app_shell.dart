import 'package:flutter/material.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/theme/tokens.dart';

/// App shell with the floating cobalt pill nav: Explore · Trips · Profile —
/// shell branch indices 0, 1, 2. The lens editor is a pushed
/// top-level route (reached from Profile / onboarding), not a tab. Feedback
/// moved to Profile.
class AppShell extends StatelessWidget {
  final int currentIndex; // shell BRANCH index (0..3)
  final Widget child;
  final void Function(int branchIndex) onTabChanged;

  const AppShell({
    super.key,
    required this.currentIndex,
    required this.child,
    required this.onTabChanged,
  });

  static const _items = <({IconData icon, String label, int branch})>[
    (icon: Icons.home_rounded, label: 'Explore', branch: 0),
    (icon: Icons.map_outlined, label: 'Trips', branch: 1),
    (icon: Icons.person_outline, label: 'Profile', branch: 2),
  ];

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).extension<OndowayColors>()!;
    return Scaffold(
      extendBody: true, // content scrolls under the floating pill
      body: child,
      bottomNavigationBar: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
              Dims.spaceLg, 0, Dims.spaceLg, Dims.spaceSm),
          child: Container(
            padding: const EdgeInsets.all(Dims.spaceXs),
            decoration: BoxDecoration(
              color: c.card,
              borderRadius: BorderRadius.circular(Dims.radiusPill),
              boxShadow: [
                BoxShadow(
                  color: c.ink.withValues(alpha: 0.12),
                  blurRadius: 24,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                for (final item in _items)
                  _NavItem(
                    icon: item.icon,
                    label: item.label,
                    selected: currentIndex == item.branch,
                    accent: c.accent,
                    onAccent: c.onAccent,
                    inactive: c.inkMute,
                    onTap: () => onTabChanged(item.branch),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final Color accent;
  final Color onAccent;
  final Color inactive;
  final VoidCallback onTap;

  const _NavItem({
    required this.icon,
    required this.label,
    required this.selected,
    required this.accent,
    required this.onAccent,
    required this.inactive,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(Dims.radiusPill),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        padding: EdgeInsets.symmetric(
          horizontal: selected ? Dims.spaceMd : Dims.spaceMd,
          vertical: Dims.spaceSm + 2,
        ),
        decoration: BoxDecoration(
          color: selected ? accent : Colors.transparent,
          borderRadius: BorderRadius.circular(Dims.radiusPill),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 22, color: selected ? onAccent : inactive),
            if (selected) ...[
              const SizedBox(width: Dims.spaceSm),
              Text(
                label,
                style: TextStyle(
                  fontFamily: 'Space Grotesk',
                  color: onAccent,
                  fontWeight: FontWeight.w600,
                  fontSize: 14,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
