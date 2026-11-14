import 'dart:io';

import 'package:flutter/services.dart';
import 'package:ondoway/models/trip.dart';
import 'package:path_provider/path_provider.dart';

/// The two on-device proof pages' shared plumbing.
///
/// Both pages drop stops around the tester and walk to them with the phone
/// locked, and both were building those stops and caching their clip with their
/// own private copy of the same twenty lines. One copy, here, so the two pages
/// cannot drift apart when either is edited.
///
/// These pages exist because a green test suite is not evidence for background
/// audio: the simulator and a real iPhone can disagree about it, and
/// the only proof that counts is a locked phone in a pocket on a pavement.

/// Wider than a real doorway, on purpose.
///
/// Measured GPS cross-track is around ten metres, so a walker crossing a bare
/// 10 m circle can pass straight through it without a single fix landing
/// inside — and the proof would prove nothing. Twenty metres trips reliably
/// while still requiring the tester to START outside it and walk in.
///
/// It rides the STOP, exactly as a server-placed footprint does. There is no
/// service-wide radius to set: a 140 m courtyard and a doorway are different
/// places, and one number cannot say so.
const double kProofRadiusMeters = 20.0;

/// A synthetic stop at [lat]/[lng], carrying a real footprint.
///
/// [beatId] doubles as the stop id and the cache key, so [cacheProofClip] and
/// this agree on where the audio lives without either knowing about the other.
ItineraryStop proofStop({
  required int order,
  required String beatId,
  required String name,
  required double lat,
  required double lng,
}) =>
    ItineraryStop(
      sortOrder: order,
      stopId: beatId,
      poiId: 'proof-poi-$order',
      poiName: name,
      lat: lat,
      lng: lng,
      beatId: beatId,
      lensName: 'history',
      lensDisplay: 'History',
      durationMin: 1,
      importanceTier: 3,
      startTime: '09:0$order',
      // Unused in practice: the cache hit below wins inside AudioService.
      audioUrl: 'cached://$beatId',
      trigger: const StopTrigger(radiusM: kProofRadiusMeters),
    );

/// Put the bundled clip where AudioService will find it for [beatId].
///
/// Mirrors AudioService's own cache convention — `temp/ondoway_audio/<id>.mp3` —
/// so the tour plays a LOCAL file rather than streaming a cold URL, which is the
/// path the locked screen actually has to survive. The bundled asset is a WAV
/// written under an .mp3 name on purpose: iOS AVFoundation content-sniffs the
/// header, so the extension is only a hint. Debug only.
Future<void> cacheProofClip(String beatId) async {
  final bytes = await rootBundle.load('assets/audio/arrived.wav');
  final dir = Directory('${(await getTemporaryDirectory()).path}/ondoway_audio');
  if (!await dir.exists()) await dir.create(recursive: true);
  await File('${dir.path}/$beatId.mp3').writeAsBytes(bytes.buffer.asUint8List());
}
