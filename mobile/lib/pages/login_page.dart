import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:provider/provider.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:ondoway/services/lens_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/session_bootstrap.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/theme/tokens.dart';

/// Sign in — editorial: photographic Paris hero with the wordmark + tagline,
/// a bone panel that slides up holding magic-link + social sign-in.
/// (Wireframe screen 01.)
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _emailController = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  bool _magicLinkSent = false;
  String? _errorMessage;

  @override
  void dispose() {
    _emailController.dispose();
    super.dispose();
  }

  Future<void> _sendMagicLink() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _errorMessage = null);

    try {
      await context.read<AuthService>().requestMagicLink(
            _emailController.text.trim(),
          );
      setState(() => _magicLinkSent = true);
    } on AuthException catch (e) {
      setState(() => _errorMessage = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final authService = context.watch<AuthService>();
    final c = Theme.of(context).extension<OndowayColors>()!;

    return Scaffold(
      // The way in to the on-device proof harnesses, debug builds only. They are
      // how the locked-phone-in-a-pocket walk gets done, and a route you can
      // only reach by typing an address is a route nobody walks with. The
      // location-spike button main also carried is gone with its page: the
      // finding that spike produced is now permanent in native_audio_backend
      // and the AppDelegate, so the harness had nothing left to prove.
      floatingActionButton: !kReleaseMode
          ? Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                FloatingActionButton.extended(
                  heroTag: 'debug-tour-pin-proof',
                  onPressed: () => context.push('/debug/tour-pin-proof'),
                  icon: const Icon(Icons.place_outlined),
                  label: const Text('Pin Proof'),
                ),
                const SizedBox(height: Dims.spaceSm),
                FloatingActionButton.extended(
                  heroTag: 'debug-tour-playback-proof',
                  onPressed: () => context.push('/debug/tour-playback-proof'),
                  icon: const Icon(Icons.headphones),
                  label: const Text('Tour Proof'),
                ),
              ],
            )
          : null,
      body: Stack(
        children: [
          // Full-bleed photographic ground.
          Positioned.fill(
            child: Image.asset(
              'assets/images/paris.jpg',
              fit: BoxFit.cover,
              errorBuilder: (context, error, stack) => ColoredBox(color: c.accentDeep),
            ),
          ),
          const Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.black45, Colors.transparent, Colors.black26],
                  stops: [0.0, 0.4, 1.0],
                ),
              ),
            ),
          ),
          SafeArea(
            // Bottom is handled inside _Panel so its background runs flush to
            // the screen edge (no square corners peeking over the photo).
            bottom: false,
            child: Column(
              children: [
                // Hero region over the photo: wordmark + editorial tagline.
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.all(Dims.spaceLg),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const _Wordmark(),
                        const Spacer(),
                        RichText(
                          text: TextSpan(
                            style: _fraunces(Colors.white, 40, FontWeight.w600),
                            children: [
                              const TextSpan(text: 'Every street\n'),
                              TextSpan(
                                text: 'has a story.',
                                style: _fraunces(
                                    c.accentLight, 40, FontWeight.w500,
                                    style: FontStyle.italic),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                // Bone panel slides up over the photo, holding sign-in.
                _Panel(
                  c: c,
                  child: _magicLinkSent
                      ? _buildCheckEmail(c)
                      : _buildLoginForm(authService, c),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// A family-invite tap bounced to this screen: the destination is stashed
  /// (router.dart's redirect), and the panel SAYS so — the four-screen walk
  /// to the join must not be silent about why signing in is worth it.
  bool get _hasPendingJoin {
    final pending = context.read<AuthService>().pendingDestination;
    return pending != null && Uri.parse(pending).path == '/auth/join-family';
  }

  Widget _buildLoginForm(AuthService authService, OndowayColors c) {
    return Form(
      key: _formKey,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('WELCOME', style: _eyebrow(c)),
          const SizedBox(height: Dims.spaceSm),
          Text('Let’s take a walk', style: _fraunces(c.ink, 26, FontWeight.w600)),
          if (_hasPendingJoin) ...[
            const SizedBox(height: Dims.spaceSm),
            Text(
              'Sign in to join your family — your invite is saved.',
              style: TextStyle(
                fontFamily: 'Space Grotesk',
                fontSize: 14,
                color: c.accentDeep,
              ),
            ),
          ],
          const SizedBox(height: Dims.spaceLg),
          TextFormField(
            controller: _emailController,
            keyboardType: TextInputType.emailAddress,
            autocorrect: false,
            style: const TextStyle(fontFamily: 'Space Grotesk'),
            decoration: _fieldDecoration(c, 'Email address', Icons.mail_outline),
            validator: (value) {
              if (value == null || value.trim().isEmpty) {
                return 'Please enter your email';
              }
              if (!value.contains('@') || !value.contains('.')) {
                return 'Please enter a valid email';
              }
              return null;
            },
          ),
          const SizedBox(height: Dims.spaceMd),
          FilledButton(
            onPressed: authService.isLoading ? null : _sendMagicLink,
            child: authService.isLoading
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                : const Text('Send magic link'),
          ),
          if (_errorMessage != null) ...[
            const SizedBox(height: Dims.spaceSm),
            Text(
              _errorMessage!,
              style: TextStyle(
                  fontFamily: 'Space Grotesk',
                  color: Theme.of(context).colorScheme.error),
              textAlign: TextAlign.center,
            ),
          ],
          const SizedBox(height: Dims.spaceLg),
          _OrDivider(c: c),
          const SizedBox(height: Dims.spaceLg),
          OutlinedButton.icon(
            onPressed: authService.isLoading ? null : _handleGoogleSignIn,
            icon: const Icon(Icons.g_mobiledata, size: 26),
            label: const Text('Continue with Google'),
          ),
          if (defaultTargetPlatform == TargetPlatform.iOS) ...[
            const SizedBox(height: Dims.spaceSm),
            SignInWithAppleButton(
              style: SignInWithAppleButtonStyle.black,
              borderRadius: BorderRadius.circular(Dims.radiusPill),
              onPressed: authService.isLoading ? () {} : _handleAppleSignIn,
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildCheckEmail(OndowayColors c) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Center(
          child: Container(
            width: 64,
            height: 64,
            decoration: BoxDecoration(
              color: c.accent.withValues(alpha: 0.12),
              shape: BoxShape.circle,
            ),
            child: Icon(Icons.mark_email_read_outlined, size: 32, color: c.accent),
          ),
        ),
        const SizedBox(height: Dims.spaceLg),
        Text('Check your email',
            textAlign: TextAlign.center,
            style: _fraunces(c.ink, 26, FontWeight.w600)),
        const SizedBox(height: Dims.spaceSm),
        Text(
          'We sent a sign-in link to\n${_emailController.text.trim()}',
          textAlign: TextAlign.center,
          style: TextStyle(
              fontFamily: 'Space Grotesk', fontSize: 15, color: c.inkSoft, height: 1.35),
        ),
        const SizedBox(height: Dims.spaceLg),
        TextButton(
          onPressed: () => setState(() => _magicLinkSent = false),
          style: TextButton.styleFrom(foregroundColor: c.accent),
          child: const Text('Use a different email'),
        ),
      ],
    );
  }

  Future<void> _handleGoogleSignIn() async {
    setState(() => _errorMessage = null);

    try {
      final googleSignIn = GoogleSignIn(scopes: ['email']);
      final account = await googleSignIn.signIn();
      if (account == null) return; // user cancelled

      final auth = await account.authentication;
      final idToken = auth.idToken;
      if (idToken == null) {
        setState(() => _errorMessage = 'Failed to get Google credentials');
        return;
      }

      if (!mounted) return;
      await context.read<AuthService>().loginWithGoogle(idToken);

      if (!mounted) return;
      final authService = context.read<AuthService>();
      final profileService = context.read<ProfileService>();

      await loadSignedInSession(
        auth: authService,
        lenses: context.read<LensService>(),
        profile: profileService,
        family: context.read<FamilyService>(),
      );

      if (mounted) {
        context.go(
          signedInLandingRoute(auth: authService, profile: profileService),
        );
      }
    } on AuthException catch (e) {
      setState(() => _errorMessage = e.message);
    } catch (e) {
      setState(() => _errorMessage = 'Google sign-in failed: $e');
    }
  }

  Future<void> _handleAppleSignIn() async {
    setState(() => _errorMessage = null);

    try {
      if (!mounted) return;
      await context.read<AuthService>().loginWithApple();

      if (!mounted) return;
      final authService = context.read<AuthService>();
      final profileService = context.read<ProfileService>();

      await loadSignedInSession(
        auth: authService,
        lenses: context.read<LensService>(),
        profile: profileService,
        family: context.read<FamilyService>(),
      );

      if (mounted) {
        context.go(
          signedInLandingRoute(auth: authService, profile: profileService),
        );
      }
    } on AuthException catch (e) {
      setState(() => _errorMessage = e.message);
    } catch (e) {
      setState(() => _errorMessage = 'Apple sign-in failed: $e');
    }
  }
}

TextStyle _fraunces(Color color, double size, FontWeight weight,
        {FontStyle style = FontStyle.normal}) =>
    TextStyle(
        fontFamily: 'Fraunces',
        color: color,
        fontSize: size,
        fontWeight: weight,
        fontStyle: style,
        height: 1.05);

TextStyle _eyebrow(OndowayColors c) => TextStyle(
      fontFamily: 'Space Mono',
      color: c.accent,
      fontSize: 12,
      fontWeight: FontWeight.w700,
      letterSpacing: 2.0,
    );

InputDecoration _fieldDecoration(OndowayColors c, String label, IconData icon) {
  OutlineInputBorder border(Color color, double width) => OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: color, width: width),
      );
  return InputDecoration(
    labelText: label,
    labelStyle: TextStyle(fontFamily: 'Space Grotesk', color: c.inkMute),
    filled: true,
    fillColor: c.card,
    prefixIcon: Icon(icon, color: c.inkMute, size: 20),
    contentPadding: const EdgeInsets.symmetric(horizontal: Dims.spaceMd, vertical: Dims.spaceMd),
    enabledBorder: border(c.line, 1),
    focusedBorder: border(c.accent, 1.6),
  );
}

class _Wordmark extends StatelessWidget {
  const _Wordmark();

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).extension<OndowayColors>()!;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.public, color: c.accentLight, size: 22),
        const SizedBox(width: Dims.spaceSm),
        const Text(
          'ondoway',
          style: TextStyle(
            fontFamily: 'Space Grotesk',
            color: Colors.white,
            fontSize: 20,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.5,
          ),
        ),
      ],
    );
  }
}

class _Panel extends StatelessWidget {
  final OndowayColors c;
  final Widget child;
  const _Panel({required this.c, required this.child});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: c.bg,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        boxShadow: Dims.liftLarge,
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
              Dims.spaceLg, Dims.spaceLg, Dims.spaceLg, Dims.spaceMd),
          child: child,
        ),
      ),
    );
  }
}

class _OrDivider extends StatelessWidget {
  final OndowayColors c;
  const _OrDivider({required this.c});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(child: Divider(color: c.line)),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: Dims.spaceMd),
          child: Text('or',
              style: TextStyle(
                  fontFamily: 'Space Mono', color: c.inkMute, fontSize: 12)),
        ),
        Expanded(child: Divider(color: c.line)),
      ],
    );
  }
}
