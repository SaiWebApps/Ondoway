"""Background audio is not a feature, it is the product: the phone KEEPS PLAYING pocketed
and hears interruptions through ONE door.

What only a file read can assert — the iOS background modes the app declares, the audio
session dependency, the one provider door the interruption events come through and the
session being configured — is asserted here and says so. The BEHAVIOURAL half (pause on a
call, resume at the cut sentence's start inside the footprint, the couple's tap, the missed
close queued off the footprint, ducking changes nothing) lives in
mobile/test/services/session_interruptions_test.dart. Free tier: files only.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOBILE = ROOT / "mobile"


def test_the_app_declares_background_audio_and_location():
    plist = plistlib.loads((MOBILE / "ios" / "Runner" / "Info.plist").read_bytes())
    modes = plist.get("UIBackgroundModes") or []
    assert "audio" in modes, "pocketed, the piece stops 26 s in (background audio is mandatory)"
    assert "location" in modes, "pocketed, the next footprint is never reached"


def test_the_audio_session_is_a_declared_dependency_and_is_configured():
    pubspec = (MOBILE / "pubspec.yaml").read_text(encoding="utf-8")
    deps = pubspec.split("dev_dependencies:")[0]
    assert re.search(r"^\s+audio_session:\s*\^?0\.1\.\d+", deps, re.M), (
        "audio_session is not a direct dependency (it is what just_audio's session API is)"
    )
    audio = (MOBILE / "lib" / "services" / "audio_service.dart").read_text(encoding="utf-8")
    assert "AudioSession.instance" in audio and "AudioSessionConfiguration.speech()" in audio, (
        "the session is never configured: iOS will not keep the piece playing pocketed"
    )
    assert "interruptionEventStream" in audio, "the session's interruptions are never heard"


def test_interruptions_reach_the_service_through_one_provider_door():
    providers = (MOBILE / "lib" / "services" / "providers.dart").read_text(encoding="utf-8")
    playback = (MOBILE / "lib" / "services" / "tour_playback_service.dart").read_text(
        encoding="utf-8"
    )
    assert "enum AudioInterruptionKind" in providers
    assert "Stream<AudioInterruptionKind> get interruptions" in providers
    assert playback.count("void _onInterruption(AudioInterruptionKind kind)") == 1, (
        "the interruption policy has not exactly one site"
    )
    assert "interruptions.listen(" in playback
    # The policy resumes through the ONE door to a position (playFrom), never a
    # second seek path, and the missed close goes through the ONE queue door.
    policy = playback[playback.index("void _onInterruption(AudioInterruptionKind kind)") :]
    policy = policy[: policy.index("\n  }\n") + 4]
    assert "sentenceStartSeconds(" in playback and "playFrom(" in playback
    assert "_queue(" in playback
