import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:ondoway/models/trip.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/theme/dims.dart';
import 'package:ondoway/theme/tokens.dart';
import 'package:provider/provider.dart';

/// Home / Explore — editorial: photographic hero, resume card, plan card.
class ExplorePage extends StatelessWidget {
  const ExplorePage({super.key});

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).extension<OndowayColors>()!;
    final text = Theme.of(context).textTheme;
    final displayName = context.watch<ProfileService>().displayName;
    final initial =
        (displayName != null && displayName.isNotEmpty) ? displayName[0].toUpperCase() : 'A';
    final savedTrips = context.watch<TripService>().savedTrips;
    final resume = savedTrips.isNotEmpty ? savedTrips.first : null;

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
            Dims.spaceLg, Dims.spaceMd, Dims.spaceLg, Dims.spaceXl),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _TopBar(c: c, text: text, initial: initial),
            const SizedBox(height: Dims.spaceMd),
            _Hero(c: c, onTakeTour: () => context.push('/tour-now/paris')),
            const SizedBox(height: Dims.spaceLg),
            if (resume != null) ...[
              Text('Pick up where you left off',
                  style: _fraunces(c.ink, 22, FontWeight.w600)),
              const SizedBox(height: Dims.spaceSm),
              _ResumeCard(
                c: c,
                trip: resume,
                onTap: () => context.push('/trip/${resume.tripId}'),
              ),
              const SizedBox(height: Dims.spaceMd),
            ],
            _PlanCard(c: c, onTap: () => context.push('/plan-trip/paris')),
          ],
        ),
      ),
    );
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

class _TopBar extends StatelessWidget {
  final OndowayColors c;
  final TextTheme text;
  final String initial;
  const _TopBar({required this.c, required this.text, required this.initial});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Icon(Icons.public, color: c.accent, size: 30),
        CircleAvatar(
          radius: 20,
          backgroundColor: c.accent,
          child: Text(initial,
              style: text.labelLarge?.copyWith(
                  color: c.onAccent, fontWeight: FontWeight.w700)),
        ),
      ],
    );
  }
}

class _Hero extends StatelessWidget {
  final OndowayColors c;
  final VoidCallback onTakeTour;
  const _Hero({required this.c, required this.onTakeTour});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(28),
      child: SizedBox(
        height: 400,
        child: Stack(
          fit: StackFit.expand,
          children: [
            Image.asset(
              'assets/images/paris.jpg',
              fit: BoxFit.cover,
              errorBuilder: (context, error, stack) =>
                  ColoredBox(color: c.accentDeep),
            ),
            const DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.transparent, Colors.black54, Colors.black87],
                  stops: [0.35, 0.75, 1.0],
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(Dims.spaceMd),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _LocationPill(c: c),
                  const Spacer(),
                  RichText(
                    text: TextSpan(
                      style: _fraunces(Colors.white, 36, FontWeight.w600),
                      children: [
                        const TextSpan(text: 'Start a walk,\n'),
                        TextSpan(
                          text: 'right here.',
                          style: _fraunces(
                              c.accentLight, 36, FontWeight.w500,
                              style: FontStyle.italic),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: Dims.spaceMd),
                  FilledButton(
                    onPressed: onTakeTour,
                    child: const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('Take a tour now'),
                        SizedBox(width: Dims.spaceSm),
                        Icon(Icons.arrow_forward, size: 20),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LocationPill extends StatelessWidget {
  final OndowayColors c;
  const _LocationPill({required this.c});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
          horizontal: Dims.spaceMd, vertical: Dims.spaceSm),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(Dims.radiusPill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(color: c.accentLight, shape: BoxShape.circle),
          ),
          const SizedBox(width: Dims.spaceSm),
          const Text('You are in Paris',
              style: TextStyle(
                  fontFamily: 'Space Grotesk',
                  color: Colors.white,
                  fontSize: 13,
                  fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

class _ResumeCard extends StatelessWidget {
  final OndowayColors c;
  final GeneratedTrip trip;
  final VoidCallback onTap;
  const _ResumeCard({required this.c, required this.trip, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: c.card,
      borderRadius: BorderRadius.circular(Dims.radiusCard),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(Dims.radiusCard),
        child: Padding(
          padding: const EdgeInsets.all(Dims.spaceMd),
          child: Column(
            children: [
              Row(
                children: [
                  Container(
                    width: 56,
                    height: 56,
                    decoration: BoxDecoration(
                      color: c.spark.withValues(alpha: 0.85),
                      borderRadius: BorderRadius.circular(Dims.spaceMd),
                    ),
                    child: const Icon(Icons.route, color: Colors.white),
                  ),
                  const SizedBox(width: Dims.spaceMd),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(trip.tripName,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: _fraunces(c.ink, 17, FontWeight.w600)),
                        const SizedBox(height: Dims.spaceXs),
                        Text(
                          '${trip.totalStops} stops · ${trip.totalDurationMin} min',
                          style: const TextStyle(
                              fontFamily: 'Space Grotesk', fontSize: 13),
                        ),
                      ],
                    ),
                  ),
                  Icon(Icons.chevron_right, color: c.inkMute),
                ],
              ),
              const SizedBox(height: Dims.spaceMd),
              ClipRRect(
                borderRadius: BorderRadius.circular(Dims.radiusPill),
                child: LinearProgressIndicator(
                  value: 0.4,
                  minHeight: 6,
                  backgroundColor: c.line,
                  valueColor: AlwaysStoppedAnimation<Color>(c.accent),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  final OndowayColors c;
  final VoidCallback onTap;
  const _PlanCard({required this.c, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: c.panel,
      borderRadius: BorderRadius.circular(Dims.radiusCard),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(Dims.radiusCard),
        child: Padding(
          padding: const EdgeInsets.all(Dims.spaceMd),
          child: Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: c.accent.withValues(alpha: 0.12),
                  shape: BoxShape.circle,
                ),
                child: Icon(Icons.add, color: c.accent),
              ),
              const SizedBox(width: Dims.spaceMd),
              Expanded(
                child: Text('Plan a tour for later',
                    style: _fraunces(c.ink, 17, FontWeight.w600)),
              ),
              Icon(Icons.chevron_right, color: c.inkMute),
            ],
          ),
        ),
      ),
    );
  }
}
