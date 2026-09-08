import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:ondoway/services/family_service.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:share_plus/share_plus.dart';

/// The profile page's "Your family" block (docs/adr/0005): the members of the
/// caller's family, a create button while there is none, and an invite that
/// renders as a shareable link AND a QR — the QR serves the family standing in
/// the same room, the link travels over any channel.
///
/// Pure presentation: the data and the two actions arrive as parameters, the
/// wiring to FamilyService/AuthService lives on the profile page.
class FamilySection extends StatelessWidget {
  final List<FamilyInfo> families;
  final bool isLoaded;

  /// Why the load failed, when it did — renders as the error line plus a
  /// Retry instead of a loading state that can never resolve. A family that
  /// already loaded stays on screen even when a refetch later fails.
  final String? loadError;
  final Future<void> Function() onCreate;
  final Future<void> Function() onRetry;
  final Future<FamilyInvite> Function(String familyId) onInvite;

  const FamilySection({
    super.key,
    required this.families,
    required this.isLoaded,
    this.loadError,
    required this.onCreate,
    required this.onRetry,
    required this.onInvite,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Text('Your family', style: Theme.of(context).textTheme.titleMedium),
        ),
        const SizedBox(height: 8),
        if (!isLoaded && loadError != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(loadError!, style: const TextStyle(color: Colors.grey)),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: () => _guarded(context, onRetry),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
              ],
            ),
          )
        else if (!isLoaded)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16),
            child: Text('Loading…', style: TextStyle(color: Colors.grey)),
          )
        else if (families.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Share your days with the people you walk with.',
                  style: TextStyle(color: Colors.grey),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: () => _guarded(context, onCreate),
                  icon: const Icon(Icons.group_add_outlined),
                  label: const Text('Create your family'),
                ),
              ],
            ),
          )
        else
          _FamilyMembers(
            family: families.first,
            onInvite: () => _showInvite(context, families.first.familyId),
          ),
      ],
    );
  }

  Future<void> _guarded(BuildContext context, Future<void> Function() action) async {
    try {
      await action();
    } on FamilyServiceException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Future<void> _showInvite(BuildContext context, String familyId) async {
    final FamilyInvite invite;
    try {
      invite = await onInvite(familyId);
    } on FamilyServiceException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
      return;
    }
    if (!context.mounted) return;
    await showDialog<void>(
      context: context,
      builder: (_) => FamilyInviteDialog(invite: invite),
    );
  }
}

class _FamilyMembers extends StatelessWidget {
  final FamilyInfo family;
  final VoidCallback onInvite;

  const _FamilyMembers({required this.family, required this.onInvite});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final member in family.members)
          ListTile(
            dense: true,
            leading: const Icon(Icons.person_outline),
            title: Text(member.displayName),
          ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: OutlinedButton.icon(
            onPressed: onInvite,
            icon: const Icon(Icons.qr_code_2_outlined),
            label: const Text('Invite'),
          ),
        ),
      ],
    );
  }
}

/// The invite, both ways at once: the link to share over any channel, and the
/// QR for the person standing next to you.
class FamilyInviteDialog extends StatelessWidget {
  final FamilyInvite invite;

  const FamilyInviteDialog({super.key, required this.invite});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Invite to your family'),
      // Scrollable so a short screen (or a large text scale) never overflows
      // the dialog — the QR alone is 200px tall.
      content: SingleChildScrollView(
        child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Tightly sized: QrImageView builds through a LayoutBuilder, which
          // cannot answer the dialog's intrinsic-size queries — the SizedBox
          // answers them itself.
          SizedBox(
            width: 200,
            height: 200,
            child: QrImageView(
              data: invite.inviteUrl,
              backgroundColor: Colors.white,
            ),
          ),
          const SizedBox(height: 16),
          SelectableText(
            invite.inviteUrl,
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          // A ~250-char URL is for SENDING, not selecting: the share sheet
          // reaches every channel the phone has, and Copy covers the rest.
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              TextButton.icon(
                onPressed: () => _copyLink(context),
                icon: const Icon(Icons.copy, size: 18),
                label: const Text('Copy'),
              ),
              const SizedBox(width: 8),
              TextButton.icon(
                onPressed: () => _shareLink(context),
                icon: const Icon(Icons.ios_share, size: 18),
                label: const Text('Share'),
              ),
            ],
          ),
          const Text(
            'The link works for 7 days.',
            style: TextStyle(color: Colors.grey, fontSize: 12),
          ),
        ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Done'),
        ),
      ],
    );
  }

  Future<void> _copyLink(BuildContext context) async {
    await Clipboard.setData(ClipboardData(text: invite.inviteUrl));
    if (context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Link copied')));
    }
  }

  Future<void> _shareLink(BuildContext context) async {
    // The iPad's share sheet is a popover and needs an anchor rectangle;
    // everywhere else the origin is simply ignored.
    final box = context.findRenderObject() as RenderBox?;
    await SharePlus.instance.share(ShareParams(
      uri: Uri.parse(invite.inviteUrl),
      sharePositionOrigin:
          box == null ? null : box.localToGlobal(Offset.zero) & box.size,
    ));
  }
}
