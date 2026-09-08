import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';

/// Lands the invite deep link (/auth/join-family?token=…) — the /auth/callback
/// precedent: redeem on arrival, then go where the result now lives (the
/// profile page's family section). An expired or tampered link is the server's
/// typed 401, shown as a plain ask-for-a-new-link message, never a stack of
/// jargon.
class JoinFamilyPage extends StatefulWidget {
  final String token;

  const JoinFamilyPage({super.key, required this.token});

  @override
  State<JoinFamilyPage> createState() => _JoinFamilyPageState();
}

class _JoinFamilyPageState extends State<JoinFamilyPage> {
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _join());
  }

  Future<void> _join() async {
    if (widget.token.isEmpty) {
      setState(() => _error = 'This invite link is missing its code.');
      return;
    }
    try {
      final auth = context.read<AuthService>();
      final familyService = context.read<FamilyService>();
      await familyService.join(
        widget.token,
        auth.accessToken!,
        refresh: () async => (await auth.refreshSession()) ? auth.accessToken : null,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('You joined the family')));
      context.go('/profile');
    } on FamilyServiceException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.statusCode == 401
            ? 'This invite has expired or is no longer valid. '
                'Ask for a new link and try again.'
            : e.message;
      });
    } catch (e) {
      if (mounted) setState(() => _error = 'Joining failed: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.error_outline,
                    size: 64, color: Theme.of(context).colorScheme.error),
                const SizedBox(height: 16),
                Text('Could not join',
                    style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 8),
                Text(_error!, textAlign: TextAlign.center),
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: () => context.go('/profile'),
                  child: const Text('Back to your profile'),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(),
            SizedBox(height: 16),
            Text('Joining the family…'),
          ],
        ),
      ),
    );
  }
}
