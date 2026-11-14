import 'package:flutter/material.dart';

/// Category tile fill colors, keyed by lens `name`. These are the secondary
/// category colors that sit beneath the accent, kept out of `LensTile` so
/// they can be retuned in one file without touching widget code.
///
/// The app has one accent, cobalt, and a harmonized secondary set beneath it.
/// Each hue here is kept for wayfinding but held to <=46% saturation and a
/// tight lightness band, so the 21 categories read as one muted family that
/// sits BENEATH cobalt rather than competing with it.
const Map<String, Color> kLensCategoryColors = <String, Color>{
  'hidden_history': Color(0xFF4A4F86),
  'war_conflict': Color(0xFFA74C4C),
  'dark_history': Color(0xFF643B96),
  'social_change': Color(0xFFB08041),
  'historic_arch': Color(0xFF369289),
  'modern_design': Color(0xFF3D81A5),
  'music_heritage': Color(0xFFA63F67),
  'visual_art': Color(0xFFAE7840),
  'street_art': Color(0xFF709252),
  'film_tv': Color(0xFF804399),
  'historic_cuisine': Color(0xFFB05C41),
  'markets_street_food': Color(0xFFBD674C),
  'local_legends': Color(0xFF4E8751),
  'literary_heritage': Color(0xFF5F6E75),
  'famous_residents': Color(0xFFB08741),
  'historic_worship': Color(0xFF338A7F),
  'sacred_traditions': Color(0xFF89469B),
  'parks_gardens': Color(0xFF538B56),
  'waterways_views': Color(0xFF3B7AA0),
  'historic_markets': Color(0xFFAA653F),
  'science_tech': Color(0xFF3D6EA6),
};

/// A glyph per lens category, keyed by lens `name` — the icon each interest
/// chip paints so a category reads at a glance. Kept beside the color map so
/// the two stay in sync.
const Map<String, IconData> kLensCategoryIcons = <String, IconData>{
  'hidden_history': Icons.history_edu,
  'war_conflict': Icons.shield_outlined,
  'dark_history': Icons.nightlight_round,
  'social_change': Icons.campaign_outlined,
  'historic_arch': Icons.account_balance,
  'modern_design': Icons.architecture,
  'music_heritage': Icons.music_note,
  'visual_art': Icons.palette_outlined,
  'street_art': Icons.brush_outlined,
  'film_tv': Icons.movie_outlined,
  'historic_cuisine': Icons.restaurant,
  'markets_street_food': Icons.storefront_outlined,
  'local_legends': Icons.auto_stories_outlined,
  'literary_heritage': Icons.menu_book_outlined,
  'famous_residents': Icons.person_pin_circle_outlined,
  'historic_worship': Icons.church_outlined,
  'sacred_traditions': Icons.self_improvement,
  'parks_gardens': Icons.park_outlined,
  'waterways_views': Icons.sailing_outlined,
  'historic_markets': Icons.storefront,
  'science_tech': Icons.science_outlined,
};
