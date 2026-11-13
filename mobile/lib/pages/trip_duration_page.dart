import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show visibleForTesting, kIsWeb;
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:ondoway/services/trip_service.dart';
import 'package:ondoway/services/auth_service.dart';
import 'package:geolocator/geolocator.dart';
import 'package:ondoway/services/location_service.dart';
import 'package:ondoway/services/profile_service.dart';
import 'package:ondoway/services/geocoding_service.dart';
import 'package:apple_maps_flutter/apple_maps_flutter.dart';

class TripDurationPage extends StatefulWidget {
  final String citySlug;

  @visibleForTesting
  final TimeOfDay? initialStartTime;
  @visibleForTesting
  final TimeOfDay? initialEndTime;

  const TripDurationPage({
    super.key,
    required this.citySlug,
    this.initialStartTime,
    this.initialEndTime,
  });

  @override
  State<TripDurationPage> createState() => _TripDurationPageState();
}

class _TripDurationPageState extends State<TripDurationPage>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  late DateTime _startDate;
  late TimeOfDay _startTime;
  late DateTime _endDate;
  late TimeOfDay _endTime;
  // Is the end time a booking or a guess? The one
  // question, asked at planning. Defaults to 'firm' — the owner's ruling: a
  // person who types an end time and skips the question gets a day that
  // finishes by it.
  String _endHardness = 'firm';
  bool _isLoading = false;
  String? _error;

  // Location state — resolved at init, consumed at generate time
  double? _pinLat;
  double? _pinLng;
  String _locationSource = 'pending'; // 'gps', 'city_center', 'pending', 'manual'
  bool _locationResolved = false;
  final _searchController = TextEditingController();
  AppleMapController? _mapController;

  static const _cityCoordinates = {
    'paris': (lat: 48.8566, lng: 2.3522),
  };

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    final now = DateTime.now();
    _startDate = DateTime(now.year, now.month, now.day + 1);
    _startTime = widget.initialStartTime ?? const TimeOfDay(hour: 9, minute: 0);
    _endDate = DateTime(now.year, now.month, now.day + 1);
    _endTime = widget.initialEndTime ?? const TimeOfDay(hour: 18, minute: 0);
    WidgetsBinding.instance.addPostFrameCallback((_) => _resolveLocation());
  }

  Future<void> _resolveLocation() async {
    final locationService = context.read<LocationService>();
    final position = await locationService.getCurrentPosition();
    if (!mounted) return;

    if (position != null) {
      final coords = _cityCoordinates[widget.citySlug];
      if (coords != null) {
        final distanceM = Geolocator.distanceBetween(
          position.latitude, position.longitude,
          coords.lat, coords.lng,
        );
        if (distanceM <= 50000) {
          setState(() {
            _pinLat = position.latitude;
            _pinLng = position.longitude;
            _locationSource = 'gps';
            _locationResolved = true;
          });
        } else {
          setState(() {
            _pinLat = coords.lat;
            _pinLng = coords.lng;
            _locationSource = 'city_center';
            _locationResolved = true;
          });
        }
      } else {
        setState(() {
          _pinLat = position.latitude;
          _pinLng = position.longitude;
          _locationSource = 'gps';
          _locationResolved = true;
        });
      }
    } else {
      final coords = _cityCoordinates[widget.citySlug];
      if (coords == null) {
        setState(() {
          _error = 'City not supported';
          _locationResolved = true;
        });
        return;
      }
      setState(() {
        _pinLat = coords.lat;
        _pinLng = coords.lng;
        _locationSource = 'city_center';
        _locationResolved = true;
      });
    }
  }

  void _onMapTap(LatLng position) {
    setState(() {
      _pinLat = position.latitude;
      _pinLng = position.longitude;
      _locationSource = 'manual';
    });
    _mapController?.animateCamera(
      CameraUpdate.newLatLng(position),
    );
  }

  static final _coordRegex = RegExp(
    r'^(-?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)$',
  );

  void _onSearchSubmitted(String value) {
    final match = _coordRegex.firstMatch(value.trim());
    if (match != null) {
      final lat = double.tryParse(match.group(1)!);
      final lng = double.tryParse(match.group(2)!);
      if (lat != null && lng != null && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180) {
        setState(() {
          _pinLat = lat;
          _pinLng = lng;
          _locationSource = 'manual';
          _locationResolved = true;
        });
        return;
      }
    }
    _geocodeQuery(value.trim());
  }

  Future<void> _geocodeQuery(String query) async {
    if (query.isEmpty) return;
    final result = await GeocodingService().search(query);
    if (!mounted || result == null) return;
    setState(() {
      _pinLat = result.lat;
      _pinLng = result.lng;
      _locationSource = 'manual';
      _locationResolved = true;
    });
  }

  @override
  void dispose() {
    _tabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  String get _cityDisplayName {
    switch (widget.citySlug) {
      case 'paris':
        return 'Paris';
      default:
        return widget.citySlug[0].toUpperCase() + widget.citySlug.substring(1);
    }
  }

  DateTime get _startDateTime => DateTime(
        _startDate.year, _startDate.month, _startDate.day,
        _startTime.hour, _startTime.minute);

  DateTime get _endDateTime => DateTime(
        _endDate.year, _endDate.month, _endDate.day,
        _endTime.hour, _endTime.minute);

  int get _totalMinutes => _endDateTime.difference(_startDateTime).inMinutes;


  String? get _validationError {
    if (_endDateTime.isBefore(_startDateTime) || _endDateTime.isAtSameMomentAs(_startDateTime)) {
      return 'End must be after start';
    }
    if (_totalMinutes < 60) {
      return 'Minimum trip duration is 1 hour';
    }
    if (_totalMinutes > 14 * 24 * 60) {
      return 'Maximum trip duration is 14 days';
    }
    return null;
  }

  bool get _isValid => _validationError == null;

  String get _locationSummary {
    switch (_locationSource) {
      case 'gps':
        return 'Using your current location';
      case 'city_center':
        return 'Using city center';
      case 'manual':
        return 'Custom location set';
      default:
        return 'Detecting location…';
    }
  }

  Future<void> _generateTrip() async {
    if (!_isValid) return;

    final tripService = context.read<TripService>();
    final authService = context.read<AuthService>();
    final profileService = context.read<ProfileService>();

    final profileId = profileService.profileId;
    final token = authService.accessToken;
    if (profileId == null || token == null) {
      setState(() => _error = 'Please log in first');
      return;
    }

    if (_pinLat == null || _pinLng == null) {
      setState(() => _error = 'Location not available');
      return;
    }

    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      final trip = await tripService.generateTrip(
        profileId: profileId,
        centerLat: _pinLat!,
        centerLng: _pinLng!,
        startDate: _formatDate(_startDate),
        endDate: _formatDate(_endDate),
        accessToken: token,
        durationMin: _totalMinutes,
        maxStops: (_totalMinutes ~/ 30).clamp(3, 30),
        startTime: _formatTime(_startTime),
        endHardness: _endHardness,
      );

      if (mounted) {
        setState(() => _isLoading = false);
        context.push('/trip/${trip.tripId}');
      }
    } on TripServiceException catch (e) {
      if (mounted) {
        setState(() { _isLoading = false; _error = e.message; });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _error = 'Something went wrong. Please try again.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;

    return Scaffold(
      appBar: AppBar(
        title: Text(_cityDisplayName),
        backgroundColor: cs.surface,
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
              child: _buildTabBar(cs, tt),
            ),
            const SizedBox(height: 16),
            Expanded(
              child: TabBarView(
                controller: _tabController,
                children: [
                  _buildDurationTab(cs, tt),
                  _buildStartPointTab(cs, tt),
                ],
              ),
            ),
            _buildBottomSection(cs, tt),
          ],
        ),
      ),
    );
  }

  Widget _buildTabBar(ColorScheme cs, TextTheme tt) {
    return Container(
      decoration: BoxDecoration(
        color: cs.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      padding: const EdgeInsets.all(4),
      child: TabBar(
        controller: _tabController,
        indicator: BoxDecoration(
          color: cs.surface,
          borderRadius: BorderRadius.circular(9),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.08),
              blurRadius: 4,
              offset: const Offset(0, 1),
            ),
          ],
        ),
        indicatorSize: TabBarIndicatorSize.tab,
        dividerColor: Colors.transparent,
        labelColor: cs.onSurface,
        unselectedLabelColor: cs.onSurfaceVariant,
        labelStyle: tt.titleSmall?.copyWith(fontWeight: FontWeight.w600),
        unselectedLabelStyle: tt.titleSmall,
        tabs: [
          Tab(
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.schedule, size: 18),
                const SizedBox(width: 8),
                const Text('Duration'),
              ],
            ),
          ),
          Tab(
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.location_on_outlined, size: 18),
                const SizedBox(width: 8),
                const Text('Start Point'),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDurationTab(ColorScheme cs, TextTheme tt) {
    final validation = _validationError;

    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'When are you visiting?',
            style: tt.headlineSmall?.copyWith(
              fontWeight: FontWeight.bold,
              color: cs.onSurface,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Default: 1 day (9:00 AM – 6:00 PM)',
            style: tt.bodyMedium?.copyWith(color: cs.onSurfaceVariant),
          ),
          const SizedBox(height: 32),

          _DateTimeSection(
            label: 'From',
            date: _startDate,
            time: _startTime,
            onDateTap: () => _pickDate(isStart: true),
            onTimeTap: () => _pickTime(isStart: true),
          ),
          const SizedBox(height: 24),

          _DateTimeSection(
            label: 'To (inclusive)',
            date: _endDate,
            time: _endTime,
            onDateTap: () => _pickDate(isStart: false),
            onTimeTap: () => _pickTime(isStart: false),
          ),
          const SizedBox(height: 12),

          // The ONE question, in plain words. Skipping it keeps
          // "Should end then" — a hard deadline (the owner's ruling).
          Text('Is the end time fixed?', style: tt.labelLarge),
          const SizedBox(height: 6),
          SegmentedButton<String>(
            key: const Key('end-hardness'),
            segments: const [
              ButtonSegment(value: 'open', label: Text('Just a guess')),
              ButtonSegment(value: 'firm', label: Text('Should end then')),
              ButtonSegment(
                  value: 'wall', label: Text("Booked — can't be late")),
            ],
            selected: {_endHardness},
            onSelectionChanged: (sel) => setState(() => _endHardness = sel.first),
          ),
          const SizedBox(height: 16),

          if (_isValid)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: cs.primaryContainer,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.timer_outlined, size: 16, color: cs.onPrimaryContainer),
                  const SizedBox(width: 8),
                  Text(
                    _formatDurationSummary(),
                    style: tt.bodyMedium?.copyWith(
                      color: cs.onPrimaryContainer,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),

          if (validation != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                validation,
                style: tt.bodySmall?.copyWith(color: cs.error),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildStartPointTab(ColorScheme cs, TextTheme tt) {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Where do you want to start?',
            style: tt.headlineSmall?.copyWith(
              fontWeight: FontWeight.bold,
              color: cs.onSurface,
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Icon(
                _locationSource == 'gps' ? Icons.my_location : Icons.location_city,
                size: 16,
                color: cs.onSurfaceVariant,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Default: ${_locationSource == 'pending' ? 'Detecting…' : _locationSummary}',
                  style: tt.bodyMedium?.copyWith(color: cs.onSurfaceVariant),
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),

          if (!kIsWeb)
            TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: 'Search a place or paste coordinates',
                prefixIcon: const Icon(Icons.search, size: 20),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16, vertical: 12,
                ),
              ),
              onSubmitted: _onSearchSubmitted,
            ),
          const SizedBox(height: 16),

          if (_locationResolved && _pinLat != null && _pinLng != null && !kIsWeb)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                'Tap the map to set your starting point',
                style: tt.bodySmall?.copyWith(color: cs.onSurfaceVariant),
              ),
            ),
          if (_locationResolved && _pinLat != null && _pinLng != null && !kIsWeb)
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: SizedBox(
                height: 280,
                width: double.infinity,
                child: AppleMap(
                  initialCameraPosition: CameraPosition(
                    target: LatLng(_pinLat!, _pinLng!),
                    zoom: 14,
                  ),
                  onMapCreated: (controller) => _mapController = controller,
                  onTap: _onMapTap,
                  annotations: {
                    Annotation(
                      annotationId: AnnotationId('trip_center'),
                      position: LatLng(_pinLat!, _pinLng!),
                    ),
                  },
                ),
              ),
            )
          else if (!_locationResolved && !kIsWeb)
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Container(
                height: 280,
                width: double.infinity,
                color: cs.surfaceContainerHighest,
                child: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      CircularProgressIndicator(
                        strokeWidth: 2,
                        color: cs.onSurfaceVariant,
                      ),
                      const SizedBox(height: 12),
                      Text(
                        'Detecting your location…',
                        style: tt.bodySmall?.copyWith(color: cs.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildBottomSection(ColorScheme cs, TextTheme tt) {
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border(
          top: BorderSide(color: cs.outlineVariant.withValues(alpha: 0.3)),
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(
                _error!,
                style: tt.bodySmall?.copyWith(color: cs.error),
                textAlign: TextAlign.center,
              ),
            ),
          SizedBox(
            width: double.infinity,
            height: 56,
            child: FilledButton(
              onPressed: _isValid && !_isLoading && _locationResolved ? _generateTrip : null,
              child: _isLoading
                  ? SizedBox(
                      height: 24,
                      width: 24,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: cs.onPrimary,
                      ),
                    )
                  : const Text(
                      'Generate Trip Plan',
                      style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                    ),
            ),
          ),
        ],
      ),
    );
  }

  String _formatDurationSummary() {
    final days = _endDate.difference(_startDate).inDays;
    final hours = _totalMinutes ~/ 60;
    final mins = _totalMinutes % 60;

    if (days > 0) {
      final nightWord = days == 1 ? 'night' : 'nights';
      final dayWord = (days + 1) == 1 ? 'day' : 'days';
      return '${days + 1} $dayWord, $days $nightWord';
    }
    if (hours > 0 && mins > 0) return '$hours h $mins min';
    if (hours > 0) return '$hours hour${hours > 1 ? 's' : ''}';
    return '$mins min';
  }

  Future<void> _pickDate({required bool isStart}) async {
    final initial = isStart ? _startDate : _endDate;
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (picked != null) {
      setState(() {
        if (isStart) {
          _startDate = picked;
          if (_endDate.isBefore(_startDate)) _endDate = _startDate;
        } else {
          _endDate = picked;
        }
      });
    }
  }

  Future<void> _pickTime({required bool isStart}) async {
    final initial = isStart ? _startTime : _endTime;
    final picked = await showTimePicker(context: context, initialTime: initial);
    if (picked != null) {
      setState(() {
        if (isStart) {
          _startTime = picked;
        } else {
          _endTime = picked;
        }
      });
    }
  }

  String _formatDate(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _formatTime(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

class _DateTimeSection extends StatelessWidget {
  final String label;
  final DateTime date;
  final TimeOfDay time;
  final VoidCallback onDateTap;
  final VoidCallback onTimeTap;

  const _DateTimeSection({
    required this.label,
    required this.date,
    required this.time,
    required this.onDateTap,
    required this.onTimeTap,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;
    final months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    final weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    final dateStr = '${weekdays[date.weekday - 1]}, ${months[date.month - 1]} ${date.day}';
    final timeStr = '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: tt.titleMedium?.copyWith(color: cs.onSurface)),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              flex: 3,
              child: OutlinedButton.icon(
                onPressed: onDateTap,
                icon: const Icon(Icons.calendar_today, size: 18),
                label: Text(dateStr),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(0, 48),
                  alignment: Alignment.centerLeft,
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              flex: 2,
              child: OutlinedButton.icon(
                onPressed: onTimeTap,
                icon: const Icon(Icons.access_time, size: 18),
                label: Text(timeStr),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(0, 48),
                  alignment: Alignment.centerLeft,
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
