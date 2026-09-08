import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/trip_service.dart';

class SavedTripsPage extends StatefulWidget {
  const SavedTripsPage({super.key});

  @override
  State<SavedTripsPage> createState() => _SavedTripsPageState();
}

class _SavedTripsPageState extends State<SavedTripsPage> {
  bool _fetching = false;
  String? _fetchError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _fetchFromServer());
  }

  /// The tab's list is the SERVER's list — GET /trips returns every trip the
  /// caller captains or crews (ADR 0005), so a day someone else planned for
  /// the family appears here without this phone ever generating anything.
  /// The in-memory list filled by the local generate flow is only the
  /// starting picture until this answers.
  Future<void> _fetchFromServer() async {
    final profileId = context.read<ProfileService>().profileId;
    final accessToken = context.read<AuthService>().accessToken;
    if (profileId == null || accessToken == null) return;

    setState(() {
      _fetching = true;
      _fetchError = null;
    });
    try {
      await context.read<TripService>().fetchSavedTrips(profileId, accessToken);
    } catch (_) {
      if (mounted) setState(() => _fetchError = 'Could not load your trips.');
    } finally {
      if (mounted) setState(() => _fetching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;
    final tripService = context.watch<TripService>();
    final trips = tripService.savedTrips;

    if (trips.isEmpty && _fetching) {
      return const SafeArea(child: Center(child: CircularProgressIndicator()));
    }
    if (trips.isEmpty && _fetchError != null) {
      return SafeArea(
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _fetchError!,
                style: textTheme.bodyMedium?.copyWith(
                  color: colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: _fetchFromServer,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
            ],
          ),
        ),
      );
    }

    return SafeArea(
      child: trips.isEmpty
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.luggage_outlined,
                      size: 64,
                      color: colorScheme.onSurfaceVariant,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      'No saved trips yet',
                      style: textTheme.titleLarge?.copyWith(
                        color: colorScheme.onSurface,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'Generate a trip from the Explore tab to get started.',
                      style: textTheme.bodyMedium?.copyWith(
                        color: colorScheme.onSurfaceVariant,
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            )
          : ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: trips.length,
              itemBuilder: (context, index) {
                final trip = trips[index];
                return Dismissible(
                  key: Key(trip.tripId),
                  direction: DismissDirection.endToStart,
                  confirmDismiss: (direction) async {
                    return await showDialog<bool>(
                      context: context,
                      builder: (ctx) => AlertDialog(
                        title: const Text('Delete trip?'),
                        content: Text(
                          'Remove "${trip.tripName}" from your saved trips?',
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.of(ctx).pop(false),
                            child: const Text('Cancel'),
                          ),
                          TextButton(
                            onPressed: () => Navigator.of(ctx).pop(true),
                            child: const Text('Delete'),
                          ),
                        ],
                      ),
                    );
                  },
                  onDismissed: (_) => tripService.deleteTrip(trip.tripId),
                  background: Container(
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: 24),
                    decoration: BoxDecoration(
                      color: colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Icon(
                      Icons.delete,
                      color: colorScheme.onErrorContainer,
                    ),
                  ),
                  child: Card(
                    margin: const EdgeInsets.symmetric(vertical: 6),
                    child: ListTile(
                      onTap: () => context.push('/trip/${trip.tripId}'),
                      leading: CircleAvatar(
                        backgroundColor: colorScheme.primaryContainer,
                        child: Icon(
                          Icons.map_outlined,
                          color: colorScheme.onPrimaryContainer,
                        ),
                      ),
                      title: Text(
                        trip.tripName,
                        style: textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w600,
                          color: colorScheme.onSurface,
                        ),
                      ),
                      subtitle: Text(
                        '${trip.totalStops} stops • ${trip.totalDurationMin} min',
                        style: textTheme.bodySmall?.copyWith(
                          color: colorScheme.onSurfaceVariant,
                        ),
                      ),
                      trailing: Icon(
                        Icons.chevron_right,
                        color: colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                );
              },
            ),
    );
  }
}
