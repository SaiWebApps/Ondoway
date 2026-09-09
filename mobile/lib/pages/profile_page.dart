import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/widgets/family_section.dart';
import 'package:ondoway/widgets/feedback_overlay.dart';

class ProfilePage extends StatelessWidget {
  const ProfilePage({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    final profile = context.watch<ProfileService>();
    final lensService = context.watch<LensService>();
    final familyService = context.watch<FamilyService>();

    Future<String?> refreshBearer() async =>
        (await auth.refreshSession()) ? auth.accessToken : null;

    final selectedLensNames = lensService.allLenses
        .where((l) => profile.selectedLensIds.contains(l.id))
        .map((l) => l.displayLabel)
        .toList()
      ..sort();

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
        children: [
          const SizedBox(height: 8),
          Center(
            child: CircleAvatar(
              radius: 40,
              backgroundColor: Theme.of(context).colorScheme.primaryContainer,
              child: Text(
                _initials(profile.displayName, auth.userEmail),
                style: TextStyle(
                  fontSize: 28,
                  color: Theme.of(context).colorScheme.onPrimaryContainer,
                ),
              ),
            ),
          ),
          const SizedBox(height: 24),
          _DisplayNameTile(
            displayName: profile.displayName ?? '',
            onSave: (name) async {
              await profile.updateDisplayName(name, auth.accessToken!);
            },
          ),
          const SizedBox(height: 8),
          ListTile(
            leading: const Icon(Icons.email_outlined),
            title: const Text('Email'),
            subtitle: Text(auth.userEmail ?? ''),
          ),
          ListTile(
            leading: const Icon(Icons.feedback_outlined),
            title: const Text('Send feedback'),
            onTap: () => showModalBottomSheet<void>(
              context: context,
              isScrollControlled: true,
              useSafeArea: true,
              builder: (_) => const FeedbackSheet(),
            ),
          ),
          // The opening times this app speaks are the map's, and its licence is
          // share-alike: the credit travels with the words (Docs/adr/0006).
          const ListTile(
            key: Key('profile-hours-credit'),
            leading: Icon(Icons.schedule_outlined),
            title: Text('Opening hours © OpenStreetMap contributors'),
          ),
          const Divider(height: 32),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Your lenses',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                TextButton(
                  onPressed: () => context.push('/lenses'),
                  child: const Text('Edit'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          if (selectedLensNames.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: Text('No lenses selected', style: TextStyle(color: Colors.grey)),
            )
          else
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: selectedLensNames
                    .map((name) => Chip(label: Text(name)))
                    .toList(),
              ),
            ),
          const SizedBox(height: 32),
          const Divider(height: 32),
          FamilySection(
            families: familyService.families,
            isLoaded: familyService.isLoaded,
            loadError: familyService.loadError,
            onCreate: () async {
              await familyService.createFamily(
                auth.accessToken!,
                refresh: refreshBearer,
              );
            },
            onRetry: () => familyService.fetchFamilies(
              auth.accessToken!,
              refresh: refreshBearer,
            ),
            onInvite: (familyId) => familyService.createInvite(
              familyId,
              auth.accessToken!,
              refresh: refreshBearer,
            ),
          ),
          const SizedBox(height: 32),
          const Divider(height: 32),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Text('Appearance', style: Theme.of(context).textTheme.titleMedium),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'system', label: Text('System'), icon: Icon(Icons.brightness_auto)),
                ButtonSegment(value: 'light', label: Text('Light'), icon: Icon(Icons.light_mode)),
                ButtonSegment(value: 'dark', label: Text('Dark'), icon: Icon(Icons.dark_mode)),
              ],
              selected: {profile.themePreference ?? 'system'},
              onSelectionChanged: (selected) {
                profile.updateThemePreference(selected.first, auth.accessToken!).catchError((e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to save theme: $e')));
                  }
                });
              },
            ),
          ),
          const SizedBox(height: 32),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: OutlinedButton.icon(
              onPressed: () {
                auth.logout();
                profile.reset();
                familyService.reset();
                context.go('/login');
              },
              icon: const Icon(Icons.logout),
              label: const Text('Log out'),
              style: OutlinedButton.styleFrom(
                foregroundColor: Theme.of(context).colorScheme.error,
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _initials(String? displayName, String? email) {
    final source = displayName ?? email ?? '?';
    final parts = source.trim().split(RegExp(r'\s+'));
    if (parts.length >= 2) {
      return '${parts[0][0]}${parts[1][0]}'.toUpperCase();
    }
    return source.isNotEmpty ? source[0].toUpperCase() : '?';
  }
}

class _DisplayNameTile extends StatefulWidget {
  final String displayName;
  final Future<void> Function(String) onSave;

  const _DisplayNameTile({required this.displayName, required this.onSave});

  @override
  State<_DisplayNameTile> createState() => _DisplayNameTileState();
}

class _DisplayNameTileState extends State<_DisplayNameTile> {
  bool _editing = false;
  late TextEditingController _controller;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.displayName);
  }

  @override
  void didUpdateWidget(_DisplayNameTile old) {
    super.didUpdateWidget(old);
    if (!_editing && old.displayName != widget.displayName) {
      _controller.text = widget.displayName;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_editing) {
      return ListTile(
        leading: const Icon(Icons.person_outline),
        title: TextField(
          controller: _controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'Display name',
            border: UnderlineInputBorder(),
          ),
          onSubmitted: (_) => _save(),
        ),
        trailing: _saving
            ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2))
            : IconButton(
                onPressed: _save,
                icon: const Icon(Icons.check),
              ),
      );
    }

    return ListTile(
      leading: const Icon(Icons.person_outline),
      title: const Text('Display name'),
      subtitle: Text(widget.displayName.isNotEmpty ? widget.displayName : 'Not set'),
      trailing: IconButton(
        onPressed: () => setState(() => _editing = true),
        icon: const Icon(Icons.edit_outlined, size: 20),
      ),
    );
  }

  Future<void> _save() async {
    final name = _controller.text.trim();
    if (name.isEmpty || name == widget.displayName) {
      setState(() => _editing = false);
      return;
    }
    setState(() => _saving = true);
    try {
      await widget.onSave(name);
      if (mounted) setState(() => _editing = false);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to save: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }
}
