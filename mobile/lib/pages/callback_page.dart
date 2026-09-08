import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/session_bootstrap.dart';

class CallbackPage extends StatefulWidget {
  final String token;

  const CallbackPage({super.key, required this.token});

  @override
  State<CallbackPage> createState() => _CallbackPageState();
}

class _CallbackPageState extends State<CallbackPage> {
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _verifyToken());
  }

  Future<void> _verifyToken() async {
    if (widget.token.isEmpty) {
      setState(() => _error = 'No token provided');
      return;
    }

    try {
      final authService = context.read<AuthService>();
      await authService.verifyMagicLink(widget.token);

      if (!mounted) return;

      final profileService = context.read<ProfileService>();

      await loadSignedInSession(
        auth: authService,
        lenses: context.read<LensService>(),
        profile: profileService,
        family: context.read<FamilyService>(),
      );

      if (!mounted) return;
      context.go(profileService.isFirstTime ? '/onboarding' : '/explore');
    } on AuthException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'Sign-in failed: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.error_outline, size: 64, color: Theme.of(context).colorScheme.error),
              const SizedBox(height: 16),
              Text('Sign-in failed', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              Text(_error!, textAlign: TextAlign.center),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: () => context.go('/login'),
                child: const Text('Back to login'),
              ),
            ],
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
            Text('Signing you in...'),
          ],
        ),
      ),
    );
  }
}
